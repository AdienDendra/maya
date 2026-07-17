"""Exact raw-ownership connectivity for Region Ownership.

For one influence, only mesh edges whose two endpoints are currently owned by
that influence are retained. The resulting induced graph is partitioned into
exact connected regions. No distance threshold, region-size rule, or body-part
rule is used.
"""

from dataclasses import dataclass
from typing import Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from ad_skin_tools.region.distance_ranking import ExactDistanceRankingResult


@dataclass(frozen=True)
class OwnershipConnectivityResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    source_joint: str
    source_influence_index: int
    raw_vertex_ids: Tuple[int, ...]
    anchor_vertex_ids: Tuple[int, ...]
    region_vertex_ids: Tuple[Tuple[int, ...], ...]
    region_minimum_squared_distances: Tuple[float, ...]
    anchor_region_indices: Tuple[int, ...]
    primary_vertex_ids: Tuple[int, ...]
    detached_vertex_ids: Tuple[int, ...]
    ambiguous_anchor_region_vertex_ids: Tuple[int, ...]

    @property
    def raw_vertex_count(self) -> int:
        return len(self.raw_vertex_ids)

    @property
    def region_count(self) -> int:
        return len(self.region_vertex_ids)

    @property
    def primary_is_unambiguous(self) -> bool:
        return len(self.anchor_region_indices) == 1

    @property
    def primary_vertex_count(self) -> int:
        return len(self.primary_vertex_ids)

    @property
    def detached_vertex_count(self) -> int:
        return len(self.detached_vertex_ids)


def build_vertex_adjacency(mesh_shape: str) -> Tuple[Tuple[int, ...], ...]:
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)
    adjacency = [set() for _ in range(int(mesh_fn.numVertices))]

    iterator = om.MItMeshEdge(dag_path)
    while not iterator.isDone():
        first = int(iterator.vertexId(0))
        second = int(iterator.vertexId(1))
        adjacency[first].add(second)
        adjacency[second].add(first)
        iterator.next()

    return tuple(tuple(sorted(neighbours)) for neighbours in adjacency)


def count_topology_components(adjacency: Tuple[Tuple[int, ...], ...]) -> int:
    unseen = set(range(len(adjacency)))
    count = 0
    while unseen:
        count += 1
        seed = min(unseen)
        unseen.remove(seed)
        stack = [seed]
        while stack:
            vertex_id = stack.pop()
            for neighbour in adjacency[vertex_id]:
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    stack.append(neighbour)
    return count


