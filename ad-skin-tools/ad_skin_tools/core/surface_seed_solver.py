from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
import heapq
import time

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import (
    get_vertex_count,
    get_vertex_normals,
    get_vertex_positions,
    get_weighted_vertex_neighbors,
    get_world_positions,
)
from ad_skin_tools.core.skin_cluster import (
    create_closest_skin_cluster,
    find_skin_cluster,
)
from ad_skin_tools.core.undo import undo_chunk

np = ensure_numpy()


@dataclass(frozen=True)
class SurfaceSeedOptions:
    """AD Skin Tool v2.6 diagnostic settings."""

    inward_dot_threshold: float = -0.05
    relaxed_inward_dot_threshold: float = 0.20
    normal_alignment_weight: float = 2.0

    segment_end_scale: float = 0.85
    segment_axial_margin: float = 0.05

    segment_bin_count: int = 6
    seeds_per_segment_bin: int = 3

    point_seed_count: int = 12

    candidate_pool_size: int = 48
    seed_min_spacing_fraction: float = 0.06

    include_unlisted_children: bool = True


@dataclass(frozen=True)
class SurfaceSeedBindResult:
    skin_cluster: str
    mesh_transform: str

    vertex_count: int
    influence_count: int

    segment_count: int
    point_count: int
    seed_count: int

    relaxed_seed_influences: Tuple[str, ...]
    forced_seed_influences: Tuple[str, ...]

    uncovered_vertex_count: int

    assignment_counts: Dict[str, int]
    seed_counts: Dict[str, int]

    elapsed_seconds: float


@dataclass(frozen=True)
class _Segment:
    owner: int

    start: np.ndarray
    vector: np.ndarray

    length: float
    length_squared: float


@dataclass(frozen=True)
class _Point:
    owner: int

    position: np.ndarray
    scale: float


@dataclass(frozen=True)
class _Seed:
    vertex_id: int
    owner: int

    score: float

    # 0 = strict normal test
    # 1 = relaxed normal test
    # 2 = forced fallback
    mode: int


