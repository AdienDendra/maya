"""Component Flood and Smooth UI operations."""

import builtins

import maya.cmds as cmds

from ad_skin_tools.components import flood
from ad_skin_tools.components import smooth
from ad_skin_tools.ui import joint_list


CTRL_FLOOD_BUTTON = "adSkin_floodSelectedToJointButton"
CTRL_SMOOTH_BUTTON = "adSkin_smoothSelectedComponentsButton"
CTRL_COMPONENT_STATUS = "adSkin_componentOperationStatus"

_TOOL_WINDOW = None
_SKIN_OPERATIONS = None
_SMOOTHING_BIND_SECTION = None


def install(
    tool_window_module,
    skin_operations_module,
    smoothing_bind_section_module,
) -> None:
    global _TOOL_WINDOW, _SKIN_OPERATIONS, _SMOOTHING_BIND_SECTION

    _TOOL_WINDOW = tool_window_module
    _SKIN_OPERATIONS = skin_operations_module
    _SMOOTHING_BIND_SECTION = smoothing_bind_section_module

    _TOOL_WINDOW.show_help = show_help
    _TOOL_WINDOW.WINDOW_LABEL = "AD Skin Weights Tool"
    _TOOL_WINDOW.WINDOW_HEIGHT = 665
    _TOOL_WINDOW.WINDOW_WIDTH = 340


