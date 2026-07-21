"""Production hard ownership resolution for AD Skin Tool Region.

Detached vertices advance monotonically to their next exact distance candidate.
Exact closest distance ties are completed from topology before connectivity and
facing are evaluated. If a vertex exhausts every candidate, direct topology
neighbours provide a pragmatic fallback owner instead of aborting the bind.
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
from ad_skin_tools.region.exact_tie import (
    ExactTieResolutionResult,
    resolve_exact_distance_ties,
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
    neighbour_fallback_vertex_ids: Tuple[int, ...]
    final_squared_distances: np.ndarray
    diagnostics: Tuple[InfluenceRegionResolution, ...]
    distance_result: ExactDistanceRankingResult
    exact_tie_result: ExactTieResolutionResult
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
    def neighbour_fallback_vertex_count(self) -> int:
        return len(self.neighbour_fallback_vertex_ids)

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
    tables = build_exact_distance_tables(
        distance_result,
        distance_chunk_size=int(distance_chunk_size),
    )
    adjacency = build_vertex_adjacency(distance_result.mesh_shape)
    exact_tie_result = resolve_exact_distance_ties(
        distance_result=distance_result,
        distance_tables=tables,
        adjacency=adjacency,
    )
    owners = np.asarray(
        exact_tie_result.owner_indices,
        dtype=np.int32,
    ).copy()
    candidate_ranks = np.asarray(
        exact_tie_result.candidate_ranks,
        dtype=np.int32,
    ).copy()

    facing_context = build_facing_mesh_context(distance_result.mesh_shape)
    topology_component_count = count_topology_components(adjacency)

    resolution_pass_count = 0
    reassigned_vertex_ids = set()
    neighbour_fallback_vertex_ids = set()
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

            detached_vertex_ids.update(
                int(vertex_id)
                for vertex_id in facing.detached_vertex_ids
                if int(vertex_id) not in neighbour_fallback_vertex_ids
            )
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
            exhausted_vertex_ids = _advance_detached_vertices(
                tuple(sorted(detached_vertex_ids)),
                owners,
                candidate_ranks,
                tables.influence_indices,
                tables.squared_distances,
                distance_result.influences,
            )
            if exhausted_vertex_ids:
                assignments = _resolve_exhausted_vertices_from_neighbours(
                    vertex_ids=exhausted_vertex_ids,
                    owners=owners,
                    candidate_ranks=candidate_ranks,
                    adjacency=adjacency,
                    ranked_influences=tables.influence_indices,
                    ranked_squared=tables.squared_distances,
                )
                for vertex_id, owner_index, candidate_rank in assignments:
                    owners[int(vertex_id)] = int(owner_index)
                    candidate_ranks[int(vertex_id)] = int(candidate_rank)
                neighbour_fallback_vertex_ids.update(exhausted_vertex_ids)

            reassigned_vertex_ids.update(detached_vertex_ids)
            continue

        if ambiguous_vertex_ids:
            # Preserve the current candidate owners and hand unresolved facing
            # ambiguity to the downstream closed-loop and distance resolver.
            break
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
        neighbour_fallback_vertex_ids=tuple(
            sorted(neighbour_fallback_vertex_ids)
        ),
        final_squared_distances=final_squared,
        diagnostics=final_diagnostics,
        distance_result=distance_result,
        exact_tie_result=exact_tie_result,
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
    exhausted_vertex_ids = []

    for vertex_id in vertex_ids:
        current_rank = int(candidate_ranks[vertex_id])
        next_rank = current_rank + 1
        if next_rank >= influence_count:
            exhausted_vertex_ids.append(int(vertex_id))
            continue

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

    return tuple(exhausted_vertex_ids)


def _resolve_exhausted_vertices_from_neighbours(
    vertex_ids,
    owners,
    candidate_ranks,
    adjacency,
    ranked_influences,
    ranked_squared,
):
    exhausted_set = {int(vertex_id) for vertex_id in vertex_ids}
    source_owners = np.asarray(owners, dtype=np.int32).copy()
    assignments = []

    for vertex_id in sorted(exhausted_set):
        neighbours = tuple(int(value) for value in adjacency[int(vertex_id)])
        if not neighbours:
            raise RuntimeError(
                "Vertex {} exhausted every supplied joint candidate and has no "
                "connected vertex neighbour for fallback ownership.".format(
                    vertex_id
                )
            )

        voting_neighbours = tuple(
            neighbour_id
            for neighbour_id in neighbours
            if neighbour_id not in exhausted_set
        )
        if not voting_neighbours:
            voting_neighbours = neighbours

        neighbour_owners = source_owners[
            np.asarray(voting_neighbours, dtype=np.int32)
        ]
        owner_indices, owner_counts = np.unique(
            neighbour_owners,
            return_counts=True,
        )
        maximum_count = int(np.max(owner_counts))
        tied_owners = owner_indices[owner_counts == maximum_count]

        ranked_candidates = []
        for owner_index in tied_owners.tolist():
            candidate_rank = _candidate_rank_for_owner(
                vertex_id=vertex_id,
                owner_index=int(owner_index),
                ranked_influences=ranked_influences,
            )
            ranked_candidates.append(
                (
                    float(ranked_squared[vertex_id, candidate_rank]),
                    int(owner_index),
                    int(candidate_rank),
                )
            )

        ranked_candidates.sort(key=lambda value: (value[0], value[1]))
        _, owner_index, candidate_rank = ranked_candidates[0]
        assignments.append(
            (int(vertex_id), int(owner_index), int(candidate_rank))
        )

    return tuple(assignments)


def _candidate_rank_for_owner(
    vertex_id,
    owner_index,
    ranked_influences,
):
    matches = np.where(
        np.asarray(ranked_influences[int(vertex_id)], dtype=np.int32)
        == int(owner_index)
    )[0]
    if matches.size != 1:
        raise RuntimeError(
            "Region distance table does not contain one unique rank for owner {} "
            "at vertex {}.".format(owner_index, vertex_id)
        )
    return int(matches[0])


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
