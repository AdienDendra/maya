"""Presentation controls for the Maya joint-list ``TtreeView``.

The controls in this module are intentionally presentation-only:

- sorting changes only the rendered row order;
- Search hides rows without deleting joints or changing selection;
- Pin shows only the currently selected visible joint rows.
"""

import builtins

import maya.cmds as cmds
from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import import_qt_modules
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter


_CONTROLS_OBJECT_NAME = "adSkinJointPresentationControls"
_FIELD_OBJECT_NAME = "adSkinJointSearchField"
_PIN_OBJECT_NAME = "adSkinJointPinButton"

_STATE_TEXT_KEY = "joint_search_text"
_STATE_HIDDEN_KEY = "joint_search_hidden_item_ids"
_STATE_SORT_KEY = "joint_sort_mode"
_STATE_PIN_ENABLED_KEY = "joint_pin_enabled"
_STATE_PIN_PATHS_KEY = "joint_pinned_paths"
_STATE_REFRESH_KEY = "joint_presentation_refresh"

SORT_A_TO_Z = "a_to_z"
SORT_Z_TO_A = "z_to_a"
SORT_PENDING_JOINTS = "pending_joints"

_TOOL_WINDOW = None
_CONTROL_NAME = None
_CONTROLS = None
_FIELD = None
_PIN_BUTTON = None
_SORT_A_TO_Z_BUTTON = None
_SORT_Z_TO_A_BUTTON = None
_SORT_PENDING_BUTTON = None


def install(tool_window_module) -> bool:
    """Insert Sort, Search, and Pin controls immediately above the joint tree.

    Failure is non-fatal. The joint tree and all native Maya selection behaviour
    remain available if the internal Qt layout cannot be resolved.
    """

    global _TOOL_WINDOW
    global _CONTROL_NAME
    global _CONTROLS
    global _FIELD
    global _PIN_BUTTON
    global _SORT_A_TO_Z_BUTTON
    global _SORT_Z_TO_A_BUTTON
    global _SORT_PENDING_BUTTON

    _TOOL_WINDOW = tool_window_module
    _CONTROL_NAME = tool_window_module.CTRL_JOINT_LIST

    state = tool_window_module._STATE
    state.setdefault(_STATE_TEXT_KEY, "")
    state.setdefault(_STATE_HIDDEN_KEY, set())
    state.setdefault(_STATE_SORT_KEY, SORT_A_TO_Z)
    state.setdefault(_STATE_PIN_ENABLED_KEY, False)
    state.setdefault(_STATE_PIN_PATHS_KEY, set())
    state[_STATE_REFRESH_KEY] = apply_filter

    from ad_skin_tools.ui import joint_list

    joint_list._sorted_display_order = _sorted_display_order

    try:
        QtWidgets, QtGui, _QtCore, binding_name = import_qt_modules()
        wrap_instance = _qt_wrap_instance(binding_name)

        pointer = omui.MQtUtil.findControl(_CONTROL_NAME)
        if not pointer:
            return False

        tree_widget = wrap_instance(int(pointer), QtWidgets.QWidget)
        if tree_widget is None:
            return False

        container, layout, tree_index = _find_managing_layout(tree_widget)
        if layout is None:
            return False

        _remove_existing_controls(container, layout, QtWidgets)

        controls = QtWidgets.QWidget(container)
        controls.setObjectName(_CONTROLS_OBJECT_NAME)
        controls_layout = QtWidgets.QVBoxLayout(controls)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(3)

        sort_row = QtWidgets.QWidget(controls)
        sort_layout = QtWidgets.QHBoxLayout(sort_row)
        sort_layout.setContentsMargins(0, 0, 0, 0)
        sort_layout.setSpacing(8)
        sort_layout.addWidget(QtWidgets.QLabel("Sort:", sort_row))

        sort_a_to_z = QtWidgets.QRadioButton("A to Z", sort_row)
        sort_z_to_a = QtWidgets.QRadioButton("Z to A", sort_row)
        sort_pending = QtWidgets.QRadioButton("Pending Joints", sort_row)
        sort_layout.addWidget(sort_a_to_z)
        sort_layout.addWidget(sort_z_to_a)
        sort_layout.addWidget(sort_pending)
        sort_layout.addStretch(1)
        controls_layout.addWidget(sort_row)

        search_row = QtWidgets.QWidget(controls)
        search_layout = QtWidgets.QHBoxLayout(search_row)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(4)

        field = QtWidgets.QLineEdit(search_row)
        field.setObjectName(_FIELD_OBJECT_NAME)
        field.setPlaceholderText("Search...")
        field.setClearButtonEnabled(True)
        field.setToolTip("Filter joints by display name or full DAG path.")
        field.setText(str(state.get(_STATE_TEXT_KEY, "")))
        search_layout.addWidget(field, 1)

        pin_button = QtWidgets.QToolButton(search_row)
        pin_button.setObjectName(_PIN_OBJECT_NAME)
        pin_button.setCheckable(True)
        pin_button.setAutoRaise(True)
        pin_button.setFixedWidth(30)
        pin_button.setToolTip("Pin the currently selected visible joints.")
        _configure_pin_button(pin_button, QtGui)
        search_layout.addWidget(pin_button)
        controls_layout.addWidget(search_row)

        field.textChanged.connect(_on_text_changed)
        pin_button.toggled.connect(_on_pin_toggled)
        sort_a_to_z.clicked.connect(
            lambda *_: _set_sort_mode(SORT_A_TO_Z)
        )
        sort_z_to_a.clicked.connect(
            lambda *_: _set_sort_mode(SORT_Z_TO_A)
        )
        sort_pending.clicked.connect(
            lambda *_: _set_sort_mode(SORT_PENDING_JOINTS)
        )

        layout.insertWidget(tree_index, controls)

        _CONTROLS = controls
        _FIELD = field
        _PIN_BUTTON = pin_button
        _SORT_A_TO_Z_BUTTON = sort_a_to_z
        _SORT_Z_TO_A_BUTTON = sort_z_to_a
        _SORT_PENDING_BUTTON = sort_pending

        _set_sort_buttons(state.get(_STATE_SORT_KEY, SORT_A_TO_Z))
        pin_button.setChecked(bool(state.get(_STATE_PIN_ENABLED_KEY, False)))
        apply_filter()
        return True
    except Exception:
        _CONTROLS = None
        _FIELD = None
        _PIN_BUTTON = None
        _SORT_A_TO_Z_BUTTON = None
        _SORT_Z_TO_A_BUTTON = None
        _SORT_PENDING_BUTTON = None
        return False


