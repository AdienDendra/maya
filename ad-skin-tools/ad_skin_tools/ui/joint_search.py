"""Presentation-only Sort, Search, and Pin controls for the joint tree."""

import maya.cmds as cmds
from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import import_qt_modules
from ad_skin_tools.ui import joint_list, qt_helpers

_CONTROLS_OBJECT_NAME = "adSkinJointPresentationControls"
_FIELD_OBJECT_NAME = "adSkinJointSearchField"
_PIN_OBJECT_NAME = "adSkinJointPinButton"

_STATE_TEXT_KEY = "joint_search_text"
_STATE_HIDDEN_KEY = "joint_search_hidden_item_ids"
_STATE_SORT_KEY = "joint_sort_mode"
_STATE_PIN_ENABLED_KEY = "joint_pin_enabled"
_STATE_PIN_PATHS_KEY = "joint_pinned_paths"
_STATE_REFRESH_KEY = "joint_presentation_refresh"

SORT_A_TO_Z = joint_list.SORT_A_TO_Z
SORT_Z_TO_A = joint_list.SORT_Z_TO_A
SORT_PENDING_JOINTS = joint_list.SORT_PENDING_JOINTS

_TOOL_WINDOW = None
_CONTROL_NAME = None
_CONTROLS = None
_FIELD = None
_PIN_BUTTON = None
_SORT_BUTTONS = {}


def install(tool_window_module) -> bool:
    """Insert Sort, Search, and Pin controls immediately above the joint tree."""

    global _TOOL_WINDOW, _CONTROL_NAME
    _TOOL_WINDOW = tool_window_module
    _CONTROL_NAME = tool_window_module.CTRL_JOINT_LIST
    _initialize_state()

    try:
        QtWidgets, QtGui, _QtCore, binding_name = import_qt_modules()
        tree_widget = qt_helpers.wrap_instance(
            omui.MQtUtil.findControl(_CONTROL_NAME),
            QtWidgets.QWidget,
            binding_name,
        )
        container, layout, tree_index = qt_helpers.find_managing_layout(
            tree_widget
        )
        if layout is None:
            return False

        _remove_existing_controls(container, layout, QtWidgets)
        controls = _build_controls(container, QtWidgets, QtGui)
        layout.insertWidget(tree_index, controls)
        _sync_controls_from_state()
        apply_filter()
        return True
    except Exception:
        _clear_widget_references()
        return False


def apply_filter(text=None) -> None:
    """Apply Search and Pin by changing row visibility only."""

    if not _tree_available():
        return

    state = _state()
    if text is None:
        text = state.get(_STATE_TEXT_KEY, "")
    text = str(text)
    state[_STATE_TEXT_KEY] = text
    needle = text.strip().casefold()

    item_to_path = state.get("joint_item_to_path", {})
    path_to_display = state.get("joint_path_to_display", {})
    current_paths = set(item_to_path.values())
    pinned_paths = set(state.get(_STATE_PIN_PATHS_KEY, set()))
    pinned_paths.intersection_update(current_paths)
    state[_STATE_PIN_PATHS_KEY] = pinned_paths

    pin_enabled = bool(state.get(_STATE_PIN_ENABLED_KEY, False))
    if pin_enabled and not pinned_paths:
        pin_enabled = False
        state[_STATE_PIN_ENABLED_KEY] = False
        qt_helpers.set_checked_silently(_PIN_BUTTON, False)

    hidden = set()
    for item_id, joint in item_to_path.items():
        if not joint_list._tree_item_exists(_CONTROL_NAME, item_id):
            continue
        display_name = path_to_display.get(joint, _short_name(joint))
        search_match = (
            not needle
            or needle in str(display_name).casefold()
            or needle in str(joint).casefold()
        )
        visible = bool(
            search_match and (not pin_enabled or joint in pinned_paths)
        )
        cmds.treeView(
            _CONTROL_NAME,
            edit=True,
            itemVisible=(item_id, visible),
        )
        if not visible:
            hidden.add(item_id)

    state[_STATE_HIDDEN_KEY] = hidden
    _update_pending_sort_availability()


