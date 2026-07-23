"""Neutral deterministic Max Influences projection for smoothed weights."""

from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass(frozen=True)
class GeometricMaxInfluenceResult:
    """Constrained weights plus diagnostics for cutoff decisions."""

    weights: np.ndarray
    pruned_vertex_ids: Tuple[int, ...]
    cutoff_weight_tie_vertex_ids: Tuple[int, ...]
    distance_resolved_vertex_ids: Tuple[int, ...]
    spatial_canonical_resolved_vertex_ids: Tuple[int, ...]
    unresolved_coincident_vertex_ids: Tuple[int, ...]
    discarded_entry_count: int

    @property
    def pruned_vertex_count(self) -> int:
        return len(self.pruned_vertex_ids)

    @property
    def spatial_canonical_resolved_vertex_count(self) -> int:
        return len(self.spatial_canonical_resolved_vertex_ids)

    @property
    def unresolved_exact_tie_vertex_ids(self) -> Tuple[int, ...]:
        return self.unresolved_coincident_vertex_ids


def enforce_maximum_influences_by_geometry(
    weights: np.ndarray,
    vertex_positions: np.ndarray,
    influence_positions: np.ndarray,
    maximum_influences: int,
    weight_epsilon: float,
) -> GeometricMaxInfluenceResult:
    """Keep the strongest influences without reserving an ownership slot.

    Active influences are ranked by larger weight. Exact cutoff-weight ties are
    resolved by smaller vertex distance, then joint world position.
    """

    matrix = _validated_weights(weights)
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
    if not np.all(np.isfinite(vertices)):
        raise ValueError("vertex_positions contains non-finite values.")
    if not np.all(np.isfinite(influences)):
        raise ValueError("influence_positions contains non-finite values.")
    if maximum_influences < 1 or maximum_influences > matrix.shape[1]:
        raise ValueError(
            "maximum_influences must be within the influence column range."
        )
    if weight_epsilon < 0.0:
        raise ValueError("weight_epsilon cannot be negative.")

    projected = matrix.copy()
    projected[projected <= weight_epsilon] = 0.0

    active_counts = np.count_nonzero(
        projected > weight_epsilon,
        axis=1,
    ).astype(np.int32)
    rows_to_prune = np.where(
        active_counts > maximum_influences
    )[0].astype(np.int32)

    pruned = []
    cutoff_ties = []
    distance_resolved = []
    spatial_resolved = []
    unresolved_coincident = []
    discarded_entry_count = 0

    for vertex_id in rows_to_prune.tolist():
        row = projected[int(vertex_id)]
        active_columns = np.flatnonzero(row > weight_epsilon).astype(np.int32)
        active_weights = row[active_columns]

        cutoff_weight = -float(
            np.partition(
                -active_weights,
                maximum_influences - 1,
            )[maximum_influences - 1]
        )
        strict_columns = active_columns[active_weights > cutoff_weight]
        tied_columns = active_columns[active_weights == cutoff_weight]
        remaining_slots = maximum_influences - int(strict_columns.size)

        if tied_columns.size > remaining_slots:
            cutoff_ties.append(int(vertex_id))
            delta = (
                influences[tied_columns]
                - vertices[int(vertex_id)][np.newaxis, :]
            )
            squared_distances = np.einsum("ji,ji->j", delta, delta)
            tie_order = np.lexsort(
                (
                    influences[tied_columns, 2],
                    influences[tied_columns, 1],
                    influences[tied_columns, 0],
                    squared_distances,
                )
            )
            ordered_ties = tied_columns[tie_order]

            selected_boundary_index = remaining_slots - 1
            excluded_boundary_index = remaining_slots
            selected_distance = float(
                squared_distances[tie_order[selected_boundary_index]]
            )
            excluded_distance = float(
                squared_distances[tie_order[excluded_boundary_index]]
            )
            if selected_distance != excluded_distance:
                distance_resolved.append(int(vertex_id))
            else:
                selected_position = tuple(
                    float(value)
                    for value in influences[
                        int(ordered_ties[selected_boundary_index])
                    ]
                )
                excluded_position = tuple(
                    float(value)
                    for value in influences[
                        int(ordered_ties[excluded_boundary_index])
                    ]
                )
                if selected_position == excluded_position:
                    unresolved_coincident.append(int(vertex_id))
                    continue
                spatial_resolved.append(int(vertex_id))

            selected_ties = ordered_ties[:remaining_slots]
        else:
            selected_ties = tied_columns

        selected_columns = np.concatenate(
            (strict_columns, selected_ties)
        ).astype(np.int32, copy=False)
        selected_values = row[selected_columns].copy()

        discarded_entry_count += int(
            active_columns.size - selected_columns.size
        )
        row.fill(0.0)
        row[selected_columns] = selected_values
        pruned.append(int(vertex_id))

    projected = _normalize_rows(projected)

    return GeometricMaxInfluenceResult(
        weights=projected,
        pruned_vertex_ids=tuple(pruned),
        cutoff_weight_tie_vertex_ids=tuple(cutoff_ties),
        distance_resolved_vertex_ids=tuple(distance_resolved),
        spatial_canonical_resolved_vertex_ids=tuple(spatial_resolved),
        unresolved_coincident_vertex_ids=tuple(unresolved_coincident),
        discarded_entry_count=int(discarded_entry_count),
    )


def _validated_weights(weights):
    matrix = np.asarray(weights, dtype=np.float64)
    if matrix.ndim != 2:
        raise ValueError("weights must be a two-dimensional matrix.")
    if matrix.shape[1] < 1:
        raise ValueError("weights must contain at least one influence.")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("weights contains non-finite values.")
    if np.any(matrix < 0.0):
        raise ValueError("weights contains negative values.")
    return matrix


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