def bind_object_surface_seed(
    mesh_shape: str,
    mesh_transform: str,
    joints: Sequence[str],
    options: Optional[SurfaceSeedOptions] = None,
) -> SurfaceSeedBindResult:
    """
    Build hard ownership from normal-guided surface seeds.

    Ownership is propagated through connected polygon edges using
    multi-source Dijkstra.

    Current diagnostic limitations:
    - exactly one influence per vertex;
    - no soft weighting;
    - no smoothing;
    - no pruning;
    - no UI integration.
    """
    options = options or SurfaceSeedOptions()

    _validate_options(
        options
    )

    if (
        not mesh_shape
        or not cmds.objExists(mesh_shape)
    ):
        raise RuntimeError(
            "Mesh shape does not exist."
        )

    if (
        not mesh_transform
        or not cmds.objExists(mesh_transform)
    ):
        raise RuntimeError(
            "Mesh transform does not exist."
        )

    if find_skin_cluster(
        mesh_shape,
        required=False,
    ):
        raise RuntimeError(
            "This mesh already has a skinCluster.\n\n"
            "Test v2.6 on an unskinned duplicate."
        )

    original_selection = cmds.ls(
        selection=True,
        long=True,
        flatten=True,
    ) or []

    adapter = None

    started = time.perf_counter()

    try:
        with undo_chunk(
            "AD Skin v2.6 Surface Seed Bind"
        ):
            # Maya creates the skinCluster container.
            #
            # Its temporary initial weights are replaced completely
            # after surface ownership has been calculated.
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=list(joints),
                max_influences=1,
            )

            influences = adapter.influences()

            influence_count = len(
                influences
            )

            if influence_count < 2:
                raise RuntimeError(
                    "At least two influences are required."
                )

            vertex_count = get_vertex_count(
                mesh_shape
            )

            if vertex_count <= 0:
                raise RuntimeError(
                    "The mesh contains no vertices."
                )

            vertex_ids = np.arange(
                vertex_count,
                dtype=np.int32,
            )

            positions = get_vertex_positions(
                mesh_shape,
                vertex_ids,
            )

            normals = get_vertex_normals(
                mesh_shape,
                vertex_ids,
            )

            joint_positions = get_world_positions(
                influences
            )

            segments, points = _build_primitives(
                joints=influences,
                joint_positions=joint_positions,
                options=options,
            )

            raw_seeds = _build_seeds(
                positions=positions,
                normals=normals,
                segments=segments,
                points=points,
                options=options,
            )

            seeds = _deduplicate_seeds(
                seeds=raw_seeds,
                influence_count=influence_count,
            )

            adjacency = get_weighted_vertex_neighbors(
                mesh_shape
            )

            owners, uncovered_count = _propagate(
                adjacency=adjacency,
                positions=positions,
                seeds=seeds,
            )

            weights = np.zeros(
                (
                    vertex_count,
                    influence_count,
                ),
                dtype=np.float64,
            )

            weights[
                vertex_ids,
                owners,
            ] = 1.0

            adapter.set_weights(
                vertex_ids=vertex_ids,
                weights=weights,
                normalize=False,
            )

            stored = adapter.get_weights(
                vertex_ids
            )

            _validate_hard_weights(
                weights=stored.weights,
                vertex_count=vertex_count,
                influence_count=influence_count,
            )

            stored_owners = np.argmax(
                stored.weights,
                axis=1,
            ).astype(
                np.int32
            )

            assignment_counts = {
                influence: int(
                    np.count_nonzero(
                        stored_owners
                        == influence_index
                    )
                )
                for influence_index, influence
                in enumerate(stored.influences)
            }

            seed_count_array = np.bincount(
                np.asarray(
                    [
                        seed.owner
                        for seed in seeds
                    ],
                    dtype=np.int32,
                ),
                minlength=influence_count,
            )

            seed_counts = {
                influence: int(
                    seed_count_array[
                        influence_index
                    ]
                )
                for influence_index, influence
                in enumerate(stored.influences)
            }

            relaxed_owners = sorted(
                {
                    seed.owner
                    for seed in seeds
                    if seed.mode == 1
                }
            )

            forced_owners = sorted(
                {
                    seed.owner
                    for seed in seeds
                    if seed.mode == 2
                }
            )

            return SurfaceSeedBindResult(
                skin_cluster=adapter.skin_cluster,
                mesh_transform=mesh_transform,

                vertex_count=vertex_count,
                influence_count=influence_count,

                segment_count=len(
                    segments
                ),
                point_count=len(
                    points
                ),
                seed_count=len(
                    seeds
                ),

                relaxed_seed_influences=tuple(
                    influences[index]
                    for index in relaxed_owners
                ),

                forced_seed_influences=tuple(
                    influences[index]
                    for index in forced_owners
                ),

                uncovered_vertex_count=(
                    uncovered_count
                ),

                assignment_counts=(
                    assignment_counts
                ),

                seed_counts=(
                    seed_counts
                ),

                elapsed_seconds=(
                    time.perf_counter()
                    - started
                ),
            )

    except Exception:
        if (
            adapter is not None
            and cmds.objExists(
                adapter.skin_cluster
            )
        ):
            _remove_skin_cluster(
                adapter.skin_cluster
            )

        raise

    finally:
        _restore_selection(
            original_selection
        )


