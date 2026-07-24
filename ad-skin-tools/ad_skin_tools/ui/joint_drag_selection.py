"""Live plain left-drag range selection for Maya's custom ``TtreeView``.

Maya exposes ``cmds.treeView`` to Qt as a custom Autodesk ``TtreeView`` rather
than a ``QTreeView``. Hit-testing and range selection therefore remain native:
a plain left press is translated into a normal click, then drag movement is
translated into throttled Shift-clicks so the highlighted range updates live.
"""

import time

from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import import_qt_modules


_FILTER = None
_WIDGET = None
_PREVIEW_INTERVAL_SECONDS = 0.02


def install(control_name: str, selection_pruner=None) -> bool:
    """Install optional live drag selection on a Maya ``treeView`` control.

    ``selection_pruner`` is called after each native range update. Search uses
    it to remove hidden rows from the live selection without changing normal
    click, Ctrl-click, or Shift-click behaviour.

    Failure is intentionally non-fatal. Native Maya selection remains
    available if the internal widget cannot be resolved on a Maya version or
    platform.
    """

    global _FILTER
    global _WIDGET

    try:
        QtWidgets, _QtGui, QtCore, binding_name = import_qt_modules()
        QtTest = _qt_test_module(binding_name)
        wrap_instance = _qt_wrap_instance(binding_name)

        pointer = omui.MQtUtil.findControl(control_name)
        if not pointer:
            return False

        widget = wrap_instance(int(pointer), QtWidgets.QWidget)
        if widget is None:
            return False

        if _FILTER is not None and _WIDGET is not None:
            try:
                _WIDGET.removeEventFilter(_FILTER)
            except Exception:
                pass

        event_types = getattr(QtCore.QEvent, "Type", QtCore.QEvent)
        mouse_buttons = getattr(QtCore.Qt, "MouseButton", QtCore.Qt)
        keyboard_modifiers = getattr(
            QtCore.Qt,
            "KeyboardModifier",
            QtCore.Qt,
        )

        left_button = mouse_buttons.LeftButton
        no_modifier = keyboard_modifiers.NoModifier
        shift_modifier = keyboard_modifiers.ShiftModifier

        class JointDragSelectionFilter(QtCore.QObject):
            def __init__(self, target_widget):
                super().__init__(target_widget)
                self.widget = target_widget
                self.press_position = None
                self.last_preview_position = None
                self.last_preview_time = 0.0
                self.dragging = False
                self.active = False
                self.sending_synthetic_click = False

            def eventFilter(self, watched, event):
                if self.sending_synthetic_click:
                    return False

                event_type = event.type()

                if event_type == event_types.MouseButtonPress:
                    self._reset()
                    if event.button() != left_button:
                        return False
                    if event.modifiers() != no_modifier:
                        return False

                    position = QtCore.QPoint(_mouse_event_position(event))
                    self.active = True
                    self.press_position = position
                    self.last_preview_position = position

                    # Replace the consumed physical press with one complete
                    # native click. This preserves ordinary single selection
                    # while leaving TtreeView free for synthetic Shift-clicks
                    # during the physical drag.
                    self._send_click(position, no_modifier)
                    return True

                if event_type == event_types.MouseMove:
                    if not self.active or self.press_position is None:
                        return False
                    if event.modifiers() != no_modifier:
                        self._reset()
                        return True
                    if not (event.buttons() & left_button):
                        self._reset()
                        return True

                    position = QtCore.QPoint(_mouse_event_position(event))
                    if not self.dragging:
                        distance = (
                            position - self.press_position
                        ).manhattanLength()
                        if distance < QtWidgets.QApplication.startDragDistance():
                            return True
                        self.dragging = True

                    if self._preview_is_due(position):
                        self._send_range_preview(position)
                    return True

                if event_type == event_types.MouseButtonRelease:
                    if not self.active:
                        return False
                    if event.button() != left_button:
                        self._reset()
                        return False

                    position = QtCore.QPoint(_mouse_event_position(event))
                    if self.dragging and position != self.last_preview_position:
                        self._send_range_preview(position)
                    self._reset()
                    return True

                return False

            def _preview_is_due(self, position) -> bool:
                if position == self.last_preview_position:
                    return False
                now = time.monotonic()
                if now - self.last_preview_time < _PREVIEW_INTERVAL_SECONDS:
                    return False
                return True

            def _send_range_preview(self, position) -> None:
                self._send_click(position, shift_modifier)
                self.last_preview_position = QtCore.QPoint(position)
                self.last_preview_time = time.monotonic()
                if selection_pruner is not None:
                    try:
                        selection_pruner()
                    except Exception:
                        pass

            def _send_click(self, position, modifiers) -> None:
                self.sending_synthetic_click = True
                try:
                    QtTest.QTest.mouseClick(
                        self.widget,
                        left_button,
                        modifiers,
                        position,
                    )
                finally:
                    self.sending_synthetic_click = False

            def _reset(self) -> None:
                self.press_position = None
                self.last_preview_position = None
                self.last_preview_time = 0.0
                self.dragging = False
                self.active = False

        _WIDGET = widget
        _FILTER = JointDragSelectionFilter(widget)
        widget.installEventFilter(_FILTER)
        return True
    except Exception:
        _FILTER = None
        _WIDGET = None
        return False


def _mouse_event_position(event):
    position = getattr(event, "position", None)
    if callable(position):
        return position().toPoint()
    return event.pos()


def _qt_wrap_instance(binding_name):
    if binding_name == "PySide6":
        from shiboken6 import wrapInstance

        return wrapInstance

    from shiboken2 import wrapInstance

    return wrapInstance


def _qt_test_module(binding_name):
    if binding_name == "PySide6":
        from PySide6 import QtTest

        return QtTest

    from PySide2 import QtTest

    return QtTest