def apply_filter(text=None) -> None:
    """Apply Search and Pin by changing row visibility only."""

    if _TOOL_WINDOW is None or not _CONTROL_NAME:
        return
    if not cmds.treeView(_CONTROL_NAME, exists=True):
        return

    state = _TOOL_WINDOW._STATE
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
        _set_pin_checked(False)

    hidden = set()
    for item_id, joint in item_to_path.items():
        if not _tree_item_exists(item_id):
            continue

        display_name = path_to_display.get(joint, _short_name(joint))
        search_match = (
            not needle
            or needle in str(display_name).casefold()
            or needle in str(joint).casefold()
        )
        pin_match = not pin_enabled or joint in pinned_paths
        visible = bool(search_match and pin_match)

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

    if _TOOL_WINDOW is None or not _CONTROL_NAME:
        return
    if not cmds.treeView(_CONTROL_NAME, exists=True):
        return

    hidden = set(_TOOL_WINDOW._STATE.get(_STATE_HIDDEN_KEY, set()))
    for item_id in hidden:
        if not _tree_item_exists(item_id):
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
            cmds.treeView(
                _CONTROL_NAME,
                edit=True,
                selectItem=(item_id, False),
            )


def _on_text_changed(text) -> None:
    apply_filter(text)


def _on_pin_toggled(enabled) -> None:
    if _TOOL_WINDOW is None:
        return

    state = _TOOL_WINDOW._STATE
    if enabled:
        selected_paths = set(_selected_visible_joint_paths())
        if not selected_paths:
            cmds.warning("Select one or more visible joints before pinning the list.")
            state[_STATE_PIN_ENABLED_KEY] = False
            state[_STATE_PIN_PATHS_KEY] = set()
            _set_pin_checked(False)
            return
        state[_STATE_PIN_ENABLED_KEY] = True
        state[_STATE_PIN_PATHS_KEY] = selected_paths
    else:
        state[_STATE_PIN_ENABLED_KEY] = False
        state[_STATE_PIN_PATHS_KEY] = set()

    apply_filter()


def _set_sort_mode(mode: str) -> None:
    if _TOOL_WINDOW is None:
        return
    if mode not in {SORT_A_TO_Z, SORT_Z_TO_A, SORT_PENDING_JOINTS}:
        return
    if mode == SORT_PENDING_JOINTS and not _pending_joint_paths():
        cmds.warning("No pending joints are available in the list.")
        _set_sort_buttons(_TOOL_WINDOW._STATE.get(_STATE_SORT_KEY, SORT_A_TO_Z))
        return

    state = _TOOL_WINDOW._STATE
    if state.get(_STATE_SORT_KEY) == mode:
        return

    state[_STATE_SORT_KEY] = mode
    _set_sort_buttons(mode)
    joints = builtins.list(state.get("joints", []))
    set_joint_list = getattr(_TOOL_WINDOW, "_set_joint_list", None)
    if callable(set_joint_list):
        set_joint_list(joints)


