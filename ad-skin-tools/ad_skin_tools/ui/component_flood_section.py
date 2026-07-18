"""AD Skin Tool v4.2 component-flood and bind-section UI.

The v3 Region solver remains responsible for initial full-object binding.
Component Flood is an explicit artist override on an existing skinCluster.
Joint-list rendering and influence-lock actions live in ``joint_list`` as one
cross-version implementation shared by Maya 2023, 2025, and 2026.
"""

import builtins

import maya.cmds as cmds

from ad_skin_tools.core import component_flood
from ad_skin_tools.ui import joint_list


CTRL_FLOOD_BUTTON = "adSkin_floodSelectedToJointButton"
CTRL_FLOOD_STATUS = "adSkin_floodSelectedToJointStatus"

# Compatibility aliases for scripts that imported v4.1 callbacks from this
# module. They point to the authoritative implementation; no logic is duplicated.
add_selected_joints = joint_list.add_selected_joints
show_selected_joints_in_list = joint_list.show_selected_joints_in_list
select_joints_in_scene = joint_list.select_joints_in_scene
remove_selected_joints = joint_list.remove_selected_joints
remove_all_joints = joint_list.remove_all_joints
lock_selected_joints = joint_list.lock_selected_joints

_TOOL_WINDOW = None


def install(tool_window_module) -> None:
    """Install v4.2 UI builders directly on the existing base tool window."""

    global _TOOL_WINDOW

    _TOOL_WINDOW = tool_window_module
    joint_list.configure(tool_window_module)

    tool_window_module._build_skin_cluster_section = _build_skin_cluster_section
    tool_window_module._build_joints_section = joint_list.build_section
    tool_window_module._build_initial_bind_section = _build_bind_sections
    tool_window_module._set_joint_list = joint_list.set_joint_list
    tool_window_module.add_selected_joints = add_selected_joints
    tool_window_module.remove_selected_joints = remove_selected_joints
    tool_window_module.remove_all_joints = remove_all_joints
    tool_window_module.show_selected_joints_in_list = show_selected_joints_in_list
    tool_window_module._set_bind_busy = _set_bind_busy
    tool_window_module.show_help = show_help

    tool_window_module.WINDOW_LABEL = "AD Skin Weights Tool v4.2"
    tool_window_module.WINDOW_HEIGHT = max(
        int(tool_window_module.WINDOW_HEIGHT),
        760,
    )
    tool_window_module.WINDOW_WIDTH = 340

    # Keep historical markers for external diagnostics while v4.2 becomes the
    # authoritative UI marker.
    tool_window_module._V4_COMPONENT_FLOOD_INSTALLED = True
    tool_window_module._V41_INFLUENCE_LOCKS_INSTALLED = True
    tool_window_module._V42_UI_INSTALLED = True


