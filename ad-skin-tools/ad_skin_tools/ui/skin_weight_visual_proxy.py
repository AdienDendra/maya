"""History-free proxy rendering for live Skin Weight Visual editing."""

from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ad_skin_tools.core.skin_cluster import SkinClusterAdapter


_PROXY_SHAPE_BASE = "__adSkinWeightVisualShape__"
_PROXY_COLOR_SET = "__adSkinWeightVisualColors__"
_LEGACY_COLOR_SET_PREFIX = "__adSkinWeightPreview__"
_GEOMETRY_REFRESH_KEY = "skin_weight_visual_geometry_refresh"
_LEGACY_COLOR_NODE_TYPES = frozenset(
    (
        "createColorSet",
        "deleteColorSet",
        "polyColorPerVertex",
        "polyColorDel",
    )
)
_SOURCE_DIRTY_PLUG_TOKENS = (
    "outmesh",
    "worldmesh",
    "inmesh",
    "cachedinmesh",
)

_MODE = None
_TOOL_WINDOW = None
_SESSION = None

_ORIGINAL_MODE_FUNCTIONS = {}
_ORIGINAL_OPERATION_FUNCTIONS = {}

_REFRESH_QUEUED = False
_GEOMETRY_DIRTY = False
_SYNC_GEOMETRY_NOW = False
_CLEANING_UP = False


@dataclass
class _ProxySession:
    source_shape: str
    source_transform: str
    proxy_shape: str
    color_set: str
    source_display_state: dict
    dirty_callback_id: object = None


def prepare(skin_weight_mode_module, tool_window_module) -> None:
    """Patch preview plumbing before Skin Weight Visual installs callbacks."""

    global _MODE, _TOOL_WINDOW
    _MODE = skin_weight_mode_module
    _TOOL_WINDOW = tool_window_module

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
    skin_weight_mode_module._preview_color_set_exists = _preview_exists
    skin_weight_mode_module._make_preview_current = _make_preview_current
    skin_weight_mode_module._install_script_jobs = _install_script_jobs


def install() -> None:
    """Register explicit geometry refresh hooks after controls are built."""

    if _TOOL_WINDOW is None:
        return

    _state()[_GEOMETRY_REFRESH_KEY] = request_geometry_refresh

    from ad_skin_tools.ui import component_section, skin_operations

    _wrap_before(_TOOL_WINDOW, "load_skin_weight", _deactivate_before_mesh_load)
    _wrap_operation(component_section, "apply_component_flood")
    _wrap_operation(component_section, "apply_component_smooth")
    _replace_operation_callback(
        skin_operations,
        "_request_weight_preview_refresh",
        request_geometry_refresh,
    )


def shutdown() -> None:
    """Delete proxy data and restore all wrapped functions."""

    global _TOOL_WINDOW, _REFRESH_QUEUED, _GEOMETRY_DIRTY
    global _SYNC_GEOMETRY_NOW

    _cleanup_preview()
    _restore_operations()
    _restore_mode_functions()

    if _TOOL_WINDOW is not None:
        try:
            _state().pop(_GEOMETRY_REFRESH_KEY, None)
        except Exception:
            pass

    _TOOL_WINDOW = None
    _REFRESH_QUEUED = False
    _GEOMETRY_DIRTY = False
    _SYNC_GEOMETRY_NOW = False


def request_refresh(*_args, **_kwargs) -> None:
    """Queue only a color repaint, such as switching the selected joint."""

    _queue_refresh(sync_geometry=False)


def request_geometry_refresh(*_args, **_kwargs) -> None:
    """Queue point synchronization plus color repaint after geometry changes."""

    _queue_refresh(sync_geometry=True)


def _queue_refresh(sync_geometry: bool) -> None:
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


def _deferred_refresh() -> None:
    global _REFRESH_QUEUED, _GEOMETRY_DIRTY

    sync_geometry = bool(_GEOMETRY_DIRTY)
    _REFRESH_QUEUED = False
    _GEOMETRY_DIRTY = False
    refresh(sync_geometry=sync_geometry)


