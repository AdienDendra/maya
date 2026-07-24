"""Non-destructive joint-list search for Maya's custom ``TtreeView``."""

import maya.cmds as cmds
from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import import_qt_modules


_FIELD_OBJECT_NAME = "adSkinJointSearchField"
_STATE_TEXT_KEY = "joint_search_text"
_STATE_HIDDEN_KEY = "joint_search_hidden_item_ids"

_TOOL_WINDOW = None
_CONTROL_NAME = None
_FIELD = None
_TIMER = None
_LAST_ITEM_MAPPING = None


def install(tool_window_module) -> bool:
    """Insert a Search field immediately above the joint tree.

    Matching is case-insensitive and checks both the artist-facing display name
    and the full DAG path. Rows are hidden with ``treeView(itemVisible=...)``;
    no joint, selection, skinCluster, or weight data is removed or rewritten.
    """

    global _TOOL_WINDOW
    global _CONTROL_NAME
    global _FIELD
    global _TIMER
    global _LAST_ITEM_MAPPING

    _TOOL_WINDOW = tool_window_module
    _CONTROL_NAME = tool_window_module.CTRL_JOINT_LIST
    _LAST_ITEM_MAPPING = None

    state = tool_window_module._STATE
    state.setdefault(_STATE_TEXT_KEY, "")
    state.setdefault(_STATE_HIDDEN_KEY, set())

    try:
        QtWidgets, _QtGui, QtCore, binding_name = import_qt_modules()
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

        existing = container.findChild(
            QtWidgets.QLineEdit,
            _FIELD_OBJECT_NAME,
        )
        if existing is not None:
            try:
                layout.removeWidget(existing)
            except Exception:
                pass
            existing.hide()
            existing.deleteLater()

        field = QtWidgets.QLineEdit(container)
        field.setObjectName(_FIELD_OBJECT_NAME)
        field.setPlaceholderText("Search...")
        field.setClearButtonEnabled(True)
        field.setToolTip("Filter joints by display name or full DAG path.")
        field.setText(str(state.get(_STATE_TEXT_KEY, "")))

        layout.insertWidget(tree_index, field)
        field.textChanged.connect(_on_text_changed)

        timer = QtCore.QTimer(field)
        timer.setInterval(150)
        timer.timeout.connect(_refresh_after_tree_rebuild)
        timer.start()

        _FIELD = field
        _TIMER = timer
        apply_filter(field.text())
        return True
    except Exception:
        _FIELD = None
        _TIMER = None
        return False


def apply_filter(text=None) -> None:
    """Apply the current search by changing row visibility only."""

    global _LAST_ITEM_MAPPING

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
    hidden = set()

    for item_id, joint in item_to_path.items():
        if not _tree_item_exists(item_id):
            continue

        display_name = path_to_display.get(joint, _short_name(joint))
        visible = (
            not needle
            or needle in str(display_name).casefold()
            or needle in str(joint).casefold()
        )
        cmds.treeView(
            _CONTROL_NAME,
            edit=True,
            itemVisible=(item_id, bool(visible)),
        )
        if not visible:
            hidden.add(item_id)

    state[_STATE_HIDDEN_KEY] = hidden
    _LAST_ITEM_MAPPING = item_to_path


def prune_hidden_selection() -> None:
    """Remove hidden rows from a drag-generated range selection."""

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


def _refresh_after_tree_rebuild() -> None:
    """Reapply an active filter when ``set_joint_list`` replaces its mapping."""

    if _TOOL_WINDOW is None:
        return
    item_mapping = _TOOL_WINDOW._STATE.get("joint_item_to_path", {})
    if item_mapping is _LAST_ITEM_MAPPING:
        return
    apply_filter()


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
