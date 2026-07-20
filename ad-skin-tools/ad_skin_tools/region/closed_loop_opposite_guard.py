"""Closed-loop consensus with an opposite-joint guard.

This wrapper reuses the accepted loop discovery and diagnostics, rebuilds the
owner map from the production Region result, and preserves any two-owner loop
whose owners form a supported opposite pair on the joint set's primary symmetry
axis.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from ad_skin_tools.core import opposite_axis
from ad_skin_tools.region import closed_loop_consensus
from ad_skin_tools.region.solver import RegionOwnershipResult


SINGLE_OWNER = "single_owner"
TWO_OWNER_PROPOSAL = "two_owner_proposal"
OPPOSITE_PAIR_PRESERVED = "opposite_pair_preserved"
MULTI_OWNER_IGNORED = "multi_owner_ignored"
EXACT_COST_TIE = "exact_cost_tie"


@dataclass(frozen=True)
class OppositeGuardLoopDiagnostic:
    loop_index: int
    edge_ids: Tuple[int, ...]
    vertex_ids: Tuple[int, ...]
    owner_indices: Tuple[int, ...]
    owner_counts: Tuple[int, ...]
    aggregate_squared_costs: Tuple[float, ...]
    proposed_owner_index: int
    opposite_axis: Optional[str]
    classification: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class OppositeGuardConsensusResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    original_owner_indices: np.ndarray
    corrected_owner_indices: np.ndarray
    axis_context: opposite_axis.OppositeAxisContext
    diagnostics: Tuple[OppositeGuardLoopDiagnostic, ...]
    open_loop_count: int
    unresolved_seed_edge_ids: Tuple[int, ...]
    applied_loop_indices: Tuple[int, ...]
    conflict_loop_indices: Tuple[int, ...]
    conflicting_vertex_ids: Tuple[int, ...]
    changed_vertex_ids: Tuple[int, ...]

    @property
    def closed_loop_count(self) -> int:
        return len(self.diagnostics)

    @property
    def applied_loop_count(self) -> int:
        return len(self.applied_loop_indices)

    @property
    def conflict_loop_count(self) -> int:
        return len(self.conflict_loop_indices)

    @property
    def changed_vertex_count(self) -> int:
        return len(self.changed_vertex_ids)


def solve_closed_loop_opposite_guard(
    region_result: RegionOwnershipResult,
) -> OppositeGuardConsensusResult:
    """Apply closed-loop consensus except for supported opposite-owner pairs."""

    base_result = closed_loop_consensus.solve_closed_loop_consensus(region_result)
    axis_context = opposite_axis.build_opposite_axis_context(
        region_result.influence_positions
    )
    original = np.asarray(region_result.owner_indices, dtype=np.int32)

    diagnostics = []
    proposals_by_vertex = {}

    for loop_index, base in enumerate(base_result.diagnostics):
        proposed_owner = -1
        detected_axis = None

        if base.classification == closed_loop_consensus.SINGLE_OWNER:
            classification = SINGLE_OWNER

        elif base.classification == closed_loop_consensus.MULTI_OWNER_IGNORED:
            classification = MULTI_OWNER_IGNORED

        elif base.classification == closed_loop_consensus.EXACT_COST_TIE:
            classification = EXACT_COST_TIE

        elif base.classification == closed_loop_consensus.TWO_OWNER_PROPOSAL:
            first_owner, second_owner = base.owner_indices
            detected_axis = opposite_axis.detect_opposite_axis(
                first_owner,
                second_owner,
                axis_context,
            )
            if detected_axis is not None:
                classification = OPPOSITE_PAIR_PRESERVED
            else:
                classification = TWO_OWNER_PROPOSAL
                proposed_owner = int(base.proposed_owner_index)
                for vertex_id in base.vertex_ids:
                    proposals_by_vertex.setdefault(int(vertex_id), set()).add(
                        proposed_owner
                    )
        else:
            raise RuntimeError(
                "Unsupported closed-loop classification: {}".format(
                    base.classification
                )
            )

        diagnostics.append(
            OppositeGuardLoopDiagnostic(
                loop_index=int(loop_index),
                edge_ids=base.edge_ids,
                vertex_ids=base.vertex_ids,
                owner_indices=base.owner_indices,
                owner_counts=base.owner_counts,
                aggregate_squared_costs=base.aggregate_squared_costs,
                proposed_owner_index=int(proposed_owner),
                opposite_axis=detected_axis,
                classification=classification,
            )
        )

    conflicting_vertex_ids = tuple(
        sorted(
            vertex_id
            for vertex_id, proposals in proposals_by_vertex.items()
            if len(proposals) > 1
        )
    )
    conflicting_set = set(conflicting_vertex_ids)

    corrected = original.copy()
    applied_loop_indices = []
    conflict_loop_indices = []

    for diagnostic in diagnostics:
        if diagnostic.classification != TWO_OWNER_PROPOSAL:
            continue
        if any(
            vertex_id in conflicting_set
            for vertex_id in diagnostic.vertex_ids
        ):
            conflict_loop_indices.append(int(diagnostic.loop_index))
            continue

        corrected[
            np.asarray(diagnostic.vertex_ids, dtype=np.int32)
        ] = int(diagnostic.proposed_owner_index)
        applied_loop_indices.append(int(diagnostic.loop_index))

    changed_vertex_ids = tuple(
        np.where(corrected != original)[0].astype(np.int32).tolist()
    )

    return OppositeGuardConsensusResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        original_owner_indices=original.copy(),
        corrected_owner_indices=corrected,
        axis_context=axis_context,
        diagnostics=tuple(diagnostics),
        open_loop_count=int(base_result.open_loop_count),
        unresolved_seed_edge_ids=base_result.unresolved_seed_edge_ids,
        applied_loop_indices=tuple(applied_loop_indices),
        conflict_loop_indices=tuple(conflict_loop_indices),
        conflicting_vertex_ids=conflicting_vertex_ids,
        changed_vertex_ids=changed_vertex_ids,
    )
