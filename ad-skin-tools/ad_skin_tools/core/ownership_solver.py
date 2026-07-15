from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

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
    opposite_pair_count: int
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
    vertex_normals: Optional[np.ndarray] = None,
    smooth_iterations: int = 4,
    max_influences: int = 5,
    prune_threshold: float = 0.0001,
    include_unlisted_children: bool = True,
    endpoint_inset: float = 0.001,
    distance_chunk_size: int = 8192,
    smoothing_chunk_size: int = 2048,
    opposite_normal_dot_threshold: float = -0.7,
    opposite_distance_scale: float = 3.0,
    pair_chunk_size: int = 256,
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

    if pair_chunk_size < 1:
        raise ValueError(
            "pair_chunk_size must be at least 1."
        )

    if not (
        -1.0
        <= opposite_normal_dot_threshold
        <= 1.0
    ):
        raise ValueError(
            "opposite_normal_dot_threshold must be "
            "between -1.0 and 1.0."
        )

    if opposite_distance_scale <= 0.0:
        raise ValueError(
            "opposite_distance_scale must be greater than 0."
        )

    if vertex_normals is not None:
        vertex_normals = np.asarray(
            vertex_normals,
            dtype=np.float64,
        )

        if vertex_normals.shape != vertex_positions.shape:
            raise ValueError(
                "vertex_normals must have the same shape "
                "as vertex_positions."
            )

        if not np.all(
            np.isfinite(vertex_normals)
        ):
            invalid_normal_count = int(
                np.count_nonzero(
                    ~np.isfinite(
                        vertex_normals
                    ).all(axis=1)
                )
            )

            raise ValueError(
                "vertex_normals contains non-finite values.\n\n"
                f"Invalid normals: {invalid_normal_count}"
            )
            
        normal_lengths = np.linalg.norm(
            vertex_normals,
            axis=1,
            keepdims=True,
        )

        invalid_normals = (
            normal_lengths[:, 0] <= 1e-12
        )

        if np.any(invalid_normals):
            raise ValueError(
                "vertex_normals contains zero-length normals."
            )

        vertex_normals = (
            vertex_normals
            / normal_lengths
        )

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

    opposite_pairs = np.empty(
        (0, 2),
        dtype=np.int32,
    )

    if smooth_iterations:
        neighbor_indices, neighbor_counts = (
            _build_neighbor_matrix(
                neighbors
            )
        )

        if vertex_normals is not None:
            opposite_pairs = (
                _build_opposite_vertex_pairs(
                    vertex_positions=vertex_positions,
                    vertex_normals=vertex_normals,
                    neighbors=neighbors,
                    normal_dot_threshold=(
                        opposite_normal_dot_threshold
                    ),
                    distance_scale=(
                        opposite_distance_scale
                    ),
                    chunk_size=pair_chunk_size,
                )
            )

        weights = _relax_weights(
            weights=weights,
            neighbor_indices=neighbor_indices,
            neighbor_counts=neighbor_counts,
            opposite_pairs=opposite_pairs,
            iterations=smooth_iterations,
            chunk_size=smoothing_chunk_size,
        )

        if not np.all(
            np.isfinite(weights)
        ):
            invalid_row_count = int(
                np.count_nonzero(
                    ~np.isfinite(
                        weights
                    ).all(axis=1)
                )
            )

            raise RuntimeError(
                "Ownership solver produced non-finite weights "
                "before pruning.\n\n"
                f"Invalid rows: {invalid_row_count}"
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
        opposite_pair_count=int(
            opposite_pairs.shape[0]
        ),
        average_influence_count=float(
            influence_counts.mean()
        ),
        max_influence_count=int(
            influence_counts.max()
        ),
        hard_assignment_counts=(
            hard_assignment_counts
        ),
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
    """
    Build ownership points and parent-owned joint lines.

    Some production rigs use a leaf influence joint located exactly at
    the same position as a separate hierarchy joint:

        deformation_joint
        └─ skin_influence_joint

    In that case, the parent joint is used only as a hierarchy anchor.
    The actual listed influence remains the owner written to skin weights.

    Connections between selected influences are discovered by finding the
    nearest selected hierarchy-anchor ancestor.
    """
    point_positions = []
    point_owner_indices = []

    segment_starts = []
    segment_vectors = []
    segment_lengths_squared = []
    segment_owner_indices = []

    anchor_paths = []

    for owner_index, joint_path in enumerate(joint_paths):
        anchor_path = _resolve_hierarchy_anchor(
            influence_path=joint_path,
            influence_position=joint_positions[owner_index],
        )

        anchor_paths.append(anchor_path)

    anchor_to_owner = {}

    for owner_index, anchor_path in enumerate(anchor_paths):
        existing_owner = anchor_to_owner.get(anchor_path)

        if existing_owner is not None:
            raise RuntimeError(
                "Multiple influences resolved to the same hierarchy anchor.\n\n"
                f"Anchor: {anchor_path}\n"
                f"Influence A: {joint_paths[existing_owner]}\n"
                f"Influence B: {joint_paths[owner_index]}"
            )

        anchor_to_owner[anchor_path] = owner_index

    selected_children_by_owner = [
        []
        for _ in joint_paths
    ]

    # Determine parent-child relationships between the listed influences.
    #
    # The path may contain unselected intermediary nodes:
    #
    # parent anchor
    #   └─ CHN
    #       └─ NUL
    #           └─ child anchor
    for child_owner_index, child_anchor in enumerate(anchor_paths):
        parent_anchor = _find_nearest_selected_anchor_ancestor(
            anchor_path=child_anchor,
            anchor_to_owner=anchor_to_owner,
        )

        if parent_anchor is None:
            continue

        parent_owner_index = anchor_to_owner[parent_anchor]

        if parent_owner_index == child_owner_index:
            continue

        selected_children_by_owner[
            parent_owner_index
        ].append(child_owner_index)

    for owner_index, influence_path in enumerate(joint_paths):
        start_position = joint_positions[owner_index]
        valid_segment_count = 0

        selected_child_indices = sorted(
            set(
                selected_children_by_owner[
                    owner_index
                ]
            )
        )

        for child_owner_index in selected_child_indices:
            child_position = joint_positions[
                child_owner_index
            ]

            created = _append_ownership_segment(
                start_position=start_position,
                end_position=child_position,
                owner_index=owner_index,
                endpoint_inset=endpoint_inset,
                segment_starts=segment_starts,
                segment_vectors=segment_vectors,
                segment_lengths_squared=segment_lengths_squared,
                segment_owner_indices=segment_owner_indices,
            )

            if created:
                valid_segment_count += 1

        # Optional support for an ordinary selected joint whose child joint
        # was not included as a skinCluster influence.
        #
        # This is only used when no selected child segment was found.
        if (
            valid_segment_count == 0
            and include_unlisted_children
        ):
            anchor_path = anchor_paths[
                owner_index
            ]

            direct_joint_children = cmds.listRelatives(
                anchor_path,
                children=True,
                type="joint",
                fullPath=True,
            ) or []

            for child_path in direct_joint_children:
                # A co-located leaf influence may itself be a child of the
                # hierarchy anchor. It must not create a zero-length line.
                if child_path == influence_path:
                    continue

                # Selected child anchors were handled above.
                if child_path in anchor_to_owner:
                    continue

                child_position = np.asarray(
                    cmds.xform(
                        child_path,
                        query=True,
                        worldSpace=True,
                        translation=True,
                    ),
                    dtype=np.float64,
                )

                created = _append_ownership_segment(
                    start_position=start_position,
                    end_position=child_position,
                    owner_index=owner_index,
                    endpoint_inset=endpoint_inset,
                    segment_starts=segment_starts,
                    segment_vectors=segment_vectors,
                    segment_lengths_squared=segment_lengths_squared,
                    segment_owner_indices=segment_owner_indices,
                )

                if created:
                    valid_segment_count += 1

        # Terminal and disconnected influences remain point primitives.
        if valid_segment_count == 0:
            point_positions.append(
                start_position
            )

            point_owner_indices.append(
                owner_index
            )

    return _OwnershipPrimitives(
        point_positions=_as_matrix3(
            point_positions
        ),
        point_owner_indices=np.asarray(
            point_owner_indices,
            dtype=np.int32,
        ),
        segment_starts=_as_matrix3(
            segment_starts
        ),
        segment_vectors=_as_matrix3(
            segment_vectors
        ),
        segment_lengths_squared=np.asarray(
            segment_lengths_squared,
            dtype=np.float64,
        ),
        segment_owner_indices=np.asarray(
            segment_owner_indices,
            dtype=np.int32,
        ),
    )

def _resolve_hierarchy_anchor(
    influence_path: str,
    influence_position: np.ndarray,
    position_tolerance: float = 1e-5,
) -> str:
    """
    Resolve the node used to understand influence hierarchy.

    When an influence is a leaf joint positioned exactly on its parent
    joint, the parent becomes its hierarchy anchor.

    This detects proxy/envelope influence joints without relying on a
    naming convention such as ENV, Bind, Skin, or Deform.
    """
    parents = cmds.listRelatives(
        influence_path,
        parent=True,
        fullPath=True,
    ) or []

    if not parents:
        return influence_path

    parent_path = parents[0]

    if cmds.nodeType(parent_path) != "joint":
        return influence_path

    parent_position = np.asarray(
        cmds.xform(
            parent_path,
            query=True,
            worldSpace=True,
            translation=True,
        ),
        dtype=np.float64,
    )

    distance = float(
        np.linalg.norm(
            parent_position
            - np.asarray(
                influence_position,
                dtype=np.float64,
            )
        )
    )

    if distance <= float(position_tolerance):
        return parent_path

    return influence_path

def _find_nearest_selected_anchor_ancestor(
    anchor_path: str,
    anchor_to_owner: dict,
):
    """
    Walk upward through all DAG intermediary nodes until the nearest
    selected hierarchy anchor is found.

    Intermediary joints and transforms do not need to be skin influences.
    """
    current_path = anchor_path

    while True:
        parents = cmds.listRelatives(
            current_path,
            parent=True,
            fullPath=True,
        ) or []

        if not parents:
            return None

        current_path = parents[0]

        if current_path in anchor_to_owner:
            return current_path

def _append_ownership_segment(
    start_position: np.ndarray,
    end_position: np.ndarray,
    owner_index: int,
    endpoint_inset: float,
    segment_starts: list,
    segment_vectors: list,
    segment_lengths_squared: list,
    segment_owner_indices: list,
) -> bool:
    """
    Append one parent-owned line segment.

    Returns False for zero-length or unusable segments.
    """
    start_position = np.asarray(
        start_position,
        dtype=np.float64,
    )

    end_position = np.asarray(
        end_position,
        dtype=np.float64,
    )

    full_vector = (
        end_position
        - start_position
    )

    full_length_squared = float(
        np.dot(
            full_vector,
            full_vector,
        )
    )

    if full_length_squared <= 1e-12:
        return False

    endpoint_scale = (
        1.0
        - float(endpoint_inset)
    )

    segment_vector = (
        full_vector
        * endpoint_scale
    )

    segment_length_squared = float(
        np.dot(
            segment_vector,
            segment_vector,
        )
    )

    if segment_length_squared <= 1e-12:
        return False

    segment_starts.append(
        start_position
    )

    segment_vectors.append(
        segment_vector
    )

    segment_lengths_squared.append(
        segment_length_squared
    )

    segment_owner_indices.append(
        int(owner_index)
    )

    return True
    
                
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

def _build_opposite_vertex_pairs(
    vertex_positions: np.ndarray,
    vertex_normals: np.ndarray,
    neighbors: List[List[int]],
    normal_dot_threshold: float,
    distance_scale: float,
    chunk_size: int,
) -> np.ndarray:
    """
    Find simple one-to-one opposite vertex pairs.

    A candidate must:
    - be spatially close;
    - not be the source vertex;
    - not be a direct topology neighbor;
    - have an opposing normal;
    - choose the source vertex back as its closest candidate.

    No barycentric interpolation or face raycasting is used.
    """
    vertex_count = int(
        vertex_positions.shape[0]
    )

    median_edge_length = (
        _get_median_edge_length(
            vertex_positions=vertex_positions,
            neighbors=neighbors,
        )
    )

    maximum_distance = (
        median_edge_length
        * float(distance_scale)
    )

    maximum_distance_squared = (
        maximum_distance
        * maximum_distance
    )

    nearest_candidates = np.full(
        vertex_count,
        -1,
        dtype=np.int32,
    )

    for start_row in range(
        0,
        vertex_count,
        chunk_size,
    ):
        end_row = min(
            start_row + chunk_size,
            vertex_count,
        )

        chunk_positions = vertex_positions[
            start_row:end_row
        ]

        distances_squared = (
            _pairwise_squared_distances(
                chunk_positions,
                vertex_positions,
            )
        )

        normal_dots = np.matmul(
            vertex_normals[start_row:end_row],
            vertex_normals.T,
        )

        distances_squared[
            normal_dots
            > normal_dot_threshold
        ] = np.inf

        distances_squared[
            distances_squared
            > maximum_distance_squared
        ] = np.inf

        for local_row, vertex_id in enumerate(
            range(start_row, end_row)
        ):
            # Never pair a vertex with itself.
            distances_squared[
                local_row,
                vertex_id,
            ] = np.inf

            # Direct topology neighbors belong to the same surface patch.
            connected_vertices = neighbors[
                vertex_id
            ]

            if connected_vertices:
                distances_squared[
                    local_row,
                    np.asarray(
                        connected_vertices,
                        dtype=np.int32,
                    ),
                ] = np.inf

        best_indices = np.argmin(
            distances_squared,
            axis=1,
        ).astype(np.int32)

        best_distances = np.take_along_axis(
            distances_squared,
            best_indices[:, np.newaxis],
            axis=1,
        )[:, 0]

        valid_rows = np.isfinite(
            best_distances
        )

        chunk_result = nearest_candidates[
            start_row:end_row
        ]

        chunk_result[valid_rows] = (
            best_indices[valid_rows]
        )

    # Keep only reciprocal one-to-one matches:
    #
    # A chooses B
    # B chooses A
    pairs = []

    for vertex_a in range(vertex_count):
        vertex_b = int(
            nearest_candidates[
                vertex_a
            ]
        )

        if vertex_b < 0:
            continue

        if vertex_b <= vertex_a:
            continue

        if (
            int(
                nearest_candidates[
                    vertex_b
                ]
            )
            != vertex_a
        ):
            continue

        pairs.append(
            (
                vertex_a,
                vertex_b,
            )
        )

    if not pairs:
        return np.empty(
            (0, 2),
            dtype=np.int32,
        )

    return np.asarray(
        pairs,
        dtype=np.int32,
    ).reshape(-1, 2)

def _get_median_edge_length(
    vertex_positions: np.ndarray,
    neighbors: List[List[int]],
) -> float:
    """
    Estimate local mesh scale using the median unique edge length.
    """
    edge_lengths = []

    for vertex_id, connected_vertices in enumerate(
        neighbors
    ):
        source_position = vertex_positions[
            vertex_id
        ]

        for neighbor_id in connected_vertices:
            neighbor_id = int(
                neighbor_id
            )

            # Count every undirected edge once.
            if neighbor_id <= vertex_id:
                continue

            delta = (
                vertex_positions[neighbor_id]
                - source_position
            )

            length = float(
                np.linalg.norm(
                    delta
                )
            )

            if length > 1e-12:
                edge_lengths.append(
                    length
                )

    if not edge_lengths:
        raise RuntimeError(
            "Cannot estimate opposite-pair distance: "
            "the mesh contains no valid topology edges."
        )

    return float(
        np.median(
            np.asarray(
                edge_lengths,
                dtype=np.float64,
            )
        )
    )

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
    opposite_pairs: np.ndarray,
    iterations: int,
    chunk_size: int,
) -> np.ndarray:
    """
    Relax weights through direct topology neighbors.

    After every complete topology-smoothing pass, reciprocal opposite
    vertex pairs are assigned their shared average weight.
    """
    vertex_count, influence_count = weights.shape

    current = np.asarray(
        weights,
        dtype=np.float64,
    ).copy()

    for iteration_index in range(iterations):
        padded = np.vstack(
            [
                current,
                np.zeros(
                    (1, influence_count),
                    dtype=np.float64,
                ),
            ]
        )

        next_weights = np.empty_like(
            current
        )

        # First calculate every vertex row.
        for start_row in range(
            0,
            vertex_count,
            chunk_size,
        ):
            end_row = min(
                start_row + chunk_size,
                vertex_count,
            )

            gathered = padded[
                neighbor_indices[
                    start_row:end_row
                ]
            ]

            next_weights[
                start_row:end_row
            ] = (
                gathered.sum(axis=1)
                / neighbor_counts[
                    start_row:end_row,
                    np.newaxis,
                ]
            )

        # Only read opposite-pair rows after all rows have been initialized.
        if opposite_pairs.size:
            pair_a = opposite_pairs[
                :,
                0,
            ]

            pair_b = opposite_pairs[
                :,
                1,
            ]

            paired_average = (
                next_weights[pair_a]
                + next_weights[pair_b]
            ) * 0.5

            next_weights[
                pair_a
            ] = paired_average

            next_weights[
                pair_b
            ] = paired_average

        if not np.all(
            np.isfinite(next_weights)
        ):
            invalid_row_count = int(
                np.count_nonzero(
                    ~np.isfinite(
                        next_weights
                    ).all(axis=1)
                )
            )

            raise RuntimeError(
                "Ownership relaxation produced non-finite weights.\n\n"
                f"Iteration: {iteration_index + 1}\n"
                f"Invalid rows: {invalid_row_count}"
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
