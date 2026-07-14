from dataclasses import dataclass
from typing import List, Optional

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import get_world_positions

np = ensure_numpy()


@dataclass(frozen=True)
class SegmentSolveResult:
    weights: np.ndarray
    segment_count: int
    fallback_vertex_count: int
    average_influence_count: float
    max_influence_count: int


@dataclass(frozen=True)
class _SegmentData:
    start_indices: np.ndarray
    end_indices: np.ndarray
    starts: np.ndarray
    vectors: np.ndarray
    lengths_squared: np.ndarray
    radii: np.ndarray

    @property
    def count(self) -> int:
        return int(self.start_indices.size)


def solve_segment_weights(
    vertex_positions: np.ndarray,
    joints: List[str],
    max_influences: int = 5,
    radius_scale: float = 1.25,
    prune_threshold: float = 0.0001,
    chunk_size: int = 4096,
) -> SegmentSolveResult:
    """
    Calculate initial skin weights from bone segments.

    Main behaviour:
    - Connected selected joints form bone segments.
    - Vertex distance is measured to each segment, not only joint origins.
    - Position along a segment determines endpoint-joint blending.
    - Radial distance determines competition against nearby segments.
    - At most max_influences survive on each vertex.
    - Weight rows always normalize to 1.0.

    This solver performs no Maya native weight distribution.
    """
    vertex_positions = np.asarray(
        vertex_positions,
        dtype=np.float64,
    )

    if vertex_positions.ndim != 2 or vertex_positions.shape[1] != 3:
        raise ValueError(
            "vertex_positions must have shape (vertex_count, 3)."
        )

    joint_paths = _normalize_joint_paths(joints)

    if len(joint_paths) < 2:
        raise ValueError(
            "Segment Weighted Bind requires at least two joints."
        )

    max_influences = int(max_influences)

    if max_influences < 1:
        raise ValueError("max_influences must be at least 1.")

    if radius_scale <= 0.0:
        raise ValueError("radius_scale must be greater than zero.")

    if chunk_size < 1:
        raise ValueError("chunk_size must be at least 1.")

    joint_positions = get_world_positions(joint_paths)

    segment_pairs = _build_segment_pairs(joint_paths)

    segment_data = _build_segment_data(
        segment_pairs=segment_pairs,
        joint_positions=joint_positions,
        vertex_positions=vertex_positions,
        radius_scale=radius_scale,
    )

    vertex_count = int(vertex_positions.shape[0])
    joint_count = len(joint_paths)
    influence_limit = min(max_influences, joint_count)

    final_weights = np.zeros(
        (vertex_count, joint_count),
        dtype=np.float64,
    )

    fallback_vertex_count = 0
    epsilon = 1e-12

    segment_start_length_squared = np.einsum(
        "ij,ij->i",
        segment_data.starts,
        segment_data.starts,
    )

    start_dot_vector = np.einsum(
        "ij,ij->i",
        segment_data.starts,
        segment_data.vectors,
    )

    for start_row in range(0, vertex_count, chunk_size):
        end_row = min(
            start_row + chunk_size,
            vertex_count,
        )

        points = vertex_positions[start_row:end_row]
        block_count = end_row - start_row

        point_length_squared = np.einsum(
            "ij,ij->i",
            points,
            points,
        )

        # Squared distance from P to each segment start A:
        #
        # |P - A|² = |P|² + |A|² - 2(P dot A)
        point_to_start_squared = (
            point_length_squared[:, np.newaxis]
            + segment_start_length_squared[np.newaxis, :]
            - 2.0 * np.matmul(
                points,
                segment_data.starts.T,
            )
        )

        # Projection along the segment:
        #
        # t = dot(P - A, AB) / dot(AB, AB)
        point_dot_vector = (
            np.matmul(
                points,
                segment_data.vectors.T,
            )
            - start_dot_vector[np.newaxis, :]
        )

        segment_t = np.zeros_like(
            point_dot_vector,
            dtype=np.float64,
        )

        valid_segments = (
            segment_data.lengths_squared > epsilon
        )

        segment_t[:, valid_segments] = (
            point_dot_vector[:, valid_segments]
            / segment_data.lengths_squared[
                np.newaxis,
                valid_segments,
            ]
        )

        np.clip(
            segment_t,
            0.0,
            1.0,
            out=segment_t,
        )

        # Distance to closest point along each segment:
        #
        # |P - (A + tAB)|²
        distance_squared = (
            point_to_start_squared
            - 2.0 * segment_t * point_dot_vector
            + np.square(segment_t)
            * segment_data.lengths_squared[np.newaxis, :]
        )

        np.maximum(
            distance_squared,
            0.0,
            out=distance_squared,
        )

        distances = np.sqrt(distance_squared)

        normalized_distance = (
            distances
            / segment_data.radii[np.newaxis, :]
        )

        # Wendland C2 compact kernel:
        #
        # k(u) = (1-u)^4 (4u+1), u < 1
        # k(u) = 0,             u >= 1
        support = np.clip(
            1.0 - normalized_distance,
            0.0,
            1.0,
        )

        kernel = (
            np.power(support, 4.0)
            * (4.0 * normalized_distance + 1.0)
        )

        kernel[normalized_distance >= 1.0] = 0.0

        # Smooth interpolation along the segment.
        #
        # t=0 → full start joint
        # t=0.5 → 50/50
        # t=1 → full end joint
        smooth_t = (
            segment_t
            * segment_t
            * (3.0 - 2.0 * segment_t)
        )

        joint_scores = np.zeros(
            (block_count, joint_count),
            dtype=np.float64,
        )

        for segment_index in range(segment_data.count):
            start_joint_index = int(
                segment_data.start_indices[segment_index]
            )
            end_joint_index = int(
                segment_data.end_indices[segment_index]
            )

            segment_kernel = kernel[:, segment_index]

            if start_joint_index == end_joint_index:
                np.maximum(
                    joint_scores[:, start_joint_index],
                    segment_kernel,
                    out=joint_scores[:, start_joint_index],
                )
                continue

            end_blend = smooth_t[:, segment_index]
            start_blend = 1.0 - end_blend

            start_score = segment_kernel * start_blend
            end_score = segment_kernel * end_blend

            # Max aggregation prevents branch joints from gaining artificial
            # extra weight merely because several segments meet there.
            np.maximum(
                joint_scores[:, start_joint_index],
                start_score,
                out=joint_scores[:, start_joint_index],
            )

            np.maximum(
                joint_scores[:, end_joint_index],
                end_score,
                out=joint_scores[:, end_joint_index],
            )

        # Vertices outside every compact-support radius still need weights.
        # Fallback to the nearest segment and use its longitudinal blend.
        score_sums = joint_scores.sum(
            axis=1,
        )

        fallback_rows = np.where(
            score_sums <= epsilon
        )[0]

        fallback_vertex_count += int(
            fallback_rows.size
        )

        if fallback_rows.size:
            nearest_segments = np.argmin(
                distance_squared[fallback_rows],
                axis=1,
            )

            for fallback_row, segment_index in zip(
                fallback_rows,
                nearest_segments,
            ):
                segment_index = int(segment_index)

                start_joint_index = int(
                    segment_data.start_indices[segment_index]
                )
                end_joint_index = int(
                    segment_data.end_indices[segment_index]
                )

                if start_joint_index == end_joint_index:
                    joint_scores[
                        fallback_row,
                        start_joint_index,
                    ] = 1.0
                    continue

                end_blend = float(
                    smooth_t[fallback_row, segment_index]
                )

                joint_scores[
                    fallback_row,
                    start_joint_index,
                ] = 1.0 - end_blend

                joint_scores[
                    fallback_row,
                    end_joint_index,
                ] = end_blend

        block_weights = _keep_top_influences(
            scores=joint_scores,
            max_influences=influence_limit,
            prune_threshold=prune_threshold,
        )

        final_weights[start_row:end_row] = block_weights

    influence_counts = np.count_nonzero(
        final_weights > 1e-8,
        axis=1,
    )

    return SegmentSolveResult(
        weights=final_weights,
        segment_count=segment_data.count,
        fallback_vertex_count=fallback_vertex_count,
        average_influence_count=float(
            influence_counts.mean()
        ),
        max_influence_count=int(
            influence_counts.max()
        ),
    )


