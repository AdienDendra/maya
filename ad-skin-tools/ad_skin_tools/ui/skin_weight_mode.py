"""Brush-free, live skin-weight colour feedback for the loaded mesh."""

import maya.cmds as cmds
import maya.mel as mel
from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import import_qt_modules


MODE_OFF = "off"
MODE_HEAT = "heat"
MODE_SPECTRUM = "spectrum"
MODE_GRAYSCALE = "grayscale"

_MODE_KEY = "skin_weight_mode"
_PREVIEW_KEY = "skin_weight_preview_joint"
_SUSPENDED_KEY = "skin_weight_mode_suspended"
_REFRESH_KEY = "skin_weight_mode_refresh"
_CONTROLS_NAME = "adSkinWeightModeControls"

# Repeating position, red, green, blue, linear-interpolation entries.
_RAMPS = {
    MODE_HEAT: "0,0,0,0,1,0.5,1,0,0,1,1,1,1,0,1",
    MODE_SPECTRUM: (
        "0,0,0,1,1,0.25,0,1,0,1,0.5,1,1,0,1,"
        "0.75,1,0.5,0,1,1,1,0,0,1"
    ),
    MODE_GRAYSCALE: "0,0,0,0,1,0.5,0.5,0.5,0.5,1,1,1,1,1,1",
}

_SNAPSHOT_FLAGS = (
    "colorfeedback",
    "colorfeedbackOverride",
    "colorRamp",
    "useColorRamp",
    "useMaxMinColor",
    "rampMinColor",
    "rampMaxColor",
    "colorrangelower",
    "colorrangeupper",
    "disablelighting",
    "brushfeedback",
    "outline",
    "tangentOutline",
    "surfaceConformedBrushVertices",
    "xrayJoints",
    "showactive",
    "skinPaintMode",
    "influence",
)

_TOOL_WINDOW = None
_JOINT_LIST = None
_QT = None
_CONTROLS = None
_BUTTONS = {}
_BUTTON_GROUP = None
_CONNECTED_BUTTONS = []
_SCRIPT_JOBS = []
_PAINT_CONTEXT = None
_CONTEXT_MESH = None
_PREVIOUS_CONTEXT = None
_NATIVE_SNAPSHOT = {}
_INTERNAL_SWITCH = False
_WAS_NATIVE_PAINT = False
_REFRESH_QUEUED = False


def install(tool_window_module, joint_list_module):
    """Install controls and callbacks after the workspace has been built."""

    global _TOOL_WINDOW
    global _JOINT_LIST

    _TOOL_WINDOW = tool_window_module
    _JOINT_LIST = joint_list_module

    state = tool_window_module._STATE
    # Opening or reloading the tool always starts in normal mesh display.
    state[_MODE_KEY] = MODE_OFF
    state[_PREVIEW_KEY] = None
    state[_SUSPENDED_KEY] = False
    state[_REFRESH_KEY] = request_refresh

    result = _install_controls()
    _install_tree_callback()
    _connect_operation_buttons()
    _install_script_jobs()
    _set_button_state(MODE_OFF)
    return result


def shutdown(*_):
    """Restore native paint settings, normal mesh shading, and prior tool."""

    global _CONTROLS
    global _BUTTONS
    global _BUTTON_GROUP
    global _CONNECTED_BUTTONS
    global _SCRIPT_JOBS
    global _PAINT_CONTEXT
    global _CONTEXT_MESH
    global _PREVIOUS_CONTEXT
    global _NATIVE_SNAPSHOT
    global _WAS_NATIVE_PAINT
    global _REFRESH_QUEUED

    try:
        _deactivate_mode(restore_context=True)
    except Exception:
        pass

    for job in list(_SCRIPT_JOBS):
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass

    _CONTROLS = None
    _BUTTONS = {}
    _BUTTON_GROUP = None
    _CONNECTED_BUTTONS = []
    _SCRIPT_JOBS = []
    _PAINT_CONTEXT = None
    _CONTEXT_MESH = None
    _PREVIOUS_CONTEXT = None
    _NATIVE_SNAPSHOT = {}
    _WAS_NATIVE_PAINT = False
    _REFRESH_QUEUED = False


