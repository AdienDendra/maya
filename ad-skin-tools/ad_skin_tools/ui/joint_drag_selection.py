"""Plain left-drag range selection for Maya's custom ``TtreeView``.

Maya's ``cmds.treeView`` is exposed to Qt as a custom Autodesk ``TtreeView``
widget rather than a ``QTreeView``.  This module therefore leaves hit-testing
and range selection to Maya itself: a completed plain left-drag is translated
into one native Shift-click at the release position.
"""

from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import import_qt_modules


_FILTER = None
_WIDGET = None


def install(control_name: str) -> bool:
    """Install drag selection on an existing Maya ``treeView`` control.

    Failure is intentionally non-fatal.  Native click, Ctrl-click, and
    Shift-click selection remain available if Maya does not expose the widget
    as expected on a particular version or platform.
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
                self.dragging = False
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

                    self.press_position = _mouse_event_position(event)
                    return False

                if event_type == event_types.MouseMove:
                    if self.press_position is None:
                        return False
                    if event.modifiers() != no_modifier:
                        self._reset()
                        return False
                    if not (event.buttons() & left_button):
                        self._reset()
                        return False

                    position = _mouse_event_position(event)
                    if not self.dragging:
                        distance = (
                            position - self.press_position
                        ).manhattanLength()
                        if distance < QtWidgets.QApplication.startDragDistance():
                            return False
                        self.dragging = True

                    # Once the threshold is crossed, prevent Maya's custom
                    # widget from interpreting the motion as another gesture.
                    return True

                if event_type == event_types.MouseButtonRelease:
                    if event.button() != left_button:
                        self._reset()
                        return False

                    if not self.dragging:
                        self._reset()
                        return False

                    release_position = QtCore.QPoint(
                        _mouse_event_position(event)
                    )
                    self._reset()

                    # Defer until the original release has finished.  Maya's
                    # native TtreeView then receives a normal Shift-click and
                    # performs its own row hit-testing and range selection.
                    QtCore.QTimer.singleShot(
                        0,
                        lambda pos=release_position: self._send_shift_click(
                            pos
                        ),
                    )
                    return True

                return False

            def _send_shift_click(self, position):
                self.sending_synthetic_click = True
                try:
                    QtTest.QTest.mouseClick(
                        self.widget,
                        left_button,
                        shift_modifier,
                        position,
                    )
                finally:
                    self.sending_synthetic_click = False

            def _reset(self):
                self.press_position = None
                self.dragging = False

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
