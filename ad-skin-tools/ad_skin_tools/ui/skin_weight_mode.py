"""Independent, visualization-only skin-weight preview for the loaded mesh."""

import maya.api.OpenMaya as om
import maya.cmds as cmds
from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import ensure_numpy, import_qt_modules
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter

np = ensure_numpy()


MODE_OFF = "off"
MODE_HEAT = "heat"
MODE_SPECTRUM = "spectrum"
MODE_GRAYSCALE = "grayscale"

_MODE_KEY = "skin_weight_mode"
_PREVIEW_KEY = "skin_weight_preview_joint"
_REFRESH_KEY = "skin_weight_mode_refresh"
_CONTROLS_NAME = "adSkinWeightModeControls"
_COLOR_SET_BASE = "__adSkinWeightPreview__"

# Exact user-defined RGB points over the normalized 0..1 weight range.
_RAMPS = {
    MODE_HEAT: (
        (0.00, (0.0, 0.0, 0.0)),
        (0.50, (1.0, 0.0, 0.0)),
        (1.00, (1.0, 1.0, 0.0)),
    ),
    MODE_SPECTRUM: (
        (0.00, (0.0, 0.0, 1.0)),
        (0.25, (0.0, 1.0, 0.0)),
        (0.50, (1.0, 1.0, 0.0)),
        (0.75, (1.0, 0.5, 0.0)),
        (1.00, (1.0, 0.0, 0.0)),
    ),
    MODE_GRAYSCALE: (
        (0.00, (0.0, 0.0, 0.0)),
        (0.50, (0.5, 0.5, 0.5)),
        (1.00, (1.0, 1.0, 1.0)),
    ),
}

_TOOL_WINDOW = None
_JOINT_LIST = None
_QT = None
_CONTROLS = None
_BUTTONS = {}
_BUTTON_GROUP = None
_CONNECTED_BUTTONS = []
_SCRIPT_JOBS = []
_SCENE_CALLBACKS = []
_PREVIEW = None
_REFRESH_QUEUED = False
_CLEANING_UP = False


def install(tool_window_module, joint_list_module):
    """Install Skin Weight Mode after the workspace has been built."""

    global _TOOL_WINDOW
    global _JOINT_LIST

    _TOOL_WINDOW = tool_window_module
    _JOINT_LIST = joint_list_module

    state = tool_window_module._STATE
    state[_MODE_KEY] = MODE_OFF
    state[_PREVIEW_KEY] = None
    state[_REFRESH_KEY] = request_refresh

    controls_installed = _install_controls()
    _install_tree_callback()
    _connect_operation_buttons()
    _install_script_jobs()
    _install_scene_callbacks()
    _set_button_state(MODE_OFF)
    return controls_installed


def shutdown(*_):
    """Remove temporary color data and all persistent callbacks."""

    global _CONTROLS
    global _BUTTONS
    global _BUTTON_GROUP
    global _CONNECTED_BUTTONS
    global _SCRIPT_JOBS
    global _SCENE_CALLBACKS
    global _REFRESH_QUEUED

    _deactivate(show_message=False)

    for job in list(_SCRIPT_JOBS):
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass

    for callback_id in list(_SCENE_CALLBACKS):
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass

    _CONTROLS = None
    _BUTTONS = {}
    _BUTTON_GROUP = None
    _CONNECTED_BUTTONS = []
    _SCRIPT_JOBS = []
    _SCENE_CALLBACKS = []
    _REFRESH_QUEUED = False


def set_mode(mode):
    """Activate a color preset or restore normal mesh display."""

    if _TOOL_WINDOW is None or mode not in {
        MODE_OFF,
        MODE_HEAT,
        MODE_SPECTRUM,
        MODE_GRAYSCALE,
    }:
        return

    if mode == MODE_OFF:
        _deactivate(show_message=False)
        return

    previous_mode = _TOOL_WINDOW._STATE.get(_MODE_KEY, MODE_OFF)
    try:
        joint = _require_single_selected_bound_joint()
        _activate(mode, joint)
    except Exception as exc:
        if previous_mode == MODE_OFF:
            _deactivate(show_message=False)
        else:
            _set_button_state(previous_mode)
        _TOOL_WINDOW._show_error(exc)


def _activate(mode, joint):
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

    _ensure_preview(mesh_shape, mesh_transform)
    _update_preview_colors(mesh_shape, joint, mode)

    state[_MODE_KEY] = mode
    state[_PREVIEW_KEY] = joint
    _set_button_state(mode)


def request_refresh(*_):
    """Coalesce UI and weight-operation changes into one viewport refresh."""

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
    """Read the latest skinCluster weight column and repaint the preview set."""

    if _TOOL_WINDOW is None:
        return

    state = _TOOL_WINDOW._STATE
    mode = state.get(_MODE_KEY, MODE_OFF)
    if mode == MODE_OFF:
        return

    mesh_shape = state.get("mesh_shape")
    mesh_transform = state.get("mesh_transform")
    joint = state.get(_PREVIEW_KEY)
    bound = set(state.get("bound_joint_paths", set()))

    if not all((mesh_shape, mesh_transform, joint)):
        _deactivate(show_message=False)
        return
    if joint not in bound:
        _deactivate(show_message=False)
        return
    if not all(cmds.objExists(node) for node in (mesh_shape, mesh_transform, joint)):
        _deactivate(show_message=False)
        return

    try:
        _ensure_preview(mesh_shape, mesh_transform)
        _update_preview_colors(mesh_shape, joint, mode)
    except Exception as exc:
        cmds.warning("Skin Weight Mode refresh failed: {}".format(exc))


