"""Resolve Maya component selection into weighted mesh vertices."""

from dataclasses import dataclass
from typing import Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ad_skin_tools.core.component_selection import (
    MeshComponentSelection,
    collect_selected_mesh_vertices,
)


@dataclass(frozen=True)
class WeightedVertexSelection:
    mesh_shape: str
    mesh_transform: str
    vertex_ids: Tuple[int, ...]
    falloff_weights: Tuple[float, ...]
    soft_selection_enabled: bool
    soft_selection_used: bool
    source_component_count: int
    ignored_component_count: int

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


def collect_weighted_mesh_vertices(
    mesh_shape: str,
    mesh_transform: str,
    hard_scope: Optional[MeshComponentSelection] = None,
) -> WeightedVertexSelection:
    """Return loaded-mesh vertices and Maya soft-selection weights.

    ``hard_scope`` lets callers reuse an already resolved component selection so
    Maya does not need to flatten and convert the same selection twice.
    """

    if hard_scope is None:
        hard_scope = collect_selected_mesh_vertices(mesh_shape, mesh_transform)
    elif (
        hard_scope.mesh_shape != mesh_shape
        or hard_scope.mesh_transform != mesh_transform
    ):
        raise ValueError("hard_scope refers to a different loaded mesh.")

    if not hard_scope.vertex_ids:
        raise RuntimeError(
            "Select vertices, edges, or faces on the loaded mesh.\n\n"
            "Components from other meshes are ignored."
        )

    soft_enabled = bool(cmds.softSelect(query=True, softSelectEnabled=True))
    if not soft_enabled:
        return _from_hard_scope(hard_scope, soft_enabled=False)

    rich_weights = _rich_vertex_weights(
        hard_scope.mesh_shape,
        hard_scope.mesh_transform,
    )
    if not rich_weights:
        return _from_hard_scope(hard_scope, soft_enabled=True)

    # Maya already resolves face and edge soft selection to weighted vertices.
    # Force the original hard-selected scope to exactly 1.0.
    for vertex_id in hard_scope.vertex_ids:
        rich_weights[int(vertex_id)] = 1.0

    vertex_ids = tuple(sorted(rich_weights))
    falloff_weights = tuple(
        float(rich_weights[vertex_id])
        for vertex_id in vertex_ids
    )
    return WeightedVertexSelection(
        mesh_shape=hard_scope.mesh_shape,
        mesh_transform=hard_scope.mesh_transform,
        vertex_ids=vertex_ids,
        falloff_weights=falloff_weights,
        soft_selection_enabled=True,
        soft_selection_used=True,
        source_component_count=hard_scope.source_component_count,
        ignored_component_count=hard_scope.ignored_component_count,
    )


def _from_hard_scope(
    hard_scope: MeshComponentSelection,
    soft_enabled: bool,
) -> WeightedVertexSelection:
    return WeightedVertexSelection(
        mesh_shape=hard_scope.mesh_shape,
        mesh_transform=hard_scope.mesh_transform,
        vertex_ids=hard_scope.vertex_ids,
        falloff_weights=tuple(1.0 for _ in hard_scope.vertex_ids),
        soft_selection_enabled=bool(soft_enabled),
        soft_selection_used=False,
        source_component_count=hard_scope.source_component_count,
        ignored_component_count=hard_scope.ignored_component_count,
    )


def _rich_vertex_weights(mesh_shape: str, mesh_transform: str):
    resolved = {}
    rich_selection = om.MGlobal.getRichSelection()
    iterator = om.MItSelectionList(rich_selection.getSelection())

    while not iterator.isDone():
        dag_path, component = iterator.getComponent()
        if component.isNull():
            iterator.next()
            continue

        node_path = dag_path.fullPathName()
        if node_path not in (mesh_shape, mesh_transform):
            iterator.next()
            continue

        component_fn = om.MFnComponent(component)
        if component_fn.componentType != om.MFn.kMeshVertComponent:
            iterator.next()
            continue

        indexed_fn = om.MFnSingleIndexedComponent(component)
        element_ids = indexed_fn.getElements()

        for local_index, element_id in enumerate(element_ids):
            influence = (
                component_fn.weight(local_index).influence
                if component_fn.hasWeights
                else 1.0
            )
            influence = max(0.0, min(1.0, float(influence)))
            if influence <= 0.0:
                continue

            vertex_id = int(element_id)
            resolved[vertex_id] = max(
                resolved.get(vertex_id, 0.0),
                influence,
            )

        iterator.next()

    return resolved
