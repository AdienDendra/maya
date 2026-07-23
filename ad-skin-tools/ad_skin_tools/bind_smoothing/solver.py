"""Smoothing pipeline using blocking ownership only as the initial condition."""

from dataclasses import dataclass
import time
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
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.validation import (
    BindWeightValidationResult,
    validate_bind_weights,
)


@dataclass(frozen=True)
class BindSmoothingResult:
    """Final weights, diagnostics, and isolated smoothing-stage timings."""

    weights: np.ndarray
    blocking_owner_indices: np.ndarray
    constrained_vertex_ids: np.ndarray
    options: BindSmoothingOptions
    effective_maximum_influences: int
    diffusion_result: BindDiffusionResult
    projection_result: GeometricMaxInfluenceResult
    validation_result: BindWeightValidationResult
    input_validation_seconds: float
    diffusion_seconds: float
    maximum_influence_seconds: float
    validation_seconds: float
    assembly_seconds: float
    elapsed_seconds: float

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
    """Diffuse initial ownership, then apply neutral Max Influences."""

    started = time.perf_counter()
    input_started = time.perf_counter()
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
    input_validation_seconds = time.perf_counter() - input_started

    diffusion_started = time.perf_counter()
    diffusion_result = diffuse_hard_ownership(
        owner_indices=owners,
        adjacency=adjacency,
        influence_count=influence_count,
        iterations=options.iterations,
        blend=options.blend,
        initial_weights=initial_weights,
        mutable_vertex_ids=mutable_vertex_ids,
    )
    diffusion_seconds = time.perf_counter() - diffusion_started

    constrained_weights = np.asarray(
        diffusion_result.weights[constrained_ids],
        dtype=np.float64,
    ).copy()
    constrained_owners = owners[constrained_ids]
    constrained_positions = vertices[constrained_ids]

    projection_started = time.perf_counter()
    projection_result = enforce_maximum_influences_by_geometry(
        weights=constrained_weights,
        vertex_positions=constrained_positions,
        influence_positions=influences,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
    )
    maximum_influence_seconds = time.perf_counter() - projection_started
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

    validation_started = time.perf_counter()
    validation_result = validate_bind_weights(
        weights=projection_result.weights,
        owner_indices=constrained_owners,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
        require_exact_one_hot=options.iterations == 0,
    )
    validation_seconds = time.perf_counter() - validation_started

    assembly_started = time.perf_counter()
    final_weights = np.asarray(
        diffusion_result.weights,
        dtype=np.float64,
    ).copy()
    final_weights[constrained_ids] = projection_result.weights
    assembly_seconds = time.perf_counter() - assembly_started

    return BindSmoothingResult(
        weights=final_weights,
        blocking_owner_indices=owners.copy(),
        constrained_vertex_ids=constrained_ids.copy(),
        options=options,
        effective_maximum_influences=effective_maximum,
        diffusion_result=diffusion_result,
        projection_result=projection_result,
        validation_result=validation_result,
        input_validation_seconds=float(input_validation_seconds),
        diffusion_seconds=float(diffusion_seconds),
        maximum_influence_seconds=float(maximum_influence_seconds),
        validation_seconds=float(validation_seconds),
        assembly_seconds=float(assembly_seconds),
        elapsed_seconds=float(time.perf_counter() - started),
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
