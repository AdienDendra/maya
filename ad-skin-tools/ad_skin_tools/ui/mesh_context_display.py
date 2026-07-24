"""Aligned Mesh / Skin Context rows and shared value-column positioning."""

import maya.cmds as cmds


CTRL_LISTED_JOINTS_VALUE = "adSkin_listedJointCountValue"

_TOOL_WINDOW = None
_SKIN_OPERATIONS = None


def install(tool_window_module, skin_operations_module) -> None:
    """Install aligned context rows without changing mesh-loading behavior."""

    global _TOOL_WINDOW, _SKIN_OPERATIONS
    _TOOL_WINDOW = tool_window_module
    _SKIN_OPERATIONS = skin_operations_module

    tool_window_module._build_skin_cluster_section = _build_skin_cluster_section
    tool_window_module._update_joint_count_label = _update_listed_joint_count
    skin_operations_module._refresh_mesh_context_labels = (
        _refresh_mesh_context_labels
    )


def align_visual_controls(
    skin_weight_mode_module,
    skin_weight_mode_integration_module,
) -> None:
    """Align Qt visual controls with Maya label/value rows."""

    if _TOOL_WINDOW is None:
        return

    qt_data = getattr(skin_weight_mode_module, "_QT", None)
    visual_controls = getattr(skin_weight_mode_module, "_CONTROLS", None)
    live_controls = getattr(
        skin_weight_mode_integration_module,
        "_LIVE_CONTROLS",
        None,
    )
    live_label = getattr(
        skin_weight_mode_integration_module,
        "_LIVE_LABEL",
        None,
    )
    if qt_data is None or visual_controls is None:
        return

    QtWidgets, _QtGui, _QtCore, _binding_name = qt_data
    visual_labels = visual_controls.findChildren(QtWidgets.QLabel)
    if not visual_labels:
        return

    visual_label = visual_labels[0]
    visual_label.setText("Skin Weight Visual:")

    # Maya rows start controls after LABEL_WIDTH + CONTROL_GAP. Both Qt rows
    # contain a 3 px explicit spacer, so compensate in the fixed label width.
    explicit_spacer = 3
    label_width = max(
        0,
        int(_TOOL_WINDOW.LABEL_WIDTH)
        + int(_TOOL_WINDOW.CONTROL_GAP)
        - explicit_spacer,
    )

    for controls in (visual_controls, live_controls):
        if controls is not None and controls.layout() is not None:
            controls.layout().setSpacing(0)

    visual_label.setFixedWidth(label_width)
    if live_label is not None:
        live_label.setFixedWidth(label_width)


def _build_skin_cluster_section() -> None:
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
        lambda: cmds.textField(
            _TOOL_WINDOW.CTRL_SKIN_MENU,
            text="<no skinCluster>",
            editable=False,
        ),
    )
    _TOOL_WINDOW._label_control_row(
        "Loaded Mesh",
        lambda: cmds.text(
            _TOOL_WINDOW.CTRL_MESH_LABEL,
            label="<none>",
            align="left",
        ),
    )
    cmds.text(
        _TOOL_WINDOW.CTRL_MODE_LABEL,
        label="",
        visible=False,
        manage=False,
    )
    _TOOL_WINDOW._label_control_row(
        "Listed Joints",
        lambda: cmds.text(
            CTRL_LISTED_JOINTS_VALUE,
            label="0",
            align="left",
        ),
    )

    # Skin Weight Visual uses CTRL_JOINT_LABEL as its insertion anchor. Keep a
    # hidden standalone control in the parent column so the Qt rows are placed
    # beneath Listed Joints rather than inside its formLayout.
    cmds.text(
        _TOOL_WINDOW.CTRL_JOINT_LABEL,
        label="",
        visible=False,
        manage=False,
    )

    _SKIN_OPERATIONS._named_button_row(
        [
            (
                _SKIN_OPERATIONS.CTRL_LOAD_MESH_BUTTON,
                "Load Mesh",
                lambda *_: _TOOL_WINDOW.load_skin_weight(),
            )
        ],
        height=30,
    )
    cmds.setParent("..")
    cmds.setParent("..")


def _refresh_mesh_context_labels() -> None:
    if _TOOL_WINDOW is None:
        return

    state = _state()
    _set_text_field(
        _TOOL_WINDOW.CTRL_SKIN_MENU,
        state.get("skin_cluster") or "<no skinCluster>",
    )
    mesh_transform = state.get("mesh_transform")
    _set_text(
        _TOOL_WINDOW.CTRL_MESH_LABEL,
        _short_dag_name(mesh_transform) if mesh_transform else "<none>",
    )
    _update_listed_joint_count()


def _update_listed_joint_count() -> None:
    if _TOOL_WINDOW is None:
        return
    _set_text(
        CTRL_LISTED_JOINTS_VALUE,
        str(len(_state().get("joints", []))),
    )


def _set_text_field(control, value) -> None:
    if cmds.textField(control, exists=True):
        cmds.textField(control, edit=True, text=str(value))


def _set_text(control, value) -> None:
    if cmds.text(control, exists=True):
        cmds.text(control, edit=True, label=str(value))


def _short_dag_name(node) -> str:
    return str(node).rsplit("|", 1)[-1]


def _state():
    return _TOOL_WINDOW._STATE