def set_mode(mode):
    """Activate one visual preset or return to normal mesh display."""

    if _TOOL_WINDOW is None or mode not in {
        MODE_OFF,
        MODE_HEAT,
        MODE_SPECTRUM,
        MODE_GRAYSCALE,
    }:
        return

    state = _TOOL_WINDOW._STATE
    previous_mode = state.get(_MODE_KEY, MODE_OFF)

    if mode == MODE_OFF:
        state[_MODE_KEY] = MODE_OFF
        state[_PREVIEW_KEY] = None
        state[_SUSPENDED_KEY] = False
        _set_button_state(MODE_OFF)
        _deactivate_mode(restore_context=True)
        return

    try:
        joint = _require_single_selected_bound_joint()
        _activate_mode(mode, joint, capture_previous=(previous_mode == MODE_OFF))
    except Exception as exc:
        if previous_mode == MODE_OFF:
            state[_MODE_KEY] = MODE_OFF
            state[_PREVIEW_KEY] = None
            state[_SUSPENDED_KEY] = False
            _set_button_state(MODE_OFF)
            _deactivate_mode(restore_context=True)
        else:
            _set_button_state(previous_mode)
        _TOOL_WINDOW._show_error(exc)


def _activate_mode(mode, joint, capture_previous):
    global _PREVIOUS_CONTEXT

    state = _TOOL_WINDOW._STATE
    mesh_shape = state.get("mesh_shape")
    mesh_transform = state.get("mesh_transform")
    skin_cluster = state.get("skin_cluster")

    if not all((mesh_shape, mesh_transform, skin_cluster)):
        raise RuntimeError(
            "Skin Weight Mode requires a loaded mesh with an existing skinCluster."
        )
    for node in (mesh_shape, mesh_transform, skin_cluster, joint):
        if not cmds.objExists(node):
            raise RuntimeError(
                "Skin Weight Mode could not resolve the loaded skin context."
            )

    if capture_previous:
        current = _current_context()
        if current and not _is_skin_paint_context(current):
            _PREVIOUS_CONTEXT = current

    context = _ensure_context(mesh_transform)
    _capture_snapshot(context, only_if_empty=True)
    _apply_feedback(context, joint, mode)

    state[_MODE_KEY] = mode
    state[_PREVIEW_KEY] = joint
    state[_SUSPENDED_KEY] = False
    _set_button_state(mode)


def request_refresh(*_):
    """Coalesce selection and operation callbacks into one deferred refresh."""

    global _REFRESH_QUEUED

    if _TOOL_WINDOW is None:
        return
    if _TOOL_WINDOW._STATE.get(_MODE_KEY, MODE_OFF) == MODE_OFF:
        return
    if _REFRESH_QUEUED:
        return

    _REFRESH_QUEUED = True
    try:
        cmds.evalDeferred(_deferred_refresh, lowestPriority=True)
    except Exception:
        _REFRESH_QUEUED = False
        refresh()


def _deferred_refresh():
    global _REFRESH_QUEUED

    _REFRESH_QUEUED = False
    refresh()


def refresh(*_):
    """Re-read current skinCluster weights through Maya colour feedback."""

    if _TOOL_WINDOW is None:
        return

    state = _TOOL_WINDOW._STATE
    mode = state.get(_MODE_KEY, MODE_OFF)
    if mode == MODE_OFF:
        return

    if _native_paint_active():
        state[_SUSPENDED_KEY] = True
        return

    mesh_shape = state.get("mesh_shape")
    mesh_transform = state.get("mesh_transform")
    skin_cluster = state.get("skin_cluster")
    if not all((mesh_shape, mesh_transform, skin_cluster)):
        _disable_feedback(restore=False)
        return
    if not all(
        cmds.objExists(node)
        for node in (mesh_shape, mesh_transform, skin_cluster)
    ):
        _disable_feedback(restore=False)
        return

    joint = _resolve_preview_joint()
    if not joint:
        _disable_feedback(restore=False)
        return

    try:
        context = _ensure_context(mesh_transform)
        _capture_snapshot(context, only_if_empty=True)
        _apply_feedback(context, joint, mode)
        state[_PREVIEW_KEY] = joint
        state[_SUSPENDED_KEY] = False
    except Exception as exc:
        cmds.warning(str(exc))


