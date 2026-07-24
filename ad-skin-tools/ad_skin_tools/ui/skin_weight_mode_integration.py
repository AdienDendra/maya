"""Skin Weight Visual integration and live Maya joint selection."""

import maya.cmds as cmds
from maya import OpenMayaUI as omui

from ad_skin_tools.ui import qt_helpers


_LIVE_STATE_KEY = "skin_weight_live_joint_selection"
_LIVE_CONTROLS_NAME = "adSkinWeightLiveSelectionControls"

_SKIN_WEIGHT_MODE = None
_SKIN_OPERATIONS = None
_TOOL_WINDOW = None
_JOINT_LIST = None

_ORIGINAL_SET_MODE = None
_ORIGINAL_FIND_LOAD_MESH_BUTTON = None

_LIVE_CONTROLS = None
_LIVE_LABEL = None
_LIVE_BUTTONS = {}
_LIVE_BUTTON_GROUP = None
_SELECTION_JOB = None


def prepare(skin_weight_mode_module, skin_operations_module) -> None:
    """Configure stable Maya control lookup before visual controls are built."""

    global _SKIN_WEIGHT_MODE, _SKIN_OPERATIONS
    global _ORIGINAL_FIND_LOAD_MESH_BUTTON

    _SKIN_WEIGHT_MODE = skin_weight_mode_module
    _SKIN_OPERATIONS = skin_operations_module

    current = skin_weight_mode_module._find_load_mesh_button
    if current is not _find_load_mesh_button:
        _ORIGINAL_FIND_LOAD_MESH_BUTTON = current
        skin_weight_mode_module._find_load_mesh_button = _find_load_mesh_button


def install(tool_window_module, joint_list_module) -> bool:
    """Install compact Live Joint Selection controls and scene synchronization."""

    global _TOOL_WINDOW, _JOINT_LIST
    _TOOL_WINDOW = tool_window_module
    _JOINT_LIST = joint_list_module

    if _SKIN_WEIGHT_MODE is None:
        return False

    _state().setdefault(_LIVE_STATE_KEY, True)
    _install_mode_wrapper()
    controls_installed = _install_live_controls()
    _install_selection_job()
    _set_live_button_state(_live_enabled())
    _set_live_controls_enabled(_visual_is_active())
    return controls_installed


def shutdown() -> None:
    """Remove live-selection callbacks and restore wrapped visual functions."""

    global _LIVE_CONTROLS, _LIVE_LABEL
    global _LIVE_BUTTONS, _LIVE_BUTTON_GROUP

    _remove_selection_job()
    _restore_wrapped_functions()

    _LIVE_CONTROLS = None
    _LIVE_LABEL = None
    _LIVE_BUTTONS = {}
    _LIVE_BUTTON_GROUP = None


def set_mode(mode) -> None:
    """Prepare viewport selection, then delegate to Skin Weight Visual."""

    if _ORIGINAL_SET_MODE is None:
        return

    mode_off = getattr(_SKIN_WEIGHT_MODE, "MODE_OFF", "off")
    if mode != mode_off and _live_enabled():
        _sync_scene_selection(update_preview=False)

    _ORIGINAL_SET_MODE(mode)
    _set_live_controls_enabled(_visual_is_active())

    if _visual_is_active() and _live_enabled():
        _sync_scene_selection(update_preview=True)


def set_live_selection(enabled) -> None:
    """Store the artist preference without changing the current visual mode."""

    if _TOOL_WINDOW is None:
        return

    _state()[_LIVE_STATE_KEY] = bool(enabled)
    _set_live_button_state(enabled)

    if enabled and _visual_is_active():
        _sync_scene_selection(update_preview=True)


def _install_mode_wrapper() -> None:
    global _ORIGINAL_SET_MODE

    current = _SKIN_WEIGHT_MODE.set_mode
    if current is set_mode:
        return

    _ORIGINAL_SET_MODE = current
    _SKIN_WEIGHT_MODE.set_mode = set_mode


