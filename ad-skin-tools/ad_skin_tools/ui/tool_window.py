import traceback

import maya.cmds as cmds

from ad_skin_tools.core.selection import (
    get_selected_mesh_object,
    get_selected_joints,
)
from ad_skin_tools.core.compat import environment_report
from ad_skin_tools.core.skin_cluster import (
    SkinClusterAdapter,
    SkinClusterError,
)


WINDOW_NAME = "ADSkinWeightsToolWorkspace"
WINDOW_LABEL = "AD Skin Weights Tool"

WINDOW_WIDTH = 390
WINDOW_HEIGHT = 640

CTRL_MAIN_SCROLL = "adSkin_mainScroll"
CTRL_MAIN_COLUMN = "adSkin_mainColumn"

CTRL_SKIN_MENU = "adSkin_skinClusterMenu"
CTRL_MESH_LABEL = "adSkin_meshLabel"
CTRL_MODE_LABEL = "adSkin_modeLabel"
CTRL_JOINT_LABEL = "adSkin_jointCountLabel"
CTRL_JOINT_LIST = "adSkin_jointList"
CTRL_OPERATION_MODE = "adSkin_operationMode"
CTRL_STRENGTH = "adSkin_strength"
CTRL_SMOOTH_ITERATIONS = "adSkin_smoothIterations"


_STATE = {
    "mesh_shape": None,
    "mesh_transform": None,
    "skin_cluster": None,
    "has_skin_cluster": False,
    "joints": [],
    "joint_display_to_path": {},
    "joint_path_to_display": {},
    "component_selection": None,
}

def _delete_existing_workspace():
    if cmds.workspaceControl(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)

    try:
        if cmds.workspaceControlState(WINDOW_NAME, exists=True):
            cmds.workspaceControlState(WINDOW_NAME, remove=True)
    except Exception:
        pass

def show(auto_refresh=False):
    _delete_existing_workspace()

    cmds.workspaceControl(
        WINDOW_NAME,
        label=WINDOW_LABEL,
        retain=False,
        floating=True,
        initialWidth=WINDOW_WIDTH,
        initialHeight=WINDOW_HEIGHT,
    )

    cmds.workspaceControl(
        WINDOW_NAME,
        edit=True,
        label=WINDOW_LABEL,
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
        rowSpacing=4,
        columnAttach=("both", 4),
    )

    _build_header()
    _build_skin_cluster_section()
    _build_joints_section()
    _build_operation_section()
    _build_falloff_section()
    _build_visualization_section()
    _build_advanced_section()

    cmds.setParent("..")
    cmds.setParent("..")

    if auto_refresh:
        load_skin_weight(silent=True)

def _build_header():
    cmds.rowLayout(
        numberOfColumns=2,
        adjustableColumn=1,
        columnAttach=[
            (1, "both", 2),
            (2, "both", 2),
        ],
    )

    cmds.button(
        label="Tool Help",
        height=26,
        command=lambda *_: show_help(),
    )

    cmds.button(
        label="Env",
        width=42,
        height=26,
        command=lambda *_: show_environment_report(),
    )

    cmds.setParent("..")

