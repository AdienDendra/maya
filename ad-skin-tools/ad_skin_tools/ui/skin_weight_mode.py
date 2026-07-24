"""Independent, visualization-only skin-weight preview for the loaded mesh."""

from dataclasses import dataclass

import maya.api.OpenMaya as om
import maya.cmds as cmds
from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import ensure_numpy, import_qt_modules
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.ui import qt_helpers
from ad_skin_tools.ui.skin_weight_ramps import (
    MODE_GRAYSCALE,
    MODE_HEAT,
    MODE_OFF,
    MODE_ORDER,
    MODE_SPECTRUM,
    MODE_TOOLTIPS,
    RAMPS,
    VALID_MODES,
)

np = ensure_numpy()

_MODE_KEY = "skin_weight_mode"
_PREVIEW_KEY = "skin_weight_preview_joint"
_REFRESH_KEY = "skin_weight_mode_refresh"
_CONTROLS_NAME = "adSkinWeightModeControls"
_COLOR_SET_BASE = "__adSkinWeightPreview__"
_DISPLAY_FLAGS = (
    "colorShadedDisplay",
    "colorMaterialChannel",
    "materialBlend",
)

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


@dataclass
class _PreviewSession:
    mesh_shape: str
    mesh_transform: str
    color_set: str
    previous_color_set: object
    display_options: dict


def install(tool_window_module, joint_list_module):
    """Install Skin Weight Mode after the Maya workspace has been built."""

    global _TOOL_WINDOW, _JOINT_LIST
    _TOOL_WINDOW = tool_window_module
    _JOINT_LIST = joint_list_module

    state = _state()
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
    """Remove temporary preview data and all persistent callbacks."""

    global _CONTROLS, _BUTTONS, _BUTTON_GROUP, _CONNECTED_BUTTONS
    global _SCRIPT_JOBS, _SCENE_CALLBACKS, _REFRESH_QUEUED

    _deactivate()
    _remove_script_jobs()
    _remove_scene_callbacks()
    _CONTROLS = None
    _BUTTONS = {}
    _BUTTON_GROUP = None
    _CONNECTED_BUTTONS = []
    _SCRIPT_JOBS = []
    _SCENE_CALLBACKS = []
    _REFRESH_QUEUED = False


def set_mode(mode):
    """Activate one visual preset or restore normal mesh display."""

    if _TOOL_WINDOW is None or mode not in VALID_MODES:
        return
    if mode == MODE_OFF:
        _deactivate()
        return

    previous_mode = _state().get(_MODE_KEY, MODE_OFF)
    try:
        joint = _require_single_selected_bound_joint()
        _activate(mode, joint)
    except Exception as exc:
        if previous_mode == MODE_OFF:
            _deactivate()
        else:
            _set_button_state(previous_mode)
        _TOOL_WINDOW._show_error(exc)


def request_refresh(*_):
    """Coalesce weight and selection changes into one viewport refresh."""

    global _REFRESH_QUEUED
    if not _mode_is_active() or _REFRESH_QUEUED:
        return
    _REFRESH_QUEUED = True
    try:
        cmds.evalDeferred(_deferred_refresh, lowestPriority=True)
    except Exception:
        _REFRESH_QUEUED = False
        refresh()


def refresh(*_):
    """Read the latest influence weights and repaint the preview."""

    if not _mode_is_active():
        return

    state = _state()
    mode = state.get(_MODE_KEY, MODE_OFF)
    mesh_shape = state.get("mesh_shape")
    mesh_transform = state.get("mesh_transform")
    joint = state.get(_PREVIEW_KEY)
    context_nodes = (mesh_shape, mesh_transform, joint)

    if not all(context_nodes):
        _deactivate()
        return
    if joint not in set(state.get("bound_joint_paths", set())):
        _deactivate()
        return
    if not all(cmds.objExists(node) for node in context_nodes):
        _deactivate()
        return

    try:
        _ensure_preview(mesh_shape, mesh_transform)
        _update_preview_colors(mesh_shape, joint, mode)
    except Exception as exc:
        cmds.warning("Skin Weight Mode refresh failed: {}".format(exc))


def _activate(mode, joint):
    state = _state()
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


def _deferred_refresh():
    global _REFRESH_QUEUED
    _REFRESH_QUEUED = False
    refresh()


def _ensure_preview(mesh_shape, mesh_transform):
    global _PREVIEW

    if _PREVIEW is not None:
        same_mesh = (
            _PREVIEW.mesh_shape == mesh_shape
            and _PREVIEW.mesh_transform == mesh_transform
        )
        if same_mesh and _preview_color_set_exists():
            _make_preview_current()
            _enable_color_display(mesh_transform)
            return
        _cleanup_preview()

    existing_sets = _all_color_sets(mesh_transform)
    color_set = _unique_color_set_name(existing_sets)
    previous_set = _current_color_set(mesh_transform)
    display_options = _query_display_options(mesh_transform)

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
    _PREVIEW = _PreviewSession(
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        color_set=color_set,
        previous_color_set=previous_set,
        display_options=display_options,
    )
    _enable_color_display(mesh_transform)


