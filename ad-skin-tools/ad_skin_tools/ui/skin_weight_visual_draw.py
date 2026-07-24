"""Viewport-only Skin Weight Visual draw state.

The module owns one lightweight locator draw node. It never creates a mesh,
color set, deformer, or construction-history node.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import os

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr
import maya.cmds as cmds

from ad_skin_tools.core.skin_cluster import SkinClusterAdapter


NODE_TYPE_NAME = "adSkinWeightVisualDraw"
NODE_TYPE_ID_VALUE = 0x0013A9F0
DRAW_CLASSIFICATION = "drawdb/geometry/adSkinWeightVisualDraw"
DRAW_REGISTRANT_ID = "ADSkinWeightVisualDrawRegistrant"

_DRAW_SHAPE_BASE = "__adSkinWeightVisualDrawShape__"
_DRAW_TRANSFORM_BASE = "__adSkinWeightVisualDraw__"

_SOURCE_DISPLAY_ATTRIBUTES = (
    "overrideEnabled",
    "overrideDisplayType",
    "overrideShading",
    "displayColors",
)


@dataclass
class _Session:
    source_shape: str
    source_transform: str
    draw_shape: str
    draw_transform: str
    source_display_state: dict
    joint: object = None
    mode: object = None
    colors_dirty: bool = True
    topology_signature: object = None
    triangle_indices: object = None
    colors: object = None
    draw_handle: object = None


_SESSION = None
_CLEANING_UP = False


def ensure(source_shape, source_transform):
    """Create the lightweight draw node and prepare the source display."""

    global _SESSION

    source_shape = _long_name(source_shape)
    source_transform = _long_name(source_transform)
    if not source_shape or not source_transform:
        raise RuntimeError("Skin Weight Visual could not resolve the loaded mesh.")

    same_source = bool(
        _SESSION is not None
        and _SESSION.source_shape == source_shape
        and _SESSION.source_transform == source_transform
        and _session_nodes_exist(_SESSION)
    )
    if same_source:
        return _SESSION

    cleanup()
    _ensure_plugin_loaded()

    source_display_state = _capture_source_display(source_shape)
    with without_undo(), preserve_selection():
        draw_shape = cmds.createNode(
            NODE_TYPE_NAME,
            name=_DRAW_SHAPE_BASE,
        )
        draw_shape = _long_name(draw_shape)
        draw_transform = _parent_transform(draw_shape)
        if not draw_shape or not draw_transform:
            raise RuntimeError(
                "Skin Weight Visual could not create its viewport draw node."
            )

        try:
            draw_transform = cmds.rename(draw_transform, _DRAW_TRANSFORM_BASE)
        except Exception:
            pass
        draw_transform = _long_name(draw_transform)
        draw_shape = _child_shape(draw_transform, draw_shape)

        _configure_draw_node(draw_transform, draw_shape)
        _show_source_as_selection_wire(source_shape)

    _SESSION = _Session(
        source_shape=source_shape,
        source_transform=source_transform,
        draw_shape=draw_shape,
        draw_transform=draw_transform,
        source_display_state=source_display_state,
        draw_handle=om.MObjectHandle(_depend_node(draw_shape)),
    )
    mark_draw_dirty(topology_changed=True)
    return _SESSION


def update_context(source_shape, source_transform, joint, mode) -> None:
    """Set the current influence and ramp without touching the Maya scene."""

    current = ensure(source_shape, source_transform)
    changed = current.joint != joint or current.mode != mode
    current.joint = str(joint) if joint else None
    current.mode = mode
    if changed:
        current.colors_dirty = True
    mark_draw_dirty(topology_changed=False)


def exists() -> bool:
    return bool(_SESSION is not None and _session_nodes_exist(_SESSION))


def mark_colors_dirty(mesh_shape=None) -> None:
    """Invalidate only the cached colors for the active loaded mesh."""

    current = _SESSION
    if current is None:
        return
    if mesh_shape:
        raw = str(mesh_shape)
        source_short = current.source_shape.rsplit("|", 1)[-1]
        if raw not in (current.source_shape, source_short):
            return
    current.colors_dirty = True
    mark_draw_dirty(topology_changed=False)


def mark_draw_dirty(topology_changed=False) -> None:
    """Ask Viewport 2.0 to rebuild this lightweight drawable."""

    current = _SESSION
    if current is None or current.draw_handle is None:
        return
    try:
        if not current.draw_handle.isValid() or not current.draw_handle.isAlive():
            return
        omr.MRenderer.setGeometryDrawDirty(
            current.draw_handle.object(),
            bool(topology_changed),
        )
    except Exception:
        pass


def populate_draw_data(draw_node_object, data) -> None:
    """Fill reusable MUserData during MPxDrawOverride.prepareForDraw()."""

    data.valid = False
    if not exists():
        return

    try:
        draw_path = om.MDagPath.getAPathTo(draw_node_object).fullPathName()
    except Exception:
        return
    if draw_path != _SESSION.draw_shape:
        return

    if not all(
        (
            _SESSION.joint,
            _SESSION.mode,
            cmds.objExists(_SESSION.source_shape),
            cmds.objExists(_SESSION.joint),
        )
    ):
        return

    mesh_fn = _mesh_fn(_SESSION.source_shape)
    vertex_count = int(mesh_fn.numVertices)
    polygon_count = int(mesh_fn.numPolygons)
    topology_signature = (vertex_count, polygon_count)

    if (
        _SESSION.topology_signature != topology_signature
        or _SESSION.triangle_indices is None
    ):
        _SESSION.triangle_indices = _triangle_indices(mesh_fn)
        _SESSION.topology_signature = topology_signature
        _SESSION.colors_dirty = True

    if _SESSION.colors_dirty or _SESSION.colors is None:
        _SESSION.colors = _build_colors(
            _SESSION.source_shape,
            _SESSION.joint,
            _SESSION.mode,
            vertex_count,
        )
        _SESSION.colors_dirty = False

    data.positions = mesh_fn.getPoints(om.MSpace.kWorld)
    data.colors = _SESSION.colors
    data.indices = _SESSION.triangle_indices
    data.valid = bool(
        len(data.positions) == len(data.colors)
        and len(data.indices) >= 3
    )


def cleanup() -> None:
    """Restore source shading and delete only the lightweight draw node."""

    global _SESSION, _CLEANING_UP

    if _CLEANING_UP:
        return

    current = _SESSION
    _SESSION = None
    if current is None:
        return

    _CLEANING_UP = True
    try:
        with without_undo(), preserve_selection():
            _restore_source_display(current)
            if current.draw_transform and cmds.objExists(current.draw_transform):
                cmds.delete(current.draw_transform)
            elif current.draw_shape and cmds.objExists(current.draw_shape):
                cmds.delete(current.draw_shape)
            cmds.refresh(force=True)
    finally:
        _CLEANING_UP = False


def shutdown(unload_plugin=False) -> None:
    cleanup()
    if unload_plugin:
        _unload_plugin()


def _build_colors(source_shape, joint, mode, vertex_count):
    adapter = SkinClusterAdapter.from_mesh(source_shape)

    from ad_skin_tools.ui import skin_weight_mode

    rgb = skin_weight_mode._map_weights_to_rgb(
        adapter.influence_weights(joint),
        mode,
    )
    if tuple(rgb.shape) != (vertex_count, 3):
        raise RuntimeError(
            "Skin Weight Visual color count does not match the loaded mesh."
        )

    colors = om.MColorArray()
    colors.setLength(vertex_count)
    for index in range(vertex_count):
        colors[index] = om.MColor(
            (
                float(rgb[index, 0]),
                float(rgb[index, 1]),
                float(rgb[index, 2]),
                1.0,
            )
        )
    return colors


def _triangle_indices(mesh_fn):
    _triangle_counts, vertex_ids = mesh_fn.getTriangles()
    result = om.MUintArray()
    result.setLength(len(vertex_ids))
    for index in range(len(vertex_ids)):
        result[index] = int(vertex_ids[index])
    return result


def _configure_draw_node(draw_transform, draw_shape):
    for node in (draw_transform, draw_shape):
        _set_attr_if_present(node, "hiddenInOutliner", True)
        _set_attr_if_present(node, "isHistoricallyInteresting", False)
        _set_attr_if_present(node, "overrideEnabled", True)
        _set_attr_if_present(node, "overrideDisplayType", 2)

    for attribute in ("translate", "rotate"):
        for axis in "XYZ":
            _set_attr_if_present(
                draw_transform,
                "{}{}".format(attribute, axis),
                0.0,
            )
    for axis in "XYZ":
        _set_attr_if_present(draw_transform, "scale{}".format(axis), 1.0)


def _capture_source_display(source_shape):
    result = {}
    for attribute in _SOURCE_DISPLAY_ATTRIBUTES:
        plug = "{}.{}".format(source_shape, attribute)
        if not cmds.objExists(plug):
            continue
        try:
            result[attribute] = cmds.getAttr(plug)
        except Exception:
            pass
    return result


def _show_source_as_selection_wire(source_shape):
    _set_attr_if_present(source_shape, "overrideEnabled", True)
    _set_attr_if_present(source_shape, "overrideDisplayType", 0)
    _set_attr_if_present(source_shape, "overrideShading", False)
    _set_attr_if_present(source_shape, "displayColors", False)


def _restore_source_display(current):
    if not current.source_shape or not cmds.objExists(current.source_shape):
        return
    for attribute, value in current.source_display_state.items():
        _set_attr_if_present(current.source_shape, attribute, value)


def _session_nodes_exist(current):
    return bool(
        current.draw_shape
        and current.source_shape
        and cmds.objExists(current.draw_shape)
        and cmds.objExists(current.source_shape)
    )


def _ensure_plugin_loaded():
    plugin_path = _plugin_path()
    plugin_name = _plugin_name()
    try:
        loaded = bool(
            cmds.pluginInfo(plugin_name, query=True, loaded=True)
        )
    except Exception:
        loaded = False
    if not loaded:
        cmds.loadPlugin(plugin_path, quiet=True)


def _unload_plugin():
    plugin_name = _plugin_name()
    try:
        loaded = bool(
            cmds.pluginInfo(plugin_name, query=True, loaded=True)
        )
    except Exception:
        loaded = False
    if loaded:
        try:
            cmds.unloadPlugin(plugin_name, force=True)
        except Exception:
            pass


def _plugin_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "skin_weight_visual_draw_plugin.py",
    )


def _plugin_name():
    return os.path.splitext(os.path.basename(_plugin_path()))[0]


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


def _parent_transform(shape):
    parents = cmds.listRelatives(
        shape,
        parent=True,
        fullPath=True,
    ) or []
    return parents[0] if parents else None


def _child_shape(parent_transform, child):
    expected = str(child).rsplit("|", 1)[-1]
    shapes = cmds.listRelatives(
        parent_transform,
        shapes=True,
        fullPath=True,
    ) or []
    for shape in shapes:
        if str(shape).rsplit("|", 1)[-1] == expected:
            return shape
    return shapes[0] if shapes else None


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
    """Disable undo recording without flushing the existing queue."""

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
