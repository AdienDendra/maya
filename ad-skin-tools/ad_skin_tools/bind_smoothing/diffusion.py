"""Topology diffusion for the AD Skin Tool bind-smoothing pipeline.

The public smoothing value remains an artist-facing integer from zero to ten.
v7.5 maps each positive level to two Jacobi diffusion passes so level five
matches the former level-ten result while level ten remains available for
very dense characters.
"""

from dataclasses import dataclass
from typing import Sequence, Tuple

import numpy as np


DEFAULT_RELAXATION = 1.0
SMOOTHING_PASS_MULTIPLIER = 2
MINIMUM_ITERATIONS = 0
MAXIMUM_ITERATIONS = 10


@dataclass(frozen=True)
class BindDiffusionResult:
    """In-memory result and diagnostics for one diffusion solve."""

    weights: np.ndarray
    owner_indices: np.ndarray
    iterations: int
    relaxation: float
    changed_vertex_ids: Tuple[int, ...]
    mixed_vertex_ids: Tuple[int, ...]
    dominant_owner_changed_vertex_ids: Tuple[int, ...]
    iteration_changed_counts: Tuple[int, ...]
    iteration_mixed_counts: Tuple[int, ...]
    active_influence_histogram: Tuple[Tuple[int, int], ...]
    maximum_row_sum_error: float

    @property
    def vertex_count(self) -> int:
        return int(self.owner_indices.size)

    @property
    def influence_count(self) -> int:
        return int(self.weights.shape[1])

    @property
    def changed_vertex_count(self) -> int:
        return len(self.changed_vertex_ids)

    @property
    def mixed_vertex_count(self) -> int:
        return len(self.mixed_vertex_ids)

    @property
    def dominant_owner_changed_vertex_count(self) -> int:
        return len(self.dominant_owner_changed_vertex_ids)


def diffuse_hard_ownership(
    owner_indices: np.ndarray,
    adjacency: Sequence[Sequence[int]],
    influence_count: int,
    iterations: int,
    relaxation: float = DEFAULT_RELAXATION,
) -> BindDiffusionResult:
    """Diffuse one-hot ownership through connected polygon edges.

    ``iterations`` is the artist-facing smoothing level. Every positive level
    runs ``SMOOTHING_PASS_MULTIPLIER`` Jacobi passes. Iteration zero still
    returns the original one-hot matrix exactly.
    """

    owners = _validate_inputs(
        owner_indices=owner_indices,
        adjacency=adjacency,
        influence_count=influence_count,
        iterations=iterations,
        relaxation=relaxation,
    )
    influence_count = int(influence_count)
    smoothing_level = int(iterations)
    diffusion_passes = smoothing_level * SMOOTHING_PASS_MULTIPLIER
    relaxation = float(relaxation)

    hard_weights = _build_one_hot_weights(
        owner_indices=owners,
        influence_count=influence_count,
    )
    current = hard_weights.copy()
    degrees, source_ids, neighbour_ids = _build_edge_arrays(adjacency)
    tolerance = _numerical_tolerance(influence_count)

    iteration_changed_counts = []
    iteration_mixed_counts = []

    for _ in range(diffusion_passes):
        neighbour_sums = np.zeros_like(current)
        if source_ids.size:
            np.add.at(
                neighbour_sums,
                source_ids,
                current[neighbour_ids],
            )

        neighbour_average = current.copy()
        connected_mask = degrees > 0
        neighbour_average[connected_mask] = (
            neighbour_sums[connected_mask]
            / degrees[connected_mask, np.newaxis]
        )

        next_weights = current + relaxation * (
            neighbour_average - current
        )
        next_weights[np.abs(next_weights) <= tolerance] = 0.0
        current = _normalize_rows(
            next_weights,
            tolerance=tolerance,
        )

        iteration_changed_counts.append(
            int(
                np.count_nonzero(
                    np.any(
                        np.abs(current - hard_weights) > tolerance,
                        axis=1,
                    )
                )
            )
        )
        iteration_mixed_counts.append(
            int(
                np.count_nonzero(
                    np.count_nonzero(
                        current > tolerance,
                        axis=1,
                    )
                    > 1
                )
            )
        )

    changed_vertex_ids = np.where(
        np.any(
            np.abs(current - hard_weights) > tolerance,
            axis=1,
        )
    )[0].astype(np.int32)

    active_counts = np.count_nonzero(
        current > tolerance,
        axis=1,
    ).astype(np.int32)
    mixed_vertex_ids = np.where(active_counts > 1)[0].astype(np.int32)

    dominant_owner_changed_vertex_ids = np.where(
        np.argmax(current, axis=1).astype(np.int32) != owners
    )[0].astype(np.int32)

    histogram_values, histogram_counts = np.unique(
        active_counts,
        return_counts=True,
    )
    active_influence_histogram = tuple(
        (int(active_count), int(vertex_count))
        for active_count, vertex_count in zip(
            histogram_values.tolist(),
            histogram_counts.tolist(),
        )
    )

    row_sums = np.sum(current, axis=1, dtype=np.float64)
    maximum_row_sum_error = (
        float(np.max(np.abs(row_sums - 1.0)))
        if row_sums.size
        else 0.0
    )

    return BindDiffusionResult(
        weights=current,
        owner_indices=owners.copy(),
        iterations=diffusion_passes,
        relaxation=relaxation,
        changed_vertex_ids=tuple(
            int(value) for value in changed_vertex_ids.tolist()
        ),
        mixed_vertex_ids=tuple(
            int(value) for value in mixed_vertex_ids.tolist()
        ),
        dominant_owner_changed_vertex_ids=tuple(
            int(value)
            for value in dominant_owner_changed_vertex_ids.tolist()
        ),
        iteration_changed_counts=tuple(iteration_changed_counts),
        iteration_mixed_counts=tuple(iteration_mixed_counts),
        active_influence_histogram=active_influence_histogram,
        maximum_row_sum_error=maximum_row_sum_error,
    )


