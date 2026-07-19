"""Assign post-v3.10J ambiguous islands to a neighbouring closed-loop owner.

The production Region solver, v3.10D, and v3.10J remain unchanged. This layer
starts from the v3.10J corrected owner map, finds non-anchor AMBIGUOUS connected
regions through the existing connectivity/facing logic, and scores neighbouring
owners only through boundary edges whose outside vertex belongs to a final
single-owner closed Maya edge loop.

A unique highest positive score assigns the whole ambiguous island. Zero support
or an exact score tie preserves the current owner. No joint names, hierarchy,
body-part rules, or distance fallback are used.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np

from ad_skin_tools.region import closed_loop_consensus
from ad_skin_tools.region import facing
from ad_skin_tools.region.closed_loop_opposite_guard import (
    OppositeGuardConsensusResult,
)
from ad_skin_tools.region.connectivity import (
    build_vertex_adjacency,
    partition_influence_ownership,
)
from ad_skin_tools.region.facing import (
    build_facing_mesh_context,
    classify_region_facing,
)
from ad_skin_tools.region.solver import RegionOwnershipResult


ASSIGNED = "assigned"
PRESERVED_NO_LOOP_SUPPORT = "preserved_no_loop_support"
PRESERVED_SCORE_TIE = "preserved_score_tie"


@dataclass(frozen=True)
class NeighbourLoopSupport:
    owner_index: int
    boundary_edge_count: int
    loop_supported_edge_count: int
    supporting_loop_indices: Tuple[int, ...]


@dataclass(frozen=True)
class AmbiguousLoopNeighbourDiagnostic:
    source_owner_index: int
    source_region_index: int
    vertex_ids: Tuple[int, ...]
    neighbour_supports: Tuple[NeighbourLoopSupport, ...]
    target_owner_index: int
    classification: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class AmbiguousLoopNeighbourResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    original_owner_indices: np.ndarray
    corrected_owner_indices: np.ndarray
    diagnostics: Tuple[AmbiguousLoopNeighbourDiagnostic, ...]
    final_validation: closed_loop_consensus.CorrectedOwnerValidationResult
    changed_vertex_ids: Tuple[int, ...]

    @property
    def ambiguous_region_count(self) -> int:
        return len(self.diagnostics)

    @property
    def assigned_region_count(self) -> int:
        return sum(
            diagnostic.classification == ASSIGNED
            for diagnostic in self.diagnostics
        )

    @property
    def changed_vertex_count(self) -> int:
        return len(self.changed_vertex_ids)


def solve_ambiguous_loop_neighbours(
    region_result: RegionOwnershipResult,
    guarded_result: OppositeGuardConsensusResult,
) -> AmbiguousLoopNeighbourResult:
    """Assign ambiguous islands from unique closed-loop boundary support."""

    if guarded_result.mesh_shape != region_result.mesh_shape:
        raise RuntimeError("Region and v3.10J results refer to different meshes.")
    if guarded_result.influences != region_result.influences:
        raise RuntimeError("Region and v3.10J influence lists differ.")

    original = np.asarray(
        guarded_result.corrected_owner_indices,
        dtype=np.int32,
    )
    if original.shape != (region_result.vertex_count,):
        raise ValueError("v3.10J owner map must contain one owner per vertex.")

    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    facing_context = build_facing_mesh_context(region_result.mesh_shape)
    vertex_loop_support = _single_owner_loop_support(
        guarded_result,
        original,
        region_result.vertex_count,
    )

    diagnostics = []
    for source_index in range(region_result.influence_count):
        connectivity = partition_influence_ownership(
            region_result.distance_result,
            original,
            source_index,
            adjacency,
        )
        facing_result = classify_region_facing(
            region_result.distance_result,
            connectivity,
            facing_context,
        )

        for region_diagnostic in facing_result.diagnostics:
            if region_diagnostic.classification != facing.AMBIGUOUS:
                continue

            supports = _boundary_loop_supports(
                source_owner_index=int(source_index),
                vertex_ids=region_diagnostic.vertex_ids,
                owner_indices=original,
                adjacency=adjacency,
                vertex_loop_support=vertex_loop_support,
            )
            target_owner, classification = _choose_target(supports)
            diagnostics.append(
                AmbiguousLoopNeighbourDiagnostic(
                    source_owner_index=int(source_index),
                    source_region_index=int(region_diagnostic.region_index),
                    vertex_ids=tuple(
                        int(value) for value in region_diagnostic.vertex_ids
                    ),
                    neighbour_supports=supports,
                    target_owner_index=int(target_owner),
                    classification=classification,
                )
            )

    corrected = original.copy()
    for diagnostic in diagnostics:
        if diagnostic.classification != ASSIGNED:
            continue
        corrected[
            np.asarray(diagnostic.vertex_ids, dtype=np.int32)
        ] = int(diagnostic.target_owner_index)

    changed_vertex_ids = tuple(
        np.where(corrected != original)[0].astype(np.int32).tolist()
    )
    validation = closed_loop_consensus.validate_corrected_owner_map(
        region_result,
        corrected,
    )

    return AmbiguousLoopNeighbourResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        original_owner_indices=original.copy(),
        corrected_owner_indices=corrected,
        diagnostics=tuple(diagnostics),
        final_validation=validation,
        changed_vertex_ids=changed_vertex_ids,
    )


def _single_owner_loop_support(guarded_result, owner_indices, vertex_count):
    support = [dict() for _ in range(int(vertex_count))]

    for loop_index, diagnostic in enumerate(guarded_result.diagnostics):
        loop_vertices = np.asarray(diagnostic.vertex_ids, dtype=np.int32)
        final_owners = np.unique(owner_indices[loop_vertices])
        if final_owners.size != 1:
            continue

        owner_index = int(final_owners[0])
        for vertex_id in diagnostic.vertex_ids:
            support[int(vertex_id)].setdefault(owner_index, set()).add(
                int(loop_index)
            )

    return support


def _boundary_loop_supports(
    source_owner_index,
    vertex_ids,
    owner_indices,
    adjacency,
    vertex_loop_support,
):
    region_set = set(int(value) for value in vertex_ids)
    boundary_counts = {}
    supported_counts = {}
    loops_by_owner = {}

    for vertex_id in region_set:
        for neighbour_id in adjacency[int(vertex_id)]:
            neighbour_id = int(neighbour_id)
            if neighbour_id in region_set:
                continue

            neighbour_owner = int(owner_indices[neighbour_id])
            if neighbour_owner == int(source_owner_index):
                continue

            boundary_counts[neighbour_owner] = (
                boundary_counts.get(neighbour_owner, 0) + 1
            )
            loop_ids = vertex_loop_support[neighbour_id].get(
                neighbour_owner,
                set(),
            )
            if loop_ids:
                supported_counts[neighbour_owner] = (
                    supported_counts.get(neighbour_owner, 0) + 1
                )
                loops_by_owner.setdefault(neighbour_owner, set()).update(loop_ids)

    return tuple(
        NeighbourLoopSupport(
            owner_index=int(owner_index),
            boundary_edge_count=int(boundary_counts[owner_index]),
            loop_supported_edge_count=int(supported_counts.get(owner_index, 0)),
            supporting_loop_indices=tuple(
                sorted(int(value) for value in loops_by_owner.get(owner_index, set()))
            ),
        )
        for owner_index in sorted(boundary_counts)
    )


def _choose_target(supports):
    if not supports:
        return -1, PRESERVED_NO_LOOP_SUPPORT

    maximum = max(
        int(support.loop_supported_edge_count)
        for support in supports
    )
    if maximum <= 0:
        return -1, PRESERVED_NO_LOOP_SUPPORT

    winners = [
        support
        for support in supports
        if int(support.loop_supported_edge_count) == maximum
    ]
    if len(winners) != 1:
        return -1, PRESERVED_SCORE_TIE

    return int(winners[0].owner_index), ASSIGNED
