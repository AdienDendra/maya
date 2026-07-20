"""Distance-aware Max Influences and blocking-owner maximality constraints."""

from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass(frozen=True)
class OwnerMaximumResult:
    weights: np.ndarray
    projected_vertex_ids: Tuple[int, ...]
    owner_below_maximum_before: Tuple[int, ...]
    owner_below_maximum_after: Tuple[int, ...]

    @property
    def projected_vertex_count(self) -> int:
        return len(self.projected_vertex_ids)


@dataclass(frozen=True)
class DistanceMaxInfluenceResult:
    weights: np.ndarray
    pruned_vertex_ids: Tuple[int, ...]
    cutoff_weight_tie_vertex_ids: Tuple[int, ...]
    distance_resolved_vertex_ids: Tuple[int, ...]
    unresolved_exact_tie_vertex_ids: Tuple[int, ...]
    discarded_entry_count: int

    @property
    def pruned_vertex_count(self) -> int:
        return len(self.pruned_vertex_ids)


def project_region_owner_to_maximum(
    weights: np.ndarray,
    owner_indices: np.ndarray,
) -> OwnerMaximumResult:
    """Make the final blocking owner maximal with minimal equalising change.

    Influences above the owner are pooled with the owner and share their total
    weight equally. This allows co-maximum weights without a tuned owner boost.
    """

    matrix, owners = _validated_weight_inputs(weights, owner_indices)
    projected = matrix.copy()
    tolerance = _numerical_tolerance(projected.shape[1])
    before = _owner_below_maximum_ids(projected, owners, tolerance)
    projected_ids = []

    for vertex_id in before:
        row = projected[int(vertex_id)]
        owner_column = int(owners[int(vertex_id)])
        other_columns = [
            int(column)
            for column in range(row.size)
            if int(column) != owner_column
        ]
        other_columns.sort(key=lambda column: -float(row[column]))

        pooled_columns = []
        pooled_sum = float(row[owner_column])
        for column in other_columns:
            pooled_average = pooled_sum / float(len(pooled_columns) + 1)
            if float(row[column]) <= pooled_average + tolerance:
                break
            pooled_columns.append(column)
            pooled_sum += float(row[column])

        if not pooled_columns:
            continue

        pooled_average = pooled_sum / float(len(pooled_columns) + 1)
        row[owner_column] = pooled_average
        row[np.asarray(pooled_columns, dtype=np.int32)] = pooled_average
        projected_ids.append(int(vertex_id))

    projected = _normalize_rows(projected)
    after = _owner_below_maximum_ids(projected, owners, tolerance)

    return OwnerMaximumResult(
        weights=projected,
        projected_vertex_ids=tuple(projected_ids),
        owner_below_maximum_before=tuple(int(value) for value in before),
        owner_below_maximum_after=tuple(int(value) for value in after),
    )