def _restore_wrapped_functions() -> None:
    global _ORIGINAL_SET_MODE, _ORIGINAL_FIND_LOAD_MESH_BUTTON

    if (
        _SKIN_WEIGHT_MODE is not None
        and _ORIGINAL_SET_MODE is not None
        and _SKIN_WEIGHT_MODE.set_mode is set_mode
    ):
        _SKIN_WEIGHT_MODE.set_mode = _ORIGINAL_SET_MODE

    if (
        _SKIN_WEIGHT_MODE is not None
        and _ORIGINAL_FIND_LOAD_MESH_BUTTON is not None
        and _SKIN_WEIGHT_MODE._find_load_mesh_button is _find_load_mesh_button
    ):
        _SKIN_WEIGHT_MODE._find_load_mesh_button = (
            _ORIGINAL_FIND_LOAD_MESH_BUTTON
        )

    _ORIGINAL_SET_MODE = None
    _ORIGINAL_FIND_LOAD_MESH_BUTTON = None


def _find_load_mesh_button(QtWidgets, binding_name):
    """Resolve only the active named Load Mesh control."""

    if _SKIN_OPERATIONS is None:
        return None

    return qt_helpers.wrap_instance(
        omui.MQtUtil.findControl(_SKIN_OPERATIONS.CTRL_LOAD_MESH_BUTTON),
        QtWidgets.QPushButton,
        binding_name,
    )


def _install_live_controls() -> bool:
    global _LIVE_CONTROLS, _LIVE_LABEL
    global _LIVE_BUTTONS, _LIVE_BUTTON_GROUP

    qt_data = getattr(_SKIN_WEIGHT_MODE, "_QT", None)
    visual_controls = getattr(_SKIN_WEIGHT_MODE, "_CONTROLS", None)
    if qt_data is None or visual_controls is None:
        return False

    QtWidgets, _QtGui, _QtCore, _binding_name = qt_data
    container, layout, visual_index = qt_helpers.find_managing_layout(
        visual_controls
    )
    if layout is None:
        return False

    qt_helpers.remove_named_child(
        container,
        layout,
        QtWidgets.QWidget,
        _LIVE_CONTROLS_NAME,
    )
    _rename_visual_label(visual_controls, QtWidgets)

    controls = QtWidgets.QWidget(container)
    controls.setObjectName(_LIVE_CONTROLS_NAME)
    row = QtWidgets.QHBoxLayout(controls)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(2)

    label = QtWidgets.QLabel("Live Joint Selection:", controls)
    row.addWidget(label)
    row.addSpacing(3)

    group = QtWidgets.QButtonGroup(controls)
    group.setExclusive(True)
    buttons = {}

    for enabled, text in ((True, "On"), (False, "Off")):
        button = QtWidgets.QToolButton(controls)
        button.setText(text)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFixedSize(30, 20)
        button.setToolTip(
            "Follow the last selected listed bound joint in the Maya viewport."
            if enabled
            else "Keep Skin Weight Visual controlled only from the joint list."
        )
        button.clicked.connect(
            lambda _checked=False, value=enabled: set_live_selection(value)
        )
        group.addButton(button)
        row.addWidget(button)
        buttons[enabled] = button

    row.addStretch(1)
    layout.insertWidget(visual_index + 1, controls)

    _LIVE_CONTROLS = controls
    _LIVE_LABEL = label
    _LIVE_BUTTONS = buttons
    _LIVE_BUTTON_GROUP = group
    _align_labels(visual_controls, label, QtWidgets)
    return True


def _rename_visual_label(visual_controls, QtWidgets) -> None:
    labels = visual_controls.findChildren(QtWidgets.QLabel)
    if labels:
        labels[0].setText("Skin Weight Visual:")


def _align_labels(visual_controls, live_label, QtWidgets) -> None:
    labels = visual_controls.findChildren(QtWidgets.QLabel)
    if not labels:
        return

    visual_label = labels[0]
    width = max(
        int(visual_label.sizeHint().width()),
        int(live_label.sizeHint().width()),
    )
    visual_label.setFixedWidth(width)
    live_label.setFixedWidth(width)