def _build_skin_cluster_section():
    cmds.frameLayout(
        label="Skin Cluster",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )

    cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

    cmds.optionMenu(CTRL_SKIN_MENU, label="Skin Cluster")

    cmds.text(CTRL_MESH_LABEL, label="Mesh: <none>", align="left")
    cmds.text(CTRL_MODE_LABEL, label="Mode: No object loaded", align="left")
    cmds.text(CTRL_JOINT_LABEL, label="Joints: 0", align="left")

    cmds.button(
        label="Load Skin Weight",
        height=28,
        command=lambda *_: load_skin_weight(),
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_joints_section():
    cmds.frameLayout(
        label="Joints / Influences",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )

    cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

    cmds.radioButtonGrp(
        label="Sort",
        labelArray3=["Alphabetical", "Hierarchy", "Active Only"],
        numberOfRadioButtons=3,
        select=2,
        enable=False,
    )

    cmds.textField(
        placeholderText="Search joint... (v0.2)",
        editable=False,
    )

    cmds.textScrollList(
        CTRL_JOINT_LIST,
        allowMultiSelection=True,
        height=145,
    )

    cmds.rowLayout(numberOfColumns=3, adjustableColumn=1)

    cmds.button(
        label="Add Selected Joints",
        height=28,
        command=lambda *_: add_selected_joints(),
    )

    cmds.button(
        label="Remove Selected",
        height=28,
        command=lambda *_: remove_selected_joints(),
    )

    cmds.button(
        label="Remove All",
        height=28,
        command=lambda *_: remove_all_joints(),
    )

    cmds.setParent("..")

    cmds.button(
        label="Show Selected Joint In List",
        height=28,
        command=lambda *_: show_selected_joints_in_list(),
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_operation_section():
    cmds.frameLayout(
        label="Operation",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )

    cmds.columnLayout(adjustableColumn=True, rowSpacing=6)

    cmds.radioButtonGrp(
        CTRL_OPERATION_MODE,
        label="Mode",
        labelArray4=["Closest", "Even", "Smooth", "Normalize"],
        numberOfRadioButtons=4,
        select=1,
        enable=False,
    )

    cmds.radioButtonGrp(
        label="Apply To",
        labelArray3=["Object", "Selected Vertices", "Soft Selection"],
        numberOfRadioButtons=3,
        select=1,
        enable=False,
    )

    cmds.button(
        label="Apply Operation",
        height=34,
        command=lambda *_: apply_operation(),
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_falloff_section():
    cmds.frameLayout(
        label="Brush / Falloff",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )

    cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

    cmds.floatSliderGrp(
        CTRL_STRENGTH,
        label="Strength",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        value=0.5,
        enable=False,
    )

    cmds.intSliderGrp(
        CTRL_SMOOTH_ITERATIONS,
        label="Smooth Iterations",
        field=True,
        minValue=1,
        maxValue=20,
        value=1,
        enable=False,
    )

    cmds.floatSliderGrp(
        label="Prune Below",
        field=True,
        minValue=0.0,
        maxValue=0.1,
        value=0.001,
        enable=False,
    )

    cmds.intSliderGrp(
        label="Max Influences",
        field=True,
        minValue=1,
        maxValue=8,
        value=4,
        enable=False,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_visualization_section():
    cmds.frameLayout(
        label="Visualization",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )

    cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

    cmds.checkBox(label="Use Maya Color Feedback", value=True, enable=False)
    cmds.button(label="Show Selected Weights", enable=False)
    cmds.button(label="Clear Display", enable=False)

    cmds.setParent("..")
    cmds.setParent("..")


def _build_advanced_section():
    cmds.frameLayout(
        label="Advanced",
        collapsable=True,
        collapse=True,
        marginWidth=6,
        marginHeight=6,
    )

    cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

    cmds.checkBox(label="Preserve Locked Influences", value=True, enable=False)
    cmds.checkBox(label="Limit Max Influences", value=False, enable=False)
    cmds.checkBox(label="Normalize After Operation", value=True, enable=False)

    cmds.setParent("..")
    cmds.setParent("..")


def load_skin_weight(silent=False):
    """
    QC-1:
    Load mesh skin context from selected object.

    This does not require vertex selection.
    This supports mesh with or without skinCluster.
    """
    try:
        mesh_selection = get_selected_mesh_object()

        mesh_shape = mesh_selection.mesh_shape
        mesh_transform = mesh_selection.mesh_transform

        skin_cluster = None
        joints = []
        has_skin = False

        try:
            adapter = SkinClusterAdapter.from_mesh(mesh_shape)
            skin_cluster = adapter.skin_cluster
            joints = adapter.influences()
            has_skin = True

        except SkinClusterError:
            skin_cluster = None
            joints = []
            has_skin = False

        _STATE["mesh_shape"] = mesh_shape
        _STATE["mesh_transform"] = mesh_transform
        _STATE["skin_cluster"] = skin_cluster
        _STATE["has_skin_cluster"] = has_skin
        _STATE["joints"] = list(joints)
        _STATE["component_selection"] = None

        skin_label = skin_cluster if skin_cluster else "<no skinCluster>"

        _set_option_menu_items(CTRL_SKIN_MENU, [skin_label])
        _set_joint_list(_STATE["joints"])

        cmds.text(
            CTRL_MESH_LABEL,
            edit=True,
            label=f"Mesh: {mesh_transform}",
        )

        if has_skin:
            cmds.text(
                CTRL_MODE_LABEL,
                edit=True,
                label="Mode: Object loaded with skinCluster",
            )
        else:
            cmds.text(
                CTRL_MODE_LABEL,
                edit=True,
                label="Mode: Object loaded without skinCluster",
            )

        cmds.text(
            CTRL_JOINT_LABEL,
            edit=True,
            label=f"Joints: {len(_STATE['joints'])}",
        )

        if has_skin:
            _info("Skin weight loaded from selected object.")
        else:
            _info("Mesh loaded. No skinCluster found. Add joints to continue.")

    except Exception as exc:
        if not silent:
            _show_error(exc)


def refresh_from_selection(silent=False):
    """
    Backward-compatible alias during QC transition.
    """
    load_skin_weight(silent=silent)


def add_selected_joints():
    try:
        _require_loaded_mesh()

        selected_joints = get_selected_joints()

        if not selected_joints:
            cmds.warning("No selected joints found.")
            return

        current_joints = list(_STATE.get("joints", []))
        added = []

        for joint in selected_joints:
            normalized = _normalize_joint_path(joint)

            if not _joint_exists_in_list(normalized, current_joints):
                current_joints.append(normalized)
                added.append(normalized)

        _set_joint_list(current_joints)
        _update_joint_count_label()

        if added:
            _info(f"Added {len(added)} joint(s).")
        else:
            cmds.warning("Selected joints already exist in the list.")

    except Exception as exc:
        _show_error(exc)


def remove_selected_joints():
    try:
        _require_loaded_mesh()

        selected_labels = cmds.textScrollList(
            CTRL_JOINT_LIST,
            query=True,
            selectItem=True,
        ) or []

        if not selected_labels:
            cmds.warning("No joints selected in the list.")
            return

        selected_paths = {
            _path_from_display_label(label)
            for label in selected_labels
        }
        selected_paths = {path for path in selected_paths if path}

        current_joints = list(_STATE.get("joints", []))
        remaining = [
            joint for joint in current_joints
            if joint not in selected_paths
        ]

        removed_count = len(current_joints) - len(remaining)

        _set_joint_list(remaining)
        _update_joint_count_label()

        _info(f"Removed {removed_count} joint(s).")

    except Exception as exc:
        _show_error(exc)

def remove_all_joints():
    try:
        _clear_tool_context()
        _info("Cleared loaded mesh, skinCluster, and joint list.")

    except Exception as exc:
        _show_error(exc)

def show_selected_joints_in_list():
    try:
        _require_loaded_mesh()

        selected_joints = get_selected_joints()

        if not selected_joints:
            cmds.warning("No selected joints found in the scene.")
            return

        cmds.textScrollList(
            CTRL_JOINT_LIST,
            edit=True,
            deselectAll=True,
        )

        all_items = cmds.textScrollList(
            CTRL_JOINT_LIST,
            query=True,
            allItems=True,
        ) or []

        matched_labels = []

        for joint in selected_joints:
            label = _display_label_from_path(joint)

            if label:
                matched_labels.append(label)

        if not matched_labels:
            cmds.warning("Selected joint was not found in the window list.")
            return

        first_index = None

        for label in matched_labels:
            cmds.textScrollList(
                CTRL_JOINT_LIST,
                edit=True,
                selectItem=label,
            )

            if label in all_items and first_index is None:
                first_index = all_items.index(label) + 1

        if first_index is not None:
            cmds.textScrollList(
                CTRL_JOINT_LIST,
                edit=True,
                showIndexedItem=first_index,
            )

        _info(f"Found {len(matched_labels)} selected joint(s) in the list.")

    except Exception as exc:
        _show_error(exc)

def _clear_tool_context():
    _STATE["mesh_shape"] = None
    _STATE["mesh_transform"] = None
    _STATE["skin_cluster"] = None
    _STATE["has_skin_cluster"] = False
    _STATE["joints"] = []
    _STATE["joint_display_to_path"] = {}
    _STATE["joint_path_to_display"] = {}
    _STATE["component_selection"] = None

    _set_option_menu_items(CTRL_SKIN_MENU, ["<none>"])

    cmds.text(
        CTRL_MESH_LABEL,
        edit=True,
        label="Mesh: <none>",
    )

    cmds.text(
        CTRL_MODE_LABEL,
        edit=True,
        label="Mode: No object loaded",
    )

    cmds.text(
        CTRL_JOINT_LABEL,
        edit=True,
        label="Joints: 0",
    )

    cmds.textScrollList(
        CTRL_JOINT_LIST,
        edit=True,
        removeAll=True,
    )

def apply_operation():
    """
    Disabled for QC-1.

    QC-1 only validates:
    - Load Skin Weight
    - mesh with skinCluster
    - mesh without skinCluster
    - add/remove joint list
    """
    message = (
        "Apply Operation is temporarily disabled.\n\n"
        "QC-1 only tests Load Skin Weight and Joint List management."
    )

    cmds.warning(message)
    cmds.confirmDialog(
        title="AD Skin Tools QC-1",
        message=message,
        button=["OK"],
    )


def show_help():
    cmds.confirmDialog(
        title="AD Skin Weights Tool",
        message=(
            "QC-1 Workflow:\n\n"
            "1. Select a mesh object.\n"
            "2. Click Load Skin Weight.\n"
            "3. If the mesh has a skinCluster, existing joints will appear.\n"
            "4. If there is no skinCluster, the mesh still loads.\n"
            "5. Select joints in Maya.\n"
            "6. Click Add Selected Joints.\n"
            "7. Use Remove Selected or Remove All to edit the list.\n\n"
            "Apply Operation is disabled for this QC pass."
        ),
        button=["OK"],
    )


def show_environment_report():
    cmds.confirmDialog(
        title="AD Skin Tools Environment",
        message=environment_report(),
        button=["OK"],
    )


def _set_option_menu_items(menu_name, items):
    existing_items = cmds.optionMenu(menu_name, query=True, itemListLong=True) or []

    for item in existing_items:
        cmds.deleteUI(item)

    for item in items:
        cmds.menuItem(label=item, parent=menu_name)


def _set_joint_list(joints):
    """
    Store full joint paths internally, but show readable names in the UI.

    Display rule:
    - unique joint name      -> joint
    - duplicate joint name   -> parent|joint
    - still duplicate        -> grandparent|parent|joint
    - fallback               -> full DAG path
    """
    normalized_joints = _unique_joint_paths(joints)

    _STATE["joints"] = normalized_joints
    _STATE["joint_display_to_path"] = {}
    _STATE["joint_path_to_display"] = {}

    cmds.textScrollList(
        CTRL_JOINT_LIST,
        edit=True,
        removeAll=True,
    )

    for joint in normalized_joints:
        display_name = _make_unique_joint_label(joint, normalized_joints)

        _STATE["joint_display_to_path"][display_name] = joint
        _STATE["joint_path_to_display"][joint] = display_name

        cmds.textScrollList(
            CTRL_JOINT_LIST,
            edit=True,
            append=display_name,
        )

def _update_joint_count_label():
    joints = _STATE.get("joints", [])

    cmds.text(
        CTRL_JOINT_LABEL,
        edit=True,
        label=f"Joints: {len(joints)}",
    )


def _require_loaded_mesh():
    if not _STATE.get("mesh_shape"):
        raise RuntimeError("No mesh loaded. Select a mesh object and click Load Skin Weight.")


def _joint_exists_in_list(joint: str, joint_list: list[str]) -> bool:
    normalized = _normalize_joint_path(joint)

    for existing in joint_list:
        if _normalize_joint_path(existing) == normalized:
            return True

    return False


def _short_name(node: str) -> str:
    return node.split("|")[-1]

def _normalize_joint_path(joint: str) -> str:
    matches = cmds.ls(joint, long=True, type="joint") or []

    if matches:
        return matches[0]

    return joint


def _unique_joint_paths(joints: list[str]) -> list[str]:
    result = []
    seen = set()

    for joint in joints:
        normalized = _normalize_joint_path(joint)

        key = normalized
        if key in seen:
            continue

        seen.add(key)
        result.append(normalized)

    return result


def _make_unique_joint_label(joint: str, all_joints: list[str]) -> str:
    """
    Make readable but unique label.

    Example:
        index_01_bind

    If duplicate:
        fingerA|index_01_bind
        fingerB|index_01_bind
    """
    joint_parts = _dag_parts(joint)

    if not joint_parts:
        return joint

    for depth in range(1, len(joint_parts) + 1):
        label = "|".join(joint_parts[-depth:])

        same_label_count = 0

        for other_joint in all_joints:
            other_parts = _dag_parts(other_joint)
            other_label = "|".join(other_parts[-depth:])

            if other_label == label:
                same_label_count += 1

        if same_label_count == 1:
            return label

    return joint


def _dag_parts(node: str) -> list[str]:
    return [part for part in node.split("|") if part]


def _path_from_display_label(display_label: str):
    return _STATE.get("joint_display_to_path", {}).get(display_label)


def _display_label_from_path(joint: str):
    normalized = _normalize_joint_path(joint)
    return _STATE.get("joint_path_to_display", {}).get(normalized)

def _info(message: str):
    cmds.inViewMessage(
        assistMessage=message,
        position="topCenter",
        fade=True,
    )


def _show_error(exc: Exception):
    traceback.print_exc()
    cmds.warning(str(exc))
    cmds.confirmDialog(
        title="AD Skin Tool Error",
        message=str(exc),
        button=["OK"],
    )