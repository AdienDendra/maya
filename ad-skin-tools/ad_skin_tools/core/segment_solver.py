from dataclasses import dataclass
from typing import List, Optional

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import get_world_positions
from ad_skin_tools.core.surface_distance import (
    compute_top_k_surface_distances,
)

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

@dataclass(frozen=True)
class _SurfaceSeedData:
    vertex_ids: np.ndarray
    segment_indices: np.ndarray
    initial_costs: np.ndarray
    segment_parameters: np.ndarray

    @property
    def count(self) -> int:
        return int(self.vertex_ids.size)

def solve_segment_weights(
    vertex_positions: np.ndarray,
    joints: List[str],
    max_influences: int = 5,
    radius_scale: float = 1.25,
    prune_threshold: float = 0.0001,
    chunk_size: int = 4096,
    adjacency=None,
    distance_mode: str = "surface",
) -> SegmentSolveResult:
    """
    Dispatch segment weighting by distance mode.

    volume:
        Straight world-space distance. Fast, but may cross gaps between
        nearby surfaces.

    surface:
        Geodesic distance through connected mesh edges. This behaves like
        Maya Soft Selection Surface falloff and prevents direct propagation
        between neighboring fingers.
    """
    distance_mode = str(
        distance_mode
    ).strip().lower()

    if distance_mode == "volume":
        return _solve_volume_segment_weights(
            vertex_positions=vertex_positions,
            joints=joints,
            max_influences=max_influences,
            radius_scale=radius_scale,
            prune_threshold=prune_threshold,
            chunk_size=chunk_size,
        )

    if distance_mode == "surface":
        if adjacency is None:
            raise ValueError(
                "Surface distance mode requires weighted mesh adjacency."
            )

        return _solve_surface_segment_weights(
            vertex_positions=vertex_positions,
            joints=joints,
            adjacency=adjacency,
            max_influences=max_influences,
            radius_scale=radius_scale,
            prune_threshold=prune_threshold,
        )

    raise ValueError(
        f"Unsupported segment distance mode: {distance_mode}"
    )

