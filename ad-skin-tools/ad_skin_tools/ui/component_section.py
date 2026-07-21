"""Component Flood and Smooth UI operations."""

import builtins

import maya.cmds as cmds

from ad_skin_tools.components import flood
from ad_skin_tools.components import smooth
from ad_skin_tools.ui import joint_list


CTRL_COMPONENT_BLEND = "adSkin_componentSmoothBlend"
CTRL_COMPONENT_PASSES = "adSkin_componentSmoothPasses"
CTRL_FLOOD_BUTTON = "adSkin_floodSelectedToJointButton"
CTRL_SMOOTH_BUTTON = "adSkin_smoothSelectedComponentsButton"
CTRL_COMPONENT_STATUS = "adSkin_componentOperationStatus"

STATE_COMPONENT_BLEND = "component_smooth_blend"
STATE_COMPONENT_PASSES = "component_smooth_passes"

_TOOL_WINDOW = None
_SKIN_OPERATIONS = None


def install(
    tool_window_module,
    skin_operations_module,
    _smoothing_bind_section_module,
) -> None:
    global _TOOL_WINDOW, _SKIN_OPERATIONS

    _TOOL_WINDOW = tool_window_module
    _SKIN_OPERATIONS = skin_operations_module

    _TOOL_WINDOW._STATE.setdefault(
        STATE_COMPONENT_BLEND,
        smooth.DEFAULT_COMPONENT_BLEND,
    )
    _TOOL_WINDOW._STATE.setdefault(
        STATE_COMPONENT_PASSES,
        smooth.DEFAULT_COMPONENT_PASSES,
    )

    _TOOL_WINDOW.show_help = show_help
    _TOOL_WINDOW.WINDOW_LABEL = "AD Skin Weights Tool"
    _TOOL_WINDOW.WINDOW_HEIGHT = 720
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

    cmds.floatSliderGrp(
        CTRL_COMPONENT_BLEND,
        label="Blend",
        field=True,
        minValue=smooth.MINIMUM_COMPONENT_BLEND,
        maxValue=smooth.MAXIMUM_COMPONENT_BLEND,
        fieldMinValue=smooth.MINIMUM_COMPONENT_BLEND,
        fieldMaxValue=smooth.MAXIMUM_COMPONENT_BLEND,
        value=_stored_blend(),
        step=0.05,
        precision=3,
        columnWidth3=(90, 52, 170),
        adjustableColumn=3,
        dragCommand=_store_blend,
        changeCommand=_store_blend,
    )
    cmds.intSliderGrp(
        CTRL_COMPONENT_PASSES,
        label="Passes",
        field=True,
        minValue=smooth.MINIMUM_COMPONENT_PASSES,
        maxValue=smooth.MAXIMUM_COMPONENT_PASSES,
        fieldMinValue=smooth.MINIMUM_COMPONENT_PASSES,
        fieldMaxValue=smooth.MAXIMUM_COMPONENT_PASSES,
        value=_stored_passes(),
        step=1,
        columnWidth3=(90, 52, 170),
        adjustableColumn=3,
        dragCommand=_store_passes,
        changeCommand=_store_passes,
    )

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

        blend = _query_blend()
        passes = _query_passes()
        scope = smooth.collect_smooth_scope(
            mesh_shape=_TOOL_WINDOW._STATE["mesh_shape"],
            mesh_transform=_TOOL_WINDOW._STATE["mesh_transform"],
        )

        if scope.whole_object:
            response = cmds.confirmDialog(
                title="Smooth Entire Mesh",
                message=(
                    "Smooth all current skin weights on the loaded mesh?\n\n"
                    "Blend: {:.3f}\n"
                    "Passes: {}\n"
                    "Locked influences will remain unchanged."
                ).format(blend, passes),
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
            blend=blend,
            passes=passes,
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
            (
                "Smooth complete: {} of {} vertices changed in {}. "
                "Blend {:.3f}, {} pass(es).{}"
            ).format(
                result.smoothed_vertex_count,
                result.selected_vertex_count,
                mode,
                result.blend,
                result.passes,
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


def _stored_blend() -> float:
    value = float(
        _TOOL_WINDOW._STATE.get(
            STATE_COMPONENT_BLEND,
            smooth.DEFAULT_COMPONENT_BLEND,
        )
    )
    return max(
        smooth.MINIMUM_COMPONENT_BLEND,
        min(smooth.MAXIMUM_COMPONENT_BLEND, value),
    )


def _store_blend(value=None, *_unused) -> None:
    if value is None:
        value = _stored_blend()
    value = max(
        smooth.MINIMUM_COMPONENT_BLEND,
        min(smooth.MAXIMUM_COMPONENT_BLEND, float(value)),
    )
    _TOOL_WINDOW._STATE[STATE_COMPONENT_BLEND] = value


def _query_blend() -> float:
    if cmds.floatSliderGrp(CTRL_COMPONENT_BLEND, exists=True):
        value = cmds.floatSliderGrp(
            CTRL_COMPONENT_BLEND,
            query=True,
            value=True,
        )
        _store_blend(value)
    return _stored_blend()


def _stored_passes() -> int:
    value = int(
        _TOOL_WINDOW._STATE.get(
            STATE_COMPONENT_PASSES,
            smooth.DEFAULT_COMPONENT_PASSES,
        )
    )
    return max(
        smooth.MINIMUM_COMPONENT_PASSES,
        min(smooth.MAXIMUM_COMPONENT_PASSES, value),
    )


def _store_passes(value=None, *_unused) -> None:
    if value is None:
        value = _stored_passes()
    value = max(
        smooth.MINIMUM_COMPONENT_PASSES,
        min(smooth.MAXIMUM_COMPONENT_PASSES, int(value)),
    )
    _TOOL_WINDOW._STATE[STATE_COMPONENT_PASSES] = value


def _query_passes() -> int:
    if cmds.intSliderGrp(CTRL_COMPONENT_PASSES, exists=True):
        value = cmds.intSliderGrp(
            CTRL_COMPONENT_PASSES,
            query=True,
            value=True,
        )
        _store_passes(value)
    return _stored_passes()


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
    _set_smooth_controls_enabled(not busy)
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


def _set_smooth_controls_enabled(enabled: bool) -> None:
    if cmds.floatSliderGrp(CTRL_COMPONENT_BLEND, exists=True):
        cmds.floatSliderGrp(
            CTRL_COMPONENT_BLEND,
            edit=True,
            enable=bool(enabled),
        )
    if cmds.intSliderGrp(CTRL_COMPONENT_PASSES, exists=True):
        cmds.intSliderGrp(
            CTRL_COMPONENT_PASSES,
            edit=True,
            enable=bool(enabled),
        )


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool",
        message=(
            "Binding\n"
            "Smoothing Iterations applies to Bind Skin and Add Influence. "
            "Level 0 keeps the final Region blocking as hard weights. "
            "Positive levels run topology diffusion and keep Region as the "
            "ownership authority.\n\n"
            "Component Flood\n"
            "Select exactly one target joint in the influence list, then select "
            "vertices, edges, or faces on the loaded mesh. With Soft Selection "
            "disabled, the target receives weight 1.0. With Soft Selection "
            "enabled, Maya falloff controls the target weight.\n\n"
            "Component Smooth\n"
            "Blend controls how far each pass moves the current weights toward "
            "the average of connected vertices. Passes controls how many times "
            "that averaging is repeated. Soft Selection multiplies Blend per "
            "vertex, so the hard selected area receives the full Blend value and "
            "the falloff area receives less. Select the loaded mesh object to "
            "smooth the entire mesh.\n\n"
            "Locked influence values remain unchanged."
        ),
        button=["OK"],
    )
