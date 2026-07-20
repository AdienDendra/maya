"""Base Maya workspace window and shared UI state."""

import builtins
import traceback

import maya.cmds as cmds

from ad_skin_tools.core import automatic_surface_commands
from ad_skin_tools.core.compat import environment_report
from ad_skin_tools.core.selection import get_selected_joints, get_selected_mesh_object
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter, SkinClusterError


WINDOW_NAME = "ADSkinWeightsToolWorkspace"
WINDOW_LABEL = "AD Skin Weights Tool"
WINDOW_WIDTH = 340
WINDOW_HEIGHT = 665

BUTTON_HEIGHT = 28
ROW_HEIGHT = 26
LABEL_WIDTH = 108
CONTROL_GAP = 6
BUTTON_GAP = 4

CTRL_MAIN_SCROLL = "adSkin_mainScroll"
CTRL_MAIN_COLUMN = "adSkin_mainColumn"
CTRL_SKIN_MENU = "adSkin_skinClusterMenu"
CTRL_MESH_LABEL = "adSkin_meshLabel"
CTRL_MODE_LABEL = "adSkin_modeLabel"
CTRL_JOINT_LABEL = "adSkin_jointCountLabel"
CTRL_JOINT_LIST = "adSkin_jointList"
CTRL_BIND_BUTTON = "adSkin_bindAutomaticSurfaceButton"
CTRL_BIND_PROGRESS = "adSkin_bindAutomaticSurfaceProgress"
CTRL_BIND_STATUS = "adSkin_bindAutomaticSurfaceStatus"

_STATE = {
    "mesh_shape": None,
    "mesh_transform": None,
    "skin_cluster": None,
    "has_skin_cluster": False,
    "joints": [],
    "joint_display_to_path": {},
    "joint_path_to_display": {},
    "busy": False,
}


def show(auto_refresh=False):
    """Create the tool workspace after operation modules install their hooks."""

    _delete_existing_workspace()

    cmds.workspaceControl(
        WINDOW_NAME,
        label=WINDOW_LABEL,
        retain=False,
        floating=True,
        initialWidth=WINDOW_WIDTH,
        initialHeight=WINDOW_HEIGHT,
    )
    cmds.setParent(WINDOW_NAME)

    cmds.scrollLayout(
        CTRL_MAIN_SCROLL,
        childResizable=True,
        verticalScrollBarThickness=16,
        horizontalScrollBarThickness=0,
    )
    cmds.columnLayout(
        CTRL_MAIN_COLUMN,
        adjustableColumn=True,
        rowSpacing=5,
        columnAttach=("both", 5),
    )

    _build_header()
    _build_skin_cluster_section()
    _build_joints_section()
    _build_initial_bind_section()

    cmds.setParent("..")
    cmds.setParent("..")

    if auto_refresh:
        load_skin_weight(silent=True)


def _delete_existing_workspace():
    if cmds.workspaceControl(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)

    try:
        if cmds.workspaceControlState(WINDOW_NAME, exists=True):
            cmds.workspaceControlState(WINDOW_NAME, remove=True)
    except Exception:
        pass


def _build_header():
    _button_row(
        [
            ("Tool Help", lambda *_: show_help()),
            ("Environment", lambda *_: show_environment_report()),
        ]
    )


