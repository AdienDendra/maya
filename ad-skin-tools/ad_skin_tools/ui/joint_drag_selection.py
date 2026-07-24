"""Live plain-left-drag range selection for Maya's custom ``TtreeView``."""

import time

from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import import_qt_modules
from ad_skin_tools.ui import qt_helpers

_FILTER = None
_WIDGET = None
_PREVIEW_INTERVAL_SECONDS = 0.02


def install(control_name: str, selection_pruner=None) -> bool:
    """Install optional native range-selection emulation on one Maya treeView."""

    global _FILTER, _WIDGET
    uninstall()

    try:
        QtWidgets, _QtGui, QtCore, binding_name = import_qt_modules()
        QtTest = qt_helpers.import_qt_test(binding_name)
        widget = qt_helpers.wrap_instance(
            omui.MQtUtil.findControl(control_name),
            QtWidgets.QWidget,
            binding_name,
        )
        if widget is None:
            return False

        filter_class = _build_filter_class(
            QtWidgets,
            QtCore,
            QtTest,
        )
        _WIDGET = widget
        _FILTER = filter_class(widget, selection_pruner)
        widget.installEventFilter(_FILTER)
        return True
    except Exception:
        _FILTER = None
        _WIDGET = None
        return False


def uninstall() -> None:
    """Remove the current event filter without affecting native Maya selection."""

    global _FILTER, _WIDGET
    if _FILTER is not None and _WIDGET is not None:
        try:
            _WIDGET.removeEventFilter(_FILTER)
        except Exception:
            pass
    _FILTER = None
    _WIDGET = None


def _build_filter_class(QtWidgets, QtCore, QtTest):
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
        def __init__(self, target_widget, selection_pruner):
            super().__init__(target_widget)
            self.widget = target_widget
            self.selection_pruner = selection_pruner
            self.sending_synthetic_click = False
            self._reset()

        def eventFilter(self, watched, event):
            if self.sending_synthetic_click:
                return False

            event_type = event.type()
            if event_type == event_types.MouseButtonPress:
                return self._on_press(event)
            if event_type == event_types.MouseMove:
                return self._on_move(event)
            if event_type == event_types.MouseButtonRelease:
                return self._on_release(event)
            return False

        def _on_press(self, event):
            self._reset()
            if event.button() != left_button or event.modifiers() != no_modifier:
                return False

            position = QtCore.QPoint(_mouse_event_position(event))
            self.active = True
            self.press_position = position
            self.last_preview_position = position
            self._send_click(position, no_modifier)
            return True

        def _on_move(self, event):
            if not self.active or self.press_position is None:
                return False
            if event.modifiers() != no_modifier or not (event.buttons() & left_button):
                self._reset()
                return True

            position = QtCore.QPoint(_mouse_event_position(event))
            if not self.dragging:
                distance = (position - self.press_position).manhattanLength()
                if distance < QtWidgets.QApplication.startDragDistance():
                    return True
                self.dragging = True

            if self._preview_is_due(position):
                self._send_range_preview(position)
            return True

        def _on_release(self, event):
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

        def _preview_is_due(self, position):
            if position == self.last_preview_position:
                return False
            return (
                time.monotonic() - self.last_preview_time
                >= _PREVIEW_INTERVAL_SECONDS
            )

        def _send_range_preview(self, position):
            self._send_click(position, shift_modifier)
            self.last_preview_position = QtCore.QPoint(position)
            self.last_preview_time = time.monotonic()
            if callable(self.selection_pruner):
                try:
                    self.selection_pruner()
                except Exception:
                    pass

        def _send_click(self, position, modifiers):
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

        def _reset(self):
            self.press_position = None
            self.last_preview_position = None
            self.last_preview_time = 0.0
            self.dragging = False
            self.active = False

    return JointDragSelectionFilter


def _mouse_event_position(event):
    position = getattr(event, "position", None)
    if callable(position):
        return position().toPoint()
    return event.pos()
