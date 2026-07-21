"""Smoothing pipeline for final Region ownership and fixed boundary weights.

Region remains the blocking authority. Blend and Iterations control only topology
weight diffusion. Bind Skin updates every row. Add Influence can restrict updates
and final constraints to claimed rows while existing rows remain fixed context.
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
    constrained_vertex_ids: np.ndarray
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
    initial_weights: Optional[np.ndarray] = None,
    mutable_vertex_ids: Optional[Sequence[int]] = None,
    constrained_vertex_ids: Optional[Sequence[int]] = None,
) -> BindSmoothingResult:
    """Smooth weights without recalculating Region ownership.

    ``initial_weights`` defaults to exact one-hot ownership. ``mutable_vertex_ids``
    restricts diffusion updates. ``constrained_vertex_ids`` restricts Max
    Influences, owner maximality, and final validation to rows that will be written.
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
    constrained_ids = _resolve_vertex_ids(
        constrained_vertex_ids,
        owners.size,
        default_all=True,
        label="constrained_vertex_ids",
    )

    diffusion_result = diffuse_hard_ownership(
        owner_indices=owners,
        adjacency=adjacency,
        influence_count=influence_count,
        iterations=options.iterations,
        blend=options.blend,
        initial_weights=initial_weights,
        mutable_vertex_ids=mutable_vertex_ids,
    )

    constrained_weights = np.asarray(
        diffusion_result.weights[constrained_ids],
        dtype=np.float64,
    ).copy()
    constrained_owners = owners[constrained_ids]
    constrained_positions = vertices[constrained_ids]

    projection_result = enforce_maximum_influences_by_geometry(
        weights=constrained_weights,
        owner_indices=constrained_owners,
        vertex_positions=constrained_positions,
        influence_positions=influences,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
    )
    if projection_result.unresolved_coincident_vertex_ids:
        bad = constrained_ids[
            np.asarray(
                projection_result.unresolved_coincident_vertex_ids[:20],
                dtype=np.int32,
            )
        ]
        raise RuntimeError(
            "Max Influences cannot distinguish exactly coincident cutoff joints. "
            "Their smoothed weights, vertex distances, and world positions are "
            "identical. First vertex IDs: {}".format(bad.tolist())
        )

    owner_maximum_result = project_region_owner_to_maximum(
        weights=projection_result.weights,
        owner_indices=constrained_owners,
    )
    if owner_maximum_result.owner_below_maximum_after:
        bad = constrained_ids[
            np.asarray(
                owner_maximum_result.owner_below_maximum_after[:20],
                dtype=np.int32,
            )
        ]
        raise RuntimeError(
            "Final blocking owner remains below another influence after owner "
            "preservation. First vertex IDs: {}".format(bad.tolist())
        )

    validation_result = validate_bind_weights(
        weights=owner_maximum_result.weights,
        owner_indices=constrained_owners,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
        require_exact_one_hot=options.iterations == 0,
    )

    final_weights = np.asarray(
        diffusion_result.weights,
        dtype=np.float64,
    ).copy()
    final_weights[constrained_ids] = owner_maximum_result.weights

    return BindSmoothingResult(
        weights=final_weights,
        blocking_owner_indices=owners.copy(),
        constrained_vertex_ids=constrained_ids.copy(),
        options=options,
        effective_maximum_influences=effective_maximum,
        diffusion_result=diffusion_result,
        projection_result=projection_result,
        owner_maximum_result=owner_maximum_result,
        validation_result=validation_result,
    )


def _resolve_vertex_ids(values, vertex_count, default_all, label):
    if values is None:
        if default_all:
            return np.arange(int(vertex_count), dtype=np.int32)
        return np.empty(0, dtype=np.int32)

    ids = np.asarray(
        sorted({int(value) for value in values}),
        dtype=np.int32,
    )
    if ids.size and (
        np.any(ids < 0) or np.any(ids >= int(vertex_count))
    ):
        raise ValueError("{} contains an invalid vertex ID.".format(label))
    return ids


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
