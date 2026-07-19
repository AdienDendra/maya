"""Final maximum-influence projection for bind-smoothing weights."""

from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass(frozen=True)
class MaxInfluenceProjectionResult:
    """Constrained weight matrix and projection diagnostics."""

    weights: np.ndarray
    effective_maximum_influences: int
    pruned_vertex_ids: Tuple[int, ...]
    owner_reinserted_vertex_ids: Tuple[int, ...]
    cutoff_tie_vertex_ids: Tuple[int, ...]
    near_zero_entry_count: int
    discarded_entry_count: int
    discarded_weight_sum: float
    maximum_discarded_weight: float
    active_influence_histogram: Tuple[Tuple[int, int], ...]

    @property
    def pruned_vertex_count(self) -> int:
        return len(self.pruned_vertex_ids)

    @property
    def owner_reinserted_vertex_count(self) -> int:
        return len(self.owner_reinserted_vertex_ids)

    @property
    def cutoff_tie_vertex_count(self) -> int:
        return len(self.cutoff_tie_vertex_ids)


def enforce_maximum_influences(
    weights: np.ndarray,
    owner_indices: np.ndarray,
    maximum_influences: int,
    weight_epsilon: float,
) -> MaxInfluenceProjectionResult:
    """Keep at most ``maximum_influences`` non-zero values per vertex.

    The hard Region owner is semantic scene data and is therefore never removed.
    Remaining slots are filled by the largest non-owner values. Exact equal
    weights at the cutoff are reported so a later production stage can decide
    whether an additional geometric tie-break is necessary.

    Equal-weight ties are ordered by influence column only to make this in-memory
    smoke test deterministic. The diagnostic never hides that this happened.
    """

    matrix, owners, maximum_influences, weight_epsilon = _validate_inputs(
        weights=weights,
        owner_indices=owner_indices,
        maximum_influences=maximum_influences,
        weight_epsilon=weight_epsilon,
    )
    projected = matrix.copy()
    vertex_count, influence_count = projected.shape
    rows = np.arange(vertex_count, dtype=np.int32)

    near_zero_mask = (
        (projected > 0.0)
        & (projected <= weight_epsilon)
    )
    near_zero_mask[rows, owners] = False
    near_zero_entry_count = int(np.count_nonzero(near_zero_mask))
    projected[near_zero_mask] = 0.0

    pruned_vertex_ids = []
    owner_reinserted_vertex_ids = []
    cutoff_tie_vertex_ids = []
    discarded_entry_count = 0
    discarded_weight_sum = 0.0
    maximum_discarded_weight = 0.0

    all_columns = np.arange(influence_count, dtype=np.int32)

    for vertex_id in range(vertex_count):
        row = projected[vertex_id]
        owner_column = int(owners[vertex_id])
        active_columns = np.where(row > weight_epsilon)[0].astype(np.int32)

        if owner_column not in active_columns:
            active_columns = np.append(
                active_columns,
                np.asarray([owner_column], dtype=np.int32),
            )

        if active_columns.size <= maximum_influences:
            continue

        pruned_vertex_ids.append(vertex_id)

        # Primary order: larger weight first. Secondary order: stable influence
        # column, used only so this smoke test can produce repeatable output.
        ranked_columns = np.lexsort(
            (
                all_columns,
                -row,
            )
        ).astype(np.int32)
        selected = ranked_columns[:maximum_influences].copy()

        cutoff_weight = float(row[int(selected[-1])])
        excluded = ranked_columns[maximum_influences:]
        if excluded.size and np.any(row[excluded] == cutoff_weight):
            cutoff_tie_vertex_ids.append(vertex_id)

        if owner_column not in selected:
            owner_reinserted_vertex_ids.append(vertex_id)
            selected[-1] = owner_column

        selected_set = set(int(column) for column in selected.tolist())
        discarded_columns = np.asarray(
            [
                int(column)
                for column in active_columns.tolist()
                if int(column) not in selected_set
            ],
            dtype=np.int32,
        )

        if discarded_columns.size:
            discarded_values = row[discarded_columns].copy()
            discarded_entry_count += int(discarded_columns.size)
            discarded_weight_sum += float(
                np.sum(discarded_values, dtype=np.float64)
            )
            maximum_discarded_weight = max(
                maximum_discarded_weight,
                float(np.max(discarded_values)),
            )
            row[discarded_columns] = 0.0

    projected = _normalize_rows(
        projected,
        owner_indices=owners,
        weight_epsilon=weight_epsilon,
    )
    active_influence_histogram = _active_histogram(
        projected,
        weight_epsilon=weight_epsilon,
    )

    return MaxInfluenceProjectionResult(
        weights=projected,
        effective_maximum_influences=maximum_influences,
        pruned_vertex_ids=tuple(
            int(value) for value in pruned_vertex_ids
        ),
        owner_reinserted_vertex_ids=tuple(
            int(value) for value in owner_reinserted_vertex_ids
        ),
        cutoff_tie_vertex_ids=tuple(
            int(value) for value in cutoff_tie_vertex_ids
        ),
        near_zero_entry_count=near_zero_entry_count,
        discarded_entry_count=discarded_entry_count,
        discarded_weight_sum=discarded_weight_sum,
        maximum_discarded_weight=maximum_discarded_weight,
        active_influence_histogram=active_influence_histogram,
    )