def _install_selection_job() -> None:
    global _SELECTION_JOB

    _remove_selection_job()
    if _TOOL_WINDOW is None:
        return

    parent = _TOOL_WINDOW.WINDOW_NAME
    try:
        _SELECTION_JOB = cmds.scriptJob(
            event=("SelectionChanged", _on_scene_selection_changed),
            parent=parent,
            protected=True,
        )
    except Exception:
        _SELECTION_JOB = None


def _remove_selection_job() -> None:
    global _SELECTION_JOB

    if _SELECTION_JOB is not None:
        try:
            if cmds.scriptJob(exists=_SELECTION_JOB):
                cmds.scriptJob(kill=_SELECTION_JOB, force=True)
        except Exception:
            pass
    _SELECTION_JOB = None


def _on_scene_selection_changed(*_):
    if not _live_is_operational():
        return
    _sync_scene_selection(update_preview=True)


def _sync_scene_selection(update_preview):
    joint = _last_valid_selected_scene_joint()
    if joint is None:
        return None

    _JOINT_LIST.select_joint_paths([joint])
    _show_joint_in_list(joint)

    if update_preview and _visual_is_active():
        preview_key = getattr(
            _SKIN_WEIGHT_MODE,
            "_PREVIEW_KEY",
            "skin_weight_preview_joint",
        )
        _state()[preview_key] = joint
        _SKIN_WEIGHT_MODE.request_refresh()
    return joint


def _last_valid_selected_scene_joint():
    selected = _selected_scene_joints()
    if not selected:
        return None

    state = _state()
    listed = state.get("joint_path_to_item", {})
    bound = set(state.get("bound_joint_paths", set()))

    for joint in reversed(selected):
        try:
            normalized = _TOOL_WINDOW._normalize_joint_path(joint)
        except Exception:
            normalized = str(joint)

        if normalized in listed and normalized in bound:
            return normalized
    return None


def _selected_scene_joints():
    try:
        selected = cmds.ls(
            orderedSelection=True,
            long=True,
            type="joint",
        ) or []
    except Exception:
        selected = []

    if selected:
        return selected

    return cmds.ls(
        selection=True,
        long=True,
        type="joint",
    ) or []


def _show_joint_in_list(joint) -> None:
    if _TOOL_WINDOW is None:
        return

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    item_id = _state().get("joint_path_to_item", {}).get(joint)
    if not item_id or not cmds.treeView(control, exists=True):
        return

    try:
        if not cmds.treeView(
            control,
            query=True,
            itemExists=item_id,
        ):
            return
        cmds.treeView(
            control,
            edit=True,
            showItem=item_id,
        )
    except Exception:
        pass


def _set_live_button_state(enabled) -> None:
    for value, button in _LIVE_BUTTONS.items():
        try:
            qt_helpers.set_checked_silently(button, value == bool(enabled))
        except Exception:
            pass


def _set_live_controls_enabled(enabled) -> None:
    for button in _LIVE_BUTTONS.values():
        try:
            button.setEnabled(bool(enabled))
        except Exception:
            pass


def _live_is_operational() -> bool:
    return bool(
        _TOOL_WINDOW is not None
        and not _state().get("busy")
        and _live_enabled()
        and _visual_is_active()
    )


def _live_enabled() -> bool:
    return bool(
        _TOOL_WINDOW is not None
        and _state().get(_LIVE_STATE_KEY, True)
    )


def _visual_is_active() -> bool:
    if _SKIN_WEIGHT_MODE is None:
        return False

    try:
        return bool(_SKIN_WEIGHT_MODE._mode_is_active())
    except Exception:
        mode_key = getattr(
            _SKIN_WEIGHT_MODE,
            "_MODE_KEY",
            "skin_weight_mode",
        )
        mode_off = getattr(_SKIN_WEIGHT_MODE, "MODE_OFF", "off")
        return _state().get(mode_key, mode_off) != mode_off


def _state():
    return _TOOL_WINDOW._STATE
