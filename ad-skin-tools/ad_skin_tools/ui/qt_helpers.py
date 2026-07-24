"""Small Qt compatibility helpers shared by AD Skin Tool UI modules."""


def wrap_instance(pointer, base_class, binding_name):
    """Wrap a Maya UI pointer with the active PySide binding."""

    if not pointer:
        return None
    if binding_name == "PySide6":
        from shiboken6 import wrapInstance
    else:
        from shiboken2 import wrapInstance
    return wrapInstance(int(pointer), base_class)


def import_qt_test(binding_name):
    """Return the QtTest module matching the active PySide binding."""

    if binding_name == "PySide6":
        from PySide6 import QtTest
    else:
        from PySide2 import QtTest
    return QtTest


def find_managing_layout(widget):
    """Return ``(container, layout, index)`` for a managed Qt widget."""

    child = widget
    parent = widget.parentWidget() if widget is not None else None
    while parent is not None:
        layout = parent.layout()
        if layout is not None:
            index = layout.indexOf(child)
            if index >= 0:
                return parent, layout, index
        child = parent
        parent = parent.parentWidget()
    return None, None, -1


def remove_named_child(container, layout, widget_type, object_name):
    """Detach and schedule deletion of one named direct/descendant widget."""

    if container is None:
        return None
    widget = container.findChild(widget_type, object_name)
    if widget is None:
        return None
    try:
        layout.removeWidget(widget)
    except Exception:
        pass
    widget.hide()
    widget.deleteLater()
    return widget


def set_checked_silently(button, checked):
    """Change a checkable widget without emitting its connected signals."""

    if button is None:
        return
    previous = button.blockSignals(True)
    try:
        button.setChecked(bool(checked))
    finally:
        button.blockSignals(previous)
