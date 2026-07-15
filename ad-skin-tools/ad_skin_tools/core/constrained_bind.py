from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import (
    get_vertex_count,
    get_vertex_normals,
    get_vertex_positions,
    get_world_positions,
)
from ad_skin_tools.core.skin_cluster import (
    SkinClusterAdapter,
    create_closest_skin_cluster,
    find_skin_cluster,
)
from ad_skin_tools.core.undo import undo_chunk

np = ensure_numpy()


@dataclass(frozen=True)
class ConstrainedClosestOptions:
    """Settings for the v2.5 constrained hard-ownership experiment."""

    root_back_fraction: float = 0.05
    terminal_back_fraction: float = 0.35
    normal_penalty_strength: float = 2.0
    endpoint_inset: float = 0.001
    chunk_size: int = 4096


@dataclass(frozen=True)
class ConstrainedClosestResult:
    skin_cluster: str
    mesh_transform: str
    vertex_count: int
    influence_count: int
    segment_count: int
    point_count: int
    fallback_vertex_count: int
    assignment_counts: Dict[str, int]


@dataclass(frozen=True)
class _Primitives:
    point_positions: np.ndarray
    point_axes: np.ndarray
    point_back_limits: np.ndarray
    point_owner_indices: np.ndarray

    segment_starts: np.ndarray
    segment_vectors: np.ndarray
    segment_lengths_squared: np.ndarray
    segment_axes: np.ndarray
    segment_back_limits: np.ndarray
    segment_owner_indices: np.ndarray

    @property
    def point_count(self) -> int:
        return int(self.point_owner_indices.size)

    @property
    def segment_count(self) -> int:
        return int(self.segment_owner_indices.size)


def bind_object_constrained_closest(
    mesh_shape: str,
    mesh_transform: str,
    joints: Sequence[str],
    options: Optional[ConstrainedClosestOptions] = None,
) -> ConstrainedClosestResult:
    """
    Build a hard initial bind using constrained closest bone ownership.

    This is intentionally a diagnostic stage:
    - exactly one influence per vertex;
    - no topology smoothing;
    - no soft weighting;
    - no pruning pass.

    Candidate score uses distance to a parent-owned joint segment, rejects
    vertices that sit too far behind a segment root, and softly penalizes
    candidates that lie in the outward hemisphere of a vertex normal.
    """
    options = options or ConstrainedClosestOptions()
    _validate_options(options)

    if not mesh_shape or not cmds.objExists(mesh_shape):
        raise RuntimeError(
            "The loaded mesh shape no longer exists. Load the mesh again."
        )

    if not mesh_transform or not cmds.objExists(mesh_transform):
        raise RuntimeError(
            "The loaded mesh transform no longer exists. Load the mesh again."
        )

    existing_skin = find_skin_cluster(mesh_shape, required=False)

    if existing_skin:
        raise RuntimeError(
            "This mesh already has a skinCluster.\n\n"
            "Constrained initial binding is only available for an "
            "unskinned mesh."
        )

    original_selection = cmds.ls(
        selection=True,
        long=True,
        flatten=True,
    ) or []

    adapter = None

    try:
        with undo_chunk("AD Skin Constrained Closest Bone Bind"):
            # Create only the skinCluster container. Every weight row is
            # replaced below by the constrained solver result.
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=list(joints),
                max_influences=1,
            )

            influence_names = adapter.influences()
            influence_count = len(influence_names)

            if influence_count < 2:
                raise RuntimeError(
                    "The created skinCluster contains fewer than two "
                    "influences."
                )

            vertex_count = get_vertex_count(mesh_shape)

            if vertex_count <= 0:
                raise RuntimeError("The loaded mesh has no vertices.")

            vertex_ids = np.arange(vertex_count, dtype=np.int32)
            vertex_positions = get_vertex_positions(mesh_shape, vertex_ids)
            vertex_normals = get_vertex_normals(mesh_shape, vertex_ids)
            joint_positions = get_world_positions(influence_names)

            primitives = _build_primitives(
                joint_paths=influence_names,
                joint_positions=joint_positions,
                root_back_fraction=float(options.root_back_fraction),
                terminal_back_fraction=float(
                    options.terminal_back_fraction
                ),
                endpoint_inset=float(options.endpoint_inset),
            )

            owner_indices, fallback_vertex_count = _assign_owners(
                vertex_positions=vertex_positions,
                vertex_normals=vertex_normals,
                influence_count=influence_count,
                primitives=primitives,
                normal_penalty_strength=float(
                    options.normal_penalty_strength
                ),
                chunk_size=int(options.chunk_size),
            )

            weights = np.zeros(
                (vertex_count, influence_count),
                dtype=np.float64,
            )
            weights[
                np.arange(vertex_count, dtype=np.int32),
                owner_indices,
            ] = 1.0

            adapter.set_weights(
                vertex_ids=vertex_ids,
                weights=weights,
                normalize=False,
            )

            stored = adapter.get_weights(vertex_ids)
            _validate_hard_weights(
                stored.weights,
                vertex_count=vertex_count,
                influence_count=influence_count,
            )

            stored_owners = np.argmax(
                stored.weights,
                axis=1,
            ).astype(np.int32)

            assignment_counts = {
                influence: int(
                    np.count_nonzero(stored_owners == influence_index)
                )
                for influence_index, influence in enumerate(
                    stored.influences
                )
            }

            return ConstrainedClosestResult(
                skin_cluster=adapter.skin_cluster,
                mesh_transform=mesh_transform,
                vertex_count=vertex_count,
                influence_count=influence_count,
                segment_count=primitives.segment_count,
                point_count=primitives.point_count,
                fallback_vertex_count=fallback_vertex_count,
                assignment_counts=assignment_counts,
            )

    except Exception:
        if (
            adapter is not None
            and cmds.objExists(adapter.skin_cluster)
        ):
            _remove_skin_cluster(adapter.skin_cluster)
        raise

    finally:
        _restore_selection(original_selection)