def _install_controls():
    global _QT
    global _CONTROLS
    global _BUTTONS
    global _BUTTON_GROUP

    try:
        QtWidgets, QtGui, QtCore, binding = import_qt_modules()
        _QT = (QtWidgets, QtGui, QtCore, binding)

        pointer = omui.MQtUtil.findControl(_TOOL_WINDOW.CTRL_JOINT_LABEL)
        if not pointer:
            return False

        widget = _wrap(binding)(int(pointer), QtWidgets.QWidget)
        container, layout, index = _managing_layout(widget)
        if layout is None:
            return False

        existing = container.findChild(QtWidgets.QWidget, _CONTROLS_NAME)
        if existing is not None:
            layout.removeWidget(existing)
            existing.deleteLater()

        controls = QtWidgets.QWidget(container)
        controls.setObjectName(_CONTROLS_NAME)
        row = QtWidgets.QHBoxLayout(controls)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(1)
        row.addWidget(QtWidgets.QLabel("Skin Weight Mode:", controls))
        row.addSpacing(4)

        group = QtWidgets.QButtonGroup(controls)
        group.setExclusive(True)
        buttons = {}

        definitions = (
            (MODE_OFF, "Off — normal mesh shading"),
            (MODE_HEAT, "Black / Red / Yellow"),
            (MODE_SPECTRUM, "Blue / Green / Yellow / Orange / Red"),
            (MODE_GRAYSCALE, "Black / Grey / White"),
        )

        for mode, tooltip in definitions:
            button = QtWidgets.QToolButton(controls)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setFixedSize(22, 22)
            button.setToolTip(tooltip)
            button.setIcon(_icon(mode, QtGui, QtCore))
            button.setIconSize(QtCore.QSize(16, 16))
            button.clicked.connect(
                lambda _checked=False, value=mode: set_mode(value)
            )
            group.addButton(button)
            row.addWidget(button)
            buttons[mode] = button

        row.addStretch(1)
        layout.insertWidget(index + 1, controls)

        _CONTROLS = controls
        _BUTTONS = buttons
        _BUTTON_GROUP = group
        return True
    except Exception:
        _CONTROLS = None
        _BUTTONS = {}
        _BUTTON_GROUP = None
        return False


def _install_tree_callback():
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return

    try:
        cmds.treeView(
            control,
            edit=True,
            selectCommand=_tree_selection_changed,
        )
    except Exception:
        pass


def _tree_selection_changed(item_id, selected):
    try:
        allowed = bool(
            _JOINT_LIST._allow_tree_selection_change(item_id, selected)
        )
    except Exception:
        allowed = True

    if not allowed:
        return False

    if bool(selected):
        state = _TOOL_WINDOW._STATE
        joint = state.get("joint_item_to_path", {}).get(item_id)
        if joint in set(state.get("bound_joint_paths", set())):
            state[_PREVIEW_KEY] = joint
            request_refresh()

    return True


