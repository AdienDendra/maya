"""Integrate the history-free proxy with Skin Weight Visual and operations."""

from functools import wraps

import maya.cmds as cmds

from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.ui import skin_weight_proxy_shape


_GEOMETRY_REFRESH_KEY = "skin_weight_visual_geometry_refresh"

_MODE = None
_TOOL_WINDOW = None
_LOAD_BUTTON = None

_ORIGINAL_MODE_FUNCTIONS = {}
_ORIGINAL_OPERATION_FUNCTIONS = {}

_REFRESH_QUEUED = False
_GEOMETRY_DIRTY = False
_SYNC_GEOMETRY_NOW = False


def prepare(skin_weight_mode_module, tool_window_module) -> None:
    """Patch preview plumbing before Skin Weight Visual installs callbacks."""

    global _MODE, _TOOL_WINDOW
    _MODE = skin_weight_mode_module
    _TOOL_WINDOW = tool_window_module
    skin_weight_proxy_shape.configure(request_geometry_refresh)

    if skin_weight_mode_module.request_refresh is request_refresh:
        return

    _ORIGINAL_MODE_FUNCTIONS.clear()
    _ORIGINAL_MODE_FUNCTIONS.update(
        {
            "request_refresh": skin_weight_mode_module.request_refresh,
            "refresh": skin_weight_mode_module.refresh,
            "_ensure_preview": skin_weight_mode_module._ensure_preview,
            "_update_preview_colors": skin_weight_mode_module._update_preview_colors,
            "_cleanup_preview": skin_weight_mode_module._cleanup_preview,
            "_preview_color_set_exists": (
                skin_weight_mode_module._preview_color_set_exists
            ),
            "_make_preview_current": skin_weight_mode_module._make_preview_current,
            "_install_script_jobs": skin_weight_mode_module._install_script_jobs,
        }
    )

    skin_weight_mode_module.request_refresh = request_refresh
    skin_weight_mode_module.refresh = refresh
    skin_weight_mode_module._ensure_preview = _ensure_preview
    skin_weight_mode_module._update_preview_colors = _update_preview_colors
    skin_weight_mode_module._cleanup_preview = _cleanup_preview
    skin_weight_mode_module._preview_color_set_exists = (
        skin_weight_proxy_shape.exists
    )
    skin_weight_mode_module._make_preview_current = (
        skin_weight_proxy_shape.make_current
    )
    skin_weight_mode_module._install_script_jobs = _install_script_jobs


def install() -> None:
    """Connect live editing and mesh-reload hooks after controls are built."""

    if _TOOL_WINDOW is None:
        return

    _state()[_GEOMETRY_REFRESH_KEY] = request_geometry_refresh
    _connect_load_button_pressed()

    from ad_skin_tools.ui import component_section, skin_operations

    _wrap_operation(component_section, "apply_component_flood")
    _wrap_operation(component_section, "apply_component_smooth")
    _replace_operation_callback(
        skin_operations,
        "_request_weight_preview_refresh",
        request_geometry_refresh,
    )


def shutdown() -> None:
    """Delete proxy data and restore wrapped module functions."""

    global _TOOL_WINDOW, _SYNC_GEOMETRY_NOW

    _cleanup_preview()
    skin_weight_proxy_shape.shutdown()
    _disconnect_load_button_pressed()
    _restore_operations()
    _restore_mode_functions()

    if _TOOL_WINDOW is not None:
        try:
            _state().pop(_GEOMETRY_REFRESH_KEY, None)
        except Exception:
            pass

    _TOOL_WINDOW = None
    _reset_refresh_queue()
    _SYNC_GEOMETRY_NOW = False


def request_refresh(*_args, **_kwargs) -> None:
    """Queue only a color repaint, such as changing the selected joint."""

    _queue_refresh(sync_geometry=False)


def request_geometry_refresh(*_args, **_kwargs) -> None:
    """Queue point synchronization plus a color repaint."""

    _queue_refresh(sync_geometry=True)


def _queue_refresh(sync_geometry):
    global _REFRESH_QUEUED, _GEOMETRY_DIRTY

    if _MODE is None or not _MODE._mode_is_active():
        return

    _GEOMETRY_DIRTY = bool(_GEOMETRY_DIRTY or sync_geometry)
    if _REFRESH_QUEUED:
        return

    _REFRESH_QUEUED = True
    try:
        cmds.evalDeferred(_deferred_refresh, lowestPriority=True)
    except Exception:
        _deferred_refresh()


def _deferred_refresh():
    global _REFRESH_QUEUED, _GEOMETRY_DIRTY

    sync_geometry = bool(_GEOMETRY_DIRTY)
    _REFRESH_QUEUED = False
    _GEOMETRY_DIRTY = False
    refresh(sync_geometry=sync_geometry)


def refresh(*_args, **kwargs) -> None:
    """Reuse canonical validation while controlling full point-copy work."""

    global _SYNC_GEOMETRY_NOW

    original = _ORIGINAL_MODE_FUNCTIONS.get("refresh")
    if original is None:
        return

    sync_geometry = bool(kwargs.pop("sync_geometry", False))
    previous = _SYNC_GEOMETRY_NOW
    _SYNC_GEOMETRY_NOW = bool(previous or sync_geometry)
    try:
        original()
    finally:
        _SYNC_GEOMETRY_NOW = previous


