from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
import heapq
import time

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.joint_seed_competition import (
    CompetitiveSeedResult,
)
from ad_skin_tools.core.mesh import (
    get_vertex_count,
    get_weighted_vertex_neighbors,
)
from ad_skin_tools.core.skin_cluster import (
    create_closest_skin_cluster,
    find_skin_cluster,
)
from ad_skin_tools.core.joint_unseeded_shells import (
    SyntheticShellSeed,
    build_nearest_joint_shell_seeds,
)
from ad_skin_tools.core.undo import undo_chunk

np = ensure_numpy()

@dataclass(frozen=True)
class SurfacePropagationOptions:
    """
    AD Skin Tool v2.6 Phase 3 options.

    Unseeded disconnected shells receive a synthetic source seed at
    the globally nearest joint-vertex pair.
    """

    assign_unseeded_shells: bool = True

    # Optional joints that may compete for unseeded shells without
    # participating in radial Phase 1.
    additional_shell_candidate_joints: Tuple[str, ...] = ()

    shell_distance_chunk_size: int = 20000

@dataclass(frozen=True)
class SurfacePropagationResult:
    """
    AD Skin Tool v2.6 Phase 3 result.

    The result contains hard ownership only:

        one vertex = one joint = weight 1.0

    No smoothing, soft weighting, pruning, or hierarchy logic is used.
    """

    skin_cluster: str
    mesh_shape: str
    mesh_transform: str

    influences: Tuple[str, ...]

    vertex_count: int
    influence_count: int
    seed_count: int

    uncovered_vertex_count: int
    synthetic_shell_seed_count: int
    synthetic_shell_vertex_count: int

    synthetic_shell_seeds: Tuple[
        SyntheticShellSeed,
        ...,
    ]

    average_surface_distance: float
    maximum_surface_distance: float

    seed_counts: Dict[str, int]
    ownership_counts: Dict[str, int]

    owner_vertex_ids: Dict[
        str,
        Tuple[int, ...],
    ]

    elapsed_seconds: float


