"""v4.0 UI integration for Maya-style selected-component flooding.

The existing v3 Region UI remains the source of truth for initial binding. This
module composes a second operation into that window and relaxes the joint-list
editing rules only enough to stage a new target influence for Component Flood.
"""

import builtins
import maya.cmds as cmds

from ad_skin_tools.core import component_flood
from ad_skin_tools.core.selection import get_selected_joints
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter


CTRL_FLOOD_BUTTON = "adSkin_floodSelectedToJointButton"
CTRL_FLOOD_STATUS = "adSkin_floodSelectedToJointStatus"

_TOOL_WINDOW = None
_ORIGINAL_REMOVE_SELECTED_JOINTS = None
_ORIGINAL_REMOVE_ALL_JOINTS = None


def install(tool_window_module) -> None:
    """Compose the v4.0 flood operation into the existing tool window module."""

    global _TOOL_WINDOW
    global _ORIGINAL_REMOVE_SELECTED_JOINTS
    global _ORIGINAL_REMOVE_ALL_JOINTS

    _TOOL_WINDOW = tool_window_module
    if getattr(tool_window_module, "_V4_COMPONENT_FLOOD_INSTALLED", False):
        return

    _ORIGINAL_REMOVE_SELECTED_JOINTS = tool_window_module.remove_selected_joints
    _ORIGINAL_REMOVE_ALL_JOINTS = tool_window_module.remove_all_joints

    tool_window_module._build_initial_bind_section = _build_bind_sections
    tool_window_module.add_selected_joints = add_selected_joints
    tool_window_module.remove_selected_joints = remove_selected_joints
    tool_window_module.remove_all_joints = remove_all_joints
    tool_window_module.show_help = show_help
    tool_window_module.WINDOW_LABEL = "AD Skin Weights Tool v4.0"
    tool_window_module.WINDOW_HEIGHT = max(
        int(tool_window_module.WINDOW_HEIGHT),
        760,
    )
    tool_window_module._V4_COMPONENT_FLOOD_INSTALLED = True


def _build_bind_sections() -> None:
    _build_initial_bind_section_v4()
    _build_component_flood_section()