def _build_skin_cluster_section():
    """Default mesh-context section replaced by the active UI installer."""

    cmds.frameLayout(
        label="Mesh / Skin Context",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)

    _label_control_row(
        "Skin Cluster",
        lambda: cmds.optionMenu(CTRL_SKIN_MENU),
    )
    cmds.text(CTRL_MESH_LABEL, label="Mesh: <none>", align="left")
    cmds.text(
        CTRL_MODE_LABEL,
        label="Mode: No object loaded",
        align="left",
    )
    cmds.text(CTRL_JOINT_LABEL, label="Joints: 0", align="left")

    _button_row(
        [("Load Mesh", lambda *_: load_skin_weight())],
        height=30,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_joints_section():
    """Default joint list replaced by the active joint-list module."""

    cmds.frameLayout(
        label="Joints / Influences",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)

    cmds.textScrollList(
        CTRL_JOINT_LIST,
        allowMultiSelection=True,
        height=220,
    )
    _button_row(
        [
            ("Add Selected", lambda *_: add_selected_joints()),
            ("Remove Selected", lambda *_: remove_selected_joints()),
            ("Remove All", lambda *_: remove_all_joints()),
        ],
        height=30,
    )
    _button_row(
        [
            (
                "Show Maya Selection In List",
                lambda *_: show_selected_joints_in_list(),
            )
        ],
        height=30,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_initial_bind_section():
    """Default bind section replaced by the active operation modules."""

    cmds.frameLayout(
        label="Binding",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    cmds.button(
        CTRL_BIND_BUTTON,
        label="Bind Skin",
        height=38,
        command=lambda *_: apply_operation(),
    )
    _create_bind_progress_bar()
    cmds.text(
        CTRL_BIND_STATUS,
        label="",
        align="left",
        wordWrap=True,
        visible=False,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _create_bind_progress_bar():
    try:
        cmds.progressBar(
            CTRL_BIND_PROGRESS,
            maxValue=100,
            progress=0,
            isIndeterminate=True,
            visible=False,
            height=12,
        )
    except TypeError:
        cmds.progressBar(
            CTRL_BIND_PROGRESS,
            maxValue=100,
            progress=50,
            visible=False,
            height=12,
        )


def load_skin_weight(silent=False):
    """Load one selected polygon mesh and its current skin context."""

    try:
        _require_not_busy()
        mesh_selection = get_selected_mesh_object()
        mesh_shape = mesh_selection.mesh_shape
        mesh_transform = mesh_selection.mesh_transform

        try:
            adapter = SkinClusterAdapter.from_mesh(mesh_shape)
            skin_cluster = adapter.skin_cluster
            joints = adapter.influences()
            has_skin = True
        except SkinClusterError:
            skin_cluster = None
            joints = []
            has_skin = False

        _STATE.update(
            {
                "mesh_shape": mesh_shape,
                "mesh_transform": mesh_transform,
                "skin_cluster": skin_cluster,
                "has_skin_cluster": has_skin,
                "joints": builtins.list(joints),
            }
        )

        skin_label = skin_cluster if skin_cluster else "<no skinCluster>"
        _set_option_menu_items(CTRL_SKIN_MENU, [skin_label])
        _set_joint_list(_STATE["joints"])

        cmds.text(
            CTRL_MESH_LABEL,
            edit=True,
            label="Mesh: {}".format(mesh_transform),
        )
        cmds.text(
            CTRL_MODE_LABEL,
            edit=True,
            label=(
                "Mode: Existing skinCluster"
                if has_skin
                else "Mode: Unskinned mesh"
            ),
        )
        _update_joint_count_label()

        if has_skin:
            _info("Loaded the selected mesh and existing skinCluster.")
        else:
            _info("Loaded an unskinned mesh. Add joints to bind.")
    except Exception as exc:
        if not silent:
            _show_error(exc)


def refresh_from_selection(silent=False):
    load_skin_weight(silent=silent)


def _sync_loaded_skin_context():
    """Refresh the loaded skinCluster and influence list after an operation."""

    mesh_shape = _STATE.get("mesh_shape")
    mesh_transform = _STATE.get("mesh_transform")

    if not mesh_shape or not cmds.objExists(mesh_shape):
        raise RuntimeError("Loaded mesh no longer exists.")

    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    joints = adapter.influences()

    _STATE.update(
        {
            "skin_cluster": adapter.skin_cluster,
            "has_skin_cluster": True,
            "joints": builtins.list(joints),
        }
    )

    _set_option_menu_items(CTRL_SKIN_MENU, [adapter.skin_cluster])
    _set_joint_list(joints)
    _update_joint_count_label()

    cmds.text(
        CTRL_MESH_LABEL,
        edit=True,
        label="Mesh: {}".format(mesh_transform),
    )
    cmds.text(
        CTRL_MODE_LABEL,
        edit=True,
        label="Mode: Existing skinCluster",
    )


def add_selected_joints():
    """Fallback joint-list callback replaced by the active joint-list module."""

    try:
        _require_not_busy()
        _require_loaded_mesh()
        selected_joints = get_selected_joints()
        if not selected_joints:
            cmds.warning("No selected joints found.")
            return

        current_joints = builtins.list(_STATE.get("joints", []))
        added = []
        for joint in selected_joints:
            normalized = _normalize_joint_path(joint)
            if not _joint_exists_in_list(normalized, current_joints):
                current_joints.append(normalized)
                added.append(normalized)

        _set_joint_list(current_joints)
        _update_joint_count_label()
        if added:
            _info("Added {} joint(s).".format(builtins.len(added)))
        else:
            cmds.warning("Selected joints already exist in the list.")
    except Exception as exc:
        _show_error(exc)


def remove_selected_joints():
    """Fallback removal callback replaced by the active joint-list module."""

    try:
        _require_not_busy()
        _require_unskinned_mesh()
        labels = cmds.textScrollList(
            CTRL_JOINT_LIST,
            query=True,
            selectItem=True,
        ) or []
        if not labels:
            cmds.warning("No joints selected in the list.")
            return

        selected_paths = {
            path
            for path in (
                _path_from_display_label(label)
                for label in labels
            )
            if path
        }
        current_joints = builtins.list(_STATE.get("joints", []))
        remaining = [
            joint for joint in current_joints if joint not in selected_paths
        ]
        removed_count = len(current_joints) - len(remaining)
        _set_joint_list(remaining)
        _update_joint_count_label()
        _info("Removed {} joint(s).".format(removed_count))
    except Exception as exc:
        _show_error(exc)


def remove_all_joints():
    """Fallback clear callback replaced by the active joint-list module."""

    try:
        _require_not_busy()
        _require_unskinned_mesh()
        _set_joint_list([])
        _update_joint_count_label()
        _info("Removed all joints from the bind list.")
    except Exception as exc:
        _show_error(exc)


def show_selected_joints_in_list():
    """Fallback selection callback replaced by the active joint-list module."""

    try:
        _require_not_busy()
        _require_loaded_mesh()
        selected_joints = get_selected_joints()
        if not selected_joints:
            cmds.warning("No selected joints found in Maya.")
            return

        labels = [
            _display_label_from_path(joint)
            for joint in selected_joints
        ]
        labels = [label for label in labels if label]
        if not labels:
            cmds.warning("Selected joints were not found in the tool list.")
            return

        cmds.textScrollList(
            CTRL_JOINT_LIST,
            edit=True,
            deselectAll=True,
        )
        for label in labels:
            cmds.textScrollList(
                CTRL_JOINT_LIST,
                edit=True,
                selectItem=label,
            )
        _info("Found {} selected joint(s) in the list.".format(len(labels)))
    except Exception as exc:
        _show_error(exc)


def apply_operation():
    """Fallback bind callback replaced by the active smoothing bind module."""

    wait_cursor_active = False
    try:
        _require_not_busy()
        _require_unskinned_mesh()

        joints = builtins.list(_STATE.get("joints", []))
        if builtins.len(joints) < 2:
            raise RuntimeError(
                "Bind Skin requires at least two joints.\n\n"
                "Select joints in Maya and add them to the list."
            )

        _set_bind_busy(True, "Calculating surface ownership...")
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        result = automatic_surface_commands.bind_object_automatic_surface(
            mesh=_STATE["mesh_transform"],
            joints=joints,
        )

        _sync_loaded_skin_context()
        builtins.AD_SKIN_BIND_RESULT = result
        automatic_surface_commands.print_report(result)
        _info("Bind complete: {} vertices.".format(result.vertex_count))
    except Exception as exc:
        _show_error(exc)
    finally:
        if wait_cursor_active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass
        _set_bind_busy(False)


def _set_bind_busy(busy, status=""):
    _STATE["busy"] = bool(busy)

    if cmds.button(CTRL_BIND_BUTTON, exists=True):
        cmds.button(
            CTRL_BIND_BUTTON,
            edit=True,
            enable=not busy,
            label="Binding..." if busy else "Bind Skin",
        )

    if cmds.progressBar(CTRL_BIND_PROGRESS, exists=True):
        kwargs = {"edit": True, "visible": bool(busy)}
        try:
            cmds.progressBar(
                CTRL_BIND_PROGRESS,
                isIndeterminate=bool(busy),
                **kwargs
            )
        except TypeError:
            cmds.progressBar(
                CTRL_BIND_PROGRESS,
                progress=50 if busy else 0,
                **kwargs
            )

    if cmds.text(CTRL_BIND_STATUS, exists=True):
        cmds.text(
            CTRL_BIND_STATUS,
            edit=True,
            label=status if busy else "",
            visible=bool(busy),
        )

    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def show_help():
    cmds.confirmDialog(
        title="AD Skin Weights Tool",
        message=(
            "Load one polygon mesh, add the intended joints, then use the "
            "Binding or Component operations. Locked influence values are "
            "preserved by operations that support existing skin weights."
        ),
        button=["OK"],
    )


def show_environment_report():
    cmds.confirmDialog(
        title="AD Skin Tools Environment",
        message=environment_report(),
        button=["OK"],
    )


def _require_not_busy():
    if _STATE.get("busy"):
        raise RuntimeError("An AD Skin Tool operation is already running.")


def _require_loaded_mesh():
    if not _STATE.get("mesh_shape"):
        raise RuntimeError(
            "No mesh loaded. Select a mesh and click Load Mesh."
        )


def _require_unskinned_mesh():
    _require_loaded_mesh()
    if _STATE.get("has_skin_cluster"):
        raise RuntimeError(
            "This mesh already has a skinCluster.\n\n"
            "Initial object binding is only available for an unskinned mesh."
        )


def _set_option_menu_items(menu_name, items):
    existing_items = cmds.optionMenu(
        menu_name,
        query=True,
        itemListLong=True,
    ) or []
    for item in existing_items:
        cmds.deleteUI(item)
    for item in items:
        cmds.menuItem(label=item, parent=menu_name)


def _set_joint_list(joints):
    """Default text-list renderer replaced by the active joint-list module."""

    normalized_joints = _unique_joint_paths(joints)
    _STATE["joints"] = normalized_joints
    _STATE["joint_display_to_path"] = {}
    _STATE["joint_path_to_display"] = {}

    if not cmds.textScrollList(CTRL_JOINT_LIST, exists=True):
        return
    cmds.textScrollList(
        CTRL_JOINT_LIST,
        edit=True,
        removeAll=True,
    )

    for joint in normalized_joints:
        label = _make_unique_joint_label(joint, normalized_joints)
        _STATE["joint_display_to_path"][label] = joint
        _STATE["joint_path_to_display"][joint] = label
        cmds.textScrollList(
            CTRL_JOINT_LIST,
            edit=True,
            append=label,
        )


def _update_joint_count_label():
    if not cmds.text(CTRL_JOINT_LABEL, exists=True):
        return
    cmds.text(
        CTRL_JOINT_LABEL,
        edit=True,
        label="Joints: {}".format(
            builtins.len(_STATE.get("joints", []))
        ),
    )


def _joint_exists_in_list(joint, joint_list):
    normalized = _normalize_joint_path(joint)
    return any(
        _normalize_joint_path(existing) == normalized
        for existing in joint_list
    )


def _normalize_joint_path(joint):
    matches = cmds.ls(joint, long=True, type="joint") or []
    return matches[0] if matches else joint


def _unique_joint_paths(joints):
    result = []
    seen = set()
    for joint in joints:
        normalized = _normalize_joint_path(joint)
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def _make_unique_joint_label(joint, all_joints):
    joint_parts = _dag_parts(joint)
    if not joint_parts:
        return joint

    for depth in range(1, builtins.len(joint_parts) + 1):
        label = "|".join(joint_parts[-depth:])
        count = sum(
            1
            for other_joint in all_joints
            if "|".join(_dag_parts(other_joint)[-depth:]) == label
        )
        if count == 1:
            return label
    return joint


def _dag_parts(node):
    return [part for part in node.split("|") if part]


def _path_from_display_label(display_label):
    return _STATE.get(
        "joint_display_to_path",
        {},
    ).get(display_label)


def _display_label_from_path(joint):
    return _STATE.get(
        "joint_path_to_display",
        {},
    ).get(_normalize_joint_path(joint))


def _label_control_row(label, control_builder, height=ROW_HEIGHT):
    layout = cmds.formLayout(height=height)
    label_control = cmds.text(
        label=label,
        align="left",
        width=LABEL_WIDTH,
    )
    control = control_builder()

    cmds.formLayout(
        layout,
        edit=True,
        attachForm=[
            (label_control, "left", 0),
            (label_control, "top", 2),
            (label_control, "bottom", 2),
            (control, "right", 0),
            (control, "top", 2),
            (control, "bottom", 2),
        ],
        attachControl=[
            (control, "left", CONTROL_GAP, label_control)
        ],
    )
    cmds.setParent("..")
    return control


def _button_row(buttons, height=BUTTON_HEIGHT, gap=BUTTON_GAP):
    layout = cmds.formLayout(
        numberOfDivisions=100,
        height=height,
    )
    count = builtins.len(buttons)
    if count == 0:
        cmds.setParent("..")
        return layout

    for index, (label, callback) in enumerate(buttons):
        left_position = int(index * 100 / count)
        right_position = int((index + 1) * 100 / count)
        left_offset = 0 if index == 0 else gap // 2
        right_offset = 0 if index == count - 1 else gap // 2

        button = cmds.button(
            label=label,
            height=height,
            command=callback,
        )
        cmds.formLayout(
            layout,
            edit=True,
            attachForm=[
                (button, "top", 1),
                (button, "bottom", 1),
            ],
            attachPosition=[
                (button, "left", left_offset, left_position),
                (button, "right", right_offset, right_position),
            ],
        )

    cmds.setParent("..")
    return layout


def _info(message):
    cmds.inViewMessage(
        assistMessage=message,
        position="topCenter",
        fade=True,
    )


def _show_error(exc):
    traceback.print_exc()
    cmds.warning(str(exc))
    cmds.confirmDialog(
        title="AD Skin Tool Error",
        message=str(exc),
        button=["OK"],
    )