def _connect_operation_buttons():
    """Refresh after existing Maya button commands finish."""

    global _CONNECTED_BUTTONS

    if _QT is None:
        return

    QtWidgets, _QtGui, _QtCore, binding = _QT
    connected = []
    names = (
        _TOOL_WINDOW.CTRL_BIND_BUTTON,
        "adSkin_addInfluenceButton",
        "adSkin_floodSelectedToJointButton",
        "adSkin_smoothSelectedComponentsButton",
    )

    for name in names:
        pointer = omui.MQtUtil.findControl(name)
        if not pointer:
            continue
        try:
            button = _wrap(binding)(int(pointer), QtWidgets.QPushButton)
            button.clicked.connect(request_refresh)
            connected.append(button)
        except Exception:
            pass

    pointer = omui.MQtUtil.findControl(_TOOL_WINDOW.CTRL_JOINT_LABEL)
    if pointer:
        try:
            label = _wrap(binding)(int(pointer), QtWidgets.QWidget)
            container, _layout, _index = _managing_layout(label)
            for button in container.findChildren(QtWidgets.QPushButton):
                if button.text() == "Load Mesh":
                    button.clicked.connect(request_refresh)
                    connected.append(button)
                    break
        except Exception:
            pass

    _CONNECTED_BUTTONS = connected


def _install_script_jobs():
    global _SCRIPT_JOBS

    for job in list(_SCRIPT_JOBS):
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass

    _SCRIPT_JOBS = []
    parent = _TOOL_WINDOW.WINDOW_NAME

    for event_name, callback in (
        ("ToolChanged", _tool_changed),
        ("Undo", request_refresh),
        ("Redo", request_refresh),
    ):
        try:
            _SCRIPT_JOBS.append(
                cmds.scriptJob(
                    event=(event_name, callback),
                    parent=parent,
                    protected=True,
                )
            )
        except Exception:
            pass

    try:
        _SCRIPT_JOBS.append(
            cmds.scriptJob(
                uiDeleted=(parent, shutdown),
                runOnce=True,
            )
        )
    except Exception:
        pass


def _tool_changed(*_):
    global _WAS_NATIVE_PAINT

    if _INTERNAL_SWITCH or _TOOL_WINDOW is None:
        return

    state = _TOOL_WINDOW._STATE
    mode = state.get(_MODE_KEY, MODE_OFF)
    if mode == MODE_OFF:
        return

    current = _current_context()
    if _is_skin_paint_context(current) and _query_skin_paint_mode(current) != 0:
        _WAS_NATIVE_PAINT = True
        state[_SUSPENDED_KEY] = True
        _restore_snapshot(force_mesh=False)
        return

    if _WAS_NATIVE_PAINT:
        _WAS_NATIVE_PAINT = False
        if _context_exists(_PAINT_CONTEXT):
            _capture_snapshot(_PAINT_CONTEXT, only_if_empty=False)
        state[_SUSPENDED_KEY] = False
        request_refresh()
        return

    # Skin Weight Mode remains current until Off is pressed. If another Maya
    # tool is selected, return to the brush-free marquee paint context.
    request_refresh()


def _require_single_selected_bound_joint():
    try:
        selected = list(_JOINT_LIST.selected_joint_paths())
    except Exception:
        selected = []

    if len(selected) != 1:
        raise RuntimeError(
            "Skin Weight Mode requires exactly one selected bound influence."
        )

    joint = selected[0]
    bound = set(_TOOL_WINDOW._STATE.get("bound_joint_paths", set()))
    if joint not in bound:
        raise RuntimeError(
            "The selected joint is pending and has no skin weights to display."
        )
    if not cmds.objExists(joint):
        raise RuntimeError(
            "The selected bound influence no longer exists in the scene."
        )
    return joint


def _resolve_preview_joint():
    state = _TOOL_WINDOW._STATE
    bound = set(state.get("bound_joint_paths", set()))
    joint = state.get(_PREVIEW_KEY)

    if joint in bound and cmds.objExists(joint):
        return joint

    return None