def _update_preview_colors(mesh_shape, joint, mode):
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    rgb = _map_weights_to_rgb(adapter.influence_weights(joint), mode)
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
    # Maya 2025 rejects explicit ``None`` for the optional MDGModifier.
    mesh_fn.setVertexColors(colors, vertex_ids)
    _enable_color_display(_PREVIEW.mesh_transform)
    cmds.refresh(force=True)


def _map_weights_to_rgb(weights, mode):
    values = np.clip(
        np.asarray(weights, dtype=np.float64).reshape(-1),
        0.0,
        1.0,
    )
    ramp = RAMPS[mode]
    positions = np.asarray([point[0] for point in ramp], dtype=np.float64)
    colors = np.asarray([point[1] for point in ramp], dtype=np.float64)
    return np.column_stack(
        [
            np.interp(values, positions, colors[:, channel])
            for channel in range(3)
        ]
    )


def _deactivate():
    if _TOOL_WINDOW is not None:
        _state()[_MODE_KEY] = MODE_OFF
        _state()[_PREVIEW_KEY] = None
    _set_button_state(MODE_OFF)
    _cleanup_preview()


def _cleanup_preview():
    global _PREVIEW, _CLEANING_UP
    if _PREVIEW is None or _CLEANING_UP:
        return

    _CLEANING_UP = True
    preview = _PREVIEW
    _PREVIEW = None
    try:
        if not preview.mesh_transform or not cmds.objExists(
            preview.mesh_transform
        ):
            return

        all_sets = _all_color_sets(preview.mesh_transform)
        if preview.previous_color_set in all_sets:
            _set_current_color_set(
                preview.mesh_transform,
                preview.previous_color_set,
            )
        if preview.color_set in all_sets:
            try:
                cmds.polyColorSet(
                    preview.mesh_transform,
                    delete=True,
                    colorSet=preview.color_set,
                )
            except Exception:
                pass
        _restore_display_options(
            preview.mesh_transform,
            preview.display_options,
        )
        cmds.refresh(force=True)
    finally:
        _CLEANING_UP = False


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
    if joint not in set(_state().get("bound_joint_paths", set())):
        raise RuntimeError(
            "The selected joint is pending and has no skin weights to display."
        )
    if not cmds.objExists(joint):
        raise RuntimeError(
            "The selected bound influence no longer exists in the scene."
        )
    return joint


def _install_controls():
    global _QT, _CONTROLS, _BUTTONS, _BUTTON_GROUP
    try:
        QtWidgets, QtGui, QtCore, binding = import_qt_modules()
        _QT = (QtWidgets, QtGui, QtCore, binding)
        label_widget = qt_helpers.wrap_instance(
            omui.MQtUtil.findControl(_TOOL_WINDOW.CTRL_JOINT_LABEL),
            QtWidgets.QWidget,
            binding,
        )
        container, layout, index = qt_helpers.find_managing_layout(label_widget)
        if layout is None:
            return False

        qt_helpers.remove_named_child(
            container,
            layout,
            QtWidgets.QWidget,
            _CONTROLS_NAME,
        )
        controls, buttons, group = _build_mode_controls(
            container,
            QtWidgets,
            QtGui,
            QtCore,
        )
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


def _build_mode_controls(container, QtWidgets, QtGui, QtCore):
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
    for mode in MODE_ORDER:
        button = QtWidgets.QToolButton(controls)
        button.setCheckable(True)
        button.setAutoRaise(True)
        button.setFixedSize(22, 22)
        button.setToolTip(MODE_TOOLTIPS[mode])
        button.setIcon(_icon(mode, QtGui, QtCore))
        button.setIconSize(QtCore.QSize(18, 18))
        button.clicked.connect(
            lambda _checked=False, value=mode: set_mode(value)
        )
        group.addButton(button)
        row.addWidget(button)
        buttons[mode] = button
    row.addStretch(1)
    return controls, buttons, group


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

    if bool(selected) and _mode_is_active():
        joint = _state().get("joint_item_to_path", {}).get(item_id)
        if joint in set(_state().get("bound_joint_paths", set())):
            _state()[_PREVIEW_KEY] = joint
            request_refresh()
    return True


