"""Smoothing pipeline for an already-final hard Region owner map.

The Region stage owns every blocking decision. This module accepts one final
owner index per vertex and never calls Region connectivity, facing, exact-tie,
or closed-loop correction logic. It only converts immutable hard ownership into
continuous, constrained skin weights.
"""

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

from ad_skin_tools.bind_smoothing.cutoff_projection import (
    GeometricMaxInfluenceResult,
    enforce_maximum_influences_by_geometry,
)
from ad_skin_tools.bind_smoothing.diffusion import (
    BindDiffusionResult,
    diffuse_hard_ownership,
)
from ad_skin_tools.bind_smoothing.final_constraints import (
    OwnerMaximumResult,
    project_region_owner_to_maximum,
)
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.validation import (
    BindWeightValidationResult,
    validate_bind_weights,
)


@dataclass(frozen=True)
class BindSmoothingResult:
    """Final weights and diagnostics derived from immutable blocking owners."""

    weights: np.ndarray
    blocking_owner_indices: np.ndarray
    options: BindSmoothingOptions
    effective_maximum_influences: int
    diffusion_result: BindDiffusionResult
    projection_result: GeometricMaxInfluenceResult
    owner_maximum_result: OwnerMaximumResult
    validation_result: BindWeightValidationResult

    @property
    def vertex_count(self) -> int:
        return int(self.weights.shape[0])

    @property
    def influence_count(self) -> int:
        return int(self.weights.shape[1])


def solve_bind_smoothing(
    owner_indices: np.ndarray,
    adjacency: Sequence[Sequence[int]],
    vertex_positions: np.ndarray,
    influence_positions: np.ndarray,
    options: Optional[BindSmoothingOptions] = None,
) -> BindSmoothingResult:
    """Smooth final v3.2 blocking ownership without recalculating ownership.

    ``owner_indices`` is the final blocking contract: exactly one valid influence
    index per vertex. The array is copied on entry and never modified.
    """

    options = (options or BindSmoothingOptions()).validated()
    owners, vertices, influences = _validate_final_blocking_input(
        owner_indices=owner_indices,
        adjacency=adjacency,
        vertex_positions=vertex_positions,
        influence_positions=influence_positions,
    )
    influence_count = int(influences.shape[0])
    effective_maximum = options.effective_maximum_influences(influence_count)

    diffusion_result = diffuse_hard_ownership(
        owner_indices=owners,
        adjacency=adjacency,
        influence_count=influence_count,
        iterations=options.iterations,
        relaxation=options.relaxation,
    )

    projection_result = enforce_maximum_influences_by_geometry(
        weights=diffusion_result.weights,
        owner_indices=owners,
        vertex_positions=vertices,
        influence_positions=influences,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
    )
    if projection_result.unresolved_coincident_vertex_ids:
        raise RuntimeError(
            "Max Influences cannot distinguish exactly coincident cutoff joints. "
            "Their smoothed weights, vertex distances, and world positions are "
            "identical. First vertex IDs: {}".format(
                list(projection_result.unresolved_coincident_vertex_ids[:20])
            )
        )

    owner_maximum_result = project_region_owner_to_maximum(
        weights=projection_result.weights,
        owner_indices=owners,
    )
    if owner_maximum_result.owner_below_maximum_after:
        raise RuntimeError(
            "Final blocking owner remains below another influence after owner "
            "preservation. First vertex IDs: {}".format(
                list(owner_maximum_result.owner_below_maximum_after[:20])
            )
        )

    validation_result = validate_bind_weights(
        weights=owner_maximum_result.weights,
        owner_indices=owners,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
        require_exact_one_hot=options.iterations == 0,
    )

    return BindSmoothingResult(
        weights=owner_maximum_result.weights,
        blocking_owner_indices=owners.copy(),
        options=options,
        effective_maximum_influences=effective_maximum,
        diffusion_result=diffusion_result,
        projection_result=projection_result,
        owner_maximum_result=owner_maximum_result,
        validation_result=validation_result,
    )


def _validate_final_blocking_input(
    owner_indices,
    adjacency,
    vertex_positions,
    influence_positions,
):
    owners = np.asarray(owner_indices, dtype=np.int32)
    vertices = np.asarray(vertex_positions, dtype=np.float64)
    influences = np.asarray(influence_positions, dtype=np.float64)

    if owners.ndim != 1:
        raise ValueError("owner_indices must be a one-dimensional array.")
    if vertices.shape != (owners.size, 3):
        raise ValueError(
            "vertex_positions must have shape (vertex_count, 3)."
        )
    if influences.ndim != 2 or influences.shape[1] != 3:
        raise ValueError(
            "influence_positions must have shape (influence_count, 3)."
        )
    if influences.shape[0] < 1:
        raise ValueError("At least one influence position is required.")
    if len(adjacency) != owners.size:
        raise ValueError(
            "Adjacency row count must match final blocking owner count."
        )
    if not np.all(np.isfinite(vertices)):
        raise ValueError("vertex_positions contains non-finite values.")
    if not np.all(np.isfinite(influences)):
        raise ValueError("influence_positions contains non-finite values.")
    if owners.size:
        invalid = (owners < 0) | (owners >= influences.shape[0])
        if np.any(invalid):
            bad = np.where(invalid)[0][:20]
            raise ValueError(
                "owner_indices contains invalid influence indices. "
                "First vertex IDs: {}".format(bad.tolist())
            )

    return owners.copy(), vertices.copy(), influences.copy()