def prune_hidden_selection() -> None:
    """Remove hidden rows from a drag-generated native range selection."""

    if not _tree_available():
        return
    hidden = set(_state().get(_STATE_HIDDEN_KEY, set()))
    for item_id in hidden:
        if not joint_list._tree_item_exists(_CONTROL_NAME, item_id):
            continue
        try:
            selected = cmds.treeView(
                _CONTROL_NAME,
                query=True,
                itemSelected=item_id,
            )
        except Exception:
            selected = False
        if selected:
            joint_list._set_tree_item_selected(
                _CONTROL_NAME,
                item_id,
                False,
            )


def _initialize_state() -> None:
    state = _state()
    state.setdefault(_STATE_TEXT_KEY, "")
    state.setdefault(_STATE_HIDDEN_KEY, set())
    state.setdefault(_STATE_SORT_KEY, SORT_A_TO_Z)
    state.setdefault(_STATE_PIN_ENABLED_KEY, False)
    state.setdefault(_STATE_PIN_PATHS_KEY, set())
    state[_STATE_REFRESH_KEY] = apply_filter


def _build_controls(container, QtWidgets, QtGui):
    global _CONTROLS, _FIELD, _PIN_BUTTON, _SORT_BUTTONS

    controls = QtWidgets.QWidget(container)
    controls.setObjectName(_CONTROLS_OBJECT_NAME)
    column = QtWidgets.QVBoxLayout(controls)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(3)

    sort_row, sort_buttons = _build_sort_row(controls, QtWidgets)
    search_row, field, pin_button = _build_search_row(
        controls,
        QtWidgets,
        QtGui,
    )
    column.addWidget(sort_row)
    column.addWidget(search_row)

    _CONTROLS = controls
    _FIELD = field
    _PIN_BUTTON = pin_button
    _SORT_BUTTONS = sort_buttons
    return controls


def _build_sort_row(parent, QtWidgets):
    row = QtWidgets.QWidget(parent)
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    layout.addWidget(QtWidgets.QLabel("Sort:", row))

    definitions = (
        (SORT_A_TO_Z, "A to Z"),
        (SORT_Z_TO_A, "Z to A"),
        (SORT_PENDING_JOINTS, "Pending Joints"),
    )
    buttons = {}
    for mode, label in definitions:
        button = QtWidgets.QRadioButton(label, row)
        button.clicked.connect(
            lambda *_args, value=mode: _set_sort_mode(value)
        )
        layout.addWidget(button)
        buttons[mode] = button
    layout.addStretch(1)
    return row, buttons


def _build_search_row(parent, QtWidgets, QtGui):
    row = QtWidgets.QWidget(parent)
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)

    field = QtWidgets.QLineEdit(row)
    field.setObjectName(_FIELD_OBJECT_NAME)
    field.setPlaceholderText("Search...")
    field.setClearButtonEnabled(True)
    field.setToolTip("Filter joints by display name or full DAG path.")
    field.setText(str(_state().get(_STATE_TEXT_KEY, "")))
    field.textChanged.connect(apply_filter)
    layout.addWidget(field, 1)

    pin_button = QtWidgets.QToolButton(row)
    pin_button.setObjectName(_PIN_OBJECT_NAME)
    pin_button.setCheckable(True)
    pin_button.setAutoRaise(True)
    pin_button.setFixedWidth(30)
    pin_button.setToolTip("Pin the currently selected visible joints.")
    _configure_pin_button(pin_button, QtGui)
    pin_button.toggled.connect(_on_pin_toggled)
    layout.addWidget(pin_button)
    return row, field, pin_button


def _sync_controls_from_state() -> None:
    _set_sort_buttons(_state().get(_STATE_SORT_KEY, SORT_A_TO_Z))
    qt_helpers.set_checked_silently(
        _PIN_BUTTON,
        _state().get(_STATE_PIN_ENABLED_KEY, False),
    )