def _ensure_context(mesh_transform):
    global _PAINT_CONTEXT
    global _CONTEXT_MESH
    global _INTERNAL_SWITCH

    current = _current_context()
    if (
        _PAINT_CONTEXT
        and _CONTEXT_MESH == mesh_transform
        and _context_exists(_PAINT_CONTEXT)
    ):
        if current != _PAINT_CONTEXT:
            _INTERNAL_SWITCH = True
            try:
                cmds.setToolTo(_PAINT_CONTEXT)
            finally:
                _INTERNAL_SWITCH = False
        return _PAINT_CONTEXT

    selection = cmds.ls(selection=True, long=True) or []
    _INTERNAL_SWITCH = True
    try:
        cmds.select(mesh_transform, replace=True)
        mel.eval("ArtPaintSkinWeightsTool;")
        activated = _current_context()
        if not _is_skin_paint_context(activated):
            raise RuntimeError(
                "Skin Weight Mode could not initialize Maya colour feedback."
            )
        _PAINT_CONTEXT = activated
        _CONTEXT_MESH = mesh_transform
    finally:
        try:
            cmds.select(clear=True)
            existing = [node for node in selection if cmds.objExists(node)]
            if existing:
                cmds.select(existing, replace=True)
        except Exception:
            pass
        _INTERNAL_SWITCH = False

    return _PAINT_CONTEXT


def _apply_feedback(context, joint, mode):
    if _current_context() != context:
        _set_tool_context(context)

    ramp = _RAMPS[mode]
    try:
        cmds.artAttrSkinPaintCtx(
            context,
            edit=True,
            colorfeedback=False,
        )
    except Exception:
        pass

    settings = (
        ("skinPaintMode", 0),
        ("influence", joint),
        ("colorRamp", ramp),
        ("useColorRamp", True),
        ("useMaxMinColor", False),
        ("colorrangelower", 0.0),
        ("colorrangeupper", 1.0),
        ("disablelighting", True),
        ("brushfeedback", False),
        ("outline", False),
        ("tangentOutline", False),
        ("surfaceConformedBrushVertices", False),
        ("xrayJoints", False),
        ("showactive", False),
        ("colorfeedbackOverride", True),
        ("colorfeedback", True),
    )

    for flag, value in settings:
        try:
            cmds.artAttrSkinPaintCtx(
                context,
                edit=True,
                **{flag: value}
            )
        except Exception:
            pass

    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _capture_snapshot(context, only_if_empty):
    global _NATIVE_SNAPSHOT

    if only_if_empty and _NATIVE_SNAPSHOT:
        return
    if not _context_exists(context):
        return

    snapshot = {}
    for flag in _SNAPSHOT_FLAGS:
        try:
            snapshot[flag] = cmds.artAttrSkinPaintCtx(
                context,
                query=True,
                **{flag: True}
            )
        except Exception:
            pass

    if snapshot:
        _NATIVE_SNAPSHOT = snapshot


def _restore_snapshot(force_mesh):
    if not _context_exists(_PAINT_CONTEXT):
        return

    for flag, value in _NATIVE_SNAPSHOT.items():
        try:
            cmds.artAttrSkinPaintCtx(
                _PAINT_CONTEXT,
                edit=True,
                **{flag: value}
            )
        except Exception:
            pass

    if force_mesh:
        for flag, value in (
            ("colorfeedback", False),
            ("colorfeedbackOverride", False),
            ("brushfeedback", False),
            ("outline", False),
            ("tangentOutline", False),
            ("surfaceConformedBrushVertices", False),
        ):
            try:
                cmds.artAttrSkinPaintCtx(
                    _PAINT_CONTEXT,
                    edit=True,
                    **{flag: value}
                )
            except Exception:
                pass


def _disable_feedback(restore):
    if restore:
        _restore_snapshot(force_mesh=True)
    elif _context_exists(_PAINT_CONTEXT):
        for flag in ("colorfeedback", "colorfeedbackOverride"):
            try:
                cmds.artAttrSkinPaintCtx(
                    _PAINT_CONTEXT,
                    edit=True,
                    **{flag: False}
                )
            except Exception:
                pass

    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _deactivate_mode(restore_context):
    global _PREVIOUS_CONTEXT
    global _INTERNAL_SWITCH

    _disable_feedback(restore=True)

    if restore_context and _PREVIOUS_CONTEXT and _context_exists(_PREVIOUS_CONTEXT):
        _INTERNAL_SWITCH = True
        try:
            cmds.setToolTo(_PREVIOUS_CONTEXT)
        except Exception:
            pass
        finally:
            _INTERNAL_SWITCH = False

    _PREVIOUS_CONTEXT = None


