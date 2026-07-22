"""Fast topology diffusion for Bind Skin, Add Influence, and Component Smooth.

Blend is the fraction moved toward the connected-neighbour average in one
iteration. Iterations is the exact number of Jacobi repetitions. The numerical
result matches the previous dense implementation, but neighbour accumulation is
performed with NumPy ``bincount`` and reusable matrix buffers.
"""

from dataclasses import dataclass
import time
from typing import Optional, Sequence, Tuple

import numpy as np


DEFAULT_BLEND = 0.25
MINIMUM_BLEND = 0.0
MAXIMUM_BLEND = 1.0
MINIMUM_ITERATIONS = 0
MAXIMUM_ITERATIONS = 10


@dataclass(frozen=True)
class BindDiffusionResult:
    """Final diffused weights plus lightweight end-of-solve diagnostics."""

    weights: np.ndarray
    owner_indices: np.ndarray
    iterations: int
    blend: float
    mutable_vertex_ids: Tuple[int, ...]
    changed_vertex_ids: Tuple[int, ...]
    mixed_vertex_ids: Tuple[int, ...]
    dominant_owner_changed_vertex_ids: Tuple[int, ...]
    iteration_changed_counts: Tuple[int, ...]
    iteration_mixed_counts: Tuple[int, ...]
    active_influence_histogram: Tuple[Tuple[int, int], ...]
    maximum_row_sum_error: float
    topology_setup_seconds: float
    iteration_seconds: float
    finalization_seconds: float
    elapsed_seconds: float

    @property
    def relaxation(self) -> float:
        """Compatibility alias for older callers."""

        return self.blend

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
    blend: float = DEFAULT_BLEND,
    initial_weights: Optional[np.ndarray] = None,
    mutable_vertex_ids: Optional[Sequence[int]] = None,
    relaxation: Optional[float] = None,
) -> BindDiffusionResult:
    """Average neighbouring weights using exact artist-facing iterations.

    Rows outside ``mutable_vertex_ids`` remain fixed and still contribute to the
    neighbour average. Expensive per-iteration diagnostic scans are deliberately
    omitted; development validation evaluates the final matrix after the solve.
    """

    started = time.perf_counter()
    if relaxation is not None:
        if float(blend) != DEFAULT_BLEND and float(blend) != float(relaxation):
            raise ValueError("Supply either blend or relaxation, not both.")
        blend = float(relaxation)

    owners, initial, mutable_ids = _validate_inputs(
        owner_indices=owner_indices,
        adjacency=adjacency,
        influence_count=influence_count,
        iterations=iterations,
        blend=blend,
        initial_weights=initial_weights,
        mutable_vertex_ids=mutable_vertex_ids,
    )
    iterations = int(iterations)
    blend = float(blend)
    tolerance = _numerical_tolerance(int(influence_count))

    baseline = initial
    current = initial.copy()

    topology_started = time.perf_counter()
    degrees, source_ids, neighbour_ids, update_ids, update_all_rows = (
        _build_update_topology(adjacency, mutable_ids)
    )
    topology_setup_seconds = time.perf_counter() - topology_started

    iteration_started = time.perf_counter()
    if iterations > 0 and blend > 0.0 and update_ids.size:
        next_weights = np.empty_like(current)
        neighbour_sums = np.empty_like(current)
        keep_fraction = 1.0 - blend

        if update_all_rows:
            neighbour_scale = blend / degrees.astype(np.float64)
            for _ in range(iterations):
                _accumulate_neighbour_sums(
                    neighbour_sums,
                    current,
                    source_ids,
                    neighbour_ids,
                )
                np.multiply(current, keep_fraction, out=next_weights)
                next_weights += neighbour_sums * neighbour_scale[:, np.newaxis]
                current, next_weights = next_weights, current
        else:
            update_scale = blend / degrees[update_ids].astype(np.float64)
            for _ in range(iterations):
                _accumulate_neighbour_sums(
                    neighbour_sums,
                    current,
                    source_ids,
                    neighbour_ids,
                )
                next_weights[:] = current
                next_weights[update_ids] = (
                    current[update_ids] * keep_fraction
                    + neighbour_sums[update_ids]
                    * update_scale[:, np.newaxis]
                )
                current, next_weights = next_weights, current
    iteration_seconds = time.perf_counter() - iteration_started

    finalization_started = time.perf_counter()
    current = _normalize_rows(current, tolerance=tolerance)
    current[np.abs(current) <= tolerance] = 0.0
    current = _normalize_rows(current, tolerance=tolerance)

    changed_vertex_ids = np.where(
        np.any(np.abs(current - baseline) > tolerance, axis=1)
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
    finalization_seconds = time.perf_counter() - finalization_started

    return BindDiffusionResult(
        weights=current,
        owner_indices=owners.copy(),
        iterations=iterations,
        blend=blend,
        mutable_vertex_ids=tuple(int(value) for value in mutable_ids.tolist()),
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
        iteration_changed_counts=tuple(),
        iteration_mixed_counts=tuple(),
        active_influence_histogram=active_influence_histogram,
        maximum_row_sum_error=maximum_row_sum_error,
        topology_setup_seconds=float(topology_setup_seconds),
        iteration_seconds=float(iteration_seconds),
        finalization_seconds=float(finalization_seconds),
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _accumulate_neighbour_sums(
    output,
    current,
    source_ids,
    neighbour_ids,
):
    """Accumulate each influence column in compiled NumPy code."""

    vertex_count, influence_count = current.shape
    if not source_ids.size:
        output.fill(0.0)
        return

    for influence_index in range(influence_count):
        output[:, influence_index] = np.bincount(
            source_ids,
            weights=current[neighbour_ids, influence_index],
            minlength=vertex_count,
        )


def _build_update_topology(adjacency, mutable_ids):
    degrees = np.asarray(
        [len(neighbours) for neighbours in adjacency],
        dtype=np.int32,
    )
    vertex_count = len(adjacency)
    all_rows_mutable = (
        mutable_ids.size == vertex_count
        and np.array_equal(
            mutable_ids,
            np.arange(vertex_count, dtype=np.int32),
        )
    )

    if all_rows_mutable:
        source_degrees = degrees
        source_vertices = np.arange(vertex_count, dtype=np.int32)
        neighbour_count = int(np.sum(source_degrees, dtype=np.int64))
        neighbour_ids = np.fromiter(
            (
                int(neighbour_id)
                for neighbours in adjacency
                for neighbour_id in neighbours
            ),
            dtype=np.int32,
            count=neighbour_count,
        )
    else:
        source_vertices = mutable_ids
        source_degrees = degrees[source_vertices]
        neighbour_count = int(np.sum(source_degrees, dtype=np.int64))
        neighbour_ids = np.fromiter(
            (
                int(neighbour_id)
                for vertex_id in source_vertices.tolist()
                for neighbour_id in adjacency[int(vertex_id)]
            ),
            dtype=np.int32,
            count=neighbour_count,
        )

    source_ids = np.repeat(source_vertices, source_degrees)
    if source_ids.size != neighbour_ids.size:
        raise RuntimeError("Failed to flatten mesh adjacency consistently.")

    update_ids = mutable_ids[degrees[mutable_ids] > 0]
    update_all_rows = bool(
        all_rows_mutable
        and update_ids.size == vertex_count
    )
    return degrees, source_ids, neighbour_ids, update_ids, update_all_rows


def _validate_inputs(
    owner_indices,
    adjacency,
    influence_count,
    iterations,
    blend,
    initial_weights,
    mutable_vertex_ids,
):
    owners = np.asarray(owner_indices, dtype=np.int32)
    if owners.ndim != 1:
        raise ValueError("owner_indices must be a one-dimensional array.")

    influence_count = int(influence_count)
    if influence_count < 1:
        raise ValueError("influence_count must be at least 1.")
    if owners.size:
        invalid_owner_mask = (owners < 0) | (owners >= influence_count)
        if np.any(invalid_owner_mask):
            invalid_rows = np.where(invalid_owner_mask)[0][:20]
            raise ValueError(
                "owner_indices contains values outside the influence range. "
                "First vertex IDs: {}".format(invalid_rows.tolist())
            )

    if len(adjacency) != owners.size:
        raise ValueError(
            "Adjacency row count must match owner_indices: {} != {}.".format(
                len(adjacency),
                owners.size,
            )
        )

    iterations = int(iterations)
    if iterations < MINIMUM_ITERATIONS or iterations > MAXIMUM_ITERATIONS:
        raise ValueError(
            "iterations must be between {} and {}.".format(
                MINIMUM_ITERATIONS,
                MAXIMUM_ITERATIONS,
            )
        )

    blend = float(blend)
    if blend < MINIMUM_BLEND or blend > MAXIMUM_BLEND:
        raise ValueError(
            "blend must be between {:.1f} and {:.1f}.".format(
                MINIMUM_BLEND,
                MAXIMUM_BLEND,
            )
        )

    vertex_count = int(owners.size)
    for vertex_id, neighbours in enumerate(adjacency):
        for neighbour_id in neighbours:
            neighbour_id = int(neighbour_id)
            if neighbour_id < 0 or neighbour_id >= vertex_count:
                raise ValueError(
                    "Adjacency for vertex {} contains invalid neighbour {}.".format(
                        vertex_id,
                        neighbour_id,
                    )
                )
            if neighbour_id == vertex_id:
                raise ValueError(
                    "Adjacency must not contain self-edges. Vertex: {}.".format(
                        vertex_id
                    )
                )

    if initial_weights is None:
        initial = _build_one_hot_weights(
            owner_indices=owners,
            influence_count=influence_count,
        )
    else:
        initial = np.asarray(initial_weights, dtype=np.float64).copy()
        if initial.shape != (vertex_count, influence_count):
            raise ValueError(
                "initial_weights must have shape ({}, {}).".format(
                    vertex_count,
                    influence_count,
                )
            )
        if not np.all(np.isfinite(initial)):
            raise ValueError("initial_weights contains non-finite values.")
        tolerance = _numerical_tolerance(influence_count)
        if np.any(initial < -tolerance):
            bad = np.where(np.any(initial < -tolerance, axis=1))[0][:20]
            raise ValueError(
                "initial_weights contains negative values. First vertex IDs: {}"
                .format(bad.tolist())
            )
        initial = np.maximum(initial, 0.0)
        row_sums = np.sum(initial, axis=1, dtype=np.float64)
        bad = np.where(np.abs(row_sums - 1.0) > 1e-8)[0]
        if bad.size:
            raise ValueError(
                "initial_weights rows must total 1.0. First vertex IDs: {}".format(
                    bad[:20].tolist()
                )
            )

    if mutable_vertex_ids is None:
        mutable_ids = np.arange(vertex_count, dtype=np.int32)
    else:
        mutable_ids = np.asarray(
            sorted({int(value) for value in mutable_vertex_ids}),
            dtype=np.int32,
        )
        if mutable_ids.size and (
            np.any(mutable_ids < 0) or np.any(mutable_ids >= vertex_count)
        ):
            raise ValueError("mutable_vertex_ids contains an invalid vertex ID.")

    return owners, initial, mutable_ids


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


def _normalize_rows(weights, tolerance):
    if not np.all(np.isfinite(weights)):
        raise RuntimeError("Bind diffusion produced non-finite weights.")

    if np.any(weights < -tolerance):
        bad_rows = np.where(
            np.any(weights < -tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Bind diffusion produced negative weights. First vertex IDs: {}".format(
                bad_rows.tolist()
            )
        )

    weights = np.maximum(weights, 0.0)
    row_sums = np.sum(weights, axis=1, dtype=np.float64)
    invalid_rows = np.where(row_sums <= tolerance)[0]
    if invalid_rows.size:
        raise RuntimeError(
            "Bind diffusion produced empty weight rows. First vertex IDs: {}".format(
                invalid_rows[:20].tolist()
            )
        )
    return weights / row_sums[:, np.newaxis]


def _numerical_tolerance(influence_count):
    return (
        float(np.finfo(np.float64).eps)
        * max(1, int(influence_count))
        * 32.0
    )