def _build_skin_cluster_section() -> None:
    """Build the mesh/skin context using the existing load operation."""

    cmds.frameLayout(
        label="Mesh / Skin Context",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)

    _TOOL_WINDOW._label_control_row(
        "Skin Cluster",
        lambda: cmds.optionMenu(_TOOL_WINDOW.CTRL_SKIN_MENU),
    )
    cmds.text(
        _TOOL_WINDOW.CTRL_MESH_LABEL,
        label="Mesh: <none>",
        align="left",
    )
    cmds.text(
        _TOOL_WINDOW.CTRL_MODE_LABEL,
        label="Mode: No object loaded",
        align="left",
    )
    cmds.text(
        _TOOL_WINDOW.CTRL_JOINT_LABEL,
        label="Joints: 0",
        align="left",
    )

    _TOOL_WINDOW._button_row(
        [("Load Mesh", lambda *_: _TOOL_WINDOW.load_skin_weight())],
        height=30,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_bind_sections() -> None:
    _build_initial_bind_section()
    _build_component_flood_section()


def _build_initial_bind_section() -> None:
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
        label="Component Flood (v4.2)",
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
            "Select exactly one target joint in the list, then select vertices, "
            "edges, or faces on the loaded mesh. Locked ownership is preserved."
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
            "Add the target as an influence when needed, then Replace 1.0 on "
            "writable selected vertices. Locked areas are ignored."
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
            "Green joints are bound influences. The left lock protects their "
            "weights from Flood. Right-click the list for bulk lock operations."
        ),
        align="left",
        wordWrap=True,
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
                "Use Bind Automatic Surface first for an unskinned mesh."
            )

        selected_joints = joint_list.selected_joint_paths()
        if len(selected_joints) != 1:
            raise RuntimeError(
                "Select exactly one target joint in the UI influence list."
            )
        target_joint = selected_joints[0]

        _set_flood_busy(
            True,
            "Flooding writable vertices and preserving locked ownership...",
        )
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        staged_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        staged_locks = set(
            _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
        )
        result = component_flood.flood_selected_components_to_joint(
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

        builtins.AD_SKIN_V42_FLOOD_RESULT = result
        builtins.AD_SKIN_V41_FLOOD_RESULT = result
        builtins.AD_SKIN_V40_FLOOD_RESULT = result
        component_flood.print_component_flood_report(result)

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
        _TOOL_WINDOW._info(
            "Flood complete: {} of {} selected vertices set to {} = 1.0.{}".format(
                result.flooded_vertex_count,
                result.vertex_count,
                short_name,
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
        _set_flood_busy(False)


def _set_bind_busy(busy, status="") -> None:
    """Mirror the base bind busy state without assuming a textScrollList."""

    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    if cmds.button(_TOOL_WINDOW.CTRL_BIND_BUTTON, exists=True):
        cmds.button(
            _TOOL_WINDOW.CTRL_BIND_BUTTON,
            edit=True,
            enable=not busy,
            label="Binding..." if busy else "Bind Automatic Surface",
        )
    if cmds.button(CTRL_FLOOD_BUTTON, exists=True):
        cmds.button(
            CTRL_FLOOD_BUTTON,
            edit=True,
            enable=not busy,
        )
    if cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        cmds.treeView(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            edit=True,
            enable=not busy,
        )

    if cmds.progressBar(_TOOL_WINDOW.CTRL_BIND_PROGRESS, exists=True):
        kwargs = {"edit": True, "visible": bool(busy)}
        try:
            cmds.progressBar(
                _TOOL_WINDOW.CTRL_BIND_PROGRESS,
                isIndeterminate=bool(busy),
                **kwargs
            )
        except TypeError:
            cmds.progressBar(
                _TOOL_WINDOW.CTRL_BIND_PROGRESS,
                progress=50 if busy else 0,
                **kwargs
            )

    if cmds.text(_TOOL_WINDOW.CTRL_BIND_STATUS, exists=True):
        cmds.text(
            _TOOL_WINDOW.CTRL_BIND_STATUS,
            edit=True,
            label=status if busy else "",
            visible=bool(busy),
        )
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


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
    if cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        cmds.treeView(
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
        title="AD Skin Weights Tool v4.2",
        message=(
            "Initial Automatic Surface Bind:\n\n"
            "1. Load an unskinned mesh.\n"
            "2. Add every intended joint.\n"
            "3. Click Bind Automatic Surface.\n\n"
            "Component Flood Override:\n\n"
            "1. Load a mesh with an existing skinCluster.\n"
            "2. Add a new target joint when needed.\n"
            "3. Select exactly one target joint in the list.\n"
            "4. Select vertices, edges, or faces on the loaded mesh.\n"
            "5. Click Flood Selected to Joint.\n\n"
            "Green rows are bound influences. Click the lock icon to protect "
            "an influence. A locked target ignores Flood. Vertices carrying "
            "weight from another locked influence are skipped. Right-click "
            "the list for bulk lock, inverse lock, pending-joint removal, and "
            "scene selection."
        ),
        button=["OK"],
    )