def _normalize_joint_paths(joints: List[str]) -> List[str]:
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


def _build_segment_pairs(
    joints: List[str],
) -> List[tuple[int, int]]:
    """
    Connect each selected joint to its nearest selected ancestor.

    Intermediate non-selected hierarchy nodes may be skipped.
    Disconnected joints become point segments.
    """
    joint_to_index = {
        joint: index
        for index, joint in enumerate(joints)
    }

    selected_set = set(joints)

    pairs = []
    pair_set = set()
    connected_indices = set()

    for child_index, child_joint in enumerate(joints):
        selected_parent = _find_nearest_selected_parent(
            child_joint,
            selected_set,
        )

        if selected_parent is None:
            continue

        parent_index = joint_to_index[selected_parent]
        pair = (parent_index, child_index)

        if pair in pair_set:
            continue

        pair_set.add(pair)
        pairs.append(pair)

        connected_indices.add(parent_index)
        connected_indices.add(child_index)

    # A selected joint that has no selected ancestor or descendant behaves
    # as a point-distance influence.
    for joint_index in range(len(joints)):
        if joint_index in connected_indices:
            continue

        pairs.append(
            (joint_index, joint_index)
        )

    if not pairs:
        raise RuntimeError(
            "Unable to build any joint segments."
        )

    return pairs


