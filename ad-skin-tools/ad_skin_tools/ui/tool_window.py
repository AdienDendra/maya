import traceback

import maya.cmds as cmds

from ad_skin_tools.core import commands
from ad_skin_tools.core.compat import environment_report
from ad_skin_tools.core.selection import (
    get_selected_joints,
    get_selected_mesh_object,
)
from ad_skin_tools.core.skin_cluster import (
    SkinClusterAdapter,
    SkinClusterError,
)


WINDOW_NAME = "ADSkinWeightsToolWorkspace"
WINDOW_LABEL = "AD Skin Weights Tool v2.5"
WINDOW_WIDTH = 320
WINDOW_HEIGHT = 620

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
CTRL_ROOT_BACK_FRACTION = "adSkin_rootBackFraction"
CTRL_TERMINAL_BACK_FRACTION = "adSkin_terminalBackFraction"
CTRL_NORMAL_PENALTY = "adSkin_normalPenalty"

_STATE = {
    "mesh_shape": None,
    "mesh_transform": None,
    "skin_cluster": None,
    "has_skin_cluster": False,
    "joints": [],
    "joint_display_to_path": {},
    "joint_path_to_display": {},
}


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
            cmds.workspaceControlState(
                WINDOW_NAME,
                remove=True,
            )
    except Exception:
        pass


def _build_header():
    _button_row(
        [
            ("Tool Help", lambda *_: show_help()),
            ("Environment", lambda *_: show_environment_report()),
        ],
        height=BUTTON_HEIGHT,
    )


