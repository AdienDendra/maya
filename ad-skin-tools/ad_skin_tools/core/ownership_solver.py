from dataclasses import dataclass
from typing import List, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import get_world_positions

np = ensure_numpy()


@dataclass(frozen=True)
class OwnershipSolveResult:
    weights: np.ndarray
    primitive_count: int
    segment_count: int
    point_count: int
    smooth_iterations: int
    average_influence_count: float
    max_influence_count: int
    hard_assignment_counts: np.ndarray


@dataclass(frozen=True)
class _OwnershipPrimitives:
    point_positions: np.ndarray
    point_owner_indices: np.ndarray
    segment_starts: np.ndarray
    segment_vectors: np.ndarray
    segment_lengths_squared: np.ndarray
    segment_owner_indices: np.ndarray

    @property
    def point_count(self) -> int:
        return int(self.point_owner_indices.size)

    @property
    def segment_count(self) -> int:
        return int(self.segment_owner_indices.size)

    @property
    def count(self) -> int:
        return self.point_count + self.segment_count


def solve_closest_ownership_weights(
    vertex_positions: np.ndarray,
    joints: Sequence[str],
    neighbors: List[List[int]],
    smooth_iterations: int = 4,
    max_influences: int = 5,
    prune_threshold: float = 0.0001,
    include_unlisted_children: bool = True,
    endpoint_inset: float = 0.001,
    distance_chunk_size: int = 8192,
    smoothing_chunk_size: int = 2048,
) -> OwnershipSolveResult:
    """
    Build initial skin weights using hard closest ownership followed by
    controlled topology relaxation.

    Each listed influence owns either:
    - its joint position, when it has no usable child segment; or
    - one or more joint-to-child segments.

    Every vertex first receives exactly one owning influence. The one-hot
    ownership matrix is then averaged through direct mesh neighbors for a
    small number of iterations. This produces a local transition around
    ownership boundaries without propagating labels across empty space.
    """
    vertex_positions = np.asarray(vertex_positions, dtype=np.float64)

    if vertex_positions.ndim != 2 or vertex_positions.shape[1] != 3:
        raise ValueError(
            "vertex_positions must have shape (vertex_count, 3)."
        )

    vertex_count = int(vertex_positions.shape[0])

    if vertex_count == 0:
        raise ValueError("The mesh contains no vertices.")

    if len(neighbors) != vertex_count:
        raise ValueError(
            "Neighbor row count does not match vertex count: "
            f"{len(neighbors)} != {vertex_count}"
        )

    joint_paths = _normalize_joint_paths(joints)
    influence_count = len(joint_paths)

    if influence_count < 2:
        raise ValueError(
            "Closest Ownership Bind requires at least two joints."
        )

    smooth_iterations = int(smooth_iterations)
    max_influences = int(max_influences)
    distance_chunk_size = int(distance_chunk_size)
    smoothing_chunk_size = int(smoothing_chunk_size)
    prune_threshold = float(prune_threshold)
    endpoint_inset = float(endpoint_inset)

    if smooth_iterations < 0:
        raise ValueError("smooth_iterations cannot be negative.")

    if max_influences < 1:
        raise ValueError("max_influences must be at least 1.")

    if prune_threshold < 0.0:
        raise ValueError("prune_threshold cannot be negative.")

    if endpoint_inset < 0.0 or endpoint_inset >= 1.0:
        raise ValueError(
            "endpoint_inset must be in the range [0.0, 1.0)."
        )

    if distance_chunk_size < 1 or smoothing_chunk_size < 1:
        raise ValueError("Chunk sizes must be at least 1.")

    joint_positions = get_world_positions(joint_paths)

    primitives = _build_ownership_primitives(
        joint_paths=joint_paths,
        joint_positions=joint_positions,
        include_unlisted_children=include_unlisted_children,
        endpoint_inset=endpoint_inset,
    )

    owner_indices = _assign_hard_ownership(
        vertex_positions=vertex_positions,
        influence_count=influence_count,
        primitives=primitives,
        chunk_size=distance_chunk_size,
    )

    hard_assignment_counts = np.bincount(
        owner_indices,
        minlength=influence_count,
    ).astype(np.int64)

    weights = np.zeros(
        (vertex_count, influence_count),
        dtype=np.float64,
    )

    weights[
        np.arange(vertex_count, dtype=np.int32),
        owner_indices,
    ] = 1.0

    if smooth_iterations:
        neighbor_indices, neighbor_counts = _build_neighbor_matrix(
            neighbors
        )

        weights = _relax_weights(
            weights=weights,
            neighbor_indices=neighbor_indices,
            neighbor_counts=neighbor_counts,
            iterations=smooth_iterations,
            chunk_size=smoothing_chunk_size,
        )

    weights = _limit_prune_and_normalize(
        weights=weights,
        max_influences=min(max_influences, influence_count),
        prune_threshold=prune_threshold,
    )

    influence_counts = np.count_nonzero(
        weights > 1e-8,
        axis=1,
    )

    return OwnershipSolveResult(
        weights=weights,
        primitive_count=primitives.count,
        segment_count=primitives.segment_count,
        point_count=primitives.point_count,
        smooth_iterations=smooth_iterations,
        average_influence_count=float(influence_counts.mean()),
        max_influence_count=int(influence_counts.max()),
        hard_assignment_counts=hard_assignment_counts,
    )