def enforce_maximum_influences_by_distance(
    weights: np.ndarray,
    owner_indices: np.ndarray,
    vertex_positions: np.ndarray,
    influence_positions: np.ndarray,
    maximum_influences: int,
    weight_epsilon: float,
) -> DistanceMaxInfluenceResult:
    """Limit active weights and resolve equal cutoff weights by joint distance.

    The final blocking owner always occupies one slot. Remaining influences are
    ordered by larger weight and then smaller vertex-to-joint squared distance.
    Exact weight-and-distance ties remain unresolved and are reported.
    """

    matrix, owners = _validated_weight_inputs(weights, owner_indices)
    vertices = np.asarray(vertex_positions, dtype=np.float64)
    influences = np.asarray(influence_positions, dtype=np.float64)
    maximum_influences = int(maximum_influences)
    weight_epsilon = float(weight_epsilon)

    if vertices.shape != (matrix.shape[0], 3):
        raise ValueError("vertex_positions must have shape (vertex_count, 3).")
    if influences.shape != (matrix.shape[1], 3):
        raise ValueError(
            "influence_positions must have shape (influence_count, 3)."
        )
    if maximum_influences < 1 or maximum_influences > matrix.shape[1]:
        raise ValueError(
            "maximum_influences must be within the influence column range."
        )
    if weight_epsilon < 0.0:
        raise ValueError("weight_epsilon cannot be negative.")

    projected = matrix.copy()
    rows = np.arange(projected.shape[0], dtype=np.int32)
    near_zero = projected <= weight_epsilon
    near_zero[rows, owners] = False
    projected[near_zero] = 0.0

    pruned = []
    cutoff_ties = []
    distance_resolved = []
    unresolved = []
    discarded_entry_count = 0

    for vertex_id in range(projected.shape[0]):
        row = projected[vertex_id]
        owner_column = int(owners[vertex_id])
        active_columns = [
            int(column)
            for column in np.where(row > weight_epsilon)[0].tolist()
        ]
        if owner_column not in active_columns:
            active_columns.append(owner_column)
        if len(active_columns) <= maximum_influences:
            continue

        candidate_columns = [
            column for column in active_columns if column != owner_column
        ]
        slot_count = maximum_influences - 1
        delta = influences - vertices[vertex_id][np.newaxis, :]
        squared_distances = np.einsum("ji,ji->j", delta, delta)
        candidate_columns.sort(
            key=lambda column: (
                -float(row[column]),
                float(squared_distances[column]),
            )
        )

        if slot_count > 0 and len(candidate_columns) > slot_count:
            selected_boundary = candidate_columns[slot_count - 1]
            excluded_boundary = candidate_columns[slot_count]
            if float(row[selected_boundary]) == float(row[excluded_boundary]):
                cutoff_ties.append(vertex_id)
                if (
                    float(squared_distances[selected_boundary])
                    == float(squared_distances[excluded_boundary])
                ):
                    unresolved.append(vertex_id)
                    continue
                distance_resolved.append(vertex_id)

        selected = {owner_column}
        selected.update(candidate_columns[:slot_count])
        discarded_columns = [
            column for column in active_columns if column not in selected
        ]
        if discarded_columns:
            row[np.asarray(discarded_columns, dtype=np.int32)] = 0.0
            discarded_entry_count += len(discarded_columns)
            pruned.append(vertex_id)

    projected = _normalize_rows(projected)

    return DistanceMaxInfluenceResult(
        weights=projected,
        pruned_vertex_ids=tuple(int(value) for value in pruned),
        cutoff_weight_tie_vertex_ids=tuple(
            int(value) for value in cutoff_ties
        ),
        distance_resolved_vertex_ids=tuple(
            int(value) for value in distance_resolved
        ),
        unresolved_exact_tie_vertex_ids=tuple(
            int(value) for value in unresolved
        ),
        discarded_entry_count=int(discarded_entry_count),
    )


def owner_below_maximum_vertex_ids(
    weights: np.ndarray,
    owner_indices: np.ndarray,
) -> Tuple[int, ...]:
    matrix, owners = _validated_weight_inputs(weights, owner_indices)
    tolerance = _numerical_tolerance(matrix.shape[1])
    return tuple(
        int(value)
        for value in _owner_below_maximum_ids(matrix, owners, tolerance)
    )


def maximum_active_influences(
    weights: np.ndarray,
    weight_epsilon: float,
) -> int:
    matrix = np.asarray(weights, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("weights must be a two-dimensional matrix.")
    if not matrix.size:
        return 0
    return int(
        np.max(
            np.count_nonzero(
                matrix > float(weight_epsilon),
                axis=1,
            )
        )
    )


def _validated_weight_inputs(weights, owner_indices):
    matrix = np.asarray(weights, dtype=np.float64)
    owners = np.asarray(owner_indices, dtype=np.int32)
    if matrix.ndim != 2:
        raise ValueError("weights must be a two-dimensional matrix.")
    if owners.shape != (matrix.shape[0],):
        raise ValueError("owner_indices must contain one owner per vertex.")
    if matrix.shape[1] < 1:
        raise ValueError("weights must contain at least one influence.")
    if owners.size and (
        np.any(owners < 0) or np.any(owners >= matrix.shape[1])
    ):
        raise ValueError("owner_indices contains an invalid influence index.")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("weights contains non-finite values.")
    if np.any(matrix < 0.0):
        raise ValueError("weights contains negative values.")
    return matrix, owners


def _owner_below_maximum_ids(matrix, owners, tolerance):
    rows = np.arange(matrix.shape[0], dtype=np.int32)
    owner_values = matrix[rows, owners]
    row_maximums = np.max(matrix, axis=1)
    return np.where(owner_values + tolerance < row_maximums)[0].astype(np.int32)


def _normalize_rows(weights):
    row_sums = np.sum(weights, axis=1, dtype=np.float64)
    invalid = np.where(row_sums <= 0.0)[0]
    if invalid.size:
        raise RuntimeError(
            "Constraint projection produced empty rows: {}.".format(
                invalid[:20].tolist()
            )
        )
    return weights / row_sums[:, np.newaxis]


def _numerical_tolerance(influence_count):
    return (
        float(np.finfo(np.float64).eps)
        * max(1, int(influence_count))
        * 64.0
    )