def build_section() -> None:
    cmds.frameLayout(
        label="Component",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    _SKIN_OPERATIONS._named_button_row(
        [
            (
                CTRL_FLOOD_BUTTON,
                "Flood",
                lambda *_: apply_component_flood(),
            ),
            (
                CTRL_SMOOTH_BUTTON,
                "Smooth",
                lambda *_: apply_component_smooth(),
            ),
        ],
        height=38,
    )
    cmds.text(
        CTRL_COMPONENT_STATUS,
        label="",
        align="left",
        wordWrap=True,
        visible=False,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def apply_component_flood() -> None:
    wait_cursor_active = False

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            raise RuntimeError(
                "Component Flood requires an existing skinCluster.\n\n"
                "Use Bind Skin first."
            )

        selected_joints = joint_list.selected_joint_paths()
        if len(selected_joints) != 1:
            raise RuntimeError(
                "Select exactly one target joint in the UI influence list."
            )
        target_joint = selected_joints[0]

        _set_flood_busy(
            True,
            "Reading component falloff and redistributing weights...",
        )
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        staged_joints = builtins.list(
            _TOOL_WINDOW._STATE.get("joints", [])
        )
        staged_locks = set(
            _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
        )
        result = flood.flood_selected_components_to_joint(
            mesh_shape=_TOOL_WINDOW._STATE["mesh_shape"],
            mesh_transform=_TOOL_WINDOW._STATE["mesh_transform"],
            target_joint=target_joint,
            target_locked_override=joint_list.joint_is_locked(target_joint),
        )

        if not result.target_locked:
            joint_list.sync_after_flood_preserving_pending(
                staged_joints,
                staged_locks,
            )
        joint_list.select_joint_paths([result.target_joint])

        builtins.AD_SKIN_FLOOD_RESULT = result
        flood.print_component_flood_report(result)

        short_name = result.target_joint.split("|")[-1]
        if result.target_locked:
            _TOOL_WINDOW._info(
                "Flood ignored: {} is locked.".format(short_name)
            )
            return

        suffixes = []
        if result.influence_added:
            suffixes.append("Added new influence.")
        if result.protected_vertex_count:
            suffixes.append(
                "{} locked vertex/vertices protected.".format(
                    result.protected_vertex_count
                )
            )
        if result.ignored_component_count:
            suffixes.append(
                "{} other component(s) ignored.".format(
                    result.ignored_component_count
                )
            )
        suffix = " " + " ".join(suffixes) if suffixes else ""

        if result.soft_selection_used:
            message = (
                "Flood complete: {} of {} affected vertices set to {} "
                "from {:.3f} to {:.3f}.{}"
            ).format(
                result.flooded_vertex_count,
                result.vertex_count,
                short_name,
                result.minimum_target_weight,
                result.maximum_target_weight,
                suffix,
            )
        else:
            message = (
                "Flood complete: {} of {} selected vertices set to {} = 1.0.{}"
            ).format(
                result.flooded_vertex_count,
                result.vertex_count,
                short_name,
                suffix,
            )
        _TOOL_WINDOW._info(message)
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)
    finally:
        if wait_cursor_active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass
        _set_flood_busy(False)


def apply_component_smooth() -> None:
    wait_cursor_active = False

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            raise RuntimeError(
                "Component Smooth requires an existing skinCluster.\n\n"
                "Use Bind Skin first."
            )

        smoothing_level = _SMOOTHING_BIND_SECTION._query_iterations()
        if smoothing_level == 0:
            cmds.warning(
                "Component Smooth requires Smoothing Iterations of at least 1."
            )
            return

        scope = smooth.collect_smooth_scope(
            mesh_shape=_TOOL_WINDOW._STATE["mesh_shape"],
            mesh_transform=_TOOL_WINDOW._STATE["mesh_transform"],
        )

        if scope.whole_object:
            response = cmds.confirmDialog(
                title="Smooth Entire Mesh",
                message=(
                    "Smooth all current skin weights on the loaded mesh?\n\n"
                    "Smoothing Iterations: {}\n"
                    "Locked influences will remain unchanged."
                ).format(smoothing_level),
                button=["Yes", "Cancel"],
                defaultButton="Yes",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if response != "Yes":
                return

        _set_smooth_busy(
            True,
            "Smoothing selected skin weights...",
        )
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        result = smooth.smooth_skin_weights(
            scope=scope,
            smoothing_level=smoothing_level,
        )

        builtins.AD_SKIN_SMOOTH_RESULT = result
        smooth.print_component_smooth_report(result)

        mode = "entire mesh" if result.whole_object else "selection"
        suffixes = []
        if result.skipped_empty_vertex_ids:
            suffixes.append(
                "{} empty vertex/vertices skipped.".format(
                    len(result.skipped_empty_vertex_ids)
                )
            )
        if result.skipped_locked_vertex_ids:
            suffixes.append(
                "{} fully locked vertex/vertices skipped.".format(
                    len(result.skipped_locked_vertex_ids)
                )
            )
        suffix = " " + " ".join(suffixes) if suffixes else ""

        _TOOL_WINDOW._info(
            "Smooth complete: {} of {} vertices changed in {}, level {}.{}".format(
                result.smoothed_vertex_count,
                result.selected_vertex_count,
                mode,
                result.smoothing_level,
                suffix,
            )
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)
    finally:
        if wait_cursor_active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass
        _set_smooth_busy(False)


def _set_flood_busy(busy: bool, status: str = "") -> None:
    _set_component_busy(
        busy=busy,
        active_control=CTRL_FLOOD_BUTTON,
        active_label="Flooding..." if busy else "Flood",
        status=status,
    )


def _set_smooth_busy(busy: bool, status: str = "") -> None:
    _set_component_busy(
        busy=busy,
        active_control=CTRL_SMOOTH_BUTTON,
        active_label="Smoothing..." if busy else "Smooth",
        status=status,
    )


def _set_component_busy(
    busy: bool,
    active_control: str,
    active_label: str,
    status: str,
) -> None:
    if _TOOL_WINDOW is None:
        return

    _SKIN_OPERATIONS._set_common_enabled(not busy)
    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    if cmds.button(active_control, exists=True):
        cmds.button(
            active_control,
            edit=True,
            enable=not busy,
            label=active_label,
        )

    if cmds.text(CTRL_COMPONENT_STATUS, exists=True):
        cmds.text(
            CTRL_COMPONENT_STATUS,
            edit=True,
            label=status if busy else "",
            visible=bool(busy),
        )

    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool",
        message=(
            "Binding\n"
            "- Smoothing Iterations is shared by Bind Skin, Add Influence, "
            "and Component Smooth.\n"
            "- Bind Skin creates the initial skinCluster from all listed joints.\n"
            "- Add Influence calculates Region ownership for selected pending joints.\n\n"
            "Component Flood\n"
            "- Select exactly one target joint in the influence list.\n"
            "- Select vertices, edges, or faces on the loaded mesh.\n"
            "- Soft Selection off writes target weight 1.0.\n"
            "- Soft Selection on writes Maya's per-vertex falloff.\n"
            "- Remaining weight is returned proportionally to previous donors.\n\n"
            "Component Smooth\n"
            "- Smoothing Iterations must be at least 1.\n"
            "- Select vertices, edges, or faces; no target joint is required.\n"
            "- Soft Selection controls smoothing strength per vertex.\n"
            "- Selecting the loaded mesh object offers whole-mesh smoothing.\n\n"
            "Locked influence values remain unchanged."
        ),
        button=["OK"],
    )