def refresh(*_args, **kwargs) -> None:
    """Reuse canonical validation while controlling point-copy work."""

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
    global _SESSION

    source_shape = _long_name(mesh_shape)
    source_transform = _long_name(mesh_transform)
    if not source_shape or not source_transform:
        raise RuntimeError("Skin Weight Visual could not resolve the loaded mesh.")

    same_source = bool(
        _SESSION is not None
        and _SESSION.source_shape == source_shape
        and _SESSION.source_transform == source_transform
        and _session_nodes_exist(_SESSION)
    )

    if same_source and not _topology_matches(_SESSION):
        _cleanup_preview()
        same_source = False

    if not same_source:
        _cleanup_preview()
        source_display_state = _capture_source_display(source_shape)
        _purge_stale_proxy_shapes(source_transform)
        _purge_legacy_preview_history(source_shape)
        _SESSION = _create_proxy_session(
            source_shape,
            source_transform,
            source_display_state,
        )
        if _MODE is not None:
            _MODE._PREVIEW = _SESSION
        return _SESSION

    if _SYNC_GEOMETRY_NOW:
        _sync_proxy_points(_SESSION)
    _enable_proxy_display(_SESSION.proxy_shape)
    return _SESSION


def _update_preview_colors(mesh_shape, joint, mode):
    if _SESSION is None or not _session_nodes_exist(_SESSION):
        raise RuntimeError("Skin Weight Visual proxy is unavailable.")

    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    rgb = _MODE._map_weights_to_rgb(adapter.influence_weights(joint), mode)
    _set_proxy_colors(_SESSION, rgb)
    cmds.refresh(force=True)


def _cleanup_preview():
    global _SESSION, _CLEANING_UP

    if _CLEANING_UP:
        return

    session = _SESSION
    _SESSION = None
    if _MODE is not None:
        _MODE._PREVIEW = None
    if session is None:
        return

    _CLEANING_UP = True
    try:
        _remove_dirty_callback(session)
        with _without_undo(), _preserve_selection():
            _restore_source_display(session)
            if session.proxy_shape and cmds.objExists(session.proxy_shape):
                cmds.delete(session.proxy_shape)
            cmds.refresh(force=True)
    finally:
        _CLEANING_UP = False


def _preview_exists():
    return bool(_SESSION is not None and _session_nodes_exist(_SESSION))


def _make_preview_current():
    if not _preview_exists():
        raise RuntimeError("Skin Weight Visual proxy is unavailable.")
    with _without_undo():
        _mesh_fn(_SESSION.proxy_shape).setCurrentColorSetName(
            _SESSION.color_set
        )


def _create_proxy_session(
    source_shape,
    source_transform,
    source_display_state,
):
    with _without_undo(), _preserve_selection():
        source_fn = _mesh_fn(source_shape)
        polygon_counts, polygon_connects = source_fn.getVertices()
        parent_object = _depend_node(source_transform)

        proxy_fn = om.MFnMesh()
        proxy_object = proxy_fn.create(
            source_fn.getPoints(om.MSpace.kObject),
            polygon_counts,
            polygon_connects,
            parent=parent_object,
        )
        proxy_shape = om.MDagPath.getAPathTo(proxy_object).fullPathName()
        proxy_shape = cmds.rename(proxy_shape, _PROXY_SHAPE_BASE)
        proxy_shape = _long_name(proxy_shape)

        _configure_proxy_shape(proxy_shape)
        color_set = _create_proxy_color_set(proxy_shape)
        _show_source_as_selection_wire(source_shape)

    session = _ProxySession(
        source_shape=source_shape,
        source_transform=source_transform,
        proxy_shape=proxy_shape,
        color_set=color_set,
        source_display_state=source_display_state,
    )
    session.dirty_callback_id = _install_source_dirty_callback(source_shape)
    return session


def _configure_proxy_shape(proxy_shape):
    for attribute, value in (
        ("overrideEnabled", True),
        ("overrideDisplayType", 2),
        ("overrideShading", True),
        ("displayColors", True),
        ("hiddenInOutliner", True),
        ("isHistoricallyInteresting", False),
        ("castsShadows", False),
        ("receiveShadows", False),
        ("motionBlur", False),
        ("primaryVisibility", False),
        ("visibleInReflections", False),
        ("visibleInRefractions", False),
    ):
        _set_attr_if_present(proxy_shape, attribute, value)

    try:
        cmds.sets(proxy_shape, edit=True, forceElement="initialShadingGroup")
    except Exception:
        pass

    _enable_proxy_display(proxy_shape)


def _create_proxy_color_set(proxy_shape):
    mesh_fn = _mesh_fn(proxy_shape)
    color_set = mesh_fn.createColorSet(
        _PROXY_COLOR_SET,
        True,
        om.MFnMesh.kRGB,
    )
    mesh_fn.setCurrentColorSetName(color_set)
    return str(color_set)