def _validate_inputs(
    owner_indices,
    adjacency,
    influence_count,
    iterations,
    relaxation,
):
    owners = np.asarray(owner_indices, dtype=np.int32)
    if owners.ndim != 1:
        raise ValueError("owner_indices must be a one-dimensional array.")

    influence_count = int(influence_count)
    if influence_count < 1:
        raise ValueError("influence_count must be at least 1.")

    if owners.size:
        invalid_owner_mask = (
            (owners < 0) | (owners >= influence_count)
        )
        if np.any(invalid_owner_mask):
            invalid_rows = np.where(invalid_owner_mask)[0][:20]
            raise ValueError(
                "owner_indices contains values outside the influence range. "
                "First vertex IDs: {}".format(invalid_rows.tolist())
            )

    if len(adjacency) != owners.size:
        raise ValueError(
            "Adjacency row count does not match owner_indices: {} != {}."
            .format(len(adjacency), owners.size)
        )

    iterations = int(iterations)
    if iterations < MINIMUM_ITERATIONS or iterations > MAXIMUM_ITERATIONS:
        raise ValueError(
            "iterations must be between {} and {}.".format(
                MINIMUM_ITERATIONS,
                MAXIMUM_ITERATIONS,
            )
        )

    relaxation = float(relaxation)
    if not 0.0 <= relaxation <= 1.0:
        raise ValueError("relaxation must be between 0.0 and 1.0.")

    vertex_count = int(owners.size)
    for vertex_id, neighbours in enumerate(adjacency):
        for neighbour_id in neighbours:
            neighbour_id = int(neighbour_id)
            if neighbour_id < 0 or neighbour_id >= vertex_count:
                raise ValueError(
                    "Adjacency for vertex {} contains invalid neighbour {}."
                    .format(vertex_id, neighbour_id)
                )
            if neighbour_id == vertex_id:
                raise ValueError(
                    "Adjacency must not contain self-edges. Vertex: {}."
                    .format(vertex_id)
                )

    return owners


def _build_one_hot_weights(owner_indices, influence_count):
    vertex_count = int(owner_indices.size)
    weights = np.zeros(
        (vertex_count, int(influence_count)),
        dtype=np.float64,
    )
    if vertex_count:
        weights[
            np.arange(vertex_count, dtype=np.int32),
            owner_indices,
        ] = 1.0
    return weights


def _build_edge_arrays(adjacency):
    degrees = np.asarray(
        [len(neighbours) for neighbours in adjacency],
        dtype=np.int32,
    )
    edge_count = int(np.sum(degrees, dtype=np.int64))
    if edge_count == 0:
        empty = np.empty(0, dtype=np.int32)
        return degrees, empty, empty.copy()

    source_ids = np.repeat(
        np.arange(len(adjacency), dtype=np.int32),
        degrees,
    )
    neighbour_ids = np.concatenate(
        [
            np.asarray(neighbours, dtype=np.int32)
            for neighbours in adjacency
            if neighbours
        ]
    )
    if source_ids.size != edge_count or neighbour_ids.size != edge_count:
        raise RuntimeError("Failed to flatten mesh adjacency consistently.")
    return degrees, source_ids, neighbour_ids


def _normalize_rows(weights, tolerance):
    if not np.all(np.isfinite(weights)):
        raise RuntimeError("Bind diffusion produced non-finite weights.")

    if np.any(weights < -tolerance):
        bad_rows = np.where(
            np.any(weights < -tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Bind diffusion produced negative weights. First vertex IDs: {}"
            .format(bad_rows.tolist())
        )

    weights = np.maximum(weights, 0.0)
    row_sums = np.sum(weights, axis=1, dtype=np.float64)
    invalid_rows = np.where(row_sums <= tolerance)[0]
    if invalid_rows.size:
        raise RuntimeError(
            "Bind diffusion produced empty weight rows. First vertex IDs: {}"
            .format(invalid_rows[:20].tolist())
        )
    return weights / row_sums[:, np.newaxis]


def _numerical_tolerance(influence_count):
    return (
        float(np.finfo(np.float64).eps)
        * max(1, int(influence_count))
        * 32.0
    )