def _find_nearest_selected_parent(
    joint: str,
    selected_joints: set,
) -> Optional[str]:
    current = joint

    while True:
        parents = cmds.listRelatives(
            current,
            parent=True,
            fullPath=True,
        ) or []

        if not parents:
            return None

        current = parents[0]

        if current in selected_joints:
            return current


def _build_segment_data(
    segment_pairs: List[tuple[int, int]],
    joint_positions: np.ndarray,
    vertex_positions: np.ndarray,
    radius_scale: float,
) -> _SegmentData:
    start_indices = np.array(
        [pair[0] for pair in segment_pairs],
        dtype=np.int32,
    )

    end_indices = np.array(
        [pair[1] for pair in segment_pairs],
        dtype=np.int32,
    )

    starts = joint_positions[start_indices]
    ends = joint_positions[end_indices]

    vectors = ends - starts

    lengths_squared = np.einsum(
        "ij,ij->i",
        vectors,
        vectors,
    )

    lengths = np.sqrt(
        np.maximum(lengths_squared, 0.0)
    )

    reference_length = _calculate_reference_length(
        lengths=lengths,
        joint_positions=joint_positions,
        vertex_positions=vertex_positions,
    )

    minimum_radius = max(
        reference_length * 0.35,
        1e-6,
    )

    radii = np.maximum(
        lengths * float(radius_scale),
        minimum_radius,
    )

    point_segments = lengths <= 1e-10

    radii[point_segments] = max(
        reference_length * float(radius_scale),
        minimum_radius,
    )

    return _SegmentData(
        start_indices=start_indices,
        end_indices=end_indices,
        starts=starts,
        vectors=vectors,
        lengths_squared=lengths_squared,
        radii=radii,
    )


def _calculate_reference_length(
    lengths: np.ndarray,
    joint_positions: np.ndarray,
    vertex_positions: np.ndarray,
) -> float:
    non_zero_lengths = lengths[
        lengths > 1e-10
    ]

    if non_zero_lengths.size:
        return float(
            np.median(non_zero_lengths)
        )

    # Fallback for disconnected point-only influences:
    # use median nearest-neighbour joint distance.
    joint_count = joint_positions.shape[0]

    if joint_count > 1:
        delta = (
            joint_positions[:, np.newaxis, :]
            - joint_positions[np.newaxis, :, :]
        )

        distance_matrix = np.linalg.norm(
            delta,
            axis=2,
        )

        np.fill_diagonal(
            distance_matrix,
            np.inf,
        )

        nearest_distances = np.min(
            distance_matrix,
            axis=1,
        )

        finite_distances = nearest_distances[
            np.isfinite(nearest_distances)
            & (nearest_distances > 1e-10)
        ]

        if finite_distances.size:
            return float(
                np.median(finite_distances)
            )

    bounding_size = np.linalg.norm(
        vertex_positions.max(axis=0)
        - vertex_positions.min(axis=0)
    )

    return max(
        float(bounding_size) * 0.05,
        1e-3,
    )


def _keep_top_influences(
    scores: np.ndarray,
    max_influences: int,
    prune_threshold: float,
) -> np.ndarray:
    scores = np.asarray(
        scores,
        dtype=np.float64,
    )

    row_count, influence_count = scores.shape
    keep_count = min(
        int(max_influences),
        influence_count,
    )

    if keep_count < influence_count:
        top_indices = np.argpartition(
            scores,
            kth=influence_count - keep_count,
            axis=1,
        )[:, -keep_count:]

        top_values = np.take_along_axis(
            scores,
            top_indices,
            axis=1,
        )

        result = np.zeros_like(scores)

        np.put_along_axis(
            result,
            top_indices,
            top_values,
            axis=1,
        )

    else:
        result = scores.copy()

    row_sums = result.sum(
        axis=1,
        keepdims=True,
    )

    if np.any(row_sums <= 0.0):
        raise RuntimeError(
            "Segment solver produced an empty weight row."
        )

    result /= row_sums

    result[result < float(prune_threshold)] = 0.0

    row_sums = result.sum(
        axis=1,
        keepdims=True,
    )

    if np.any(row_sums <= 0.0):
        raise RuntimeError(
            "Weight pruning removed every influence from a vertex."
        )

    result /= row_sums

    return result