def _validate_options(
    options: SurfaceSeedOptions,
) -> None:
    strict = float(
        options.inward_dot_threshold
    )

    relaxed = float(
        options.relaxed_inward_dot_threshold
    )

    if (
        not -1.0 <= strict <= 1.0
        or not -1.0 <= relaxed <= 1.0
    ):
        raise ValueError(
            "Normal thresholds must be "
            "between -1.0 and 1.0."
        )

    if relaxed < strict:
        raise ValueError(
            "relaxed_inward_dot_threshold "
            "must be greater than or equal to "
            "inward_dot_threshold."
        )

    if float(
        options.normal_alignment_weight
    ) < 0.0:
        raise ValueError(
            "normal_alignment_weight "
            "cannot be negative."
        )

    if not (
        0.05
        <= float(options.segment_end_scale)
        <= 1.0
    ):
        raise ValueError(
            "segment_end_scale must be "
            "between 0.05 and 1.0."
        )

    if not (
        0.0
        <= float(options.segment_axial_margin)
        <= 1.0
    ):
        raise ValueError(
            "segment_axial_margin must be "
            "between 0.0 and 1.0."
        )

    positive_integer_options = (
        "segment_bin_count",
        "seeds_per_segment_bin",
        "point_seed_count",
        "candidate_pool_size",
    )

    for option_name in positive_integer_options:
        value = int(
            getattr(
                options,
                option_name,
            )
        )

        if value < 1:
            raise ValueError(
                f"{option_name} must be at least 1."
            )

    if float(
        options.seed_min_spacing_fraction
    ) < 0.0:
        raise ValueError(
            "seed_min_spacing_fraction "
            "cannot be negative."
        )