def _validate_inputs(
    weights,
    owner_indices,
    maximum_influences,
    weight_epsilon,
):
    matrix = np.asarray(weights, dtype=np.float64)
    owners = np.asarray(owner_indices, dtype=np.int32)
    maximum_influences = int(maximum_influences)
    weight_epsilon = float(weight_epsilon)

    if matrix.ndim != 2:
        raise ValueError("weights must be a two-dimensional matrix.")
    if owners.shape != (matrix.shape[0],):
        raise ValueError(
            "owner_indices must contain one value per weight row."
        )
    if matrix.shape[1] < 1:
        raise ValueError(
            "weights must contain at least one influence column."
        )
    if maximum_influences < 1:
        raise ValueError(
            "maximum_influences must be at least 1."
        )
    if maximum_influences > matrix.shape[1]:
        raise ValueError(
            "maximum_influences cannot exceed the influence column count."
        )
    if weight_epsilon < 0.0:
        raise ValueError("weight_epsilon cannot be negative.")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("weights contain non-finite values.")
    if np.any(matrix < 0.0):
        bad = np.where(np.any(matrix < 0.0, axis=1))[0][:20]
        raise ValueError(
            "weights contain negative values. First vertex IDs: {}".format(
                bad.tolist()
            )
        )
    if owners.size:
        invalid = (owners < 0) | (owners >= matrix.shape[1])
        if np.any(invalid):
            bad = np.where(invalid)[0][:20]
            raise ValueError(
                "owner_indices contains invalid influence columns. "
                "First vertex IDs: {}".format(bad.tolist())
            )

    return (
        matrix,
        owners,
        maximum_influences,
        weight_epsilon,
    )


def _normalize_rows(
    weights,
    owner_indices,
    weight_epsilon,
):
    row_sums = np.sum(weights, axis=1, dtype=np.float64)
    empty_rows = np.where(row_sums <= weight_epsilon)[0]
    if empty_rows.size:
        weights[empty_rows] = 0.0
        weights[
            empty_rows,
            owner_indices[empty_rows],
        ] = 1.0
        row_sums[empty_rows] = 1.0

    return weights / row_sums[:, np.newaxis]


def _active_histogram(
    weights,
    weight_epsilon,
):
    active_counts = np.count_nonzero(
        weights > weight_epsilon,
        axis=1,
    ).astype(np.int32)
    values, counts = np.unique(
        active_counts,
        return_counts=True,
    )
    return tuple(
        (int(value), int(count))
        for value, count in zip(
            values.tolist(),
            counts.tolist(),
        )
    )