def _enable_proxy_display(proxy_shape):
    _set_attr_if_present(proxy_shape, "displayColors", True)
    try:
        cmds.polyOptions(
            proxy_shape,
            colorShadedDisplay=True,
            colorMaterialChannel="none",
            materialBlend="overwrite",
        )
    except Exception:
        pass


def _show_source_as_selection_wire(source_shape):
    _set_attr_if_present(source_shape, "overrideEnabled", True)
    _set_attr_if_present(source_shape, "overrideDisplayType", 0)
    _set_attr_if_present(source_shape, "overrideShading", False)
    _set_attr_if_present(source_shape, "displayColors", False)


def _capture_source_display(source_shape):
    state = {}
    for attribute in (
        "overrideEnabled",
        "overrideDisplayType",
        "overrideShading",
        "displayColors",
    ):
        plug = "{}.{}".format(source_shape, attribute)
        if not cmds.objExists(plug):
            continue
        try:
            state[attribute] = cmds.getAttr(plug)
        except Exception:
            pass
    return state


def _restore_source_display(session):
    if not session.source_shape or not cmds.objExists(session.source_shape):
        return
    for attribute, value in session.source_display_state.items():
        _set_attr_if_present(session.source_shape, attribute, value)


def _sync_proxy_points(session):
    source_fn = _mesh_fn(session.source_shape)
    proxy_fn = _mesh_fn(session.proxy_shape)

    if int(source_fn.numVertices) != int(proxy_fn.numVertices):
        raise RuntimeError(
            "Skin Weight Visual proxy topology no longer matches the loaded mesh."
        )

    with _without_undo():
        proxy_fn.setPoints(
            source_fn.getPoints(om.MSpace.kObject),
            om.MSpace.kObject,
        )
        try:
            proxy_fn.updateSurface()
        except Exception:
            pass


def _set_proxy_colors(session, rgb):
    proxy_fn = _mesh_fn(session.proxy_shape)
    vertex_count = int(proxy_fn.numVertices)
    if tuple(rgb.shape) != (vertex_count, 3):
        raise RuntimeError(
            "Skin Weight Visual color count does not match the proxy vertex count."
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

    with _without_undo():
        proxy_fn.setCurrentColorSetName(session.color_set)
        # Maya 2025 rejects explicit None for the optional MDGModifier.
        proxy_fn.setVertexColors(colors, vertex_ids)
        _enable_proxy_display(session.proxy_shape)


def _topology_matches(session):
    try:
        return int(_mesh_fn(session.source_shape).numVertices) == int(
            _mesh_fn(session.proxy_shape).numVertices
        )
    except Exception:
        return False


def _install_source_dirty_callback(source_shape):
    try:
        return om.MNodeMessage.addNodeDirtyPlugCallback(
            _depend_node(source_shape),
            _source_mesh_dirty,
        )
    except Exception:
        return None


def _source_mesh_dirty(_node, plug, *_):
    try:
        name = plug.partialName(
            includeNodeName=False,
            useLongNames=True,
        ).casefold()
    except Exception:
        name = ""
    if any(token in name for token in _SOURCE_DIRTY_PLUG_TOKENS):
        request_geometry_refresh()


def _remove_dirty_callback(session):
    callback_id = session.dirty_callback_id
    session.dirty_callback_id = None
    if callback_id is None:
        return
    try:
        om.MMessage.removeCallback(callback_id)
    except Exception:
        pass


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
                uiDeleted=(parent, _MODE.shutdown),
                runOnce=True,
            )
        )
    except Exception:
        pass


def _deactivate_before_mesh_load():
    if _MODE is None:
        _cleanup_preview()
        return
    try:
        _MODE._deactivate()
    except Exception:
        _cleanup_preview()


def _wrap_before(module, function_name, before):
    current = getattr(module, function_name)
    key = (module.__name__, function_name)
    if getattr(current, "_ad_skin_visual_proxy_before_wrapper", False):
        return

    _ORIGINAL_OPERATION_FUNCTIONS[key] = (module, current)

    @wraps(current)
    def wrapper(*args, **kwargs):
        before()
        return current(*args, **kwargs)

    wrapper._ad_skin_visual_proxy_before_wrapper = True
    setattr(module, function_name, wrapper)

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
        "_preview_color_set_exists": _preview_exists,
        "_make_preview_current": _make_preview_current,
        "_install_script_jobs": _install_script_jobs,
    }
    for name, original in list(_ORIGINAL_MODE_FUNCTIONS.items()):
        try:
            if getattr(_MODE, name, None) is patched[name]:
                setattr(_MODE, name, original)
        except Exception:
            pass
    _ORIGINAL_MODE_FUNCTIONS.clear()