def _on_pin_toggled(enabled) -> None:
    if _TOOL_WINDOW is None:
        return
    state = _state()
    if enabled:
        selected = set(_selected_visible_joint_paths())
        if not selected:
            cmds.warning("Select one or more visible joints before pinning the list.")
            state[_STATE_PIN_ENABLED_KEY] = False
            state[_STATE_PIN_PATHS_KEY] = set()
            qt_helpers.set_checked_silently(_PIN_BUTTON, False)
            return
        state[_STATE_PIN_ENABLED_KEY] = True
        state[_STATE_PIN_PATHS_KEY] = selected
    else:
        state[_STATE_PIN_ENABLED_KEY] = False
        state[_STATE_PIN_PATHS_KEY] = set()
    apply_filter()


def _set_sort_mode(mode: str) -> None:
    if _TOOL_WINDOW is None or mode not in joint_list.VALID_SORT_MODES:
        return
    if mode == SORT_PENDING_JOINTS and not joint_list.pending_joint_paths():
        cmds.warning("No pending joints are available in the list.")
        _set_sort_buttons(_state().get(_STATE_SORT_KEY, SORT_A_TO_Z))
        return
    if _state().get(_STATE_SORT_KEY) == mode:
        return

    _state()[_STATE_SORT_KEY] = mode
    _set_sort_buttons(mode)
    set_joint_list = getattr(_TOOL_WINDOW, "_set_joint_list", None)
    if callable(set_joint_list):
        set_joint_list(list(_state().get("joints", [])))


def _update_pending_sort_availability() -> None:
    has_pending = bool(joint_list.pending_joint_paths())
    pending_button = _SORT_BUTTONS.get(SORT_PENDING_JOINTS)
    if pending_button is not None:
        pending_button.setEnabled(has_pending)

    mode = _state().get(_STATE_SORT_KEY, SORT_A_TO_Z)
    if not has_pending and mode == SORT_PENDING_JOINTS:
        _state()[_STATE_SORT_KEY] = SORT_A_TO_Z
        mode = SORT_A_TO_Z
    _set_sort_buttons(mode)


def _set_sort_buttons(mode: str) -> None:
    for value, button in _SORT_BUTTONS.items():
        qt_helpers.set_checked_silently(button, value == mode)


def _selected_visible_joint_paths():
    if not _tree_available():
        return []
    hidden = set(_state().get(_STATE_HIDDEN_KEY, set()))
    item_to_path = _state().get("joint_item_to_path", {})
    selected = set(joint_list.selected_joint_paths())
    return [
        joint
        for item_id, joint in item_to_path.items()
        if item_id not in hidden and joint in selected
    ]


def _configure_pin_button(button, QtGui) -> None:
    for icon_name in (
        "pin.png",
        "pinOn.png",
        "pinOff.png",
        "pinSmall.png",
        "pinTab.png",
    ):
        try:
            matches = cmds.resourceManager(nameFilter=icon_name) or []
        except Exception:
            matches = []
        if icon_name not in matches:
            continue
        icon = QtGui.QIcon(":/{}".format(icon_name))
        if not icon.isNull():
            button.setIcon(icon)
            return
    button.setText("PIN")


def _remove_existing_controls(container, layout, QtWidgets) -> None:
    qt_helpers.remove_named_child(
        container,
        layout,
        QtWidgets.QWidget,
        _CONTROLS_OBJECT_NAME,
    )
    legacy = container.findChild(QtWidgets.QLineEdit, _FIELD_OBJECT_NAME)
    if legacy is not None and legacy.parentWidget() is container:
        try:
            layout.removeWidget(legacy)
        except Exception:
            pass
        legacy.hide()
        legacy.deleteLater()


def _clear_widget_references() -> None:
    global _CONTROLS, _FIELD, _PIN_BUTTON, _SORT_BUTTONS
    _CONTROLS = None
    _FIELD = None
    _PIN_BUTTON = None
    _SORT_BUTTONS = {}


def _tree_available() -> bool:
    return bool(
        _TOOL_WINDOW is not None
        and _CONTROL_NAME
        and cmds.treeView(_CONTROL_NAME, exists=True)
    )


def _state():
    return _TOOL_WINDOW._STATE


def _short_name(path: str) -> str:
    return str(path).rsplit("|", 1)[-1]
