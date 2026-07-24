"""Ephemeral direct-mesh colors for Skin Weight Visual.

The loaded skinned mesh remains the live display and component-selection target.
Visual commands are excluded from Maya's undo queue, and every preview history
node is removed when the visual is deactivated.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field

import maya.api.OpenMaya as om
import maya.cmds as cmds


COLOR_SET_BASE = "__adSkinWeightPreview__"
OWNER_ATTRIBUTE = "adSkinWeightVisualOwner"
OWNER_VALUE = "ephemeral-color-session"

_COLOR_NODE_TYPES = frozenset(
    (
        "createColorSet",
        "deleteColorSet",
        "polyColorPerVertex",
        "polyColorDel",
    )
)
_DISPLAY_FLAGS = (
    "colorShadedDisplay",
    "colorMaterialChannel",
    "materialBlend",
)


@dataclass
class ColorSession:
    mesh_shape: str
    mesh_transform: str
    color_set: str
    previous_color_set: object
    display_options: dict
    owned_nodes: set = field(default_factory=set)


_SESSION = None
_CLEANING_UP = False
_UNDO_DEPTH = 0
_UNDO_WAS_ENABLED = False


def ensure(mesh_shape, mesh_transform):
    """Create one fresh preview color set for the loaded mesh."""

    global _SESSION

    mesh_shape = _long_name(mesh_shape)
    mesh_transform = _long_name(mesh_transform)
    if not mesh_shape or not mesh_transform:
        raise RuntimeError("Skin Weight Visual could not resolve the loaded mesh.")

    if (
        _SESSION is not None
        and _SESSION.mesh_shape == mesh_shape
        and _SESSION.mesh_transform == mesh_transform
        and exists()
    ):
        make_current()
        return _SESSION

    cleanup()
    with without_undo(), preserve_selection():
        stale_found = _purge_visual_history(mesh_shape, mesh_transform)
        display_options = _query_display_options(mesh_transform)
        if stale_found:
            # A lost legacy Python session could leave preview shading enabled.
            display_options["colorShadedDisplay"] = False
            _set_color_display_enabled(mesh_transform, False)

        previous_color_set = _current_non_preview_color_set(mesh_transform)
        color_set = _unique_color_set_name(_all_color_sets(mesh_transform))
        before = _history_nodes(mesh_shape)
        cmds.polyColorSet(
            mesh_transform,
            create=True,
            colorSet=color_set,
            representation="RGB",
            clamped=True,
        )
        owned_nodes = _tag_new_visual_nodes(mesh_shape, before)

        before = _history_nodes(mesh_shape)
        _set_current_color_set(mesh_transform, color_set)
        owned_nodes.update(_tag_new_visual_nodes(mesh_shape, before))
        _enable_color_display(mesh_transform)

        _SESSION = ColorSession(
            mesh_shape=mesh_shape,
            mesh_transform=mesh_transform,
            color_set=color_set,
            previous_color_set=previous_color_set,
            display_options=display_options,
            owned_nodes=owned_nodes,
        )
    return _SESSION


def set_colors(mesh_shape, mesh_transform, rgb):
    """Paint current influence colors without recording an undo item."""

    current = ensure(mesh_shape, mesh_transform)
    mesh_fn = _mesh_fn(current.mesh_shape)
    vertex_count = int(mesh_fn.numVertices)
    if tuple(rgb.shape) != (vertex_count, 3):
        raise RuntimeError(
            "Skin Weight Visual color count does not match the loaded mesh."
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

    with without_undo(), preserve_selection():
        _set_current_color_set(current.mesh_transform, current.color_set)
        before = _history_nodes(current.mesh_shape)
        # Maya 2025 rejects explicit None for the optional MDGModifier.
        mesh_fn.setVertexColors(colors, vertex_ids)
        current.owned_nodes.update(
            _tag_new_visual_nodes(current.mesh_shape, before)
        )
        _enable_color_display(current.mesh_transform)

    cmds.refresh(force=True)
    return current


def cleanup() -> None:
    """Remove all AD Skin preview history and restore normal mesh display."""

    global _SESSION, _CLEANING_UP

    if _CLEANING_UP:
        return
    current = _SESSION
    _SESSION = None
    if current is None:
        return

    _CLEANING_UP = True
    try:
        if not current.mesh_transform or not cmds.objExists(current.mesh_transform):
            return
        with without_undo(), preserve_selection():
            _restore_non_preview_color_set(current)
            _purge_visual_history(current.mesh_shape, current.mesh_transform)
            _restore_display_options(
                current.mesh_transform,
                current.display_options,
            )
            cmds.refresh(force=True)
    finally:
        _CLEANING_UP = False


def exists() -> bool:
    current = _SESSION
    return bool(
        current is not None
        and cmds.objExists(current.mesh_shape)
        and cmds.objExists(current.mesh_transform)
        and current.color_set in _all_color_sets(current.mesh_transform)
    )


def make_current() -> None:
    if not exists():
        raise RuntimeError("Skin Weight Visual color session is unavailable.")
    with without_undo():
        _set_current_color_set(_SESSION.mesh_transform, _SESSION.color_set)
        _enable_color_display(_SESSION.mesh_transform)


def session():
    return _SESSION


def _purge_visual_history(mesh_shape, mesh_transform):
    """Delete only nodes tagged by AD Skin or naming its reserved color set."""

    if not mesh_shape or not cmds.objExists(mesh_shape):
        return False

    candidates = []
    for node in _history_nodes(mesh_shape):
        if not cmds.objExists(node):
            continue
        try:
            if cmds.nodeType(node) not in _COLOR_NODE_TYPES:
                continue
        except Exception:
            continue
        if _is_owned_node(node) or _node_mentions_preview_set(node):
            candidates.append(node)

    preview_sets = {
        name
        for name in _all_color_sets(mesh_transform)
        if str(name).startswith(COLOR_SET_BASE)
    }
    stale_found = bool(candidates or preview_sets)
    if not stale_found:
        return False

    fallback = _current_non_preview_color_set(mesh_transform)
    if fallback:
        try:
            _set_current_color_set(mesh_transform, fallback)
        except Exception:
            pass

    if candidates:
        try:
            cmds.delete(list(dict.fromkeys(candidates)))
        except Exception:
            for node in reversed(candidates):
                try:
                    if cmds.objExists(node):
                        cmds.delete(node)
                except Exception:
                    pass
    return True


def _tag_new_visual_nodes(mesh_shape, history_before):
    created = _history_nodes(mesh_shape).difference(history_before)
    tagged = set()
    for node in created:
        try:
            if cmds.nodeType(node) not in _COLOR_NODE_TYPES:
                continue
            if not cmds.attributeQuery(
                OWNER_ATTRIBUTE,
                node=node,
                exists=True,
            ):
                cmds.addAttr(node, longName=OWNER_ATTRIBUTE, dataType="string")
            cmds.setAttr(
                "{}.{}".format(node, OWNER_ATTRIBUTE),
                OWNER_VALUE,
                type="string",
            )
            tagged.add(node)
        except Exception:
            pass
    return tagged


def _is_owned_node(node):
    try:
        return bool(
            cmds.attributeQuery(OWNER_ATTRIBUTE, node=node, exists=True)
            and cmds.getAttr(
                "{}.{}".format(node, OWNER_ATTRIBUTE)
            ) == OWNER_VALUE
        )
    except Exception:
        return False


def _node_mentions_preview_set(node):
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
        if _contains_preview_name(value):
            return True
    return False


def _contains_preview_name(value):
    if isinstance(value, str):
        return value.startswith(COLOR_SET_BASE)
    if isinstance(value, (tuple, list)):
        return any(_contains_preview_name(item) for item in value)
    return False


def _restore_non_preview_color_set(current):
    all_sets = _all_color_sets(current.mesh_transform)
    preferred = current.previous_color_set
    if preferred in all_sets and not str(preferred).startswith(COLOR_SET_BASE):
        _set_current_color_set(current.mesh_transform, preferred)
        return
    fallback = next(
        (
            name
            for name in sorted(all_sets)
            if not str(name).startswith(COLOR_SET_BASE)
        ),
        None,
    )
    if fallback:
        _set_current_color_set(current.mesh_transform, fallback)


def _current_non_preview_color_set(mesh_transform):
    current = _current_color_set(mesh_transform)
    if current and not str(current).startswith(COLOR_SET_BASE):
        return current
    return next(
        (
            name
            for name in sorted(_all_color_sets(mesh_transform))
            if not str(name).startswith(COLOR_SET_BASE)
        ),
        None,
    )


def _history_nodes(mesh_shape):
    try:
        return set(
            cmds.listHistory(mesh_shape, pruneDagObjects=True) or []
        )
    except Exception:
        return set()


def _all_color_sets(mesh_transform):
    try:
        return set(
            cmds.polyColorSet(
                mesh_transform,
                query=True,
                allColorSets=True,
            )
            or []
        )
    except Exception:
        return set()


def _current_color_set(mesh_transform):
    try:
        result = cmds.polyColorSet(
            mesh_transform,
            query=True,
            currentColorSet=True,
        )
    except Exception:
        return None
    if isinstance(result, (tuple, list)):
        return result[0] if result else None
    return str(result) if result else None


def _set_current_color_set(mesh_transform, color_set):
    cmds.polyColorSet(
        mesh_transform,
        currentColorSet=True,
        colorSet=color_set,
    )


def _unique_color_set_name(existing_sets):
    if COLOR_SET_BASE not in existing_sets:
        return COLOR_SET_BASE
    index = 1
    while "{}{}".format(COLOR_SET_BASE, index) in existing_sets:
        index += 1
    return "{}{}".format(COLOR_SET_BASE, index)


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


def _set_color_display_enabled(mesh_transform, enabled):
    try:
        cmds.polyOptions(
            mesh_transform,
            colorShadedDisplay=bool(enabled),
        )
    except Exception:
        pass


def _restore_display_options(mesh_transform, options):
    kwargs = {}
    for flag in _DISPLAY_FLAGS:
        value = options.get(flag)
        if isinstance(value, (tuple, list)) and len(value) == 1:
            value = value[0]
        if value is not None:
            kwargs[flag] = value
    if kwargs:
        try:
            cmds.polyOptions(mesh_transform, **kwargs)
        except Exception:
            pass


def _mesh_fn(mesh_shape):
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    return om.MFnMesh(selection.getDagPath(0))


def _long_name(node):
    matches = cmds.ls(node, long=True) or []
    return matches[0] if matches else None


@contextmanager
def without_undo():
    """Suspend visual recording without flushing Maya's undo queue."""

    global _UNDO_DEPTH, _UNDO_WAS_ENABLED

    outermost = _UNDO_DEPTH == 0
    if outermost:
        try:
            _UNDO_WAS_ENABLED = bool(cmds.undoInfo(query=True, state=True))
        except Exception:
            _UNDO_WAS_ENABLED = False
        if _UNDO_WAS_ENABLED:
            cmds.undoInfo(stateWithoutFlush=False)

    _UNDO_DEPTH += 1
    try:
        yield
    finally:
        _UNDO_DEPTH = max(0, _UNDO_DEPTH - 1)
        if outermost and _UNDO_WAS_ENABLED:
            try:
                cmds.undoInfo(stateWithoutFlush=True)
            except Exception:
                pass
            _UNDO_WAS_ENABLED = False


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
