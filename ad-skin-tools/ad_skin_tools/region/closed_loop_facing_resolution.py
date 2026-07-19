"""Facing resolution after v3.10D closed-loop Region consensus.

This module keeps the production Region solver and v3.10D implementation intact.
It starts from the corrected hard owner map produced by closed-loop consensus,
then reruns the existing connectivity and facing logic. Detached regions advance
monotonically to their next exact joint-distance candidate, matching the
production Region policy. Ambiguous regions remain unresolved and prevent bind.
"""

from dataclasses import dataclass, replace
from typing import Dict, Tuple

import numpy as np

from ad_skin_tools.region.closed_loop_consensus import ClosedLoopConsensusResult
from ad_skin_tools.region.connectivity import (
    build_vertex_adjacency,
    partition_influence_ownership,
)
from ad_skin_tools.region.distance_ranking import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    build_exact_distance_tables,
)
from ad_skin_tools.region.facing import (
    build_facing_mesh_context,
    classify_region_facing,
)
from ad_skin_tools.region.solver import RegionOwnershipResult


@dataclass(frozen=True)
class FacingResolutionPass:
    pass_index: int
    connected_region_count: int
    primary_region_count: int
    co_primary_region_count: int
    detached_vertex_ids: Tuple[int, ...]
    ambiguous_vertex_ids: Tuple[int, ...]

    @property
    def detached_vertex_count(self) -> int:
        return len(self.detached_vertex_ids)

    @property
    def ambiguous_vertex_count(self) -> int:
        return len(self.ambiguous_vertex_ids)


@dataclass(frozen=True)
class ClosedLoopFacingResolutionResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    initial_owner_indices: np.ndarray
    final_owner_indices: np.ndarray
    owner_vertex_ids: Dict[str, Tuple[int, ...]]
    ownership_counts: Dict[str, int]
    candidate_ranks: np.ndarray
    final_squared_distances: np.ndarray
    passes: Tuple[FacingResolutionPass, ...]
    reassigned_vertex_ids: Tuple[int, ...]
    final_detached_vertex_ids: Tuple[int, ...]
    final_ambiguous_vertex_ids: Tuple[int, ...]

    @property
    def resolution_pass_count(self) -> int:
        return len(self.passes)

    @property
    def reassigned_vertex_count(self) -> int:
        return len(self.reassigned_vertex_ids)

    @property
    def is_resolved(self) -> bool:
        return not self.final_detached_vertex_ids and not self.final_ambiguous_vertex_ids


