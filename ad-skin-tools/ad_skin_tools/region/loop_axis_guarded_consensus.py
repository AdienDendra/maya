"""Loop-axis guard for v3.10D closed-loop Region consensus.

This module leaves the production Region solver and v3.10D implementation
unchanged. It reuses v3.10D loop discovery/diagnostics, but rebuilds the owner
map from the original Region result and applies a two-owner loop only when the
joint-pair direction is more aligned with the loop's best-fit plane normal than
with the plane itself.

The guard prevents broad left/right loops from being collapsed into one side
while preserving cross-sectional limb loops whose joint direction passes
through the loop plane.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np

from ad_skin_tools.region import closed_loop_consensus
from ad_skin_tools.region.solver import RegionOwnershipResult


SINGLE_OWNER = "single_owner"
AXIAL_TWO_OWNER = "axial_two_owner"
NON_AXIAL_TWO_OWNER = "non_axial_two_owner"
DEGENERATE_TWO_OWNER = "degenerate_two_owner"
EXACT_COST_TIE = "exact_cost_tie"
MULTI_OWNER_IGNORED = "multi_owner_ignored"


@dataclass(frozen=True)
class LoopAxisDiagnostic:
    loop_index: int
    edge_ids: Tuple[int, ...]
    vertex_ids: Tuple[int, ...]
    owner_indices: Tuple[int, ...]
    owner_counts: Tuple[int, ...]
    aggregate_squared_costs: Tuple[float, ...]
    loop_normal: Tuple[float, float, float]
    joint_axis: Tuple[float, float, float]
    axis_alignment: float
    normal_component_squared: float
    plane_component_squared: float
    proposed_owner_index: int
    classification: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)

    @property
    def changed_vertex_count(self) -> int:
        if self.proposed_owner_index < 0:
            return 0
        return self.vertex_count - self.owner_counts[
            self.owner_indices.index(self.proposed_owner_index)
        ]


@dataclass(frozen=True)
class LoopAxisGuardedConsensusResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    original_owner_indices: np.ndarray
    corrected_owner_indices: np.ndarray
    diagnostics: Tuple[LoopAxisDiagnostic, ...]
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


def solve_loop_axis_guarded_consensus(
    region_result: RegionOwnershipResult,
) -> LoopAxisGuardedConsensusResult:
    """Apply v3.10D proposals only when the loop/joint geometry is axial."""

    base_result = closed_loop_consensus.solve_closed_loop_consensus(region_result)
    original = np.asarray(region_result.owner_indices, dtype=np.int32)
    diagnostics = []
    proposals_by_vertex = {}

    for loop_index, base in enumerate(base_result.diagnostics):
        loop_normal = (0.0, 0.0, 0.0)
        joint_axis = (0.0, 0.0, 0.0)
        axis_alignment = 0.0
        normal_component_squared = 0.0
        plane_component_squared = 0.0
        proposed_owner = -1

        if base.classification == closed_loop_consensus.SINGLE_OWNER:
            classification = SINGLE_OWNER

        elif base.classification == closed_loop_consensus.MULTI_OWNER_IGNORED:
            classification = MULTI_OWNER_IGNORED

        elif base.classification == closed_loop_consensus.EXACT_COST_TIE:
            classification = EXACT_COST_TIE

        elif base.classification == closed_loop_consensus.TWO_OWNER_PROPOSAL:
            geometry = _loop_axis_geometry(
                region_result=region_result,
                vertex_ids=base.vertex_ids,
                owner_indices=base.owner_indices,
            )

            if geometry is None:
                classification = DEGENERATE_TWO_OWNER
            else:
                (
                    loop_normal_array,
                    joint_axis_array,
                    axis_alignment,
                    normal_component_squared,
                    plane_component_squared,
                ) = geometry
                loop_normal = tuple(float(value) for value in loop_normal_array)
                joint_axis = tuple(float(value) for value in joint_axis_array)

                if normal_component_squared > plane_component_squared:
                    classification = AXIAL_TWO_OWNER
                    proposed_owner = int(base.proposed_owner_index)
                    for vertex_id in base.vertex_ids:
                        proposals_by_vertex.setdefault(int(vertex_id), set()).add(
                            proposed_owner
                        )
                else:
                    classification = NON_AXIAL_TWO_OWNER
        else:
            raise RuntimeError(
                "Unsupported v3.10D loop classification: {}".format(
                    base.classification
                )
            )

        diagnostics.append(
            LoopAxisDiagnostic(
                loop_index=int(loop_index),
                edge_ids=base.edge_ids,
                vertex_ids=base.vertex_ids,
                owner_indices=base.owner_indices,
                owner_counts=base.owner_counts,
                aggregate_squared_costs=base.aggregate_squared_costs,
                loop_normal=loop_normal,
                joint_axis=joint_axis,
                axis_alignment=float(axis_alignment),
                normal_component_squared=float(normal_component_squared),
                plane_component_squared=float(plane_component_squared),
                proposed_owner_index=int(proposed_owner),
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
        if diagnostic.classification != AXIAL_TWO_OWNER:
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

    return LoopAxisGuardedConsensusResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        original_owner_indices=original.copy(),
        corrected_owner_indices=corrected,
        diagnostics=tuple(diagnostics),
        open_loop_count=int(base_result.open_loop_count),
        unresolved_seed_edge_ids=base_result.unresolved_seed_edge_ids,
        applied_loop_indices=tuple(applied_loop_indices),
        conflict_loop_indices=tuple(conflict_loop_indices),
        conflicting_vertex_ids=conflicting_vertex_ids,
        changed_vertex_ids=changed_vertex_ids,
    )


def _loop_axis_geometry(region_result, vertex_ids, owner_indices):
    if len(owner_indices) != 2:
        raise ValueError("Loop-axis geometry requires exactly two owners.")

    loop_vertices = np.asarray(vertex_ids, dtype=np.int32)
    positions = np.asarray(
        region_result.vertex_positions[loop_vertices],
        dtype=np.float64,
    )
    centered = positions - np.mean(positions, axis=0, dtype=np.float64)
    covariance = centered.T @ centered

    if not np.all(np.isfinite(covariance)):
        return None

    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    if not np.all(np.isfinite(eigenvalues)):
        return None

    numerical_bound = float(
        np.finfo(np.float64).eps
        * max(1.0, float(np.max(np.abs(eigenvalues))))
    )
    if float(eigenvalues[1]) <= numerical_bound:
        return None

    loop_normal = np.asarray(eigenvectors[:, 0], dtype=np.float64)
    normal_length = float(np.linalg.norm(loop_normal))
    if normal_length == 0.0 or not np.isfinite(normal_length):
        return None
    loop_normal /= normal_length

    first_owner, second_owner = (int(value) for value in owner_indices)
    joint_axis = (
        np.asarray(
            region_result.influence_positions[second_owner],
            dtype=np.float64,
        )
        - np.asarray(
            region_result.influence_positions[first_owner],
            dtype=np.float64,
        )
    )
    axis_length = float(np.linalg.norm(joint_axis))
    if axis_length == 0.0 or not np.isfinite(axis_length):
        return None
    joint_axis /= axis_length

    alignment = abs(float(np.dot(joint_axis, loop_normal)))
    alignment = min(1.0, max(0.0, alignment))
    normal_component_squared = alignment * alignment
    plane_component_squared = max(0.0, 1.0 - normal_component_squared)

    return (
        loop_normal,
        joint_axis,
        alignment,
        normal_component_squared,
        plane_component_squared,
    )