def _normalize_joint_paths(joints: Sequence[str]) -> List[str]:
    result = []
    seen = set()

    for joint in joints:
        matches = cmds.ls(
            joint,
            long=True,
            type="joint",
        ) or []

        if not matches:
            raise RuntimeError(
                f"Joint no longer exists: {joint}"
            )

        joint_path = matches[0]

        if joint_path in seen:
            continue

        seen.add(joint_path)
        result.append(joint_path)

    return result


def _build_ownership_primitives(
    joint_paths: List[str],
    joint_positions: np.ndarray,
    include_unlisted_children: bool,
    endpoint_inset: float,
) -> _OwnershipPrimitives:
    selected_set = set(joint_paths)

    point_positions = []
    point_owner_indices = []

    segment_starts = []
    segment_vectors = []
    segment_lengths_squared = []
    segment_owner_indices = []

    epsilon = 1e-12
    endpoint_scale = 1.0 - endpoint_inset

    for owner_index, joint in enumerate(joint_paths):
        start = joint_positions[owner_index]

        children = cmds.listRelatives(
            joint,
            children=True,
            type="joint",
            fullPath=True,
        ) or []

        if not include_unlisted_children:
            children = [
                child
                for child in children
                if child in selected_set
            ]

        valid_segment_count = 0

        for child in children:
            child_matches = cmds.ls(
                child,
                long=True,
                type="joint",
            ) or []

            if not child_matches:
                continue

            child_position = np.asarray(
                cmds.xform(
                    child_matches[0],
                    query=True,
                    worldSpace=True,
                    translation=True,
                ),
                dtype=np.float64,
            )

            full_vector = child_position - start
            full_length_squared = float(
                np.dot(full_vector, full_vector)
            )

            if full_length_squared <= epsilon:
                continue

            vector = full_vector * endpoint_scale
            length_squared = float(
                np.dot(vector, vector)
            )

            if length_squared <= epsilon:
                continue

            segment_starts.append(start)
            segment_vectors.append(vector)
            segment_lengths_squared.append(length_squared)
            segment_owner_indices.append(owner_index)
            valid_segment_count += 1

        if valid_segment_count == 0:
            point_positions.append(start)
            point_owner_indices.append(owner_index)

    return _OwnershipPrimitives(
        point_positions=_as_matrix3(point_positions),
        point_owner_indices=np.asarray(
            point_owner_indices,
            dtype=np.int32,
        ),
        segment_starts=_as_matrix3(segment_starts),
        segment_vectors=_as_matrix3(segment_vectors),
        segment_lengths_squared=np.asarray(
            segment_lengths_squared,
            dtype=np.float64,
        ),
        segment_owner_indices=np.asarray(
            segment_owner_indices,
            dtype=np.int32,
        ),
    )


def _as_matrix3(values) -> np.ndarray:
    if not values:
        return np.empty((0, 3), dtype=np.float64)

    return np.asarray(values, dtype=np.float64).reshape(-1, 3)


def _assign_hard_ownership(
    vertex_positions: np.ndarray,
    influence_count: int,
    primitives: _OwnershipPrimitives,
    chunk_size: int,
) -> np.ndarray:
    vertex_count = int(vertex_positions.shape[0])
    owner_indices = np.empty(vertex_count, dtype=np.int32)

    for start_row in range(0, vertex_count, chunk_size):
        end_row = min(start_row + chunk_size, vertex_count)
        points = vertex_positions[start_row:end_row]
        block_count = end_row - start_row

        owner_distances_squared = np.full(
            (block_count, influence_count),
            np.inf,
            dtype=np.float64,
        )

        if primitives.point_count:
            point_distances_squared = _pairwise_squared_distances(
                points,
                primitives.point_positions,
            )

            owner_distances_squared[
                :,
                primitives.point_owner_indices,
            ] = point_distances_squared

        for segment_index in range(primitives.segment_count):
            start = primitives.segment_starts[segment_index]
            vector = primitives.segment_vectors[segment_index]
            length_squared = primitives.segment_lengths_squared[
                segment_index
            ]
            owner_index = int(
                primitives.segment_owner_indices[segment_index]
            )

            relative = points - start[np.newaxis, :]
            segment_t = np.matmul(relative, vector) / length_squared
            np.clip(segment_t, 0.0, 1.0, out=segment_t)

            closest_points = (
                start[np.newaxis, :]
                + segment_t[:, np.newaxis]
                * vector[np.newaxis, :]
            )

            delta = points - closest_points
            distances_squared = np.einsum(
                "ij,ij->i",
                delta,
                delta,
            )

            np.minimum(
                owner_distances_squared[:, owner_index],
                distances_squared,
                out=owner_distances_squared[:, owner_index],
            )

        if np.any(~np.isfinite(owner_distances_squared).any(axis=1)):
            raise RuntimeError(
                "Ownership solver could not evaluate one or more vertices."
            )

        owner_indices[start_row:end_row] = np.argmin(
            owner_distances_squared,
            axis=1,
        ).astype(np.int32)

    return owner_indices