def _purge_stale_proxy_shapes(source_transform):
    with _without_undo(), _preserve_selection():
        shapes = cmds.listRelatives(
            source_transform,
            shapes=True,
            fullPath=True,
            type="mesh",
        ) or []
        stale = [
            shape
            for shape in shapes
            if _short_name(shape).startswith(_PROXY_SHAPE_BASE)
        ]
        if stale:
            cmds.delete(stale)


def _purge_legacy_preview_history(source_shape):
    removed_nodes = []
    legacy_sets = _legacy_color_sets(source_shape)

    with _without_undo(), _preserve_selection():
        history = cmds.listHistory(
            source_shape,
            pruneDagObjects=True,
        ) or []
        for node in history:
            try:
                if cmds.nodeType(node) not in _LEGACY_COLOR_NODE_TYPES:
                    continue
            except Exception:
                continue
            if _node_mentions_legacy_color_set(node):
                removed_nodes.append(node)

        if removed_nodes:
            try:
                cmds.delete(list(dict.fromkeys(removed_nodes)))
            except Exception:
                pass

        if removed_nodes or legacy_sets:
            _set_attr_if_present(source_shape, "displayColors", False)

    if removed_nodes:
        print(
            "[AD Skin] Removed {} legacy Skin Weight Visual history node(s).".format(
                len(set(removed_nodes))
            )
        )
    if legacy_sets:
        print(
            "[AD Skin] Legacy preview color set data remains hidden: {}".format(
                ", ".join(sorted(legacy_sets))
            )
        )


def _legacy_color_sets(source_shape):
    try:
        names = _mesh_fn(source_shape).getColorSetNames()
    except Exception:
        return set()
    return {
        str(name)
        for name in names
        if str(name).startswith(_LEGACY_COLOR_SET_PREFIX)
    }


def _node_mentions_legacy_color_set(node):
    try:
        attributes = cmds.listAttr(node) or []
    except Exception:
        attributes = []

    for attribute in attributes:
        if "colorset" not in str(attribute).casefold():
            continue
        try:
            value = cmds.getAttr("{}.{}".format(node, attribute))
        except Exception:
            continue
        if _value_contains_prefix(value, _LEGACY_COLOR_SET_PREFIX):
            return True
    return False


def _value_contains_prefix(value, prefix):
    if isinstance(value, str):
        return value.startswith(prefix)
    if isinstance(value, (tuple, list)):
        return any(_value_contains_prefix(item, prefix) for item in value)
    return False


def _session_nodes_exist(session):
    return bool(
        session.source_shape
        and session.proxy_shape
        and cmds.objExists(session.source_shape)
        and cmds.objExists(session.proxy_shape)
    )


def _mesh_fn(mesh_shape):
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    return om.MFnMesh(selection.getDagPath(0))


def _depend_node(node):
    selection = om.MSelectionList()
    selection.add(node)
    return selection.getDependNode(0)


def _long_name(node):
    matches = cmds.ls(node, long=True) or []
    return matches[0] if matches else None


def _short_name(node):
    return str(node).rsplit("|", 1)[-1]


def _set_attr_if_present(node, attribute, value):
    plug = "{}.{}".format(node, attribute)
    if not cmds.objExists(plug):
        return
    try:
        cmds.setAttr(plug, value)
    except Exception:
        pass


@contextmanager
def _without_undo():
    try:
        undo_enabled = bool(cmds.undoInfo(query=True, state=True))
    except Exception:
        undo_enabled = False

    try:
        if undo_enabled:
            cmds.undoInfo(stateWithoutFlush=False)
        yield
    finally:
        if undo_enabled:
            try:
                cmds.undoInfo(stateWithoutFlush=True)
            except Exception:
                pass


@contextmanager
def _preserve_selection():
    try:
        selection = cmds.ls(selection=True, long=True) or []
    except Exception:
        selection = []

    try:
        yield
    finally:
        try:
            if selection:
                cmds.select(selection, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass


def _state():
    return _TOOL_WINDOW._STATE