def _set_tool_context(context):
    global _INTERNAL_SWITCH

    if not context or not _context_exists(context):
        raise RuntimeError(
            "Skin Weight Mode paint context is no longer available."
        )

    _INTERNAL_SWITCH = True
    try:
        cmds.setToolTo(context)
    finally:
        _INTERNAL_SWITCH = False


def _native_paint_active():
    context = _current_context()
    return bool(
        _is_skin_paint_context(context)
        and _query_skin_paint_mode(context) != 0
    )


def _query_skin_paint_mode(context):
    if not _is_skin_paint_context(context):
        return None

    try:
        return int(
            cmds.artAttrSkinPaintCtx(
                context,
                query=True,
                skinPaintMode=True,
            )
        )
    except Exception:
        return None


def _is_skin_paint_context(context):
    if not context:
        return False

    try:
        context_class = cmds.contextInfo(context, c=True)
    except Exception:
        context_class = ""

    text = "{} {}".format(context, context_class).casefold()
    return "artattrskin" in text or (
        "skin" in text
        and "paint" in text
        and "context" in text
    )


def _current_context():
    try:
        return cmds.currentCtx()
    except Exception:
        return None


def _context_exists(context):
    if not context:
        return False

    try:
        if cmds.contextInfo(context, exists=True):
            return True
    except Exception:
        pass

    try:
        return bool(cmds.artAttrSkinPaintCtx(context, exists=True))
    except Exception:
        return False


def _set_button_state(mode):
    for value, button in _BUTTONS.items():
        try:
            blocked = button.blockSignals(True)
            button.setChecked(value == mode)
            button.blockSignals(blocked)
        except Exception:
            pass


def _icon(mode, QtGui, QtCore):
    pixmap = QtGui.QPixmap(16, 16)
    colors = getattr(QtCore.Qt, "GlobalColor", QtCore.Qt)
    pixmap.fill(colors.transparent)

    painter = QtGui.QPainter(pixmap)
    try:
        hints = getattr(QtGui.QPainter, "RenderHint", QtGui.QPainter)
        painter.setRenderHint(hints.Antialiasing, True)
        rect = pixmap.rect().adjusted(1, 1, -2, -2)

        if mode == MODE_OFF:
            painter.fillRect(rect, QtGui.QColor(235, 235, 235))
            painter.setPen(QtGui.QPen(QtGui.QColor(210, 30, 30), 2.0))
            painter.drawLine(rect.bottomLeft(), rect.topRight())
        else:
            stops = {
                MODE_HEAT: (
                    (0, (0, 0, 0)),
                    (0.5, (255, 0, 0)),
                    (1, (255, 255, 0)),
                ),
                MODE_SPECTRUM: (
                    (0, (0, 0, 255)),
                    (0.25, (0, 255, 0)),
                    (0.5, (255, 255, 0)),
                    (0.75, (255, 128, 0)),
                    (1, (255, 0, 0)),
                ),
                MODE_GRAYSCALE: (
                    (0, (0, 0, 0)),
                    (0.5, (128, 128, 128)),
                    (1, (255, 255, 255)),
                ),
            }[mode]

            gradient = QtGui.QLinearGradient(
                rect.left(),
                0,
                rect.right(),
                0,
            )
            for position, rgb in stops:
                gradient.setColorAt(position, QtGui.QColor(*rgb))
            painter.fillRect(rect, gradient)

        painter.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30), 1.0))
        painter.drawRect(rect)
    finally:
        painter.end()

    return QtGui.QIcon(pixmap)


def _managing_layout(widget):
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


def _wrap(binding):
    if binding == "PySide6":
        from shiboken6 import wrapInstance

        return wrapInstance

    from shiboken2 import wrapInstance

    return wrapInstance
