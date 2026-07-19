"""Validation for final automatic bind-smoothing weight matrices."""

from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass(frozen=True)
class BindWeightValidationResult:
    """Validation metrics retained for smoke-test reporting."""

    maximum_row_sum_error: float
    active_influence_histogram: Tuple[Tuple[int, int], ...]
    dominant_owner_changed_vertex_ids: Tuple[int, ...]

    @property
    def dominant_owner_changed_vertex_count(self) -> int:
        return len(self.dominant_owner_changed_vertex_ids)


def validate_bind_weights(
    weights: np.ndarray,
    owner_indices: np.ndarray,
    maximum_influences: int,
    weight_epsilon: float,
    require_exact_one_hot: bool = False,
) -> BindWeightValidationResult:
    """Validate normalization, limits, and Region-owner presence."""

    matrix = np.asarray(weights, dtype=np.float64)
    owners = np.asarray(owner_indices, dtype=np.int32)
    maximum_influences = int(maximum_influences)
    weight_epsilon = float(weight_epsilon)

    if matrix.ndim != 2:
        raise RuntimeError(
            "Final bind weights must be a two-dimensional matrix."
        )
    if owners.shape != (matrix.shape[0],):
        raise RuntimeError(
            "Final owner_indices must contain one value per vertex."
        )
    if not np.all(np.isfinite(matrix)):
        bad = np.where(
            ~np.all(np.isfinite(matrix), axis=1)
        )[0][:20]
        raise RuntimeError(
            "Final bind weights contain non-finite values. "
            "First vertex IDs: {}".format(bad.tolist())
        )

    numerical_tolerance = (
        float(np.finfo(np.float64).eps)
        * max(1, matrix.shape[1])
        * 64.0
    )
    if np.any(matrix < -numerical_tolerance):
        bad = np.where(
            np.any(matrix < -numerical_tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Final bind weights contain negative values. "
            "First vertex IDs: {}".format(bad.tolist())
        )

    row_sums = np.sum(matrix, axis=1, dtype=np.float64)
    maximum_row_sum_error = (
        float(np.max(np.abs(row_sums - 1.0)))
        if row_sums.size
        else 0.0
    )
    allowed_error = max(
        numerical_tolerance,
        weight_epsilon,
    )
    if np.any(np.abs(row_sums - 1.0) > allowed_error):
        bad = np.where(
            np.abs(row_sums - 1.0) > allowed_error
        )[0][:20]
        raise RuntimeError(
            "Final bind weights are not normalized. "
            "First vertex IDs: {}".format(bad.tolist())
        )

    active_counts = np.count_nonzero(
        matrix > weight_epsilon,
        axis=1,
    ).astype(np.int32)
    excessive = np.where(
        active_counts > maximum_influences
    )[0]
    if excessive.size:
        raise RuntimeError(
            "Final bind weights exceed Max Influences. "
            "First vertex IDs: {}".format(
                excessive[:20].tolist()
            )
        )

    rows = np.arange(matrix.shape[0], dtype=np.int32)
    owner_values = matrix[rows, owners]
    missing_owners = np.where(
        owner_values <= weight_epsilon
    )[0]
    if missing_owners.size:
        raise RuntimeError(
            "Final bind weights removed the hard Region owner. "
            "First vertex IDs: {}".format(
                missing_owners[:20].tolist()
            )
        )

    dominant_owner_changed = np.where(
        np.argmax(matrix, axis=1).astype(np.int32)
        != owners
    )[0].astype(np.int32)

    if require_exact_one_hot:
        invalid = (
            (active_counts != 1)
            | (
                np.abs(owner_values - 1.0)
                > numerical_tolerance
            )
        )
        if np.any(invalid):
            bad = np.where(invalid)[0][:20]
            raise RuntimeError(
                "Iteration zero must remain exact one-hot Region weights. "
                "First vertex IDs: {}".format(bad.tolist())
            )

    values, counts = np.unique(
        active_counts,
        return_counts=True,
    )
    histogram = tuple(
        (int(value), int(count))
        for value, count in zip(
            values.tolist(),
            counts.tolist(),
        )
    )

    return BindWeightValidationResult(
        maximum_row_sum_error=maximum_row_sum_error,
        active_influence_histogram=histogram,
        dominant_owner_changed_vertex_ids=tuple(
            int(value)
            for value in dominant_owner_changed.tolist()
        ),
    )
