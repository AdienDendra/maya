from dataclasses import dataclass
from typing import List, Sequence, Set, Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import (
    get_vertex_positions,
    get_world_positions,
)

np = ensure_numpy()


@dataclass(frozen=True)
class SyntheticShellSeed:
    """
    A synthetic source seed created for one disconnected shell that
    contains no competitive surface seeds.
    """

    shell_index: int
    shell_vertex_count: int

    owner_index: int
    owner_joint: str

    seed_vertex_id: int
    seed_distance: float

    shell_vertex_ids: Tuple[int, ...]


def build_nearest_joint_shell_seeds(
    mesh_shape: str,
    adjacency,
    uncovered_vertex_ids: np.ndarray,
    candidate_joints: Sequence[str],
    distance_chunk_size: int = 20000,
) -> Tuple[SyntheticShellSeed, ...]:
    """
    Create one synthetic seed for each disconnected unseeded shell.

    For every shell, examine every candidate joint and every shell vertex.
    The globally closest joint-vertex pair becomes the shell's source seed.

    No hierarchy, naming, orientation, or body-side assumptions are used.
    """
    uncovered_vertex_ids = np.asarray(
        uncovered_vertex_ids,
        dtype=np.int32,
    )

    if uncovered_vertex_ids.size == 0:
        return ()

    if int(distance_chunk_size) < 1:
        raise ValueError(
            "distance_chunk_size must be at least 1."
        )

    joints = _normalize_joint_paths(
        candidate_joints
    )

    if not joints:
        raise RuntimeError(
            "At least one candidate joint is required."
        )

    vertex_count = len(
        adjacency
    )

    _validate_vertex_ids(
        vertex_ids=uncovered_vertex_ids,
        vertex_count=vertex_count,
    )

    components = _connected_components_from_ids(
        adjacency=adjacency,
        vertex_ids=uncovered_vertex_ids,
    )

    all_vertex_ids = np.arange(
        vertex_count,
        dtype=np.int32,
    )

    all_positions = get_vertex_positions(
        mesh_shape,
        all_vertex_ids,
    )

    joint_positions = get_world_positions(
        list(
            joints
        )
    )

    assignments: List[
        SyntheticShellSeed
    ] = []

    for shell_index, component in enumerate(
        components
    ):
        component_ids = np.asarray(
            sorted(
                component
            ),
            dtype=np.int32,
        )

        (
            owner_index,
            seed_vertex_id,
            seed_distance,
        ) = _nearest_joint_vertex_pair(
            component_ids=component_ids,
            all_positions=all_positions,
            joint_positions=joint_positions,
            distance_chunk_size=int(
                distance_chunk_size
            ),
        )

        assignments.append(
            SyntheticShellSeed(
                shell_index=int(
                    shell_index
                ),

                shell_vertex_count=int(
                    component_ids.size
                ),

                owner_index=int(
                    owner_index
                ),

                owner_joint=joints[
                    owner_index
                ],

                seed_vertex_id=int(
                    seed_vertex_id
                ),

                seed_distance=float(
                    seed_distance
                ),

                shell_vertex_ids=tuple(
                    int(vertex_id)
                    for vertex_id
                    in component_ids
                ),
            )
        )

    return tuple(
        assignments
    )


def _nearest_joint_vertex_pair(
    component_ids: np.ndarray,
    all_positions: np.ndarray,
    joint_positions: np.ndarray,
    distance_chunk_size: int,
) -> Tuple[int, int, float]:
    """
    Find the globally shortest distance between any joint and any vertex
    in one connected shell.

    Tie-breaking:

        1. shortest squared distance;
        2. lower joint index;
        3. lower vertex ID.
    """
    best_squared_distance = np.inf
    best_joint_index = -1
    best_vertex_id = -1

    tolerance = 1e-12

    for start in range(
        0,
        int(
            component_ids.size
        ),
        distance_chunk_size,
    ):
        chunk_ids = component_ids[
            start:
            start + distance_chunk_size
        ]

        chunk_positions = all_positions[
            chunk_ids
        ]

        delta = (
            chunk_positions[
                :,
                np.newaxis,
                :
            ]
            - joint_positions[
                np.newaxis,
                :,
                :
            ]
        )

        squared_distances = np.einsum(
            "vji,vji->vj",
            delta,
            delta,
        )

        flat_index = int(
            np.argmin(
                squared_distances
            )
        )

        local_vertex_row, local_joint_index = (
            np.unravel_index(
                flat_index,
                squared_distances.shape,
            )
        )

        local_squared_distance = float(
            squared_distances[
                local_vertex_row,
                local_joint_index,
            ]
        )

        local_vertex_id = int(
            chunk_ids[
                local_vertex_row
            ]
        )

        local_joint_index = int(
            local_joint_index
        )

        better = (
            local_squared_distance
            < best_squared_distance
            - tolerance
        )

        tied = (
            abs(
                local_squared_distance
                - best_squared_distance
            )
            <= tolerance
        )

        deterministic_tie_winner = (
            best_joint_index < 0
            or (
                local_joint_index,
                local_vertex_id,
            )
            < (
                best_joint_index,
                best_vertex_id,
            )
        )

        if (
            better
            or (
                tied
                and deterministic_tie_winner
            )
        ):
            best_squared_distance = (
                local_squared_distance
            )

            best_joint_index = (
                local_joint_index
            )

            best_vertex_id = (
                local_vertex_id
            )

    if (
        best_joint_index < 0
        or best_vertex_id < 0
        or not np.isfinite(
            best_squared_distance
        )
    ):
        raise RuntimeError(
            "Could not determine a nearest joint-vertex pair "
            "for an unseeded shell."
        )

    return (
        best_joint_index,
        best_vertex_id,
        float(
            np.sqrt(
                best_squared_distance
            )
        ),
    )


def _connected_components_from_ids(
    adjacency,
    vertex_ids: np.ndarray,
) -> List[Set[int]]:
    remaining = {
        int(vertex_id)
        for vertex_id in vertex_ids
    }

    components: List[
        Set[int]
    ] = []

    while remaining:
        start_vertex = min(
            remaining
        )

        remaining.remove(
            start_vertex
        )

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
        key=lambda component: (
            -len(
                component
            ),
            min(
                component
            ),
        )
    )

    return components


def _normalize_joint_paths(
    joints: Sequence[str],
) -> Tuple[str, ...]:
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
                "Candidate joint does not exist:\n{}".format(
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


def _validate_vertex_ids(
    vertex_ids: np.ndarray,
    vertex_count: int,
) -> None:
    invalid = vertex_ids[
        (
            vertex_ids < 0
        )
        | (
            vertex_ids >= vertex_count
        )
    ]

    if invalid.size:
        raise RuntimeError(
            "Unseeded shell contains invalid vertex IDs.\n\n"
            "First invalid IDs: {}".format(
                invalid[
                    :20
                ].tolist()
            )
        )