def _ensure_preview(mesh_shape, mesh_transform):
    global _PREVIEW

    if _PREVIEW is not None:
        same_mesh = (
            _PREVIEW.get("mesh_shape") == mesh_shape
            and _PREVIEW.get("mesh_transform") == mesh_transform
        )
        if same_mesh and _preview_color_set_exists():
            _make_preview_current()
            _enable_color_display(mesh_transform)
            return
        _cleanup_preview()

    existing_sets = set(
        cmds.polyColorSet(
            mesh_transform,
            query=True,
            allColorSets=True,
        ) or []
    )
    previous_set = _current_color_set(mesh_transform)
    color_set = _unique_color_set_name(existing_sets)
    display_snapshot = _query_display_options(mesh_transform)

    cmds.polyColorSet(
        mesh_transform,
        create=True,
        colorSet=color_set,
        representation="RGB",
        clamped=True,
    )
    cmds.polyColorSet(
        mesh_transform,
        currentColorSet=True,
        colorSet=color_set,
    )

    _PREVIEW = {
        "mesh_shape": mesh_shape,
        "mesh_transform": mesh_transform,
        "color_set": color_set,
        "previous_color_set": previous_set,
        "display_options": display_snapshot,
    }
    _enable_color_display(mesh_transform)


def _update_preview_colors(mesh_shape, joint, mode):
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    weights = adapter.influence_weights(joint)
    rgb = _map_weights_to_rgb(weights, mode)

    mesh_fn = om.MFnMesh(adapter.mesh_dag_path)
    vertex_count = int(mesh_fn.numVertices)
    if rgb.shape != (vertex_count, 3):
        raise RuntimeError(
            "Preview color count does not match the loaded mesh vertex count."
        )

    colors = om.MColorArray()
    colors.setLength(vertex_count)
    vertex_ids = om.MIntArray()
    vertex_ids.setLength(vertex_count)

    for index in range(vertex_count):
        colors[index] = om.MColor(
            (
                float(rgb[index, 0]),
                float(rgb[index, 1]),
                float(rgb[index, 2]),
                1.0,
            )
        )
        vertex_ids[index] = index

    _make_preview_current()
    mesh_fn.setVertexColors(
        colors,
        vertex_ids,
        None,
        om.MFnMesh.kRGB,
    )
    _enable_color_display(_PREVIEW["mesh_transform"])
    cmds.refresh(force=True)


def _map_weights_to_rgb(weights, mode):
    weights = np.clip(
        np.asarray(weights, dtype=np.float64).reshape(-1),
        0.0,
        1.0,
    )
    ramp = _RAMPS[mode]
    positions = np.asarray([entry[0] for entry in ramp], dtype=np.float64)
    colors = np.asarray([entry[1] for entry in ramp], dtype=np.float64)

    return np.column_stack(
        [
            np.interp(weights, positions, colors[:, channel])
            for channel in range(3)
        ]
    )


def _cleanup_preview():
    global _PREVIEW
    global _CLEANING_UP

    if _PREVIEW is None or _CLEANING_UP:
        return

    _CLEANING_UP = True
    preview = _PREVIEW
    _PREVIEW = None

    try:
        mesh_transform = preview.get("mesh_transform")
        color_set = preview.get("color_set")
        if not mesh_transform or not cmds.objExists(mesh_transform):
            return

        all_sets = set(
            cmds.polyColorSet(
                mesh_transform,
                query=True,
                allColorSets=True,
            ) or []
        )
        previous_set = preview.get("previous_color_set")

        if previous_set and previous_set in all_sets:
            try:
                cmds.polyColorSet(
                    mesh_transform,
                    currentColorSet=True,
                    colorSet=previous_set,
                )
            except Exception:
                pass

        if color_set and color_set in all_sets:
            try:
                cmds.polyColorSet(
                    mesh_transform,
                    delete=True,
                    colorSet=color_set,
                )
            except Exception:
                pass

        _restore_display_options(
            mesh_transform,
            preview.get("display_options", {}),
        )
        cmds.refresh(force=True)
    finally:
        _CLEANING_UP = False