def _pairwise_squared_distances(
    points: np.ndarray,
    targets: np.ndarray,
) -> np.ndarray:
    point_lengths_squared = np.einsum(
        "ij,ij->i",
        points,
        points,
    )

    target_lengths_squared = np.einsum(
        "ij,ij->i",
        targets,
        targets,
    )

    distances_squared = (
        point_lengths_squared[:, np.newaxis]
        + target_lengths_squared[np.newaxis, :]
        - 2.0 * np.matmul(points, targets.T)
    )

    np.maximum(
        distances_squared,
        0.0,
        out=distances_squared,
    )

    return distances_squared


def _build_neighbor_matrix(
    neighbors: List[List[int]],
) -> Tuple[np.ndarray, np.ndarray]:
    vertex_count = len(neighbors)
    rows = []
    max_neighbor_count = 1

    for vertex_id, connected_vertices in enumerate(neighbors):
        row = [vertex_id]
        seen = {vertex_id}

        for neighbor_id in connected_vertices:
            neighbor_id = int(neighbor_id)

            if neighbor_id < 0 or neighbor_id >= vertex_count:
                raise IndexError(
                    f"Neighbor vertex is outside the mesh range: {neighbor_id}"
                )

            if neighbor_id in seen:
                continue

            seen.add(neighbor_id)
            row.append(neighbor_id)

        rows.append(row)
        max_neighbor_count = max(max_neighbor_count, len(row))

    sentinel = vertex_count

    neighbor_indices = np.full(
        (vertex_count, max_neighbor_count),
        sentinel,
        dtype=np.int32,
    )

    neighbor_counts = np.empty(
        vertex_count,
        dtype=np.float64,
    )

    for vertex_id, row in enumerate(rows):
        neighbor_indices[
            vertex_id,
            :len(row),
        ] = row

        neighbor_counts[vertex_id] = float(len(row))

    return neighbor_indices, neighbor_counts


def _relax_weights(
    weights: np.ndarray,
    neighbor_indices: np.ndarray,
    neighbor_counts: np.ndarray,
    iterations: int,
    chunk_size: int,
) -> np.ndarray:
    vertex_count, influence_count = weights.shape
    current = np.asarray(weights, dtype=np.float64)

    for _ in range(iterations):
        padded = np.vstack(
            [
                current,
                np.zeros((1, influence_count), dtype=np.float64),
            ]
        )

        next_weights = np.empty_like(current)

        for start_row in range(0, vertex_count, chunk_size):
            end_row = min(start_row + chunk_size, vertex_count)

            gathered = padded[
                neighbor_indices[start_row:end_row]
            ]

            next_weights[start_row:end_row] = (
                gathered.sum(axis=1)
                / neighbor_counts[
                    start_row:end_row,
                    np.newaxis,
                ]
            )

        current = next_weights

    return current


def _limit_prune_and_normalize(
    weights: np.ndarray,
    max_influences: int,
    prune_threshold: float,
) -> np.ndarray:
    result = np.asarray(weights, dtype=np.float64).copy()
    np.maximum(result, 0.0, out=result)

    row_count, influence_count = result.shape
    keep_count = min(int(max_influences), influence_count)

    if keep_count < influence_count:
        top_indices = np.argpartition(
            result,
            kth=influence_count - keep_count,
            axis=1,
        )[:, -keep_count:]

        top_values = np.take_along_axis(
            result,
            top_indices,
            axis=1,
        )

        limited = np.zeros_like(result)

        np.put_along_axis(
            limited,
            top_indices,
            top_values,
            axis=1,
        )

        result = limited

    result = _normalize_rows_strict(result)

    if prune_threshold > 0.0:
        dominant_before_prune = np.argmax(result, axis=1)
        result[result < prune_threshold] = 0.0

        empty_rows = np.where(
            result.sum(axis=1) <= 1e-12
        )[0]

        if empty_rows.size:
            result[
                empty_rows,
                dominant_before_prune[empty_rows],
            ] = 1.0

        result = _normalize_rows_strict(result)

    return result


def _normalize_rows_strict(weights: np.ndarray) -> np.ndarray:
    row_sums = weights.sum(axis=1, keepdims=True)

    if np.any(row_sums <= 1e-12):
        raise RuntimeError(
            "Ownership solver produced an empty weight row."
        )

    return weights / row_sums