def _sorted_display_order(joints, display_labels):
    """Return presentation order without rewriting authoritative joint order."""

    mode = _TOOL_WINDOW._STATE.get(_STATE_SORT_KEY, SORT_A_TO_Z)
    key = lambda joint: (
        str(display_labels[joint]).casefold(),
        str(joint).casefold(),
    )

    if mode == SORT_Z_TO_A:
        return sorted(joints, key=key, reverse=True)

    if mode == SORT_PENDING_JOINTS:
        bound = _current_bound_paths()
        pending = {joint for joint in joints if joint not in bound}
        if pending:
            return sorted(
                joints,
                key=lambda joint: (
                    0 if joint in pending else 1,
                    str(display_labels[joint]).casefold(),
                    str(joint).casefold(),
                ),
            )

    return sorted(joints, key=key)


def _current_bound_paths():
    state = _TOOL_WINDOW._STATE
    if state.get("has_skin_cluster") and state.get("mesh_shape"):
        try:
            bound = set(
                SkinClusterAdapter.from_mesh(state["mesh_shape"]).influences()
            )
            state["bound_joint_paths"] = bound
            return bound
        except Exception:
            pass
    return set(state.get("bound_joint_paths", set()))


def _pending_joint_paths():
    if _TOOL_WINDOW is None:
        return []
    state = _TOOL_WINDOW._STATE
    bound = set(state.get("bound_joint_paths", set()))
    return [joint for joint in state.get("joints", []) if joint not in bound]


def _update_pending_sort_availability() -> None:
    has_pending = bool(_pending_joint_paths())
    if _SORT_PENDING_BUTTON is not None:
        try:
            _SORT_PENDING_BUTTON.setEnabled(has_pending)
        except Exception:
            pass

    state = _TOOL_WINDOW._STATE
    mode = state.get(_STATE_SORT_KEY, SORT_A_TO_Z)
    if has_pending:
        _set_sort_buttons(mode)
    elif mode == SORT_PENDING_JOINTS:
        _set_sort_buttons(SORT_A_TO_Z)


def _set_sort_buttons(mode: str) -> None:
    buttons = (
        (_SORT_A_TO_Z_BUTTON, mode == SORT_A_TO_Z),
        (_SORT_Z_TO_A_BUTTON, mode == SORT_Z_TO_A),
        (_SORT_PENDING_BUTTON, mode == SORT_PENDING_JOINTS),
    )
    for button, checked in buttons:
        if button is None:
            continue
        try:
            blocked = button.blockSignals(True)
            button.setChecked(bool(checked))
            button.blockSignals(blocked)
        except Exception:
            pass


def _selected_visible_joint_paths():
    if _TOOL_WINDOW is None or not _CONTROL_NAME:
        return []

    state = _TOOL_WINDOW._STATE
    hidden = set(state.get(_STATE_HIDDEN_KEY, set()))
    item_to_path = state.get("joint_item_to_path", {})
    result = []

    for item_id, joint in item_to_path.items():
        if item_id in hidden or not _tree_item_exists(item_id):
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
            result.append(joint)

    return result


def _set_pin_checked(checked: bool) -> None:
    if _PIN_BUTTON is None:
        return
    try:
        blocked = _PIN_BUTTON.blockSignals(True)
        _PIN_BUTTON.setChecked(bool(checked))
        _PIN_BUTTON.blockSignals(blocked)
    except Exception:
        pass


def _configure_pin_button(button, QtGui) -> None:
    icon_names = (
        "pin.png",
        "pinOn.png",
        "pinOff.png",
        "pinSmall.png",
        "pinTab.png",
    )
    for icon_name in icon_names:
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
    existing = container.findChild(
        QtWidgets.QWidget,
        _CONTROLS_OBJECT_NAME,
    )
    if existing is not None:
        try:
            layout.removeWidget(existing)
        except Exception:
            pass
        existing.hide()
        existing.deleteLater()

    legacy_field = container.findChild(
        QtWidgets.QLineEdit,
        _FIELD_OBJECT_NAME,
    )
    if legacy_field is not None and legacy_field.parentWidget() is container:
        try:
            layout.removeWidget(legacy_field)
        except Exception:
            pass
        legacy_field.hide()
        legacy_field.deleteLater()


def _find_managing_layout(widget):
    child = widget
    parent = widget.parentWidget()
    while parent is not None:
        layout = parent.layout()
        if layout is not None:
            index = layout.indexOf(child)
            if index >= 0:
                return parent, layout, index
        child = parent
        parent = parent.parentWidget()
    return None, None, -1


def _tree_item_exists(item_id: str) -> bool:
    try:
        return bool(
            cmds.treeView(
                _CONTROL_NAME,
                query=True,
                itemExists=item_id,
            )
        )
    except Exception:
        return False


def _short_name(path: str) -> str:
    return str(path).rsplit("|", 1)[-1]


def _qt_wrap_instance(binding_name):
    if binding_name == "PySide6":
        from shiboken6 import wrapInstance

        return wrapInstance

    from shiboken2 import wrapInstance

    return wrapInstance