def resolve_closed_loop_facing(
    region_result: RegionOwnershipResult,
    consensus_result: ClosedLoopConsensusResult,
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> ClosedLoopFacingResolutionResult:
    """Resolve detached regions after v3.10D and leave ambiguity explicit."""

    _validate_inputs(region_result, consensus_result)
    if int(distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")

    distance_result = region_result.distance_result
    tables = build_exact_distance_tables(
        distance_result,
        distance_chunk_size=int(distance_chunk_size),
    )

    owners = np.asarray(
        consensus_result.corrected_owner_indices,
        dtype=np.int32,
    ).copy()
    initial_owners = owners.copy()
    candidate_ranks = _candidate_ranks_for_owners(
        owners,
        tables.influence_indices,
    )

    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    facing_context = build_facing_mesh_context(region_result.mesh_shape)

    passes = []
    reassigned_vertex_ids = set()
    final_detached = tuple()
    final_ambiguous = tuple()

    while True:
        pass_index = len(passes) + 1
        iteration_result = _distance_result_with_owners(
            distance_result,
            owners,
        )

        detached = set()
        ambiguous = set()
        connected_region_count = 0
        primary_region_count = 0
        co_primary_region_count = 0

        for source_index in range(region_result.influence_count):
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

            connected_region_count += connectivity.region_count
            primary_region_count += len(facing.primary_region_indices)
            co_primary_region_count += len(facing.co_primary_region_indices)
            detached.update(facing.detached_vertex_ids)
            ambiguous.update(facing.ambiguous_vertex_ids)

        final_detached = tuple(sorted(int(value) for value in detached))
        final_ambiguous = tuple(sorted(int(value) for value in ambiguous))
        passes.append(
            FacingResolutionPass(
                pass_index=int(pass_index),
                connected_region_count=int(connected_region_count),
                primary_region_count=int(primary_region_count),
                co_primary_region_count=int(co_primary_region_count),
                detached_vertex_ids=final_detached,
                ambiguous_vertex_ids=final_ambiguous,
            )
        )

        if final_detached:
            _advance_detached_vertices(
                vertex_ids=final_detached,
                owners=owners,
                candidate_ranks=candidate_ranks,
                ranked_influences=tables.influence_indices,
                ranked_squared=tables.squared_distances,
                influences=region_result.influences,
            )
            reassigned_vertex_ids.update(final_detached)
            continue

        break

    owner_vertex_ids = _build_owner_vertex_map(
        owners,
        region_result.influences,
    )
    ownership_counts = {
        joint: len(owner_vertex_ids[joint])
        for joint in region_result.influences
    }
    vertex_rows = np.arange(region_result.vertex_count, dtype=np.int32)
    final_squared = tables.squared_distances[
        vertex_rows,
        candidate_ranks,
    ].astype(np.float64)

    return ClosedLoopFacingResolutionResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        initial_owner_indices=initial_owners,
        final_owner_indices=owners,
        owner_vertex_ids=owner_vertex_ids,
        ownership_counts=ownership_counts,
        candidate_ranks=candidate_ranks.copy(),
        final_squared_distances=final_squared,
        passes=tuple(passes),
        reassigned_vertex_ids=tuple(sorted(reassigned_vertex_ids)),
        final_detached_vertex_ids=final_detached,
        final_ambiguous_vertex_ids=final_ambiguous,
    )


def _candidate_ranks_for_owners(owner_indices, ranked_influences):
    matches = ranked_influences == owner_indices[:, np.newaxis]
    match_counts = np.count_nonzero(matches, axis=1)
    if np.any(match_counts != 1):
        bad = np.where(match_counts != 1)[0][:20]
        raise RuntimeError(
            "Could not find exactly one distance rank for corrected owners. "
            "First vertex IDs: {}".format(bad.tolist())
        )
    return np.argmax(matches, axis=1).astype(np.int32)


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
        vertex_id = int(vertex_id)
        current_rank = int(candidate_ranks[vertex_id])
        next_rank = current_rank + 1
        if next_rank >= influence_count:
            raise RuntimeError(
                "Vertex {} exhausted every supplied joint candidate after its "
                "post-loop Region was rejected.".format(vertex_id)
            )

        next_squared = float(ranked_squared[vertex_id, next_rank])
        tie_start = next_rank
        while (
            tie_start > 0
            and float(ranked_squared[vertex_id, tie_start - 1]) == next_squared
        ):
            tie_start -= 1

        tie_stop = next_rank + 1
        while (
            tie_stop < influence_count
            and float(ranked_squared[vertex_id, tie_stop]) == next_squared
        ):
            tie_stop += 1

        if tie_stop - tie_start != 1:
            tied_indices = ranked_influences[
                vertex_id,
                tie_start:tie_stop,
            ].tolist()
            tied_joints = [influences[int(index)] for index in tied_indices]
            raise RuntimeError(
                "Vertex {} reached an exact distance tie while resolving a "
                "post-loop detached region.\n\nCandidates:\n{}\n\n"
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


def _validate_inputs(region_result, consensus_result):
    if consensus_result.mesh_shape != region_result.mesh_shape:
        raise RuntimeError("Region and closed-loop results use different meshes.")
    if consensus_result.mesh_transform != region_result.mesh_transform:
        raise RuntimeError("Region and closed-loop results use different transforms.")
    if consensus_result.influences != region_result.influences:
        raise RuntimeError("Region and closed-loop results use different influences.")

    corrected = np.asarray(consensus_result.corrected_owner_indices)
    if corrected.shape != (region_result.vertex_count,):
        raise ValueError(
            "Closed-loop corrected owners must contain one owner per vertex."
        )
    if np.any(corrected < 0) or np.any(corrected >= region_result.influence_count):
        raise ValueError("Closed-loop corrected owners contain invalid indices.")