def _build_initial_bind_section_v4() -> None:
    cmds.frameLayout(
        label="Initial Automatic Bind (Region v3)",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    cmds.text(
        label="Automatic Surface",
        align="left",
        font="boldLabelFont",
    )
    cmds.text(
        label=(
            "For an unskinned mesh: automatically calculate Region ownership "
            "across all connected and disconnected surface components."
        ),
        align="left",
        wordWrap=True,
    )
    cmds.button(
        _TOOL_WINDOW.CTRL_BIND_BUTTON,
        label="Bind Automatic Surface",
        height=38,
        command=lambda *_: _TOOL_WINDOW.apply_operation(),
        annotation=(
            "Bind the loaded unskinned mesh using all joints in the UI list. "
            "No fallback joint or manual shell assignment is required."
        ),
    )
    _TOOL_WINDOW._create_bind_progress_bar()
    cmds.text(
        _TOOL_WINDOW.CTRL_BIND_STATUS,
        label="",
        align="left",
        wordWrap=True,
        visible=False,
    )
    cmds.text(
        label=(
            "Region v3 writes exactly one influence at weight 1.0 per vertex. "
            "Use Component Flood below for explicit local overrides."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_component_flood_section() -> None:
    cmds.frameLayout(
        label="Component Flood (v4.0)",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    cmds.text(
        label="Flood Selected to Joint",
        align="left",
        font="boldLabelFont",
    )
    cmds.text(
        label=(
            "For an existing skinCluster: select exactly one target joint in "
            "the UI list, then select vertices, edges, or faces on the loaded "
            "mesh. Components from other meshes are ignored."
        ),
        align="left",
        wordWrap=True,
    )
    cmds.button(
        CTRL_FLOOD_BUTTON,
        label="Flood Selected to Joint",
        height=38,
        command=lambda *_: apply_component_flood(),
        annotation=(
            "Add the UI-selected joint as an influence when needed, then write "
            "Replace 1.0 to selected vertices only."
        ),
    )
    cmds.text(
        CTRL_FLOOD_STATUS,
        label="",
        align="left",
        wordWrap=True,
        visible=False,
    )
    cmds.text(
        label=(
            "Selected vertices become target joint = 1.0 and all other "
            "influences = 0.0. Unselected vertices are preserved."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def add_selected_joints() -> None:
    """Allow joints to be staged in the UI for either initial bind or flood."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        selected_joints = get_selected_joints()
        if not selected_joints:
            cmds.warning("No selected joints found.")
            return

        current_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        added = []
        for joint in selected_joints:
            normalized = _TOOL_WINDOW._normalize_joint_path(joint)
            if not _TOOL_WINDOW._joint_exists_in_list(normalized, current_joints):
                current_joints.append(normalized)
                added.append(normalized)

        _TOOL_WINDOW._set_joint_list(current_joints)
        _TOOL_WINDOW._update_joint_count_label()

        if not added:
            cmds.warning("Selected joints already exist in the list.")
            return

        if len(added) == 1:
            display_label = _TOOL_WINDOW._display_label_from_path(added[0])
            if display_label:
                cmds.textScrollList(
                    _TOOL_WINDOW.CTRL_JOINT_LIST,
                    edit=True,
                    deselectAll=True,
                )
                cmds.textScrollList(
                    _TOOL_WINDOW.CTRL_JOINT_LIST,
                    edit=True,
                    selectItem=display_label,
                )

        if _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            _TOOL_WINDOW._info(
                "Added {} flood target joint(s) to the UI list. Missing "
                "influences are added when Flood runs.".format(len(added))
            )
        else:
            _TOOL_WINDOW._info("Added {} joint(s).".format(len(added)))
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def remove_selected_joints() -> None:
    """On skinned meshes remove pending UI joints, never skin influences."""

    if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
        _ORIGINAL_REMOVE_SELECTED_JOINTS()
        return

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        labels = cmds.textScrollList(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            query=True,
            selectItem=True,
        ) or []
        if not labels:
            cmds.warning("No joints selected in the list.")
            return

        selected_paths = {
            path
            for path in (
                _TOOL_WINDOW._path_from_display_label(label)
                for label in labels
            )
            if path
        }
        adapter = SkinClusterAdapter.from_mesh(_TOOL_WINDOW._STATE["mesh_shape"])
        existing_influences = set(adapter.influences())
        removable = selected_paths - existing_influences
        if not removable:
            cmds.warning(
                "Existing skinCluster influences are preserved. Only pending "
                "UI joints can be removed here."
            )
            return

        remaining = [
            joint
            for joint in _TOOL_WINDOW._STATE.get("joints", [])
            if joint not in removable
        ]
        _TOOL_WINDOW._set_joint_list(remaining)
        _TOOL_WINDOW._update_joint_count_label()
        _TOOL_WINDOW._info(
            "Removed {} pending flood target joint(s).".format(len(removable))
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def remove_all_joints() -> None:
    """On skinned meshes clear pending joints while retaining real influences."""

    if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
        _ORIGINAL_REMOVE_ALL_JOINTS()
        return

    try:
        _TOOL_WINDOW._require_not_busy()
        adapter = SkinClusterAdapter.from_mesh(_TOOL_WINDOW._STATE["mesh_shape"])
        influences = adapter.influences()
        pending_count = max(
            0,
            len(_TOOL_WINDOW._STATE.get("joints", [])) - len(influences),
        )
        _TOOL_WINDOW._set_joint_list(influences)
        _TOOL_WINDOW._update_joint_count_label()
        _TOOL_WINDOW._info(
            "Cleared {} pending joint(s); existing skinCluster influences were "
            "preserved.".format(pending_count)
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def apply_component_flood() -> None:
    wait_cursor_active = False

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            raise RuntimeError(
                "Component Flood requires an existing skinCluster.\n\n"
                "Use Bind Automatic Surface first for an unskinned mesh."
            )

        labels = cmds.textScrollList(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            query=True,
            selectItem=True,
        ) or []
        if len(labels) != 1:
            raise RuntimeError(
                "Select exactly one target joint in the UI influence list."
            )

        target_joint = _TOOL_WINDOW._path_from_display_label(labels[0])
        if not target_joint:
            raise RuntimeError("The selected UI joint could not be resolved.")

        _set_flood_busy(
            True,
            "Adding influence when needed and flooding selected vertices...",
        )
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        staged_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        result = component_flood.flood_selected_components_to_joint(
            mesh_shape=_TOOL_WINDOW._STATE["mesh_shape"],
            mesh_transform=_TOOL_WINDOW._STATE["mesh_transform"],
            target_joint=target_joint,
        )
        _sync_after_flood_preserving_pending(staged_joints)
        _select_target_in_list(result.target_joint)

        builtins.AD_SKIN_V40_FLOOD_RESULT = result
        component_flood.print_component_flood_report(result)

        ignored_suffix = ""
        if result.ignored_component_count:
            ignored_suffix = " {} other component(s) ignored.".format(
                result.ignored_component_count
            )
        added_suffix = " Added new influence." if result.influence_added else ""
        _TOOL_WINDOW._info(
            "Flood complete: {} vertices set to {} = 1.0.{}{}".format(
                result.vertex_count,
                result.target_joint.split("|")[-1],
                added_suffix,
                ignored_suffix,
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
        _set_flood_busy(False)


def _sync_after_flood_preserving_pending(staged_joints) -> None:
    _TOOL_WINDOW._sync_loaded_skin_context()
    current_influences = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
    current_set = set(current_influences)
    pending = [
        joint
        for joint in staged_joints
        if joint not in current_set and cmds.objExists(joint)
    ]
    if pending:
        _TOOL_WINDOW._set_joint_list(current_influences + pending)
        _TOOL_WINDOW._update_joint_count_label()


def _select_target_in_list(target_joint: str) -> None:
    label = _TOOL_WINDOW._display_label_from_path(target_joint)
    if not label:
        return
    cmds.textScrollList(
        _TOOL_WINDOW.CTRL_JOINT_LIST,
        edit=True,
        deselectAll=True,
    )
    cmds.textScrollList(
        _TOOL_WINDOW.CTRL_JOINT_LIST,
        edit=True,
        selectItem=label,
    )


def _set_flood_busy(busy: bool, status: str = "") -> None:
    if _TOOL_WINDOW is None:
        return
    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    if cmds.button(CTRL_FLOOD_BUTTON, exists=True):
        cmds.button(
            CTRL_FLOOD_BUTTON,
            edit=True,
            enable=not busy,
            label="Flooding..." if busy else "Flood Selected to Joint",
        )
    if cmds.button(_TOOL_WINDOW.CTRL_BIND_BUTTON, exists=True):
        cmds.button(
            _TOOL_WINDOW.CTRL_BIND_BUTTON,
            edit=True,
            enable=not busy,
        )
    if cmds.textScrollList(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        cmds.textScrollList(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            edit=True,
            enable=not busy,
        )
    if cmds.text(CTRL_FLOOD_STATUS, exists=True):
        cmds.text(
            CTRL_FLOOD_STATUS,
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
        title="AD Skin Weights Tool v4.0",
        message=(
            "Initial Automatic Surface Bind:\n\n"
            "1. Load an unskinned mesh.\n"
            "2. Add every intended joint.\n"
            "3. Click Bind Automatic Surface.\n\n"
            "The Region solver writes one influence at 1.0 for every vertex.\n\n"
            "Component Flood Override:\n\n"
            "1. Load a mesh with an existing skinCluster.\n"
            "2. Add a new joint to the UI list when needed.\n"
            "3. Select exactly one target joint in the UI list.\n"
            "4. Select vertices, edges, or faces on the loaded mesh.\n"
            "5. Click Flood Selected to Joint.\n\n"
            "A missing target influence is added at weight 0.0 first. The "
            "selected vertices are then written as target=1.0 and every other "
            "influence=0.0. Unselected vertices are not modified. Components "
            "from other meshes are ignored."
        ),
        button=["OK"],
    )