def _build_primitives(
    joints: List[str],
    joint_positions: np.ndarray,
    options: SurfaceSeedOptions,
) -> Tuple[
    List[_Segment],
    List[_Point],
]:
    selected_joints = set(
        joints
    )

    segments = []
    terminal_data = []
    segment_lengths = []

    epsilon = 1e-12

    for owner_index, joint in enumerate(
        joints
    ):
        start = np.asarray(
            joint_positions[
                owner_index
            ],
            dtype=np.float64,
        )

        children = cmds.listRelatives(
            joint,
            children=True,
            type="joint",
            fullPath=True,
        ) or []

        if not options.include_unlisted_children:
            children = [
                child
                for child in children
                if child in selected_joints
            ]

        valid_child_count = 0

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

            vector = (
                child_position
                - start
            )

            length_squared = float(
                np.dot(
                    vector,
                    vector,
                )
            )

            if length_squared <= epsilon:
                continue

            length = (
                length_squared ** 0.5
            )

            segments.append(
                _Segment(
                    owner=owner_index,
                    start=start,
                    vector=vector,
                    length=length,
                    length_squared=length_squared,
                )
            )

            segment_lengths.append(
                length
            )

            valid_child_count += 1

        if valid_child_count:
            continue

        parent_scale = None

        parents = cmds.listRelatives(
            joint,
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

            parent_length = float(
                np.linalg.norm(
                    start
                    - parent_position
                )
            )

            if parent_length > epsilon:
                parent_scale = (
                    parent_length
                )

        terminal_data.append(
            (
                owner_index,
                start,
                parent_scale,
            )
        )

    if segment_lengths:
        default_point_scale = float(
            np.median(
                np.asarray(
                    segment_lengths,
                    dtype=np.float64,
                )
            )
        )
    else:
        default_point_scale = 1.0

    points = [
        _Point(
            owner=owner_index,
            position=position,
            scale=(
                parent_scale
                if parent_scale is not None
                else default_point_scale
            ),
        )
        for (
            owner_index,
            position,
            parent_scale,
        )
        in terminal_data
    ]

    if (
        not segments
        and not points
    ):
        raise RuntimeError(
            "No usable bone primitives were built."
        )

    return (
        segments,
        points,
    )


def _build_seeds(
    positions: np.ndarray,
    normals: np.ndarray,
    segments: List[_Segment],
    points: List[_Point],
    options: SurfaceSeedOptions,
) -> List[_Seed]:
    positions = np.asarray(
        positions,
        dtype=np.float64,
    )

    normals = _normalize_vectors(
        normals
    )

    if positions.shape != normals.shape:
        raise ValueError(
            "Vertex positions and normals "
            "must have matching shapes."
        )

    seeds = []

    for segment in segments:
        segment_seeds = _segment_seeds(
            positions=positions,
            normals=normals,
            segment=segment,
            options=options,
        )

        seeds.extend(
            segment_seeds
        )

    for point in points:
        point_seeds = _point_seeds(
            positions=positions,
            normals=normals,
            point=point,
            options=options,
        )

        seeds.extend(
            point_seeds
        )

    if not seeds:
        raise RuntimeError(
            "No surface seeds were generated."
        )

    return seeds


def _segment_seeds(
    positions: np.ndarray,
    normals: np.ndarray,
    segment: _Segment,
    options: SurfaceSeedOptions,
) -> List[_Seed]:
    relative = (
        positions
        - segment.start[
            np.newaxis,
            :
        ]
    )

    raw_t = np.matmul(
        relative,
        segment.vector,
    ) / segment.length_squared

    active_end = float(
        options.segment_end_scale
    )

    active_t = np.clip(
        raw_t,
        0.0,
        active_end,
    )

    closest_points = (
        segment.start[
            np.newaxis,
            :
        ]
        + active_t[
            :,
            np.newaxis,
        ]
        * segment.vector[
            np.newaxis,
            :
        ]
    )

    scores, inward_dots = _score_candidates(
        positions=positions,
        normals=normals,
        closest_points=closest_points,
        scale=segment.length,
        normal_weight=float(
            options.normal_alignment_weight
        ),
    )

    margin = float(
        options.segment_axial_margin
    )

    within_segment = (
        (raw_t >= -margin)
        & (
            raw_t
            <= active_end + margin
        )
    )

    normalized_t = np.clip(
        raw_t / active_end,
        0.0,
        1.0,
    )

    bin_count = int(
        options.segment_bin_count
    )

    result = []

    for bin_index in range(
        bin_count
    ):
        bin_start = (
            float(bin_index)
            / float(bin_count)
        )

        bin_end = (
            float(bin_index + 1)
            / float(bin_count)
        )

        if bin_index == bin_count - 1:
            in_bin = (
                (normalized_t >= bin_start)
                & (normalized_t <= bin_end)
            )
        else:
            in_bin = (
                (normalized_t >= bin_start)
                & (normalized_t < bin_end)
            )

        selected = _pick_vertices(
            positions=positions,
            scores=scores,
            inward_dots=inward_dots,
            candidate_mask=(
                within_segment
                & in_bin
            ),
            count=int(
                options.seeds_per_segment_bin
            ),
            pool_size=int(
                options.candidate_pool_size
            ),
            minimum_spacing=(
                float(
                    options.seed_min_spacing_fraction
                )
                * segment.length
            ),
            strict_threshold=float(
                options.inward_dot_threshold
            ),
            relaxed_threshold=float(
                options.relaxed_inward_dot_threshold
            ),
        )

        for vertex_id, mode in selected:
            result.append(
                _Seed(
                    vertex_id=vertex_id,
                    owner=segment.owner,
                    score=float(
                        scores[
                            vertex_id
                        ]
                    ),
                    mode=mode,
                )
            )

    return result


def _point_seeds(
    positions: np.ndarray,
    normals: np.ndarray,
    point: _Point,
    options: SurfaceSeedOptions,
) -> List[_Seed]:
    closest_points = np.broadcast_to(
        point.position,
        positions.shape,
    )

    scores, inward_dots = _score_candidates(
        positions=positions,
        normals=normals,
        closest_points=closest_points,
        scale=point.scale,
        normal_weight=float(
            options.normal_alignment_weight
        ),
    )

    selected = _pick_vertices(
        positions=positions,
        scores=scores,
        inward_dots=inward_dots,
        candidate_mask=np.ones(
            positions.shape[0],
            dtype=bool,
        ),
        count=int(
            options.point_seed_count
        ),
        pool_size=max(
            int(
                options.candidate_pool_size
            ),
            int(
                options.point_seed_count
            ) * 4,
        ),
        minimum_spacing=(
            float(
                options.seed_min_spacing_fraction
            )
            * point.scale
        ),
        strict_threshold=float(
            options.inward_dot_threshold
        ),
        relaxed_threshold=float(
            options.relaxed_inward_dot_threshold
        ),
    )

    return [
        _Seed(
            vertex_id=vertex_id,
            owner=point.owner,
            score=float(
                scores[
                    vertex_id
                ]
            ),
            mode=mode,
        )
        for vertex_id, mode
        in selected
    ]


def _score_candidates(
    positions: np.ndarray,
    normals: np.ndarray,
    closest_points: np.ndarray,
    scale: float,
    normal_weight: float,
) -> Tuple[
    np.ndarray,
    np.ndarray,
]:
    to_bone = (
        closest_points
        - positions
    )

    distance_squared = np.einsum(
        "ij,ij->i",
        to_bone,
        to_bone,
    )

    distance = np.sqrt(
        np.maximum(
            distance_squared,
            1e-20,
        )
    )

    direction = (
        to_bone
        / distance[
            :,
            np.newaxis,
        ]
    )

    inward_dots = np.einsum(
        "ij,ij->i",
        normals,
        direction,
    )

    zero_distance = (
        distance_squared
        <= 1e-20
    )

    if np.any(
        zero_distance
    ):
        inward_dots = (
            inward_dots.copy()
        )

        inward_dots[
            zero_distance
        ] = -1.0

    # inward_dot = -1:
    # bone lies directly opposite the outward normal.
    #
    # inward_dot = +1:
    # bone lies in the outward direction.
    alignment_error = (
        1.0
        + np.clip(
            inward_dots,
            -1.0,
            1.0,
        )
    )

    multiplier = (
        1.0
        + normal_weight
        * alignment_error
        * alignment_error
    )

    scale_squared = max(
        float(scale) ** 2,
        1e-12,
    )

    score = (
        distance_squared
        / scale_squared
        * multiplier
    )

    return (
        score,
        inward_dots,
    )


def _pick_vertices(
    positions: np.ndarray,
    scores: np.ndarray,
    inward_dots: np.ndarray,
    candidate_mask: np.ndarray,
    count: int,
    pool_size: int,
    minimum_spacing: float,
    strict_threshold: float,
    relaxed_threshold: float,
) -> List[
    Tuple[int, int]
]:
    vertex_ids = np.where(
        candidate_mask
    )[0]

    if vertex_ids.size == 0:
        return []

    modes = np.full(
        vertex_ids.shape[0],
        2,
        dtype=np.int32,
    )

    candidate_inward = inward_dots[
        vertex_ids
    ]

    modes[
        candidate_inward
        <= relaxed_threshold
    ] = 1

    modes[
        candidate_inward
        <= strict_threshold
    ] = 0

    # Mode penalty ensures:
    #
    # strict candidates
    #     beat relaxed candidates
    #
    # relaxed candidates
    #     beat forced candidates
    effective_scores = (
        scores[
            vertex_ids
        ]
        + modes.astype(
            np.float64
        )
        * 1_000_000.0
    )

    order = np.argsort(
        effective_scores,
        kind="mergesort",
    )

    ordered_ids = vertex_ids[
        order
    ]

    ordered_modes = modes[
        order
    ]

    pool_count = min(
        ordered_ids.size,
        max(
            pool_size,
            count * 4,
        ),
    )

    selected_ids = []
    selected_modes = []

    spacing_squared = (
        float(minimum_spacing) ** 2
    )

    for vertex_id, mode in zip(
        ordered_ids[
            :pool_count
        ],
        ordered_modes[
            :pool_count
        ],
    ):
        vertex_id = int(
            vertex_id
        )

        if (
            selected_ids
            and spacing_squared > 0.0
        ):
            selected_positions = positions[
                np.asarray(
                    selected_ids,
                    dtype=np.int32,
                )
            ]

            delta = (
                selected_positions
                - positions[
                    vertex_id
                ]
            )

            distances_squared = np.einsum(
                "ij,ij->i",
                delta,
                delta,
            )

            if np.any(
                distances_squared
                < spacing_squared
            ):
                continue

        selected_ids.append(
            vertex_id
        )

        selected_modes.append(
            int(mode)
        )

        if len(
            selected_ids
        ) >= count:
            break

    # Sparse bins may not satisfy the spacing rule.
    # Fill the remainder strictly by score.
    if len(
        selected_ids
    ) < count:
        selected_set = set(
            selected_ids
        )

        for vertex_id, mode in zip(
            ordered_ids,
            ordered_modes,
        ):
            vertex_id = int(
                vertex_id
            )

            if vertex_id in selected_set:
                continue

            selected_ids.append(
                vertex_id
            )

            selected_modes.append(
                int(mode)
            )

            selected_set.add(
                vertex_id
            )

            if len(
                selected_ids
            ) >= count:
                break

    return list(
        zip(
            selected_ids,
            selected_modes,
        )
    )


def _deduplicate_seeds(
    seeds: List[_Seed],
    influence_count: int,
) -> List[_Seed]:
    best_by_vertex = {}

    for seed in seeds:
        current = best_by_vertex.get(
            seed.vertex_id
        )

        seed_key = (
            seed.mode,
            seed.score,
            seed.owner,
        )

        if current is None:
            best_by_vertex[
                seed.vertex_id
            ] = seed

            continue

        current_key = (
            current.mode,
            current.score,
            current.owner,
        )

        if seed_key < current_key:
            best_by_vertex[
                seed.vertex_id
            ] = seed

    result = list(
        best_by_vertex.values()
    )

    counts = np.bincount(
        np.asarray(
            [
                seed.owner
                for seed in result
            ],
            dtype=np.int32,
        ),
        minlength=influence_count,
    )

    missing = np.where(
        counts == 0
    )[0]

    if missing.size:
        raise RuntimeError(
            "One or more influences lost every seed "
            "after duplicate resolution.\n\n"
            "Increase candidate_pool_size or "
            "seeds_per_segment_bin.\n\n"
            f"Missing influence indices: "
            f"{missing.tolist()}"
        )

    return sorted(
        result,
        key=lambda seed: (
            seed.vertex_id,
            seed.owner,
        ),
    )


def _propagate(
    adjacency: List[
        List[
            Tuple[int, float]
        ]
    ],
    positions: np.ndarray,
    seeds: List[_Seed],
) -> Tuple[
    np.ndarray,
    int,
]:
    vertex_count = len(
        adjacency
    )

    distances = np.full(
        vertex_count,
        np.inf,
        dtype=np.float64,
    )

    source_scores = np.full(
        vertex_count,
        np.inf,
        dtype=np.float64,
    )

    owners = np.full(
        vertex_count,
        -1,
        dtype=np.int32,
    )

    heap = []

    for seed in seeds:
        vertex_id = int(
            seed.vertex_id
        )

        owner = int(
            seed.owner
        )

        source_score = float(
            seed.score
        )

        candidate_key = (
            0.0,
            source_score,
            owner,
        )

        current_key = (
            distances[
                vertex_id
            ],
            source_scores[
                vertex_id
            ],
            int(
                owners[
                    vertex_id
                ]
            ),
        )

        if (
            owners[vertex_id] < 0
            or candidate_key < current_key
        ):
            distances[
                vertex_id
            ] = 0.0

            source_scores[
                vertex_id
            ] = source_score

            owners[
                vertex_id
            ] = owner

            heapq.heappush(
                heap,
                (
                    0.0,
                    source_score,
                    owner,
                    vertex_id,
                ),
            )

    tolerance = 1e-12

    while heap:
        (
            current_distance,
            source_score,
            owner,
            vertex_id,
        ) = heapq.heappop(
            heap
        )

        stored_key = (
            distances[
                vertex_id
            ],
            source_scores[
                vertex_id
            ],
            int(
                owners[
                    vertex_id
                ]
            ),
        )

        popped_key = (
            current_distance,
            source_score,
            owner,
        )

        if popped_key > stored_key:
            continue

        for neighbor_id, edge_length in adjacency[
            vertex_id
        ]:
            neighbor_id = int(
                neighbor_id
            )

            new_distance = (
                current_distance
                + float(edge_length)
            )

            old_distance = distances[
                neighbor_id
            ]

            should_update = (
                new_distance
                < old_distance - tolerance
            )

            if (
                not should_update
                and abs(
                    new_distance
                    - old_distance
                )
                <= tolerance
            ):
                should_update = (
                    source_score,
                    owner,
                ) < (
                    source_scores[
                        neighbor_id
                    ],
                    int(
                        owners[
                            neighbor_id
                        ]
                    ),
                )

            if not should_update:
                continue

            distances[
                neighbor_id
            ] = new_distance

            source_scores[
                neighbor_id
            ] = source_score

            owners[
                neighbor_id
            ] = owner

            heapq.heappush(
                heap,
                (
                    new_distance,
                    source_score,
                    owner,
                    neighbor_id,
                ),
            )

    uncovered = np.where(
        owners < 0
    )[0]

    if uncovered.size:
        _assign_uncovered(
            uncovered=uncovered,
            owners=owners,
            positions=positions,
            seeds=seeds,
        )

    return (
        owners,
        int(
            uncovered.size
        ),
    )


def _assign_uncovered(
    uncovered: np.ndarray,
    owners: np.ndarray,
    positions: np.ndarray,
    seeds: List[_Seed],
) -> None:
    seed_vertex_ids = np.asarray(
        [
            seed.vertex_id
            for seed in seeds
        ],
        dtype=np.int32,
    )

    seed_owners = np.asarray(
        [
            seed.owner
            for seed in seeds
        ],
        dtype=np.int32,
    )

    seed_positions = positions[
        seed_vertex_ids
    ]

    for vertex_id in uncovered:
        vertex_id = int(
            vertex_id
        )

        delta = (
            seed_positions
            - positions[
                vertex_id
            ][
                np.newaxis,
                :
            ]
        )

        distance_squared = np.einsum(
            "ij,ij->i",
            delta,
            delta,
        )

        nearest_seed = int(
            np.argmin(
                distance_squared
            )
        )

        owners[
            vertex_id
        ] = seed_owners[
            nearest_seed
        ]


def _normalize_vectors(
    vectors: np.ndarray,
) -> np.ndarray:
    vectors = np.asarray(
        vectors,
        dtype=np.float64,
    )

    lengths = np.linalg.norm(
        vectors,
        axis=1,
    )

    result = np.zeros_like(
        vectors
    )

    valid = (
        lengths > 1e-12
    )

    result[
        valid
    ] = (
        vectors[
            valid
        ]
        / lengths[
            valid,
            np.newaxis,
        ]
    )

    return result


def _validate_hard_weights(
    weights: np.ndarray,
    vertex_count: int,
    influence_count: int,
) -> None:
    expected_shape = (
        vertex_count,
        influence_count,
    )

    if weights.shape != expected_shape:
        raise RuntimeError(
            "Stored weight matrix has an "
            "unexpected shape.\n\n"
            f"Expected: {expected_shape}\n"
            f"Received: {weights.shape}"
        )

    if not np.all(
        np.isfinite(
            weights
        )
    ):
        raise RuntimeError(
            "Weights contain non-finite values."
        )

    if np.any(
        weights < -1e-8
    ):
        raise RuntimeError(
            "Weights contain negative values."
        )

    if not np.allclose(
        weights.sum(
            axis=1
        ),
        1.0,
        atol=1e-6,
    ):
        raise RuntimeError(
            "One or more weight rows "
            "do not sum to 1.0."
        )

    influence_counts = np.count_nonzero(
        weights > 1e-8,
        axis=1,
    )

    if np.any(
        influence_counts != 1
    ):
        raise RuntimeError(
            "Every vertex must have "
            "exactly one owner."
        )


def _remove_skin_cluster(
    skin_cluster: str,
) -> None:
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
        cmds.delete(
            skin_cluster
        )

    except Exception:
        pass


def _restore_selection(
    items,
) -> None:
    try:
        cmds.select(
            clear=True
        )

        if items:
            cmds.select(
                items,
                replace=True,
            )

    except Exception:
        pass