def partition_influence_ownership(
    distance_result: ExactDistanceRankingResult,
    owner_indices: np.ndarray,
    source_influence_index: int,
    adjacency: Tuple[Tuple[int, ...], ...],
) -> OwnershipConnectivityResult:
    source_index = int(source_influence_index)
    if source_index < 0 or source_index >= distance_result.influence_count:
        raise IndexError("source_influence_index is outside the influence list.")

    owners = np.asarray(owner_indices, dtype=np.int32)
    if owners.shape != (distance_result.vertex_count,):
        raise ValueError("owner_indices must contain one value per mesh vertex.")
    if len(adjacency) != distance_result.vertex_count:
        raise ValueError("Mesh adjacency does not match the vertex count.")

    raw_array = np.where(owners == source_index)[0].astype(np.int32)
    raw_vertex_ids = tuple(int(value) for value in raw_array.tolist())
    source_joint = distance_result.influences[source_index]

    if not raw_vertex_ids:
        return OwnershipConnectivityResult(
            mesh_shape=distance_result.mesh_shape,
            mesh_transform=distance_result.mesh_transform,
            influences=distance_result.influences,
            source_joint=source_joint,
            source_influence_index=source_index,
            raw_vertex_ids=tuple(),
            anchor_vertex_ids=tuple(),
            region_vertex_ids=tuple(),
            region_minimum_squared_distances=tuple(),
            anchor_region_indices=tuple(),
            primary_vertex_ids=tuple(),
            detached_vertex_ids=tuple(),
            ambiguous_anchor_region_vertex_ids=tuple(),
        )

    regions = _induced_connected_regions(raw_vertex_ids, adjacency)
    source_position = distance_result.influence_positions[source_index]
    raw_positions = distance_result.vertex_positions[raw_array]
    raw_delta = raw_positions - source_position[np.newaxis, :]
    raw_squared = np.einsum("vi,vi->v", raw_delta, raw_delta)
    exact_minimum = float(np.min(raw_squared))
    anchor_vertex_ids = tuple(
        int(value)
        for value in raw_array[raw_squared == exact_minimum].tolist()
    )

    region_index_by_vertex = {}
    region_minima = []
    for region_index, region in enumerate(regions):
        for vertex_id in region:
            region_index_by_vertex[int(vertex_id)] = int(region_index)

        region_array = np.asarray(region, dtype=np.int32)
        region_positions = distance_result.vertex_positions[region_array]
        region_delta = region_positions - source_position[np.newaxis, :]
        region_squared = np.einsum("vi,vi->v", region_delta, region_delta)
        region_minima.append(float(np.min(region_squared)))

    anchor_region_indices = tuple(
        sorted(
            {
                region_index_by_vertex[int(vertex_id)]
                for vertex_id in anchor_vertex_ids
            }
        )
    )
    anchor_region_set = set(anchor_region_indices)

    if len(anchor_region_indices) == 1:
        primary_vertex_ids = regions[anchor_region_indices[0]]
        ambiguous_anchor_vertex_ids = tuple()
    else:
        primary_vertex_ids = tuple()
        ambiguous_anchor_vertex_ids = tuple(
            sorted(
                vertex_id
                for region_index in anchor_region_indices
                for vertex_id in regions[region_index]
            )
        )

    detached_vertex_ids = tuple(
        sorted(
            vertex_id
            for region_index, region in enumerate(regions)
            if region_index not in anchor_region_set
            for vertex_id in region
        )
    )

    return OwnershipConnectivityResult(
        mesh_shape=distance_result.mesh_shape,
        mesh_transform=distance_result.mesh_transform,
        influences=distance_result.influences,
        source_joint=source_joint,
        source_influence_index=source_index,
        raw_vertex_ids=raw_vertex_ids,
        anchor_vertex_ids=anchor_vertex_ids,
        region_vertex_ids=regions,
        region_minimum_squared_distances=tuple(region_minima),
        anchor_region_indices=anchor_region_indices,
        primary_vertex_ids=primary_vertex_ids,
        detached_vertex_ids=detached_vertex_ids,
        ambiguous_anchor_region_vertex_ids=ambiguous_anchor_vertex_ids,
    )


def probe_source_joint_ownership_connectivity(
    distance_result: ExactDistanceRankingResult,
    source_joint: str,
) -> OwnershipConnectivityResult:
    source_path = _resolve_result_joint(distance_result, source_joint)
    source_index = distance_result.influences.index(source_path)
    adjacency = build_vertex_adjacency(distance_result.mesh_shape)
    return partition_influence_ownership(
        distance_result,
        distance_result.nearest_influence_indices,
        source_index,
        adjacency,
    )


def select_connectivity_vertices(
    result: OwnershipConnectivityResult,
    category: str = "detached",
    region_index: int = -1,
) -> None:
    category = str(category).lower()
    if category == "raw":
        vertex_ids = result.raw_vertex_ids
    elif category == "anchors":
        vertex_ids = result.anchor_vertex_ids
    elif category == "primary":
        vertex_ids = result.primary_vertex_ids
    elif category == "detached":
        vertex_ids = result.detached_vertex_ids
    elif category == "ambiguous":
        vertex_ids = result.ambiguous_anchor_region_vertex_ids
    elif category == "region":
        region_index = int(region_index)
        if region_index < 0 or region_index >= result.region_count:
            raise IndexError(
                "region_index {} is outside [0, {}).".format(
                    region_index,
                    result.region_count,
                )
            )
        vertex_ids = result.region_vertex_ids[region_index]
    else:
        raise ValueError(
            "category must be raw, anchors, primary, detached, ambiguous, or region."
        )

    components = [
        "{}.vtx[{}]".format(result.mesh_transform, int(vertex_id))
        for vertex_id in vertex_ids
    ]
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)


def _induced_connected_regions(raw_vertex_ids, adjacency):
    raw_set = set(int(value) for value in raw_vertex_ids)
    unseen = set(raw_set)
    regions = []

    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        stack = [seed]
        region = []

        while stack:
            vertex_id = stack.pop()
            region.append(vertex_id)
            for neighbour in reversed(adjacency[vertex_id]):
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    stack.append(neighbour)

        regions.append(tuple(sorted(region)))

    regions.sort(key=lambda values: values[0])
    return tuple(regions)


def _resolve_result_joint(result, joint):
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Joint does not exist:\n{}".format(joint))

    path = matches[0]
    if path not in result.influences:
        raise RuntimeError(
            "Selected joint was not part of the distance result:\n{}".format(path)
        )
    return path
