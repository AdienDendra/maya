"""History-free Maya mesh used only to draw Skin Weight Visual colors."""

from contextlib import contextmanager
from dataclasses import dataclass

import maya.api.OpenMaya as om
import maya.cmds as cmds


PROXY_SHAPE_BASE = "__adSkinWeightVisualShape__"
PROXY_COLOR_SET = "__adSkinWeightVisualColors__"
LEGACY_COLOR_SET_PREFIX = "__adSkinWeightPreview__"

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

_SESSION = None
_GEOMETRY_DIRTY_CALLBACK = None
_CLEANING_UP = False


@dataclass
class ProxySession:
    source_shape: str
    source_transform: str
    proxy_shape: str
    color_set: str
    source_display_state: dict
    dirty_callback_id: object = None


def configure(geometry_dirty_callback) -> None:
    """Set the callback used when the evaluated source mesh becomes dirty."""

    global _GEOMETRY_DIRTY_CALLBACK
    _GEOMETRY_DIRTY_CALLBACK = geometry_dirty_callback


def session():
    return _SESSION


def exists() -> bool:
    return bool(_SESSION is not None and _session_nodes_exist(_SESSION))


def ensure(mesh_shape, mesh_transform, sync_geometry=False):
    """Create or refresh the history-free proxy for the loaded production mesh."""

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
        cleanup()
        same_source = False

    if not same_source:
        cleanup()
        source_display_state = _capture_source_display(source_shape)
        _purge_stale_proxy_shapes(source_transform)
        legacy_found = _purge_legacy_preview_history(source_shape)
        if legacy_found:
            source_display_state["displayColors"] = False
        _SESSION = _create_proxy_session(
            source_shape,
            source_transform,
            source_display_state,
        )
        return _SESSION

    if sync_geometry:
        _sync_proxy_points(_SESSION)
    _enable_proxy_display(_SESSION.proxy_shape)
    return _SESSION


def set_colors(rgb) -> None:
    """Write mapped RGB values only to the temporary proxy shape."""

    if not exists():
        raise RuntimeError("Skin Weight Visual proxy is unavailable.")

    proxy_fn = _mesh_fn(_SESSION.proxy_shape)
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

    with without_undo():
        proxy_fn.setCurrentColorSetName(_SESSION.color_set)
        # Maya 2025 rejects explicit None for the optional MDGModifier.
        proxy_fn.setVertexColors(colors, vertex_ids)
        _enable_proxy_display(_SESSION.proxy_shape)


def sync_geometry() -> None:
    if exists():
        _sync_proxy_points(_SESSION)


def make_current() -> None:
    if not exists():
        raise RuntimeError("Skin Weight Visual proxy is unavailable.")
    with without_undo():
        _mesh_fn(_SESSION.proxy_shape).setCurrentColorSetName(
            _SESSION.color_set
        )


def cleanup() -> None:
    """Restore source display and remove the proxy without touching undo history."""

    global _SESSION, _CLEANING_UP

    if _CLEANING_UP:
        return

    current = _SESSION
    _SESSION = None
    if current is None:
        return

    _CLEANING_UP = True
    try:
        _remove_dirty_callback(current)
        with without_undo(), preserve_selection():
            _restore_source_display(current)
            if current.proxy_shape and cmds.objExists(current.proxy_shape):
                cmds.delete(current.proxy_shape)
            cmds.refresh(force=True)
    finally:
        _CLEANING_UP = False


def shutdown() -> None:
    global _GEOMETRY_DIRTY_CALLBACK
    cleanup()
    _GEOMETRY_DIRTY_CALLBACK = None


def _create_proxy_session(
    source_shape,
    source_transform,
    source_display_state,
):
    with without_undo(), preserve_selection():
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
        proxy_path = om.MDagPath.getAPathTo(proxy_object).fullPathName()
        renamed_proxy = cmds.rename(proxy_path, PROXY_SHAPE_BASE)
        proxy_shape = _child_mesh_path(source_transform, renamed_proxy)
        if not proxy_shape:
            raise RuntimeError(
                "Skin Weight Visual could not resolve its proxy shape."
            )

        _configure_proxy_shape(proxy_shape)
        color_set = _create_proxy_color_set(proxy_shape)
        _show_source_as_selection_wire(source_shape)

    result = ProxySession(
        source_shape=source_shape,
        source_transform=source_transform,
        proxy_shape=proxy_shape,
        color_set=color_set,
        source_display_state=source_display_state,
    )
    result.dirty_callback_id = _install_source_dirty_callback(source_shape)
    return result


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
        PROXY_COLOR_SET,
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