def _solve_volume_segment_weights(
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

def _solve_surface_segment_weights(
    vertex_positions: np.ndarray,
    joints: List[str],
    adjacency,
    max_influences: int = 5,
    radius_scale: float = 1.25,
    prune_threshold: float = 0.0001,
) -> SegmentSolveResult:
    """
    Calculate bone-segment weights using surface/geodesic distance.

    Workflow:
    1. Build bone segments from the selected joint hierarchy.
    2. Sample points along every segment.
    3. Map samples to nearby mesh vertices.
    4. Propagate segment labels through connected mesh edges.
    5. Convert segment scores into endpoint-joint weights.
    6. Keep the strongest max_influences and normalize.
    """
    vertex_positions = np.asarray(
        vertex_positions,
        dtype=np.float64,
    )

    if vertex_positions.ndim != 2 or vertex_positions.shape[1] != 3:
        raise ValueError(
            "vertex_positions must have shape (vertex_count, 3)."
        )

    vertex_count = int(
        vertex_positions.shape[0]
    )

    if len(adjacency) != vertex_count:
        raise ValueError(
            "Surface adjacency size does not match vertex count: "
            f"{len(adjacency)} != {vertex_count}"
        )

    joint_paths = _normalize_joint_paths(
        joints
    )

    if len(joint_paths) < 2:
        raise ValueError(
            "Surface Segment Bind requires at least two joints."
        )

    max_influences = int(
        max_influences
    )

    if max_influences < 1:
        raise ValueError(
            "max_influences must be at least 1."
        )

    joint_positions = get_world_positions(
        joint_paths
    )

    segment_pairs = _build_segment_pairs(
        joint_paths
    )

    segment_data = _build_segment_data(
        segment_pairs=segment_pairs,
        joint_positions=joint_positions,
        vertex_positions=vertex_positions,
        radius_scale=radius_scale,
    )

    candidate_segment_count = min(
        segment_data.count,
        max(
            12,
            max_influences * 4,
        ),
    )

    surface_seeds = _build_surface_seeds(
        vertex_positions=vertex_positions,
        adjacency=adjacency,
        segment_data=segment_data,
        max_segment_candidates=candidate_segment_count,
    )

    if surface_seeds.count == 0:
        raise RuntimeError(
            "No surface seeds could be generated from the joint segments."
        )

    surface_result = compute_top_k_surface_distances(
        adjacency=adjacency,
        seed_vertex_ids=surface_seeds.vertex_ids.tolist(),
        seed_label_indices=surface_seeds.segment_indices.tolist(),
        seed_costs=surface_seeds.initial_costs.tolist(),
        max_labels=candidate_segment_count,
    )

    if surface_result.reached_vertex_count != vertex_count:
        unreachable_count = (
            vertex_count
            - surface_result.reached_vertex_count
        )

        raise RuntimeError(
            "Internal surface seed coverage failure.\n\n"
            f"Unreachable vertices: {unreachable_count}\n\n"
            "Shell-aware seeding failed to initialize part of the mesh."
        )
        
    joint_count = len(
        joint_paths
    )

    joint_scores = np.zeros(
        (vertex_count, joint_count),
        dtype=np.float64,
    )

    candidate_labels = (
        surface_result.label_indices
    )

    candidate_sources = (
        surface_result.source_indices
    )

    candidate_distances = (
        surface_result.distances
    )

    valid_candidates = (
        candidate_labels >= 0
    ) & np.isfinite(
        candidate_distances
    )

    for candidate_column in range(
        candidate_labels.shape[1]
    ):
        valid_rows = np.where(
            valid_candidates[:, candidate_column]
        )[0]

        if valid_rows.size == 0:
            continue

        segment_indices = candidate_labels[
            valid_rows,
            candidate_column,
        ]

        source_indices = candidate_sources[
            valid_rows,
            candidate_column,
        ]

        distances = candidate_distances[
            valid_rows,
            candidate_column,
        ]

        radii = segment_data.radii[
            segment_indices
        ]

        normalized_distance = (
            distances / radii
        )

        # Wendland C2 compact-support kernel.
        support = np.clip(
            1.0 - normalized_distance,
            0.0,
            1.0,
        )

        kernel = (
            np.power(support, 4.0)
            * (4.0 * normalized_distance + 1.0)
        )

        kernel[
            normalized_distance >= 1.0
        ] = 0.0

        segment_t = surface_seeds.segment_parameters[
            source_indices
        ]

        smooth_t = (
            segment_t
            * segment_t
            * (3.0 - 2.0 * segment_t)
        )

        start_joint_indices = segment_data.start_indices[
            segment_indices
        ]

        end_joint_indices = segment_data.end_indices[
            segment_indices
        ]

        start_scores = (
            kernel * (1.0 - smooth_t)
        )

        end_scores = (
            kernel * smooth_t
        )

        np.maximum.at(
            joint_scores,
            (
                valid_rows,
                start_joint_indices,
            ),
            start_scores,
        )

        np.maximum.at(
            joint_scores,
            (
                valid_rows,
                end_joint_indices,
            ),
            end_scores,
        )

    # A compact kernel may reject every candidate on a remote vertex.
    # Fall back only to its nearest SURFACE segment, never volume distance.
    score_sums = joint_scores.sum(
        axis=1
    )

    fallback_rows = np.where(
        score_sums <= 1e-12
    )[0]

    for vertex_id in fallback_rows:
        segment_index = int(
            candidate_labels[vertex_id, 0]
        )

        source_index = int(
            candidate_sources[vertex_id, 0]
        )

        if segment_index < 0 or source_index < 0:
            raise RuntimeError(
                f"No valid surface candidate for vertex {vertex_id}."
            )

        segment_t = float(
            surface_seeds.segment_parameters[
                source_index
            ]
        )

        smooth_t = (
            segment_t
            * segment_t
            * (3.0 - 2.0 * segment_t)
        )

        start_joint_index = int(
            segment_data.start_indices[
                segment_index
            ]
        )

        end_joint_index = int(
            segment_data.end_indices[
                segment_index
            ]
        )

        if start_joint_index == end_joint_index:
            joint_scores[
                vertex_id,
                start_joint_index,
            ] = 1.0
        else:
            joint_scores[
                vertex_id,
                start_joint_index,
            ] = 1.0 - smooth_t

            joint_scores[
                vertex_id,
                end_joint_index,
            ] = smooth_t

    final_weights = _keep_top_influences(
        scores=joint_scores,
        max_influences=min(
            max_influences,
            joint_count,
        ),
        prune_threshold=prune_threshold,
    )

    influence_counts = np.count_nonzero(
        final_weights > 1e-8,
        axis=1,
    )

    return SegmentSolveResult(
        weights=final_weights,
        segment_count=segment_data.count,
        fallback_vertex_count=int(
            fallback_rows.size
        ),
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

def _build_surface_seeds(
    vertex_positions: np.ndarray,
    adjacency,
    segment_data: _SegmentData,
    max_segment_candidates: int,
    seed_spacing_scale: float = 1.5,
    seeds_per_sample: int = 8,
    max_samples_per_segment: int = 48,
) -> _SurfaceSeedData:
    """
    Generate bone-segment seeds independently for every disconnected shell.

    Each mesh shell:
    - identifies nearby bone segments;
    - receives its own surface seeds;
    - propagates weights only through its own connected topology.

    This prevents one disconnected arm shell from being treated as
    unreachable merely because another shell received the first seeds.
    """
    vertex_positions = np.asarray(
        vertex_positions,
        dtype=np.float64,
    )

    reference_edge_length = _get_reference_edge_length(
        adjacency
    )

    sample_spacing = max(
        reference_edge_length * float(seed_spacing_scale),
        1e-6,
    )

    components = _get_connected_components(
        adjacency
    )

    if not components:
        raise RuntimeError(
            "No connected mesh components were found."
        )

    max_segment_candidates = max(
        1,
        min(
            int(max_segment_candidates),
            segment_data.count,
        ),
    )

    # key:
    #     (segment_index, vertex_id)
    #
    # value:
    #     (initial_cost, segment_t)
    best_seed_by_segment_vertex = {}

    for component_vertex_ids in components:
        component_vertex_ids = np.asarray(
            component_vertex_ids,
            dtype=np.int32,
        )

        component_positions = vertex_positions[
            component_vertex_ids
        ]

        ranked_segments = _rank_segments_for_component(
            component_positions=component_positions,
            segment_data=segment_data,
        )

        selected_segments = _select_component_segments(
            ranked_segments=ranked_segments,
            component_positions=component_positions,
            reference_edge_length=reference_edge_length,
            max_segment_candidates=max_segment_candidates,
        )

        _add_component_surface_seeds(
            component_vertex_ids=component_vertex_ids,
            component_positions=component_positions,
            selected_segment_indices=selected_segments,
            segment_data=segment_data,
            sample_spacing=sample_spacing,
            seeds_per_sample=seeds_per_sample,
            max_samples_per_segment=max_samples_per_segment,
            reference_edge_length=reference_edge_length,
            seed_store=best_seed_by_segment_vertex,
        )

    sorted_entries = sorted(
        (
            (
                segment_index,
                vertex_id,
                initial_cost,
                segment_t,
            )
            for (
                segment_index,
                vertex_id,
            ), (
                initial_cost,
                segment_t,
            ) in best_seed_by_segment_vertex.items()
        ),
        key=lambda item: (
            item[0],
            item[1],
            item[2],
            item[3],
        ),
    )

    return _SurfaceSeedData(
        vertex_ids=np.asarray(
            [
                entry[1]
                for entry in sorted_entries
            ],
            dtype=np.int32,
        ),
        segment_indices=np.asarray(
            [
                entry[0]
                for entry in sorted_entries
            ],
            dtype=np.int32,
        ),
        initial_costs=np.asarray(
            [
                entry[2]
                for entry in sorted_entries
            ],
            dtype=np.float64,
        ),
        segment_parameters=np.asarray(
            [
                entry[3]
                for entry in sorted_entries
            ],
            dtype=np.float64,
        ),
    )

def _get_connected_components(
    adjacency,
) -> List[np.ndarray]:
    """
    Return disconnected mesh shells as vertex-ID arrays.
    """
    vertex_count = len(adjacency)

    visited = np.zeros(
        vertex_count,
        dtype=bool,
    )

    components = []

    for start_vertex in range(vertex_count):
        if visited[start_vertex]:
            continue

        stack = [start_vertex]
        visited[start_vertex] = True
        component = []

        while stack:
            vertex_id = stack.pop()
            component.append(vertex_id)

            for neighbor_id, _ in adjacency[vertex_id]:
                neighbor_id = int(neighbor_id)

                if visited[neighbor_id]:
                    continue

                visited[neighbor_id] = True
                stack.append(neighbor_id)

        components.append(
            np.asarray(
                component,
                dtype=np.int32,
            )
        )

    return components

def _rank_segments_for_component(
    component_positions: np.ndarray,
    segment_data: _SegmentData,
):
    """
    Rank segments by their closest world-space distance to one mesh shell.

    World-space distance is used only to decide which bones belong near
    the shell. Actual weight propagation remains surface/geodesic.
    """
    ranked = []
    epsilon = 1e-12

    for segment_index in range(
        segment_data.count
    ):
        segment_start = segment_data.starts[
            segment_index
        ]

        segment_vector = segment_data.vectors[
            segment_index
        ]

        length_squared = float(
            segment_data.lengths_squared[
                segment_index
            ]
        )

        relative = (
            component_positions
            - segment_start[np.newaxis, :]
        )

        if length_squared <= epsilon:
            closest_points = np.broadcast_to(
                segment_start,
                component_positions.shape,
            )
        else:
            segment_t = np.matmul(
                relative,
                segment_vector,
            ) / length_squared

            segment_t = np.clip(
                segment_t,
                0.0,
                1.0,
            )

            closest_points = (
                segment_start[np.newaxis, :]
                + segment_t[:, np.newaxis]
                * segment_vector[np.newaxis, :]
            )

        delta = (
            component_positions
            - closest_points
        )

        distances_squared = np.einsum(
            "ij,ij->i",
            delta,
            delta,
        )

        ranked.append(
            (
                segment_index,
                float(
                    distances_squared.min()
                ),
            )
        )

    ranked.sort(
        key=lambda item: (
            item[1],
            item[0],
        )
    )

    return ranked

def _select_component_segments(
    ranked_segments,
    component_positions: np.ndarray,
    reference_edge_length: float,
    max_segment_candidates: int,
) -> List[int]:
    """
    Keep segments spatially relevant to this particular mesh shell.

    The threshold is relative to:
    - closest segment distance;
    - average topology scale;
    - shell bounding-box size.
    """
    if not ranked_segments:
        raise RuntimeError(
            "No bone segments are available for a mesh shell."
        )

    shell_extent = float(
        np.linalg.norm(
            component_positions.max(axis=0)
            - component_positions.min(axis=0)
        )
    )

    closest_distance = float(
        np.sqrt(
            max(
                ranked_segments[0][1],
                0.0,
            )
        )
    )

    selection_margin = max(
        reference_edge_length * 6.0,
        shell_extent * 0.25,
    )

    distance_limit = (
        closest_distance
        + selection_margin
    )

    selected = []

    for segment_index, distance_squared in ranked_segments:
        distance = float(
            np.sqrt(
                max(
                    distance_squared,
                    0.0,
                )
            )
        )

        if distance > distance_limit:
            continue

        selected.append(
            int(segment_index)
        )

        if len(selected) >= max_segment_candidates:
            break

    # Every shell must have at least one candidate.
    if not selected:
        selected.append(
            int(ranked_segments[0][0])
        )

    return selected

def _add_component_surface_seeds(
    component_vertex_ids: np.ndarray,
    component_positions: np.ndarray,
    selected_segment_indices: List[int],
    segment_data: _SegmentData,
    sample_spacing: float,
    seeds_per_sample: int,
    max_samples_per_segment: int,
    reference_edge_length: float,
    seed_store: dict,
) -> None:
    """
    Sample selected bone segments and map samples only to vertices belonging
    to the current connected shell.
    """
    component_vertex_count = int(
        component_vertex_ids.size
    )

    if component_vertex_count == 0:
        return

    for segment_index in selected_segment_indices:
        segment_start = segment_data.starts[
            segment_index
        ]

        segment_vector = segment_data.vectors[
            segment_index
        ]

        segment_length = float(
            np.sqrt(
                max(
                    segment_data.lengths_squared[
                        segment_index
                    ],
                    0.0,
                )
            )
        )

        if segment_length <= 1e-10:
            sample_parameters = np.asarray(
                [0.0],
                dtype=np.float64,
            )
        else:
            sample_count = int(
                np.ceil(
                    segment_length
                    / sample_spacing
                )
            ) + 1

            sample_count = max(
                3,
                min(
                    sample_count,
                    int(max_samples_per_segment),
                ),
            )

            sample_parameters = np.linspace(
                0.0,
                1.0,
                sample_count,
                dtype=np.float64,
            )

        sample_positions = (
            segment_start[np.newaxis, :]
            + sample_parameters[:, np.newaxis]
            * segment_vector[np.newaxis, :]
        )

        for sample_position, segment_t in zip(
            sample_positions,
            sample_parameters,
        ):
            delta = (
                component_positions
                - sample_position[np.newaxis, :]
            )

            distances_squared = np.einsum(
                "ij,ij->i",
                delta,
                delta,
            )

            candidate_count = min(
                int(seeds_per_sample),
                component_vertex_count,
            )

            if candidate_count == component_vertex_count:
                local_candidate_indices = np.arange(
                    component_vertex_count,
                    dtype=np.int32,
                )
            else:
                local_candidate_indices = np.argpartition(
                    distances_squared,
                    candidate_count - 1,
                )[:candidate_count]

            local_candidate_indices = local_candidate_indices[
                np.argsort(
                    distances_squared[
                        local_candidate_indices
                    ],
                    kind="stable",
                )
            ]

            candidate_distances = np.sqrt(
                distances_squared[
                    local_candidate_indices
                ]
            )

            minimum_distance = float(
                candidate_distances[0]
            )

            distance_band = max(
                reference_edge_length * 0.75,
                minimum_distance * 0.08,
            )

            allowed_distance = (
                minimum_distance
                + distance_band
            )

            accepted_local_indices = local_candidate_indices[
                candidate_distances
                <= allowed_distance
            ]

            if accepted_local_indices.size == 0:
                accepted_local_indices = (
                    local_candidate_indices[:1]
                )

            for local_vertex_index in accepted_local_indices:
                local_vertex_index = int(
                    local_vertex_index
                )

                vertex_id = int(
                    component_vertex_ids[
                        local_vertex_index
                    ]
                )

                initial_cost = float(
                    np.sqrt(
                        distances_squared[
                            local_vertex_index
                        ]
                    )
                )

                key = (
                    int(segment_index),
                    vertex_id,
                )

                candidate_value = (
                    initial_cost,
                    float(segment_t),
                )

                existing = seed_store.get(
                    key
                )

                if existing is None:
                    seed_store[key] = candidate_value
                    continue

                existing_cost, existing_t = existing

                if initial_cost < existing_cost - 1e-12:
                    seed_store[key] = candidate_value

                elif (
                    abs(
                        initial_cost
                        - existing_cost
                    ) <= 1e-12
                    and segment_t < existing_t
                ):
                    seed_store[key] = candidate_value

def _get_reference_edge_length(
    adjacency,
) -> float:
    """
    Return the median unique mesh-edge length.
    """
    edge_lengths = []

    for vertex_id, neighbors in enumerate(
        adjacency
    ):
        for neighbor_id, edge_length in neighbors:
            if int(neighbor_id) <= vertex_id:
                continue

            edge_length = float(
                edge_length
            )

            if edge_length > 1e-12:
                edge_lengths.append(
                    edge_length
                )

    if not edge_lengths:
        raise RuntimeError(
            "Mesh surface graph contains no valid edges."
        )

    return float(
        np.median(
            np.asarray(
                edge_lengths,
                dtype=np.float64,
            )
        )
    )

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