def _deactivate(show_message=False):
    if _TOOL_WINDOW is not None:
        state = _TOOL_WINDOW._STATE
        state[_MODE_KEY] = MODE_OFF
        state[_PREVIEW_KEY] = None

    _set_button_state(MODE_OFF)
    _cleanup_preview()

    if show_message and _TOOL_WINDOW is not None:
        _TOOL_WINDOW._info("Skin Weight Mode deactivated.")


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
        row.setSpacing(0)
        row.addWidget(QtWidgets.QLabel("Skin Weight Mode:", controls))
        row.addSpacing(3)

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
            button.setIconSize(QtCore.QSize(18, 18))
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

    if bool(selected) and _TOOL_WINDOW._STATE.get(_MODE_KEY) != MODE_OFF:
        state = _TOOL_WINDOW._STATE
        joint = state.get("joint_item_to_path", {}).get(item_id)
        if joint in set(state.get("bound_joint_paths", set())):
            state[_PREVIEW_KEY] = joint
            request_refresh()

    return True


def _connect_operation_buttons():
    global _CONNECTED_BUTTONS

    if _QT is None:
        return

    QtWidgets, _QtGui, _QtCore, binding = _QT
    connected = []
    operation_names = (
        _TOOL_WINDOW.CTRL_BIND_BUTTON,
        "adSkin_addInfluenceButton",
        "adSkin_floodSelectedToJointButton",
        "adSkin_smoothSelectedComponentsButton",
    )

    for name in operation_names:
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
                    button.clicked.connect(
                        lambda *_: _deactivate(show_message=False)
                    )
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


def _install_scene_callbacks():
    global _SCENE_CALLBACKS

    for callback_id in list(_SCENE_CALLBACKS):
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass

    _SCENE_CALLBACKS = []
    messages = (
        om.MSceneMessage.kBeforeNew,
        om.MSceneMessage.kBeforeOpen,
        om.MSceneMessage.kBeforeSave,
    )
    for message in messages:
        try:
            _SCENE_CALLBACKS.append(
                om.MSceneMessage.addCallback(message, _before_scene_change)
            )
        except Exception:
            pass


def _before_scene_change(*_):
    _deactivate(show_message=False)


def _tool_changed(*_):
    if _TOOL_WINDOW is None:
        return
    if _TOOL_WINDOW._STATE.get(_MODE_KEY, MODE_OFF) == MODE_OFF:
        return

    context = _current_context()
    if _is_skin_paint_context(context):
        # Native Maya Paint Skin Weights always wins. AD preview is removed and
        # remains Off until the user explicitly chooses a preset again.
        _deactivate(show_message=False)


def _preview_color_set_exists():
    if _PREVIEW is None:
        return False

    mesh_transform = _PREVIEW.get("mesh_transform")
    color_set = _PREVIEW.get("color_set")
    if not mesh_transform or not cmds.objExists(mesh_transform):
        return False

    return color_set in set(
        cmds.polyColorSet(
            mesh_transform,
            query=True,
            allColorSets=True,
        ) or []
    )


def _make_preview_current():
    if not _preview_color_set_exists():
        raise RuntimeError("Skin Weight Mode preview color set is unavailable.")

    cmds.polyColorSet(
        _PREVIEW["mesh_transform"],
        currentColorSet=True,
        colorSet=_PREVIEW["color_set"],
    )


def _current_color_set(mesh_transform):
    result = cmds.polyColorSet(
        mesh_transform,
        query=True,
        currentColorSet=True,
    )
    if isinstance(result, (list, tuple)):
        return result[0] if result else None
    return str(result) if result else None


def _unique_color_set_name(existing_sets):
    if _COLOR_SET_BASE not in existing_sets:
        return _COLOR_SET_BASE

    index = 1
    while "{}{}".format(_COLOR_SET_BASE, index) in existing_sets:
        index += 1
    return "{}{}".format(_COLOR_SET_BASE, index)


def _query_display_options(mesh_transform):
    result = {}
    queries = (
        ("colorShadedDisplay", True),
        ("colorMaterialChannel", True),
        ("materialBlend", True),
    )

    for flag, value in queries:
        try:
            result[flag] = cmds.polyOptions(
                mesh_transform,
                query=True,
                **{flag: value}
            )
        except Exception:
            pass

    return result


def _enable_color_display(mesh_transform):
    cmds.polyOptions(
        mesh_transform,
        colorShadedDisplay=True,
        colorMaterialChannel="none",
        materialBlend="overwrite",
    )


def _restore_display_options(mesh_transform, options):
    kwargs = {}
    for flag in (
        "colorShadedDisplay",
        "colorMaterialChannel",
        "materialBlend",
    ):
        value = options.get(flag)
        if isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        if value is not None:
            kwargs[flag] = value

    if not kwargs:
        return

    try:
        cmds.polyOptions(mesh_transform, **kwargs)
    except Exception:
        pass


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


def _set_button_state(mode):
    for value, button in _BUTTONS.items():
        try:
            blocked = button.blockSignals(True)
            button.setChecked(value == mode)
            button.blockSignals(blocked)
        except Exception:
            pass


def _icon(mode, QtGui, QtCore):
    pixmap = QtGui.QPixmap(18, 18)
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
            gradient = QtGui.QLinearGradient(
                rect.left(),
                0,
                rect.right(),
                0,
            )
            for position, rgb in _RAMPS[mode]:
                gradient.setColorAt(position, QtGui.QColor.fromRgbF(*rgb))
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