def _restore_source_display(current):
    if not current.source_shape or not cmds.objExists(current.source_shape):
        return
    for attribute, value in current.source_display_state.items():
        _set_attr_if_present(current.source_shape, attribute, value)


def _sync_proxy_points(current):
    source_fn = _mesh_fn(current.source_shape)
    proxy_fn = _mesh_fn(current.proxy_shape)

    if int(source_fn.numVertices) != int(proxy_fn.numVertices):
        raise RuntimeError(
            "Skin Weight Visual proxy topology no longer matches the loaded mesh."
        )

    with without_undo():
        proxy_fn.setPoints(
            source_fn.getPoints(om.MSpace.kObject),
            om.MSpace.kObject,
        )
        try:
            proxy_fn.updateSurface()
        except Exception:
            pass


def _topology_matches(current):
    try:
        return int(_mesh_fn(current.source_shape).numVertices) == int(
            _mesh_fn(current.proxy_shape).numVertices
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
        plug_name = plug.name().casefold()
    except Exception:
        plug_name = ""
    if not any(token in plug_name for token in _SOURCE_DIRTY_PLUG_TOKENS):
        return
    callback = _GEOMETRY_DIRTY_CALLBACK
    if callable(callback):
        callback()


def _remove_dirty_callback(current):
    callback_id = current.dirty_callback_id
    current.dirty_callback_id = None
    if callback_id is None:
        return
    try:
        om.MMessage.removeCallback(callback_id)
    except Exception:
        pass


def _purge_stale_proxy_shapes(source_transform):
    with without_undo(), preserve_selection():
        shapes = cmds.listRelatives(
            source_transform,
            shapes=True,
            fullPath=True,
            type="mesh",
        ) or []
        stale = [
            shape
            for shape in shapes
            if _name_without_namespace(shape).startswith(PROXY_SHAPE_BASE)
        ]
        if stale:
            cmds.delete(stale)


def _purge_legacy_preview_history(source_shape):
    removed_nodes = []
    legacy_sets_before = _legacy_color_sets(source_shape)

    with without_undo(), preserve_selection():
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

        legacy_found = bool(removed_nodes or legacy_sets_before)
        if legacy_found:
            _set_attr_if_present(source_shape, "displayColors", False)

    legacy_sets_after = _legacy_color_sets(source_shape)
    if removed_nodes:
        print(
            "[AD Skin] Removed {} legacy Skin Weight Visual history node(s).".format(
                len(set(removed_nodes))
            )
        )
    if legacy_sets_after:
        print(
            "[AD Skin] Legacy preview color set data remains hidden: {}".format(
                ", ".join(sorted(legacy_sets_after))
            )
        )
    return legacy_found


def _legacy_color_sets(source_shape):
    try:
        names = _mesh_fn(source_shape).getColorSetNames()
    except Exception:
        return set()
    return {
        str(name)
        for name in names
        if str(name).startswith(LEGACY_COLOR_SET_PREFIX)
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
        if _value_contains_prefix(value, LEGACY_COLOR_SET_PREFIX):
            return True
    return False


def _value_contains_prefix(value, prefix):
    if isinstance(value, str):
        return value.startswith(prefix)
    if isinstance(value, (tuple, list)):
        return any(_value_contains_prefix(item, prefix) for item in value)
    return False


def _session_nodes_exist(current):
    return bool(
        current.source_shape
        and current.proxy_shape
        and cmds.objExists(current.source_shape)
        and cmds.objExists(current.proxy_shape)
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


def _name_without_namespace(node):
    return _short_name(node).rsplit(":", 1)[-1]


def _child_mesh_path(parent_transform, child_name):
    expected = _short_name(child_name)
    shapes = cmds.listRelatives(
        parent_transform,
        shapes=True,
        fullPath=True,
        type="mesh",
    ) or []
    for shape in shapes:
        if _short_name(shape) == expected:
            return shape
    return None


def _set_attr_if_present(node, attribute, value):
    plug = "{}.{}".format(node, attribute)
    if not cmds.objExists(plug):
        return
    try:
        cmds.setAttr(plug, value)
    except Exception:
        pass


@contextmanager
def without_undo():
    """Disable undo recording temporarily without flushing the existing queue."""

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
def preserve_selection():
    try:
        selected = cmds.ls(selection=True, long=True) or []
    except Exception:
        selected = []

    try:
        yield
    finally:
        try:
            if selected:
                cmds.select(selected, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass
