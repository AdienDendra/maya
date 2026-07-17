"""Production hard-ownership resolution for AD Skin Tool Region.

Detached vertices advance monotonically to their next exact distance candidate.
Exact ties and ambiguous region-facing results are reported rather than broken
by naming, selection order, region size, or an arbitrary iteration limit.
"""

from dataclasses import dataclass, replace
from typing import Dict, Sequence, Tuple
import time

import numpy as np

from ad_skin_tools.region.connectivity import (
    build_vertex_adjacency,
    count_topology_components,
    partition_influence_ownership,
)
from ad_skin_tools.region.distance_ranking import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    ExactDistanceRankingResult,
    build_exact_distance_tables,
    solve_exact_distance_ranking,
)
from ad_skin_tools.region.facing import (
    build_facing_mesh_context,
    classify_region_facing,
)
from ad_skin_tools.region.maya_scene import collect_distance_input


@dataclass(frozen=True)
class InfluenceRegionResolution:
    joint: str
    ownership_count: int
    connected_region_count: int
    primary_region_count: int
    co_primary_region_count: int
    detached_region_count: int
    ambiguous_region_count: int


@dataclass(frozen=True)
class RegionOwnershipResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    vertex_positions: np.ndarray
    influence_positions: np.ndarray
    owner_indices: np.ndarray
    owner_vertex_ids: Dict[str, Tuple[int, ...]]
    ownership_counts: Dict[str, int]
    topology_component_count: int
    resolution_pass_count: int
    reassigned_vertex_ids: Tuple[int, ...]
    final_squared_distances: np.ndarray
    diagnostics: Tuple[InfluenceRegionResolution, ...]
    distance_result: ExactDistanceRankingResult
    elapsed_seconds: float

    @property
    def vertex_count(self) -> int:
        return int(self.owner_indices.size)

    @property
    def influence_count(self) -> int:
        return len(self.influences)

    @property
    def reassigned_vertex_count(self) -> int:
        return len(self.reassigned_vertex_ids)

    @property
    def primary_region_count(self) -> int:
        return sum(item.primary_region_count for item in self.diagnostics)

    @property
    def co_primary_region_count(self) -> int:
        return sum(item.co_primary_region_count for item in self.diagnostics)


def solve_region_ownership(
    mesh: str,
    joints: Sequence[str],
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> RegionOwnershipResult:
    started = time.perf_counter()
    if int(distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")

    scene_input = collect_distance_input(mesh=mesh, joints=joints)
    distance_result = solve_exact_distance_ranking(
        scene_input,
        distance_chunk_size=int(distance_chunk_size),
    )
    if distance_result.exact_tie_vertex_ids:
        raise RuntimeError(_minimum_tie_message(distance_result))

    tables = build_exact_distance_tables(
        distance_result,
        distance_chunk_size=int(distance_chunk_size),
    )
    owners = tables.influence_indices[:, 0].astype(np.int32).copy()
    candidate_ranks = np.zeros(distance_result.vertex_count, dtype=np.int32)
    adjacency = build_vertex_adjacency(distance_result.mesh_shape)
    facing_context = build_facing_mesh_context(distance_result.mesh_shape)
    topology_component_count = count_topology_components(adjacency)

    resolution_pass_count = 0
    reassigned_vertex_ids = set()
    final_diagnostics = tuple()

    while True:
        resolution_pass_count += 1
        iteration_result = _distance_result_with_owners(distance_result, owners)
        detached_vertex_ids = set()
        ambiguous_vertex_ids = set()
        diagnostics = []

        for source_index, joint in enumerate(distance_result.influences):
            connectivity = partition_influence_ownership(
                iteration_result,
                owners,
                source_index,
                adjacency,
            )
            facing = classify_region_facing(
                iteration_result,
                connectivity,
                facing_context,
            )

            detached_vertex_ids.update(facing.detached_vertex_ids)
            ambiguous_vertex_ids.update(facing.ambiguous_vertex_ids)
            diagnostics.append(
                InfluenceRegionResolution(
                    joint=joint,
                    ownership_count=connectivity.raw_vertex_count,
                    connected_region_count=connectivity.region_count,
                    primary_region_count=len(facing.primary_region_indices),
                    co_primary_region_count=len(facing.co_primary_region_indices),
                    detached_region_count=len(facing.detached_region_indices),
                    ambiguous_region_count=len(facing.ambiguous_region_indices),
                )
            )

        final_diagnostics = tuple(diagnostics)
        if detached_vertex_ids:
            _advance_detached_vertices(
                tuple(sorted(detached_vertex_ids)),
                owners,
                candidate_ranks,
                tables.influence_indices,
                tables.squared_distances,
                distance_result.influences,
            )
            reassigned_vertex_ids.update(detached_vertex_ids)
            continue

        if ambiguous_vertex_ids:
            raise RuntimeError(
                "Region ownership is geometrically underdetermined.\n\n"
                "Ambiguous vertices: {}\nFirst IDs: {}\n\n"
                "No arbitrary normal threshold, region-size rule, or joint-name "
                "tie breaker was applied.".format(
                    len(ambiguous_vertex_ids),
                    sorted(ambiguous_vertex_ids)[:20],
                )
            )
        break

    owner_vertex_ids = _build_owner_vertex_map(owners, distance_result.influences)
    ownership_counts = {
        joint: len(owner_vertex_ids[joint])
        for joint in distance_result.influences
    }
    vertex_rows = np.arange(distance_result.vertex_count, dtype=np.int32)
    final_squared = tables.squared_distances[
        vertex_rows,
        candidate_ranks,
    ].astype(np.float64)

    return RegionOwnershipResult(
        mesh_shape=distance_result.mesh_shape,
        mesh_transform=distance_result.mesh_transform,
        influences=distance_result.influences,
        vertex_positions=distance_result.vertex_positions,
        influence_positions=distance_result.influence_positions,
        owner_indices=owners,
        owner_vertex_ids=owner_vertex_ids,
        ownership_counts=ownership_counts,
        topology_component_count=topology_component_count,
        resolution_pass_count=resolution_pass_count,
        reassigned_vertex_ids=tuple(sorted(reassigned_vertex_ids)),
        final_squared_distances=final_squared,
        diagnostics=final_diagnostics,
        distance_result=distance_result,
        elapsed_seconds=time.perf_counter() - started,
    )


def _advance_detached_vertices(
    vertex_ids,
    owners,
    candidate_ranks,
    ranked_influences,
    ranked_squared,
    influences,
):
    influence_count = int(ranked_influences.shape[1])

    for vertex_id in vertex_ids:
        current_rank = int(candidate_ranks[vertex_id])
        next_rank = current_rank + 1
        if next_rank >= influence_count:
            raise RuntimeError(
                "Vertex {} exhausted every supplied joint candidate after its "
                "regions were rejected.".format(vertex_id)
            )

        next_squared = float(ranked_squared[vertex_id, next_rank])
        group_stop = next_rank + 1
        while (
            group_stop < influence_count
            and float(ranked_squared[vertex_id, group_stop]) == next_squared
        ):
            group_stop += 1

        if group_stop - next_rank != 1:
            tied_indices = ranked_influences[
                vertex_id,
                next_rank:group_stop,
            ].tolist()
            tied_joints = [influences[int(index)] for index in tied_indices]
            raise RuntimeError(
                "Vertex {} reached an exact distance tie while searching for a "
                "replacement owner.\n\nCandidates:\n{}\n\n"
                "Selection order and joint names were not used to break the tie."
                .format(vertex_id, "\n".join(tied_joints))
            )

        candidate_ranks[vertex_id] = int(next_rank)
        owners[vertex_id] = int(ranked_influences[vertex_id, next_rank])


def _distance_result_with_owners(result, owners):
    counts = {
        joint: int(np.count_nonzero(owners == influence_index))
        for influence_index, joint in enumerate(result.influences)
    }
    return replace(
        result,
        nearest_influence_indices=np.asarray(owners, dtype=np.int32).copy(),
        unique_assignment_counts=counts,
    )


def _build_owner_vertex_map(owner_indices, influences):
    return {
        joint: tuple(
            np.where(owner_indices == influence_index)[0]
            .astype(np.int32)
            .tolist()
        )
        for influence_index, joint in enumerate(influences)
    }


def _minimum_tie_message(result):
    coincident = "None"
    if result.coincident_influence_groups:
        coincident = "\n".join(
            " | ".join(group) for group in result.coincident_influence_groups
        )

    return (
        "Exact closest-distance ownership is underdetermined.\n\n"
        "Exact-tie vertices: {}\nFirst IDs: {}\n\n"
        "Exactly coincident joint groups:\n{}\n\n"
        "Selection order and joint names were not used to invent an owner."
    ).format(
        len(result.exact_tie_vertex_ids),
        list(result.exact_tie_vertex_ids[:20]),
        coincident,
    )