def _connect_operation_buttons():
    global _CONNECTED_BUTTONS
    if _QT is None:
        return

    from ad_skin_tools.ui import component_section, skin_operations

    QtWidgets, _QtGui, _QtCore, binding = _QT
    connected = []
    for name in (
        _TOOL_WINDOW.CTRL_BIND_BUTTON,
        skin_operations.CTRL_ADD_INFLUENCE_BUTTON,
        component_section.CTRL_FLOOD_BUTTON,
        component_section.CTRL_SMOOTH_BUTTON,
    ):
        button = qt_helpers.wrap_instance(
            omui.MQtUtil.findControl(name),
            QtWidgets.QPushButton,
            binding,
        )
        if button is None:
            continue
        try:
            button.clicked.connect(request_refresh)
            connected.append(button)
        except Exception:
            pass

    load_button = _find_load_mesh_button(QtWidgets, binding)
    if load_button is not None:
        load_button.clicked.connect(lambda *_: _deactivate())
        connected.append(load_button)
    _CONNECTED_BUTTONS = connected


def _find_load_mesh_button(QtWidgets, binding):
    label = qt_helpers.wrap_instance(
        omui.MQtUtil.findControl(_TOOL_WINDOW.CTRL_JOINT_LABEL),
        QtWidgets.QWidget,
        binding,
    )
    container, _layout, _index = qt_helpers.find_managing_layout(label)
    if container is None:
        return None
    for button in container.findChildren(QtWidgets.QPushButton):
        if button.text() == "Load Mesh":
            return button
    return None


def _install_script_jobs():
    global _SCRIPT_JOBS
    _remove_script_jobs()
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


def _remove_script_jobs():
    for job in list(_SCRIPT_JOBS):
        try:
            if cmds.scriptJob(exists=job):
                cmds.scriptJob(kill=job, force=True)
        except Exception:
            pass


def _install_scene_callbacks():
    global _SCENE_CALLBACKS
    _remove_scene_callbacks()
    _SCENE_CALLBACKS = []
    for message in (
        om.MSceneMessage.kBeforeNew,
        om.MSceneMessage.kBeforeOpen,
        om.MSceneMessage.kBeforeSave,
    ):
        try:
            _SCENE_CALLBACKS.append(
                om.MSceneMessage.addCallback(message, _before_scene_change)
            )
        except Exception:
            pass


def _remove_scene_callbacks():
    for callback_id in list(_SCENE_CALLBACKS):
        try:
            om.MMessage.removeCallback(callback_id)
        except Exception:
            pass


def _before_scene_change(*_):
    _deactivate()


def _tool_changed(*_):
    if _mode_is_active() and _is_skin_paint_context(_current_context()):
        _deactivate()


def _preview_color_set_exists():
    return bool(
        _PREVIEW is not None
        and _PREVIEW.mesh_transform
        and cmds.objExists(_PREVIEW.mesh_transform)
        and _PREVIEW.color_set in _all_color_sets(_PREVIEW.mesh_transform)
    )


def _make_preview_current():
    if not _preview_color_set_exists():
        raise RuntimeError("Skin Weight Mode preview color set is unavailable.")
    _set_current_color_set(_PREVIEW.mesh_transform, _PREVIEW.color_set)


def _set_current_color_set(mesh_transform, color_set):
    cmds.polyColorSet(
        mesh_transform,
        currentColorSet=True,
        colorSet=color_set,
    )


def _all_color_sets(mesh_transform):
    return set(
        cmds.polyColorSet(
            mesh_transform,
            query=True,
            allColorSets=True,
        ) or []
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
    for flag in _DISPLAY_FLAGS:
        try:
            result[flag] = cmds.polyOptions(
                mesh_transform,
                query=True,
                **{flag: True}
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
    for flag in _DISPLAY_FLAGS:
        value = options.get(flag)
        if isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        if value is not None:
            kwargs[flag] = value
    if kwargs:
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
        "skin" in text and "paint" in text and "context" in text
    )


def _current_context():
    try:
        return cmds.currentCtx()
    except Exception:
        return None


def _set_button_state(mode):
    for value, button in _BUTTONS.items():
        qt_helpers.set_checked_silently(button, value == mode)


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
            for position, rgb in RAMPS[mode]:
                gradient.setColorAt(
                    position,
                    QtGui.QColor.fromRgbF(*rgb),
                )
            painter.fillRect(rect, gradient)
        painter.setPen(QtGui.QPen(QtGui.QColor(30, 30, 30), 1.0))
        painter.drawRect(rect)
    finally:
        painter.end()
    return QtGui.QIcon(pixmap)


def _mode_is_active():
    return bool(
        _TOOL_WINDOW is not None
        and _state().get(_MODE_KEY, MODE_OFF) != MODE_OFF
    )


def _state():
    return _TOOL_WINDOW._STATE