def bind_competitive_surface_ownership(
    competitive_result: CompetitiveSeedResult,
    options: Optional[SurfacePropagationOptions] = None,
) -> SurfacePropagationResult:
    """
    Propagate exclusive Phase-2 seeds over the polygon topology.

    Pipeline:

        competitive exclusive seeds
        -> weighted mesh graph
        -> multi-source Dijkstra
        -> one owner per vertex
        -> skinCluster
        -> hard weight matrix
        -> MFnSkinCluster read-back validation

    Important:

    - Joint hierarchy is not used.
    - Bone segments are not used.
    - World-space distance between unconnected surface regions is not used.
    - All propagation travels through connected polygon edges.
    - Unreachable vertices cause an error instead of a silent fallback.
    """
    options = options or SurfacePropagationOptions()
    
    started = time.perf_counter()

    mesh_shape = competitive_result.mesh_shape

    if (
        not mesh_shape
        or not cmds.objExists(mesh_shape)
    ):
        raise RuntimeError(
            "The mesh from the competitive seed result no longer exists."
        )

    existing_skin = find_skin_cluster(
        mesh_shape,
        required=False,
    )

    if existing_skin:
        raise RuntimeError(
            "The mesh already has a skinCluster.\n\n"
            "Test Phase 3 on an unskinned duplicate."
        )

    mesh_transform = _get_mesh_transform(
        mesh_shape
    )

    competitive_influences = tuple(
        competitive_result.influences
    )

    influences = _merge_additional_influences(
        base_influences=competitive_influences,
        additional_joints=(
            options.additional_shell_candidate_joints
        ),
    )

    if len(influences) < 2:
        raise RuntimeError(
            "At least two influences are required."
        )

    _validate_influences(
        influences
    )

    vertex_count = get_vertex_count(
        mesh_shape
    )

    if vertex_count <= 0:
        raise RuntimeError(
            "The mesh contains no vertices."
        )

    adjacency = get_weighted_vertex_neighbors(
        mesh_shape
    )

    if len(adjacency) != vertex_count:
        raise RuntimeError(
            "Topology adjacency size does not match the mesh vertex count.\n\n"
            "Vertex count: {}\n"
            "Adjacency rows: {}".format(
                vertex_count,
                len(adjacency),
            )
        )

    (
        seed_owner_by_vertex,
        competitive_seed_counts,
    ) = _collect_exclusive_seeds(
        competitive_result=competitive_result,
        influences=competitive_influences,
        vertex_count=vertex_count,
    )

    seed_counts = {
        influence: 0
        for influence in influences
    }

    seed_counts.update(
        competitive_seed_counts
    )

    (
        owner_indices,
        surface_distances,
    ) = _propagate_owners(
        adjacency=adjacency,
        seed_owner_by_vertex=seed_owner_by_vertex,
        vertex_count=vertex_count,
    )

    uncovered_vertex_ids = np.where(
        owner_indices < 0
    )[0]

    synthetic_shell_seeds = ()

    if uncovered_vertex_ids.size:
        if not options.assign_unseeded_shells:
            raise RuntimeError(
                "Phase 3 found disconnected shells without seeds.\n\n"
                "Uncovered vertices: {}\n"
                "First uncovered IDs: {}".format(
                    int(
                        uncovered_vertex_ids.size
                    ),
                    uncovered_vertex_ids[
                        :20
                    ].tolist(),
                )
            )

        synthetic_shell_seeds = (
            build_nearest_joint_shell_seeds(
                mesh_shape=mesh_shape,
                adjacency=adjacency,
                uncovered_vertex_ids=(
                    uncovered_vertex_ids
                ),
                candidate_joints=influences,
                distance_chunk_size=int(
                    options.shell_distance_chunk_size
                ),
            )
        )

        for synthetic_seed in synthetic_shell_seeds:
            seed_vertex_id = int(
                synthetic_seed.seed_vertex_id
            )

            owner_index = int(
                synthetic_seed.owner_index
            )

            if seed_vertex_id in seed_owner_by_vertex:
                raise RuntimeError(
                    "Synthetic shell seed conflicts with an existing "
                    "competitive seed.\n\n"
                    "Vertex: {}\n"
                    "Existing owner: {}\n"
                    "Synthetic owner: {}".format(
                        seed_vertex_id,
                        influences[
                            seed_owner_by_vertex[
                                seed_vertex_id
                            ]
                        ],
                        synthetic_seed.owner_joint,
                    )
                )

            seed_owner_by_vertex[
                seed_vertex_id
            ] = owner_index

            seed_counts[
                synthetic_seed.owner_joint
            ] += 1

            cmds.warning(
                "AD Skin v2.6 created synthetic seed for shell {}: "
                "vertex={}, owner={}, distance={:.6f}, "
                "shell vertices={}.".format(
                    synthetic_seed.shell_index,
                    synthetic_seed.seed_vertex_id,
                    synthetic_seed.owner_joint,
                    synthetic_seed.seed_distance,
                    synthetic_seed.shell_vertex_count,
                )
            )

        # Run the same topology propagation again.
        #
        # Seeded shells are unaffected because disconnected components
        # have no topology path between them.
        (
            owner_indices,
            surface_distances,
        ) = _propagate_owners(
            adjacency=adjacency,
            seed_owner_by_vertex=seed_owner_by_vertex,
            vertex_count=vertex_count,
        )

    uncovered_vertex_ids = np.where(
        owner_indices < 0
    )[0]

    uncovered_count = int(
        uncovered_vertex_ids.size
    )

    if uncovered_count:
        raise RuntimeError(
            "Vertices remain without an owner after synthetic shell "
            "seed generation.\n\n"
            "Count: {}\n"
            "First IDs: {}".format(
                uncovered_count,
                uncovered_vertex_ids[
                    :20
                ].tolist(),
            )
        )

    if not np.all(
        np.isfinite(
            surface_distances
        )
    ):
        raise RuntimeError(
            "Surface propagation produced non-finite distances."
        )

    average_surface_distance = float(
        np.mean(
            surface_distances
        )
    )

    maximum_surface_distance = float(
        np.max(
            surface_distances
        )
    )

    synthetic_shell_vertex_count = sum(
        seed.shell_vertex_count
        for seed in synthetic_shell_seeds
    )

    owner_vertex_ids = _build_owner_vertex_map(
        owner_indices=owner_indices,
        influences=influences,
    )

    ownership_counts = {
        influence: len(
            owner_vertex_ids[
                influence
            ]
        )
        for influence in influences
    }

    vertex_ids = np.arange(
        vertex_count,
        dtype=np.int32,
    )

    original_selection = cmds.ls(
        selection=True,
        long=True,
        flatten=True,
    ) or []

    adapter = None

    try:
        with undo_chunk(
            "AD Skin v2.6 Surface Propagation Bind"
        ):
            # Maya only creates the skinCluster container here.
            #
            # All Maya-generated temporary weights are replaced below.
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=list(
                    influences
                ),
                max_influences=1,
            )

            stored_influences = tuple(
                adapter.influences()
            )

            source_to_stored = _build_influence_column_map(
                source_influences=influences,
                stored_influences=stored_influences,
            )

            expected_stored_owners = source_to_stored[
                owner_indices
            ]

            weights = np.zeros(
                (
                    vertex_count,
                    len(
                        stored_influences
                    ),
                ),
                dtype=np.float64,
            )

            weights[
                vertex_ids,
                expected_stored_owners,
            ] = 1.0

            adapter.set_weights(
                vertex_ids=vertex_ids,
                weights=weights,
                normalize=False,
            )

            stored_data = adapter.get_weights(
                vertex_ids
            )

            _validate_stored_hard_weights(
                weights=stored_data.weights,
                vertex_count=vertex_count,
                influence_count=len(
                    stored_influences
                ),
                expected_owner_indices=(
                    expected_stored_owners
                ),
            )

            return SurfacePropagationResult(
                skin_cluster=adapter.skin_cluster,
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,

                influences=influences,

                vertex_count=vertex_count,
                influence_count=len(
                    influences
                ),
                seed_count=len(
                    seed_owner_by_vertex
                ),

                uncovered_vertex_count=(
                    uncovered_count
                ),

                synthetic_shell_seed_count=len(
                    synthetic_shell_seeds
                ),

                synthetic_shell_vertex_count=int(
                    synthetic_shell_vertex_count
                ),

                synthetic_shell_seeds=(
                    synthetic_shell_seeds
                ),

                average_surface_distance=(
                    average_surface_distance
                ),

                maximum_surface_distance=(
                    maximum_surface_distance
                ),

                seed_counts=seed_counts,

                ownership_counts=(
                    ownership_counts
                ),

                owner_vertex_ids=(
                    owner_vertex_ids
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


def select_owned_vertices(
    result: SurfacePropagationResult,
    joint: str,
) -> None:
    """
    Select every final hard-owned vertex for one joint.
    """
    joint_path = _resolve_result_joint(
        result,
        joint,
    )

    components = _vertex_components(
        mesh_shape=result.mesh_shape,
        vertex_ids=result.owner_vertex_ids[
            joint_path
        ],
    )

    cmds.select(
        clear=True
    )

    if components:
        cmds.select(
            components,
            replace=True,
        )


def create_ownership_sets(
    result: SurfacePropagationResult,
    prefix: str = "AD_v26_owner",
) -> List[str]:
    """
    Create one Maya set for every final ownership region.

    These sets are diagnostic only. They do not affect the skinCluster.
    """
    created_sets = []

    for influence in result.influences:
        short_name = influence.split(
            "|"
        )[-1]

        set_name = "{}_{}".format(
            prefix,
            short_name,
        )

        if cmds.objExists(
            set_name
        ):
            cmds.delete(
                set_name
            )

        components = _vertex_components(
            mesh_shape=result.mesh_shape,
            vertex_ids=result.owner_vertex_ids[
                influence
            ],
        )

        created_sets.append(
            cmds.sets(
                components,
                name=set_name,
            )
        )

    return created_sets


def print_surface_propagation_report(
    result: SurfacePropagationResult,
) -> None:
    print(
        "\n"
        "[AD Skin Tool v2.6 Surface Propagation]"
    )

    print(
        "Skin cluster:",
        result.skin_cluster,
    )

    print(
        "Mesh:",
        result.mesh_transform,
    )

    print(
        "Vertices:",
        result.vertex_count,
    )

    print(
        "Influences:",
        result.influence_count,
    )

    print(
        "Exclusive source seeds:",
        result.seed_count,
    )

    print(
        "Uncovered vertices:",
        result.uncovered_vertex_count,
    )

    print(
        "Synthetic shell seeds:",
        result.synthetic_shell_seed_count,
    )

    print(
        "Synthetic shell vertices:",
        result.synthetic_shell_vertex_count,
    )

    for seed in result.synthetic_shell_seeds:
        print(
            "  shell {}: seed_vertex={}, owner={}, "
            "distance={}, vertices={}".format(
                seed.shell_index,
                seed.seed_vertex_id,
                seed.owner_joint,
                round(
                    seed.seed_distance,
                    6,
                ),
                seed.shell_vertex_count,
            )
        )

    print(
        "Average surface distance:",
        round(
            result.average_surface_distance,
            6,
        ),
    )

    print(
        "Maximum surface distance:",
        round(
            result.maximum_surface_distance,
            6,
        ),
    )

    print(
        "Elapsed seconds:",
        round(
            result.elapsed_seconds,
            3,
        ),
    )

    print(
        "\n"
        "Per-joint result:"
    )

    for influence in result.influences:
        print(
            "  {}: seeds={}, owned={}".format(
                influence,
                result.seed_counts[
                    influence
                ],
                result.ownership_counts[
                    influence
                ],
            )
        )


def _collect_exclusive_seeds(
    competitive_result: CompetitiveSeedResult,
    influences: Tuple[str, ...],
    vertex_count: int,
) -> Tuple[
    Dict[int, int],
    Dict[str, int],
]:
    """
    Validate that every source seed belongs to exactly one joint.
    """
    seed_owner_by_vertex: Dict[
        int,
        int,
    ] = {}

    seed_counts: Dict[
        str,
        int,
    ] = {}

    for owner_index, influence in enumerate(
        influences
    ):
        source_ids = competitive_result.resolved_seed_vertex_ids.get(
            influence
        )

        if source_ids is None:
            raise RuntimeError(
                "Competitive result is missing seed data for:\n{}".format(
                    influence
                )
            )

        unique_ids = tuple(
            sorted(
                {
                    int(vertex_id)
                    for vertex_id
                    in source_ids
                }
            )
        )

        if not unique_ids:
            raise RuntimeError(
                "Influence has no competitive seeds:\n{}".format(
                    influence
                )
            )

        seed_counts[
            influence
        ] = len(
            unique_ids
        )

        for vertex_id in unique_ids:
            if (
                vertex_id < 0
                or vertex_id >= vertex_count
            ):
                raise RuntimeError(
                    "Competitive seed contains an invalid vertex ID.\n\n"
                    "Influence: {}\n"
                    "Vertex: {}\n"
                    "Vertex count: {}".format(
                        influence,
                        vertex_id,
                        vertex_count,
                    )
                )

            previous_owner = seed_owner_by_vertex.get(
                vertex_id
            )

            if previous_owner is not None:
                raise RuntimeError(
                    "Competitive seed exclusivity validation failed.\n\n"
                    "Vertex {} is assigned to both:\n"
                    "{}\n"
                    "{}".format(
                        vertex_id,
                        influences[
                            previous_owner
                        ],
                        influence,
                    )
                )

            seed_owner_by_vertex[
                vertex_id
            ] = owner_index

    if not seed_owner_by_vertex:
        raise RuntimeError(
            "The competitive result contains no usable seeds."
        )

    return (
        seed_owner_by_vertex,
        seed_counts,
    )


def _propagate_owners(
    adjacency,
    seed_owner_by_vertex: Dict[int, int],
    vertex_count: int,
) -> Tuple[
    np.ndarray,
    np.ndarray,
]:
    """
    Multi-source Dijkstra propagation.

    Every seed begins at surface distance 0.0.

    Tie-breaking is deterministic:

        1. shortest surface distance;
        2. lower owner index;
        3. lower source seed vertex ID.
    """
    distances = np.full(
        vertex_count,
        np.inf,
        dtype=np.float64,
    )

    owner_indices = np.full(
        vertex_count,
        -1,
        dtype=np.int32,
    )

    source_seed_ids = np.full(
        vertex_count,
        -1,
        dtype=np.int32,
    )

    heap = []

    for seed_vertex_id in sorted(
        seed_owner_by_vertex
    ):
        owner_index = int(
            seed_owner_by_vertex[
                seed_vertex_id
            ]
        )

        distances[
            seed_vertex_id
        ] = 0.0

        owner_indices[
            seed_vertex_id
        ] = owner_index

        source_seed_ids[
            seed_vertex_id
        ] = seed_vertex_id

        heapq.heappush(
            heap,
            (
                0.0,
                owner_index,
                seed_vertex_id,
                seed_vertex_id,
            ),
        )

    tolerance = 1e-12

    while heap:
        (
            current_distance,
            owner_index,
            source_seed_id,
            vertex_id,
        ) = heapq.heappop(
            heap
        )

        stored_distance = float(
            distances[
                vertex_id
            ]
        )

        stored_owner = int(
            owner_indices[
                vertex_id
            ]
        )

        stored_source = int(
            source_seed_ids[
                vertex_id
            ]
        )

        if (
            current_distance
            > stored_distance + tolerance
        ):
            continue

        if (
            abs(
                current_distance
                - stored_distance
            )
            <= tolerance
            and (
                owner_index,
                source_seed_id,
            )
            != (
                stored_owner,
                stored_source,
            )
        ):
            continue

        for (
            neighbor_id,
            edge_length,
        ) in adjacency[
            vertex_id
        ]:
            neighbor_id = int(
                neighbor_id
            )

            edge_length = float(
                edge_length
            )

            if (
                not np.isfinite(
                    edge_length
                )
                or edge_length <= 0.0
            ):
                raise RuntimeError(
                    "Mesh topology contains an invalid edge cost.\n\n"
                    "Vertex: {}\n"
                    "Neighbor: {}\n"
                    "Edge length: {}".format(
                        vertex_id,
                        neighbor_id,
                        edge_length,
                    )
                )

            new_distance = (
                current_distance
                + edge_length
            )

            old_distance = float(
                distances[
                    neighbor_id
                ]
            )

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
                current_key = (
                    int(
                        owner_indices[
                            neighbor_id
                        ]
                    ),
                    int(
                        source_seed_ids[
                            neighbor_id
                        ]
                    ),
                )

                candidate_key = (
                    owner_index,
                    source_seed_id,
                )

                should_update = (
                    current_key[0] < 0
                    or candidate_key
                    < current_key
                )

            if not should_update:
                continue

            distances[
                neighbor_id
            ] = new_distance

            owner_indices[
                neighbor_id
            ] = owner_index

            source_seed_ids[
                neighbor_id
            ] = source_seed_id

            heapq.heappush(
                heap,
                (
                    new_distance,
                    owner_index,
                    source_seed_id,
                    neighbor_id,
                ),
            )

    return (
        owner_indices,
        distances,
    )



def _connected_components_from_ids(
    adjacency,
    vertex_ids: np.ndarray,
) -> List[set]:
    """
    Split uncovered vertices into disconnected topology components.
    """
    remaining = {
        int(vertex_id)
        for vertex_id in vertex_ids
    }

    components = []

    while remaining:
        start_vertex = remaining.pop()

        component = {
            start_vertex
        }

        stack = [
            start_vertex
        ]

        while stack:
            vertex_id = stack.pop()

            for neighbor_id, _edge_length in adjacency[
                vertex_id
            ]:
                neighbor_id = int(
                    neighbor_id
                )

                if neighbor_id not in remaining:
                    continue

                remaining.remove(
                    neighbor_id
                )

                component.add(
                    neighbor_id
                )

                stack.append(
                    neighbor_id
                )

        components.append(
            component
        )

    components.sort(
        key=len,
        reverse=True,
    )

    return components

def _build_owner_vertex_map(
    owner_indices: np.ndarray,
    influences: Tuple[str, ...],
) -> Dict[
    str,
    Tuple[int, ...],
]:
    return {
        influence: tuple(
            np.where(
                owner_indices
                == owner_index
            )[0].astype(
                np.int32
            ).tolist()
        )
        for owner_index, influence
        in enumerate(influences)
    }


def _build_influence_column_map(
    source_influences: Tuple[str, ...],
    stored_influences: Tuple[str, ...],
) -> np.ndarray:
    """
    Map Phase-2 influence indices to MFnSkinCluster column indices.

    Maya normally preserves the input order, but Phase 3 does not rely
    on that assumption.
    """
    stored_index_by_name = {
        influence: index
        for index, influence
        in enumerate(
            stored_influences
        )
    }

    missing = [
        influence
        for influence in source_influences
        if influence not in stored_index_by_name
    ]

    unexpected = [
        influence
        for influence in stored_influences
        if influence not in set(
            source_influences
        )
    ]

    if missing or unexpected:
        raise RuntimeError(
            "skinCluster influence membership does not match the "
            "competitive seed result.\n\n"
            "Missing from skinCluster:\n{}\n\n"
            "Unexpected in skinCluster:\n{}".format(
                "\n".join(
                    missing
                )
                if missing
                else "None",
                "\n".join(
                    unexpected
                )
                if unexpected
                else "None",
            )
        )

    return np.asarray(
        [
            stored_index_by_name[
                influence
            ]
            for influence in source_influences
        ],
        dtype=np.int32,
    )


def _validate_stored_hard_weights(
    weights: np.ndarray,
    vertex_count: int,
    influence_count: int,
    expected_owner_indices: np.ndarray,
) -> None:
    expected_shape = (
        vertex_count,
        influence_count,
    )

    if weights.shape != expected_shape:
        raise RuntimeError(
            "Stored weight matrix has an unexpected shape.\n\n"
            "Expected: {}\n"
            "Received: {}".format(
                expected_shape,
                weights.shape,
            )
        )

    if not np.all(
        np.isfinite(
            weights
        )
    ):
        raise RuntimeError(
            "Stored weights contain non-finite values."
        )

    if np.any(
        weights < -1e-8
    ):
        raise RuntimeError(
            "Stored weights contain negative values."
        )

    row_sums = weights.sum(
        axis=1
    )

    if not np.allclose(
        row_sums,
        1.0,
        atol=1e-6,
    ):
        invalid_rows = np.where(
            np.abs(
                row_sums
                - 1.0
            )
            > 1e-6
        )[0]

        raise RuntimeError(
            "Stored weight rows do not all sum to 1.0.\n\n"
            "Invalid rows: {}\n"
            "First invalid IDs: {}".format(
                int(
                    invalid_rows.size
                ),
                invalid_rows[
                    :20
                ].tolist(),
            )
        )

    active_counts = np.count_nonzero(
        weights > 1e-8,
        axis=1,
    )

    if np.any(
        active_counts != 1
    ):
        invalid_rows = np.where(
            active_counts != 1
        )[0]

        raise RuntimeError(
            "Hard ownership validation failed.\n\n"
            "{} vertices do not have exactly one active influence.\n"
            "First invalid IDs: {}".format(
                int(
                    invalid_rows.size
                ),
                invalid_rows[
                    :20
                ].tolist(),
            )
        )

    stored_owner_indices = np.argmax(
        weights,
        axis=1,
    ).astype(
        np.int32
    )

    mismatch = np.where(
        stored_owner_indices
        != expected_owner_indices
    )[0]

    if mismatch.size:
        raise RuntimeError(
            "Maya stored ownership differs from the calculated "
            "surface ownership.\n\n"
            "Mismatched vertices: {}\n"
            "First mismatched IDs: {}".format(
                int(
                    mismatch.size
                ),
                mismatch[
                    :20
                ].tolist(),
            )
        )


def _validate_influences(
    influences: Tuple[str, ...],
) -> None:
    seen = set()

    for influence in influences:
        matches = cmds.ls(
            influence,
            long=True,
            type="joint",
        ) or []

        if not matches:
            raise RuntimeError(
                "Influence no longer exists:\n{}".format(
                    influence
                )
            )

        joint_path = matches[0]

        if joint_path != influence:
            raise RuntimeError(
                "Influence path changed after competitive seed solving.\n\n"
                "Stored path:\n{}\n\n"
                "Current path:\n{}".format(
                    influence,
                    joint_path,
                )
            )

        if joint_path in seen:
            raise RuntimeError(
                "Duplicate influence found:\n{}".format(
                    joint_path
                )
            )

        seen.add(
            joint_path
        )


def _get_mesh_transform(
    mesh_shape: str,
) -> str:
    parents = cmds.listRelatives(
        mesh_shape,
        parent=True,
        fullPath=True,
    ) or []

    if not parents:
        raise RuntimeError(
            "Could not resolve the mesh transform from:\n{}".format(
                mesh_shape
            )
        )

    mesh_transform = parents[0]

    if not cmds.objExists(
        mesh_transform
    ):
        raise RuntimeError(
            "Resolved mesh transform no longer exists:\n{}".format(
                mesh_transform
            )
        )

    return mesh_transform


def _resolve_result_joint(
    result: SurfacePropagationResult,
    joint: str,
) -> str:
    matches = cmds.ls(
        joint,
        long=True,
        type="joint",
    ) or []

    if not matches:
        raise RuntimeError(
            "Joint does not exist: {}".format(
                joint
            )
        )

    joint_path = matches[0]

    if joint_path not in result.owner_vertex_ids:
        raise RuntimeError(
            "Joint was not included in this Phase-3 result:\n{}".format(
                joint_path
            )
        )

    return joint_path


def _vertex_components(
    mesh_shape: str,
    vertex_ids,
) -> List[str]:
    parents = cmds.listRelatives(
        mesh_shape,
        parent=True,
        fullPath=True,
    ) or []

    mesh_name = (
        parents[0]
        if parents
        else mesh_shape
    )

    return [
        "{}.vtx[{}]".format(
            mesh_name,
            int(
                vertex_id
            ),
        )
        for vertex_id in vertex_ids
    ]


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

def _merge_additional_influences(
    base_influences: Tuple[str, ...],
    additional_joints: Tuple[str, ...],
) -> Tuple[str, ...]:
    result = list(
        base_influences
    )

    seen = set(
        base_influences
    )

    for joint in additional_joints:
        matches = cmds.ls(
            joint,
            long=True,
            type="joint",
        ) or []

        if not matches:
            raise RuntimeError(
                "Additional shell candidate joint does not exist:\n{}".format(
                    joint
                )
            )

        joint_path = matches[0]

        if joint_path in seen:
            continue

        seen.add(
            joint_path
        )

        result.append(
            joint_path
        )

    return tuple(
        result
    )