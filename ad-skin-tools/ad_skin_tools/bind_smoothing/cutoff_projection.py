"""Geometry-complete Max Influences projection for smoothed bind weights.

The final Region owner always occupies one influence slot. Other active
influences are ranked by weight, vertex-to-joint distance, then a stable
world-position key. This completes symmetric cutoff ties without using joint
names, hierarchy, UI selection order, or influence column order.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np


@dataclass(frozen=True)
class GeometricMaxInfluenceResult:
    """Constrained weights plus diagnostics for every cutoff decision."""

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
        """Compatibility alias for the old v7.0 diagnostic name."""

        return self.unresolved_coincident_vertex_ids


def enforce_maximum_influences_by_geometry(
    weights: np.ndarray,
    owner_indices: np.ndarray,
    vertex_positions: np.ndarray,
    influence_positions: np.ndarray,
    maximum_influences: int,
    weight_epsilon: float,
) -> GeometricMaxInfluenceResult:
    """Keep at most ``maximum_influences`` active weights per vertex.

    One slot is permanently reserved for the final blocking owner. Remaining
    candidates are ordered by:

    1. larger smoothed weight;
    2. smaller squared vertex-to-joint distance;
    3. lexicographically smaller joint world position.

    The third key is the same kind of spatial-canonical fallback used by the
    v3.2 Region exact-tie stage. It is independent from influence list order.
    A row remains unresolved only when the cutoff would split candidates whose
    weight, distance, and world position are all exactly identical. That means
    the candidate joints are geometrically indistinguishable at this stage.
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
    rows = np.arange(projected.shape[0], dtype=np.int32)
    near_zero = projected <= weight_epsilon
    near_zero[rows, owners] = False
    projected[near_zero] = 0.0

    pruned = []
    cutoff_ties = []
    distance_resolved = []
    spatial_resolved = []
    unresolved_coincident = []
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
            key=lambda column: _geometric_rank_key(
                column=column,
                row=row,
                squared_distances=squared_distances,
                influence_positions=influences,
            )
        )

        if slot_count > 0 and len(candidate_columns) > slot_count:
            selected_boundary = int(candidate_columns[slot_count - 1])
            excluded_boundary = int(candidate_columns[slot_count])
            selected_weight = float(row[selected_boundary])
            excluded_weight = float(row[excluded_boundary])

            if selected_weight == excluded_weight:
                cutoff_ties.append(vertex_id)
                selected_distance = float(squared_distances[selected_boundary])
                excluded_distance = float(squared_distances[excluded_boundary])

                if selected_distance != excluded_distance:
                    distance_resolved.append(vertex_id)
                else:
                    selected_position = _position_key(
                        influences[selected_boundary]
                    )
                    excluded_position = _position_key(
                        influences[excluded_boundary]
                    )
                    if selected_position == excluded_position:
                        unresolved_coincident.append(vertex_id)
                        continue
                    spatial_resolved.append(vertex_id)

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

    return GeometricMaxInfluenceResult(
        weights=projected,
        pruned_vertex_ids=tuple(int(value) for value in pruned),
        cutoff_weight_tie_vertex_ids=tuple(
            int(value) for value in cutoff_ties
        ),
        distance_resolved_vertex_ids=tuple(
            int(value) for value in distance_resolved
        ),
        spatial_canonical_resolved_vertex_ids=tuple(
            int(value) for value in spatial_resolved
        ),
        unresolved_coincident_vertex_ids=tuple(
            int(value) for value in unresolved_coincident
        ),
        discarded_entry_count=int(discarded_entry_count),
    )


def _geometric_rank_key(
    column,
    row,
    squared_distances,
    influence_positions,
):
    position = _position_key(influence_positions[int(column)])
    return (
        -float(row[int(column)]),
        float(squared_distances[int(column)]),
        position[0],
        position[1],
        position[2],
    )


def _position_key(position):
    return tuple(float(value) for value in np.asarray(position).tolist())


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