def _validate_options(options: ConstrainedClosestOptions) -> None:
    if not 0.0 <= float(options.root_back_fraction) <= 1.0:
        raise ValueError(
            "root_back_fraction must be between 0.0 and 1.0."
        )

    if not 0.0 <= float(options.terminal_back_fraction) <= 1.0:
        raise ValueError(
            "terminal_back_fraction must be between 0.0 and 1.0."
        )

    if float(options.normal_penalty_strength) < 0.0:
        raise ValueError(
            "normal_penalty_strength cannot be negative."
        )

    if not 0.0 <= float(options.endpoint_inset) < 1.0:
        raise ValueError(
            "endpoint_inset must be in the range [0.0, 1.0)."
        )

    if int(options.chunk_size) < 1:
        raise ValueError("chunk_size must be at least 1.")


def _build_primitives(
    joint_paths: List[str],
    joint_positions: np.ndarray,
    root_back_fraction: float,
    terminal_back_fraction: float,
    endpoint_inset: float,
) -> _Primitives:
    point_positions = []
    point_axes = []
    point_back_limits = []
    point_owner_indices = []

    segment_starts = []
    segment_vectors = []
    segment_lengths_squared = []
    segment_axes = []
    segment_back_limits = []
    segment_owner_indices = []

    endpoint_scale = 1.0 - endpoint_inset
    epsilon = 1e-12

    for owner_index, joint_path in enumerate(joint_paths):
        start = np.asarray(
            joint_positions[owner_index],
            dtype=np.float64,
        )
        children = cmds.listRelatives(
            joint_path,
            children=True,
            type="joint",
            fullPath=True,
        ) or []

        valid_segment_count = 0

        for child in children:
            child_position = np.asarray(
                cmds.xform(
                    child,
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

            full_length = full_length_squared ** 0.5
            axis = full_vector / full_length
            vector = full_vector * endpoint_scale
            length_squared = float(np.dot(vector, vector))

            if length_squared <= epsilon:
                continue

            segment_starts.append(start)
            segment_vectors.append(vector)
            segment_lengths_squared.append(length_squared)
            segment_axes.append(axis)
            segment_back_limits.append(
                root_back_fraction * full_length
            )
            segment_owner_indices.append(owner_index)
            valid_segment_count += 1

        if valid_segment_count:
            continue

        axis = np.zeros(3, dtype=np.float64)
        back_limit = np.inf
        parents = cmds.listRelatives(
            joint_path,
            parent=True,
            type="joint",
            fullPath=True,
        ) or []

        if parents:
            parent_position = np.asarray(
                cmds.xform(
                    parents[0],
                    query=True,
                    worldSpace=True,
                    translation=True,
                ),
                dtype=np.float64,
            )
            parent_vector = start - parent_position
            parent_length_squared = float(
                np.dot(parent_vector, parent_vector)
            )

            if parent_length_squared > epsilon:
                parent_length = parent_length_squared ** 0.5
                axis = parent_vector / parent_length
                back_limit = (
                    terminal_back_fraction * parent_length
                )

        point_positions.append(start)
        point_axes.append(axis)
        point_back_limits.append(back_limit)
        point_owner_indices.append(owner_index)

    return _Primitives(
        point_positions=_matrix3(point_positions),
        point_axes=_matrix3(point_axes),
        point_back_limits=np.asarray(
            point_back_limits,
            dtype=np.float64,
        ),
        point_owner_indices=np.asarray(
            point_owner_indices,
            dtype=np.int32,
        ),
        segment_starts=_matrix3(segment_starts),
        segment_vectors=_matrix3(segment_vectors),
        segment_lengths_squared=np.asarray(
            segment_lengths_squared,
            dtype=np.float64,
        ),
        segment_axes=_matrix3(segment_axes),
        segment_back_limits=np.asarray(
            segment_back_limits,
            dtype=np.float64,
        ),
        segment_owner_indices=np.asarray(
            segment_owner_indices,
            dtype=np.int32,
        ),
    )


def _assign_owners(
    vertex_positions: np.ndarray,
    vertex_normals: np.ndarray,
    influence_count: int,
    primitives: _Primitives,
    normal_penalty_strength: float,
    chunk_size: int,
) -> Tuple[np.ndarray, int]:
    vertex_positions = np.asarray(
        vertex_positions,
        dtype=np.float64,
    )
    vertex_normals = _normalize_vectors(vertex_normals)

    if vertex_positions.shape != vertex_normals.shape:
        raise ValueError(
            "vertex_positions and vertex_normals must have matching "
            "shape."
        )

    vertex_count = int(vertex_positions.shape[0])
    owner_indices = np.empty(vertex_count, dtype=np.int32)
    fallback_vertex_count = 0

    for start_row in range(0, vertex_count, chunk_size):
        end_row = min(start_row + chunk_size, vertex_count)
        points = vertex_positions[start_row:end_row]
        normals = vertex_normals[start_row:end_row]
        block_count = end_row - start_row

        constrained_scores = np.full(
            (block_count, influence_count),
            np.inf,
            dtype=np.float64,
        )
        fallback_scores = np.full(
            (block_count, influence_count),
            np.inf,
            dtype=np.float64,
        )

        for primitive_index in range(primitives.segment_count):
            owner_index = int(
                primitives.segment_owner_indices[primitive_index]
            )
            segment_start = primitives.segment_starts[
                primitive_index
            ]
            segment_vector = primitives.segment_vectors[
                primitive_index
            ]
            length_squared = primitives.segment_lengths_squared[
                primitive_index
            ]
            axis = primitives.segment_axes[primitive_index]
            back_limit = primitives.segment_back_limits[
                primitive_index
            ]

            relative = points - segment_start[np.newaxis, :]
            segment_t = np.matmul(
                relative,
                segment_vector,
            ) / length_squared
            np.clip(segment_t, 0.0, 1.0, out=segment_t)

            closest_points = (
                segment_start[np.newaxis, :]
                + segment_t[:, np.newaxis]
                * segment_vector[np.newaxis, :]
            )
            _distance_squared, scored = _score_candidate(
                points=points,
                normals=normals,
                closest_points=closest_points,
                normal_penalty_strength=normal_penalty_strength,
            )

            np.minimum(
                fallback_scores[:, owner_index],
                scored,
                out=fallback_scores[:, owner_index],
            )

            axial = np.matmul(relative, axis)
            valid = axial >= -back_limit
            constrained_candidate = np.where(
                valid,
                scored,
                np.inf,
            )
            np.minimum(
                constrained_scores[:, owner_index],
                constrained_candidate,
                out=constrained_scores[:, owner_index],
            )

        for primitive_index in range(primitives.point_count):
            owner_index = int(
                primitives.point_owner_indices[primitive_index]
            )
            point_position = primitives.point_positions[
                primitive_index
            ]
            closest_points = np.broadcast_to(
                point_position,
                points.shape,
            )
            _distance_squared, scored = _score_candidate(
                points=points,
                normals=normals,
                closest_points=closest_points,
                normal_penalty_strength=normal_penalty_strength,
            )

            np.minimum(
                fallback_scores[:, owner_index],
                scored,
                out=fallback_scores[:, owner_index],
            )

            axis = primitives.point_axes[primitive_index]
            back_limit = primitives.point_back_limits[
                primitive_index
            ]

            if np.isfinite(back_limit):
                axial = np.matmul(
                    points - point_position[np.newaxis, :],
                    axis,
                )
                valid = axial >= -back_limit
                candidate = np.where(valid, scored, np.inf)
            else:
                candidate = scored

            np.minimum(
                constrained_scores[:, owner_index],
                candidate,
                out=constrained_scores[:, owner_index],
            )

        has_constrained_candidate = np.isfinite(
            constrained_scores
        ).any(axis=1)
        fallback_mask = ~has_constrained_candidate
        fallback_vertex_count += int(
            np.count_nonzero(fallback_mask)
        )

        final_scores = constrained_scores

        if np.any(fallback_mask):
            final_scores = constrained_scores.copy()
            final_scores[fallback_mask] = fallback_scores[
                fallback_mask
            ]

        if np.any(~np.isfinite(final_scores).any(axis=1)):
            raise RuntimeError(
                "Constrained solver could not evaluate one or more "
                "vertices."
            )

        owner_indices[start_row:end_row] = np.argmin(
            final_scores,
            axis=1,
        ).astype(np.int32)

    return owner_indices, fallback_vertex_count


def _score_candidate(
    points: np.ndarray,
    normals: np.ndarray,
    closest_points: np.ndarray,
    normal_penalty_strength: float,
) -> Tuple[np.ndarray, np.ndarray]:
    to_candidate = closest_points - points
    distances_squared = np.einsum(
        "ij,ij->i",
        to_candidate,
        to_candidate,
    )
    distances = np.sqrt(
        np.maximum(distances_squared, 1e-20)
    )
    directions = to_candidate / distances[:, np.newaxis]
    outward_amount = np.maximum(
        0.0,
        np.einsum("ij,ij->i", normals, directions),
    )
    multiplier = (
        1.0
        + normal_penalty_strength
        * outward_amount
        * outward_amount
    )
    return distances_squared, distances_squared * multiplier


def _normalize_vectors(vectors: np.ndarray) -> np.ndarray:
    vectors = np.asarray(vectors, dtype=np.float64)
    lengths = np.linalg.norm(vectors, axis=1)
    result = np.zeros_like(vectors)
    valid = lengths > 1e-12
    result[valid] = vectors[valid] / lengths[valid, np.newaxis]
    return result


def _matrix3(values) -> np.ndarray:
    if not values:
        return np.empty((0, 3), dtype=np.float64)
    return np.asarray(values, dtype=np.float64).reshape(-1, 3)


def _validate_hard_weights(
    weights: np.ndarray,
    vertex_count: int,
    influence_count: int,
) -> None:
    expected_shape = (vertex_count, influence_count)

    if weights.shape != expected_shape:
        raise RuntimeError(
            "Unexpected stored weight matrix shape.\n\n"
            f"Expected: {expected_shape}\n"
            f"Received: {weights.shape}"
        )

    if not np.all(np.isfinite(weights)):
        raise RuntimeError(
            "Constrained bind produced non-finite weights."
        )

    if np.any(weights < -1e-8):
        raise RuntimeError(
            "Constrained bind produced negative weights."
        )

    if not np.allclose(
        weights.sum(axis=1),
        1.0,
        atol=1e-6,
    ):
        raise RuntimeError(
            "Constrained bind produced rows that do not sum to 1.0."
        )

    influence_counts = np.count_nonzero(
        weights > 1e-8,
        axis=1,
    )

    if np.any(influence_counts != 1):
        invalid_count = int(
            np.count_nonzero(influence_counts != 1)
        )
        raise RuntimeError(
            "Hard ownership validation failed.\n\n"
            f"{invalid_count} vertices do not have exactly one owner."
        )


def _remove_skin_cluster(skin_cluster: str) -> None:
    try:
        cmds.skinCluster(
            skin_cluster,
            edit=True,
            unbind=True,
        )
        return
    except Exception:
        pass

    try:
        cmds.delete(skin_cluster)
    except Exception:
        pass


def _restore_selection(items) -> None:
    try:
        cmds.select(clear=True)
        if items:
            cmds.select(items, replace=True)
    except Exception:
        pass