def _ensure_preview(mesh_shape, mesh_transform):
    current = skin_weight_proxy_shape.ensure(
        mesh_shape,
        mesh_transform,
        sync_geometry=_SYNC_GEOMETRY_NOW,
    )
    if _MODE is not None:
        _MODE._PREVIEW = current
    return current


def _update_preview_colors(mesh_shape, joint, mode):
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    rgb = _MODE._map_weights_to_rgb(
        adapter.influence_weights(joint),
        mode,
    )
    skin_weight_proxy_shape.set_colors(rgb)
    cmds.refresh(force=True)


def _cleanup_preview():
    skin_weight_proxy_shape.cleanup()
    if _MODE is not None:
        _MODE._PREVIEW = None


def _install_script_jobs():
    if _MODE is None or _TOOL_WINDOW is None:
        return

    _MODE._remove_script_jobs()
    _MODE._SCRIPT_JOBS = []
    parent = _TOOL_WINDOW.WINDOW_NAME

    for event_name, callback in (
        ("ToolChanged", _MODE._tool_changed),
        ("Undo", request_geometry_refresh),
        ("Redo", request_geometry_refresh),
        ("timeChanged", request_geometry_refresh),
    ):
        try:
            _MODE._SCRIPT_JOBS.append(
                cmds.scriptJob(
                    event=(event_name, callback),
                    parent=parent,
                    protected=True,
                )
            )
        except Exception:
            pass

    try:
        _MODE._SCRIPT_JOBS.append(
            cmds.scriptJob(
                uiDeleted=(parent, _on_ui_deleted),
                runOnce=True,
            )
        )
    except Exception:
        pass


def _on_ui_deleted(*_):
    try:
        if _MODE is not None:
            _MODE.shutdown()
    finally:
        _reset_refresh_queue()


def _reset_refresh_queue():
    global _REFRESH_QUEUED, _GEOMETRY_DIRTY
    _REFRESH_QUEUED = False
    _GEOMETRY_DIRTY = False


def _connect_load_button_pressed():
    global _LOAD_BUTTON

    _disconnect_load_button_pressed()
    qt_data = getattr(_MODE, "_QT", None)
    if qt_data is None:
        return

    QtWidgets, _QtGui, _QtCore, binding = qt_data
    try:
        button = _MODE._find_load_mesh_button(QtWidgets, binding)
    except Exception:
        button = None
    if button is None:
        return

    try:
        button.pressed.connect(_deactivate_for_load)
        _LOAD_BUTTON = button
    except Exception:
        _LOAD_BUTTON = None


def _disconnect_load_button_pressed():
    global _LOAD_BUTTON

    button = _LOAD_BUTTON
    _LOAD_BUTTON = None
    if button is None:
        return
    try:
        button.pressed.disconnect(_deactivate_for_load)
    except Exception:
        pass


def _deactivate_for_load():
    if _MODE is None:
        skin_weight_proxy_shape.cleanup()
        return
    try:
        _MODE._deactivate()
    except Exception:
        skin_weight_proxy_shape.cleanup()


def _wrap_operation(module, function_name):
    current = getattr(module, function_name)
    key = (module.__name__, function_name)
    if getattr(current, "_ad_skin_visual_proxy_wrapper", False):
        return

    _ORIGINAL_OPERATION_FUNCTIONS[key] = (module, current)

    @wraps(current)
    def wrapper(*args, **kwargs):
        result = current(*args, **kwargs)
        request_geometry_refresh()
        return result

    wrapper._ad_skin_visual_proxy_wrapper = True
    setattr(module, function_name, wrapper)


def _replace_operation_callback(module, function_name, callback):
    current = getattr(module, function_name)
    key = (module.__name__, function_name)
    if current is callback:
        return
    _ORIGINAL_OPERATION_FUNCTIONS[key] = (module, current)
    setattr(module, function_name, callback)


def _restore_operations():
    for (_module_name, function_name), (module, original) in list(
        _ORIGINAL_OPERATION_FUNCTIONS.items()
    ):
        try:
            setattr(module, function_name, original)
        except Exception:
            pass
    _ORIGINAL_OPERATION_FUNCTIONS.clear()


def _restore_mode_functions():
    if _MODE is None:
        _ORIGINAL_MODE_FUNCTIONS.clear()
        return

    patched = {
        "request_refresh": request_refresh,
        "refresh": refresh,
        "_ensure_preview": _ensure_preview,
        "_update_preview_colors": _update_preview_colors,
        "_cleanup_preview": _cleanup_preview,
        "_preview_color_set_exists": skin_weight_proxy_shape.exists,
        "_make_preview_current": skin_weight_proxy_shape.make_current,
        "_install_script_jobs": _install_script_jobs,
    }
    for name, original in list(_ORIGINAL_MODE_FUNCTIONS.items()):
        try:
            if getattr(_MODE, name, None) is patched[name]:
                setattr(_MODE, name, original)
        except Exception:
            pass
    _ORIGINAL_MODE_FUNCTIONS.clear()


def _state():
    return _TOOL_WINDOW._STATE
