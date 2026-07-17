"""Raw-ownership connectivity smoke probe for AD Skin Tool v3.3.

The accepted v3.0 exact-distance result remains unchanged. For one selected
influence, this experiment builds the graph induced only by vertices raw-owned
by that influence, finds its exact connected regions, and identifies the region
containing the exact-nearest raw vertex as the primary region.

No visibility ray, vertex normal, joint hierarchy, naming convention, region
size threshold, replacement owner, or skinCluster write is used.
"""

from dataclasses import dataclass
from typing import Tuple
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from ad_skin_tools.v3.distance_ranking import ExactDistanceRankingResult


@dataclass(frozen=True)
class OwnershipConnectivityProbeResult:
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
    elapsed_seconds: float

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



def probe_source_joint_ownership_connectivity(
    distance_result: ExactDistanceRankingResult,
    source_joint: str,
) -> OwnershipConnectivityProbeResult:
    """Partition one influence's raw v3.0 ownership into connected regions."""

    started = time.perf_counter()
    _validate_scene_state(distance_result)

    source_path = _resolve_result_joint(distance_result, source_joint)
    source_index = distance_result.influences.index(source_path)
    raw_array = np.where(
        distance_result.nearest_influence_indices == source_index
    )[0].astype(np.int32)
    raw_vertex_ids = tuple(int(value) for value in raw_array.tolist())

    if not raw_vertex_ids:
        return OwnershipConnectivityProbeResult(
            mesh_shape=distance_result.mesh_shape,
            mesh_transform=distance_result.mesh_transform,
            influences=distance_result.influences,
            source_joint=source_path,
            source_influence_index=source_index,
            raw_vertex_ids=tuple(),
            anchor_vertex_ids=tuple(),
            region_vertex_ids=tuple(),
            region_minimum_squared_distances=tuple(),
            anchor_region_indices=tuple(),
            primary_vertex_ids=tuple(),
            detached_vertex_ids=tuple(),
            ambiguous_anchor_region_vertex_ids=tuple(),
            elapsed_seconds=time.perf_counter() - started,
        )

    regions = _induced_connected_regions(
        mesh_shape=distance_result.mesh_shape,
        raw_vertex_ids=raw_vertex_ids,
    )

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

    return OwnershipConnectivityProbeResult(
        mesh_shape=distance_result.mesh_shape,
        mesh_transform=distance_result.mesh_transform,
        influences=distance_result.influences,
        source_joint=source_path,
        source_influence_index=source_index,
        raw_vertex_ids=raw_vertex_ids,
        anchor_vertex_ids=anchor_vertex_ids,
        region_vertex_ids=regions,
        region_minimum_squared_distances=tuple(region_minima),
        anchor_region_indices=anchor_region_indices,
        primary_vertex_ids=primary_vertex_ids,
        detached_vertex_ids=detached_vertex_ids,
        ambiguous_anchor_region_vertex_ids=ambiguous_anchor_vertex_ids,
        elapsed_seconds=time.perf_counter() - started,
    )



def select_probe_vertices(
    result: OwnershipConnectivityProbeResult,
    category: str = "detached",
    region_index: int = -1,
) -> None:
    """Select a diagnostic category or one exact connected region in Maya."""

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
            "category must be raw, anchors, primary, detached, ambiguous, "
            "or region."
        )

    components = [
        "{}.vtx[{}]".format(result.mesh_transform, int(vertex_id))
        for vertex_id in vertex_ids
    ]
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)



def _induced_connected_regions(
    mesh_shape: str,
    raw_vertex_ids: Tuple[int, ...],
) -> Tuple[Tuple[int, ...], ...]:
    raw_set = set(int(value) for value in raw_vertex_ids)
    adjacency = {vertex_id: [] for vertex_id in raw_set}

    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    edge_iterator = om.MItMeshEdge(dag_path)

    while not edge_iterator.isDone():
        first = int(edge_iterator.vertexId(0))
        second = int(edge_iterator.vertexId(1))
        if first in raw_set and second in raw_set:
            adjacency[first].append(second)
            adjacency[second].append(first)
        edge_iterator.next()

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
            for neighbour in sorted(adjacency[vertex_id], reverse=True):
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    stack.append(neighbour)

        regions.append(tuple(sorted(region)))

    regions.sort(key=lambda values: values[0])
    return tuple(regions)



def _resolve_result_joint(
    result: ExactDistanceRankingResult,
    joint: str,
) -> str:
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Joint does not exist:\n{}".format(joint))

    path = matches[0]
    if path not in result.influences:
        raise RuntimeError(
            "Selected joint was not part of the v3.0 distance result:\n{}".format(
                path
            )
        )
    return path



def _current_mesh_vertex_positions(mesh_shape: str) -> np.ndarray:
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)
    points = mesh_fn.getPoints(om.MSpace.kWorld)

    positions = np.empty((len(points), 3), dtype=np.float64)
    for index, point in enumerate(points):
        positions[index] = (point.x, point.y, point.z)
    return positions



def _validate_scene_state(result: ExactDistanceRankingResult) -> None:
    if not cmds.objExists(result.mesh_shape):
        raise RuntimeError("The v3.0 distance-result mesh no longer exists.")

    current_positions = _current_mesh_vertex_positions(result.mesh_shape)
    if not np.array_equal(current_positions, result.vertex_positions):
        raise RuntimeError(
            "Mesh vertex positions changed after v3.0. Run the distance test "
            "again."
        )

    current_joint_positions = np.empty_like(result.influence_positions)
    for index, joint in enumerate(result.influences):
        if not cmds.objExists(joint):
            raise RuntimeError(
                "A v3.0 distance-result joint no longer exists:\n{}".format(joint)
            )
        current_joint_positions[index] = cmds.xform(
            joint,
            query=True,
            worldSpace=True,
            translation=True,
        )

    if not np.array_equal(
        current_joint_positions,
        result.influence_positions,
    ):
        raise RuntimeError(
            "Joint positions changed after v3.0. Run the distance test again."
        )