def _build_skin_cluster_section():
    cmds.frameLayout(
        label="Mesh / Skin Context",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=5,
    )

    _label_control_row(
        "Skin Cluster",
        lambda: cmds.optionMenu(CTRL_SKIN_MENU),
    )

    cmds.text(
        CTRL_MESH_LABEL,
        label="Mesh: <none>",
        align="left",
    )
    cmds.text(
        CTRL_MODE_LABEL,
        label="Mode: No object loaded",
        align="left",
    )
    cmds.text(
        CTRL_JOINT_LABEL,
        label="Joints: 0",
        align="left",
    )

    _button_row(
        [
            ("Load Mesh / Skin", lambda *_: load_skin_weight()),
        ],
        height=30,
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
    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=5,
    )

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
            ),
        ],
        height=30,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_initial_bind_section():
    """
    Build controls for the constrained hard-ownership experiment.

    This stage always writes exactly one influence per vertex.
    It is intended to validate ownership regions before soft weighting
    or topology smoothing is introduced.
    """
    cmds.frameLayout(
        label="Initial Bind",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )

    cmds.columnLayout(
        adjustableColumn=True,
        rowSpacing=6,
    )

    cmds.text(
        label="Method: Constrained Closest Bone",
        align="left",
        font="boldLabelFont",
    )

    cmds.text(
        label=(
            "Diagnostic hard ownership: one influence per vertex, "
            "with no smoothing or soft weighting."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.floatSliderGrp(
        CTRL_ROOT_BACK_FRACTION,
        label="Root Back Limit",
        field=True,
        minValue=0.0,
        maxValue=0.5,
        fieldMinValue=0.0,
        fieldMaxValue=2.0,
        precision=3,
        value=0.05,
        annotation=(
            "How far behind a non-terminal joint root a vertex may be, "
            "expressed as a fraction of the bone length."
        ),
    )

    cmds.floatSliderGrp(
        CTRL_TERMINAL_BACK_FRACTION,
        label="Terminal Back Limit",
        field=True,
        minValue=0.0,
        maxValue=1.0,
        fieldMinValue=0.0,
        fieldMaxValue=4.0,
        precision=3,
        value=0.35,
        annotation=(
            "How far behind a terminal joint a vertex may be, "
            "expressed as a fraction of its parent-bone length."
        ),
    )

    cmds.floatSliderGrp(
        CTRL_NORMAL_PENALTY,
        label="Normal Penalty",
        field=True,
        minValue=0.0,
        maxValue=10.0,
        fieldMinValue=0.0,
        fieldMaxValue=100.0,
        precision=3,
        value=2.0,
        annotation=(
            "Penalizes candidate bones that lie in the outward-facing "
            "hemisphere of the vertex normal."
        ),
    )

    _button_row(
        [
            (
                "Bind Constrained Closest",
                lambda *_: apply_operation(),
            ),
        ],
        height=36,
    )

    cmds.text(
        label=(
            "The result is intentionally rigid. First evaluate whether "
            "the palm, knuckles, fingers, wrist, and fingertips belong "
            "to the correct joints."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.setParent("..")
    cmds.setParent("..")

def load_skin_weight(silent=False):
    """Load the selected mesh and its current skin context."""
    try:
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

        _STATE["mesh_shape"] = mesh_shape
        _STATE["mesh_transform"] = mesh_transform
        _STATE["skin_cluster"] = skin_cluster
        _STATE["has_skin_cluster"] = has_skin
        _STATE["joints"] = list(joints)

        skin_label = (
            skin_cluster
            if skin_cluster
            else "<no skinCluster>"
        )

        _set_option_menu_items(
            CTRL_SKIN_MENU,
            [skin_label],
        )
        _set_joint_list(_STATE["joints"])

        cmds.text(
            CTRL_MESH_LABEL,
            edit=True,
            label=f"Mesh: {mesh_transform}",
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
    mesh_shape = _STATE.get("mesh_shape")
    mesh_transform = _STATE.get("mesh_transform")

    if not mesh_shape or not cmds.objExists(mesh_shape):
        raise RuntimeError("Loaded mesh no longer exists.")

    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    joints = adapter.influences()

    _STATE["skin_cluster"] = adapter.skin_cluster
    _STATE["has_skin_cluster"] = True
    _STATE["joints"] = list(joints)

    _set_option_menu_items(
        CTRL_SKIN_MENU,
        [adapter.skin_cluster],
    )
    _set_joint_list(joints)
    _update_joint_count_label()

    cmds.text(
        CTRL_MESH_LABEL,
        edit=True,
        label=f"Mesh: {mesh_transform}",
    )
    cmds.text(
        CTRL_MODE_LABEL,
        edit=True,
        label="Mode: Existing skinCluster",
    )


def add_selected_joints():
    try:
        _require_loaded_mesh()

        if _STATE.get("has_skin_cluster"):
            raise RuntimeError(
                "This mesh already has a skinCluster. The influence list "
                "is read from the existing skinCluster."
            )

        selected_joints = get_selected_joints()

        if not selected_joints:
            cmds.warning("No selected joints found.")
            return

        current_joints = list(_STATE.get("joints", []))
        added = []

        for joint in selected_joints:
            normalized = _normalize_joint_path(joint)

            if not _joint_exists_in_list(
                normalized,
                current_joints,
            ):
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
        _require_unskinned_mesh()

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
        selected_paths = {
            path
            for path in selected_paths
            if path
        }

        current_joints = list(_STATE.get("joints", []))
        remaining = [
            joint
            for joint in current_joints
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
        _require_unskinned_mesh()
        _set_joint_list([])
        _update_joint_count_label()
        _info("Removed all joints from the bind list.")
    except Exception as exc:
        _show_error(exc)


def show_selected_joints_in_list():
    try:
        _require_loaded_mesh()
        selected_joints = get_selected_joints()

        if not selected_joints:
            cmds.warning("No selected joints found in Maya.")
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
            cmds.warning(
                "Selected joints were not found in the tool list."
            )
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

        _info(
            f"Found {len(matched_labels)} selected joint(s) in the list."
        )

    except Exception as exc:
        _show_error(exc)


def apply_operation():
    """
    Run the constrained closest-bone hard-ownership bind.

    The solver writes exactly one weight of 1.0 per vertex. This allows
    ownership errors to be evaluated before smoothing or soft weighting
    can hide or spread them.
    """
    try:
        _require_unskinned_mesh()

        joints = list(
            _STATE.get(
                "joints",
                [],
            )
        )

        if len(joints) < 2:
            raise RuntimeError(
                "Constrained Closest bind requires at least two joints.\n\n"
                "Select joints in Maya and click Add Selected."
            )

        root_back_fraction = cmds.floatSliderGrp(
            CTRL_ROOT_BACK_FRACTION,
            query=True,
            value=True,
        )

        terminal_back_fraction = cmds.floatSliderGrp(
            CTRL_TERMINAL_BACK_FRACTION,
            query=True,
            value=True,
        )

        normal_penalty_strength = cmds.floatSliderGrp(
            CTRL_NORMAL_PENALTY,
            query=True,
            value=True,
        )

        result = commands.bind_object_constrained_closest(
            mesh_shape=_STATE["mesh_shape"],
            mesh_transform=_STATE["mesh_transform"],
            joints=joints,
            root_back_fraction=root_back_fraction,
            terminal_back_fraction=terminal_back_fraction,
            normal_penalty_strength=normal_penalty_strength,
        )

        _sync_loaded_skin_context()

        print(
            "\n"
            "[AD Skin Tool v2.5 Constrained Closest Bone]"
        )

        print(
            f"Skin Cluster: {result.skin_cluster}"
        )

        print(
            f"Mesh: {result.mesh_transform}"
        )

        print(
            f"Vertices: {result.vertex_count}"
        )

        print(
            f"Influences: {result.influence_count}"
        )

        print(
            f"Segment primitives: {result.segment_count}"
        )

        print(
            f"Point primitives: {result.point_count}"
        )

        print(
            "Fallback vertices: "
            f"{result.fallback_vertex_count}"
        )

        print(
            "Root back fraction: "
            f"{root_back_fraction:.3f}"
        )

        print(
            "Terminal back fraction: "
            f"{terminal_back_fraction:.3f}"
        )

        print(
            "Normal penalty strength: "
            f"{normal_penalty_strength:.3f}"
        )

        print(
            "Hard ownership assignments:"
        )

        for influence, count in (
            result.assignment_counts.items()
        ):
            print(
                f"  {influence}: {count}"
            )

        if result.fallback_vertex_count:
            cmds.warning(
                "Constrained bind completed, but "
                f"{result.fallback_vertex_count} vertices had no valid "
                "constrained candidate and used the distance fallback."
            )

        _info(
            "Constrained Closest bind complete: "
            f"{result.vertex_count} vertices, exactly one influence "
            "per vertex."
        )

    except Exception as exc:
        _show_error(exc)

def show_help():
    cmds.confirmDialog(
        title="AD Skin Weights Tool v2.5",
        message=(
            "Constrained Closest Bone Workflow:\n\n"
            "1. Select an unskinned mesh.\n"
            "2. Click Load Mesh / Skin.\n"
            "3. Select the bind joints in Maya.\n"
            "4. Click Add Selected.\n"
            "5. Use the default constraint values first.\n"
            "6. Click Bind Constrained Closest.\n\n"

            "Current diagnostic stage:\n"
            "- Exactly one influence per vertex.\n"
            "- Uses distance to parent-owned joint segments.\n"
            "- Rejects vertices too far behind a joint root.\n"
            "- Uses vertex normals as a soft outward penalty.\n"
            "- Uses special backward limits for terminal joints.\n"
            "- Does not perform smoothing.\n"
            "- Does not create soft weights.\n\n"

            "Root Back Limit:\n"
            "Controls how far behind a bone root that bone may own.\n\n"

            "Terminal Back Limit:\n"
            "Controls how far backward terminal joints may own vertices.\n\n"

            "Normal Penalty:\n"
            "Discourages ownership by bones located outside the "
            "outward-facing side of a vertex.\n\n"

            "First evaluate ownership regions only. Do not evaluate final "
            "deformation quality yet."
        ),
        button=["OK"],
    )

def show_environment_report():
    cmds.confirmDialog(
        title="AD Skin Tools Environment",
        message=environment_report(),
        button=["OK"],
    )


def _require_loaded_mesh():
    if not _STATE.get("mesh_shape"):
        raise RuntimeError(
            "No mesh loaded. Select a mesh and click Load Mesh / Skin."
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
        cmds.menuItem(
            label=item,
            parent=menu_name,
        )


def _set_joint_list(joints):
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
        display_name = _make_unique_joint_label(
            joint,
            normalized_joints,
        )

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


def _joint_exists_in_list(
    joint: str,
    joint_list: list[str],
) -> bool:
    normalized = _normalize_joint_path(joint)

    for existing in joint_list:
        if _normalize_joint_path(existing) == normalized:
            return True

    return False


def _normalize_joint_path(joint: str) -> str:
    matches = cmds.ls(
        joint,
        long=True,
        type="joint",
    ) or []

    if matches:
        return matches[0]

    return joint


def _unique_joint_paths(joints: list[str]) -> list[str]:
    result = []
    seen = set()

    for joint in joints:
        normalized = _normalize_joint_path(joint)

        if normalized in seen:
            continue

        seen.add(normalized)
        result.append(normalized)

    return result


def _make_unique_joint_label(
    joint: str,
    all_joints: list[str],
) -> str:
    joint_parts = _dag_parts(joint)

    if not joint_parts:
        return joint

    for depth in range(1, len(joint_parts) + 1):
        label = "|".join(joint_parts[-depth:])
        same_label_count = 0

        for other_joint in all_joints:
            other_parts = _dag_parts(other_joint)
            other_label = "|".join(
                other_parts[-depth:]
            )

            if other_label == label:
                same_label_count += 1

        if same_label_count == 1:
            return label

    return joint


def _dag_parts(node: str) -> list[str]:
    return [
        part
        for part in node.split("|")
        if part
    ]


def _path_from_display_label(display_label: str):
    return _STATE.get(
        "joint_display_to_path",
        {},
    ).get(display_label)


def _display_label_from_path(joint: str):
    normalized = _normalize_joint_path(joint)
    return _STATE.get(
        "joint_path_to_display",
        {},
    ).get(normalized)


def _label_control_row(
    label,
    control_builder,
    height=ROW_HEIGHT,
):
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
            (control, "left", CONTROL_GAP, label_control),
        ],
    )

    cmds.setParent("..")
    return control


def _button_row(
    buttons,
    height=BUTTON_HEIGHT,
    gap=BUTTON_GAP,
):
    layout = cmds.formLayout(
        numberOfDivisions=100,
        height=height,
    )
    count = len(buttons)

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
                (
                    button,
                    "left",
                    left_offset,
                    left_position,
                ),
                (
                    button,
                    "right",
                    right_offset,
                    right_position,
                ),
            ],
        )

    cmds.setParent("..")
    return layout


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
