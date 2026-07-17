"""Resolve Maya polygon component selection into loaded-mesh vertex IDs.

The selection layer is intentionally independent from skinCluster operations.
It accepts vertex, edge, and face components, ignores components from every
mesh except the loaded mesh, and returns stable global Maya vertex IDs.
"""

from dataclasses import dataclass
import re
from typing import Sequence, Tuple

import maya.cmds as cmds


_VERTEX_COMPONENT_PATTERN = re.compile(r"\.vtx\[(\d+)\]$")
_SUPPORTED_COMPONENT_MARKERS = (".vtx[", ".e[", ".f[")


@dataclass(frozen=True)
class MeshComponentSelection:
    """Immutable component scope for one loaded polygon mesh."""

    mesh_shape: str
    mesh_transform: str
    vertex_ids: Tuple[int, ...]
    source_components: Tuple[str, ...]
    ignored_components: Tuple[str, ...]

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)

    @property
    def source_component_count(self) -> int:
        return len(self.source_components)

    @property
    def ignored_component_count(self) -> int:
        return len(self.ignored_components)


def collect_selected_mesh_vertices(
    mesh_shape: str,
    mesh_transform: str,
) -> MeshComponentSelection:
    """Collect selected vertices, edges, and faces from the loaded mesh only.

    Polygon components belonging to other meshes are intentionally ignored.
    Object selections, joints, and unsupported component types are not part of
    the returned scope. Face and edge selections are converted to vertices.
    """

    resolved_shape, resolved_transform = _resolve_loaded_mesh(
        mesh_shape,
        mesh_transform,
    )
    selection = tuple(
        str(item)
        for item in (cmds.ls(selection=True, flatten=True, long=True) or [])
    )

    accepted = []
    ignored = []
    for item in selection:
        if not _looks_like_component(item):
            continue
        if not _is_supported_polygon_component(item):
            ignored.append(item)
            continue
        if _component_belongs_to_mesh(
            item,
            resolved_shape,
            resolved_transform,
        ):
            accepted.append(item)
        else:
            ignored.append(item)

    if accepted:
        converted = cmds.polyListComponentConversion(
            accepted,
            toVertex=True,
        ) or []
        flattened_vertices = cmds.ls(
            converted,
            flatten=True,
            long=True,
        ) or []
    else:
        flattened_vertices = []

    vertex_ids = sorted(
        {
            _vertex_id(component)
            for component in flattened_vertices
            if _is_vertex_component(component)
            and _component_belongs_to_mesh(
                component,
                resolved_shape,
                resolved_transform,
            )
        }
    )
    _validate_vertex_ids(resolved_shape, vertex_ids)

    return MeshComponentSelection(
        mesh_shape=resolved_shape,
        mesh_transform=resolved_transform,
        vertex_ids=tuple(vertex_ids),
        source_components=tuple(accepted),
        ignored_components=tuple(ignored),
    )


def _resolve_loaded_mesh(mesh_shape: str, mesh_transform: str) -> Tuple[str, str]:
    shape_matches = cmds.ls(mesh_shape, long=True, type="mesh") or []
    if not shape_matches:
        raise RuntimeError("Loaded mesh shape no longer exists:\n{}".format(mesh_shape))
    resolved_shape = shape_matches[0]

    transform_matches = cmds.ls(mesh_transform, long=True, type="transform") or []
    if not transform_matches:
        parents = cmds.listRelatives(
            resolved_shape,
            parent=True,
            fullPath=True,
        ) or []
        if not parents:
            raise RuntimeError(
                "Loaded mesh shape has no transform parent:\n{}".format(
                    resolved_shape
                )
            )
        resolved_transform = parents[0]
    else:
        resolved_transform = transform_matches[0]

    shapes = cmds.listRelatives(
        resolved_transform,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
        type="mesh",
    ) or []
    if resolved_shape not in shapes:
        raise RuntimeError(
            "Loaded mesh shape and transform no longer describe the same mesh."
        )
    return resolved_shape, resolved_transform


def _looks_like_component(item: str) -> bool:
    return "." in item and "[" in item and "]" in item


def _is_supported_polygon_component(item: str) -> bool:
    return any(marker in item for marker in _SUPPORTED_COMPONENT_MARKERS)


def _is_vertex_component(item: str) -> bool:
    return _VERTEX_COMPONENT_PATTERN.search(str(item)) is not None


def _vertex_id(component: str) -> int:
    match = _VERTEX_COMPONENT_PATTERN.search(str(component))
    if match is None:
        raise ValueError("Not a flattened mesh vertex component: {}".format(component))
    return int(match.group(1))


def _component_belongs_to_mesh(
    component: str,
    mesh_shape: str,
    mesh_transform: str,
) -> bool:
    base = str(component).split(".", 1)[0]
    matches = cmds.ls(base, long=True) or []
    if not matches:
        return False

    node = matches[0]
    node_type = cmds.nodeType(node)
    if node_type == "mesh":
        parents = cmds.listRelatives(
            node,
            parent=True,
            fullPath=True,
        ) or []
        return node == mesh_shape and bool(parents) and parents[0] == mesh_transform

    if node_type != "transform":
        return False

    shapes = cmds.listRelatives(
        node,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
        type="mesh",
    ) or []
    return node == mesh_transform and mesh_shape in shapes


def _validate_vertex_ids(mesh_shape: str, vertex_ids: Sequence[int]) -> None:
    if not vertex_ids:
        return

    vertex_count = int(cmds.polyEvaluate(mesh_shape, vertex=True))
    invalid = [
        int(vertex_id)
        for vertex_id in vertex_ids
        if int(vertex_id) < 0 or int(vertex_id) >= vertex_count
    ]
    if invalid:
        raise RuntimeError(
            "Component conversion returned invalid vertex IDs for {}: {}".format(
                mesh_shape,
                invalid[:20],
            )
        )
