from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple
import heapq
import time

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.joint_surface_solver import JointSeedResult
from ad_skin_tools.core.mesh import (
    get_vertex_count,
    get_vertex_normals,
    get_vertex_positions,
    get_weighted_vertex_neighbors,
)

np = ensure_numpy()


@dataclass(frozen=True)
class SeedResolveOptions:
    """
    AD Skin Tool v2.6 Phase 2.

    Resolve raw joint-centric radial candidates into exclusive,
    local surface seed patches.

    No hierarchy or bone segment information is used.
    """

    # Candidate score:
    #
    # normalized distance to joint
    # + vertex-normal alignment error.
    distance_weight: float = 1.0
    normal_alignment_weight: float = 1.0

    # Candidate patch may only travel this far from its primary anchor
    # through connected mesh edges.
    #
    # The value is multiplied by the joint's Phase-1 local radius.
    #
    # Around 4.0 allows a patch to travel around a tubular finger
    # circumference without wandering far into another region.
    anchor_geodesic_radius_multiplier: float = 4.0

    # Diagnostic requirements.
    minimum_anchor_component_size: int = 4
    minimum_resolved_seed_count: int = 4

    # Stop instead of silently continuing when a joint loses its
    # candidate patch.
    fail_on_invalid_joint: bool = True


@dataclass(frozen=True)
class JointResolveDiagnostic:
    joint: str

    raw_candidate_count: int
    component_count: int
    anchor_component_size: int
    geodesic_patch_size: int
    resolved_seed_count: int

    removed_disconnected_count: int
    removed_geodesic_count: int
    lost_to_other_joints_count: int

    primary_anchor_vertex_id: int
    resolved_anchor_vertex_id: int

    primary_anchor_score: float
    resolved_anchor_score: float

    local_radius: float
    maximum_patch_distance: float

    valid: bool
    messages: Tuple[str, ...]


@dataclass(frozen=True)
class ResolvedSeedResult:
    mesh_shape: str
    influences: Tuple[str, ...]

    # One joint -> exclusive seed vertices.
    resolved_seed_vertex_ids: Dict[
        str,
        Tuple[int, ...],
    ]

    # One protected anchor for each joint.
    anchor_vertex_ids: Dict[
        str,
        int,
    ]

    # Vertices that were originally claimed by more than one joint.
    contested_vertex_ids: Tuple[int, ...]

    diagnostics: Tuple[
        JointResolveDiagnostic,
        ...,
    ]

    raw_unique_candidate_count: int
    resolved_unique_seed_count: int
    contested_vertex_count: int

    elapsed_seconds: float


@dataclass
class _JointWork:
    joint: str
    joint_index: int

    local_radius: float

    raw_candidates: Tuple[int, ...]
    scores: Dict[int, float]

    component_count: int
    anchor_component: Tuple[int, ...]
    geodesic_patch: Tuple[int, ...]

    primary_anchor: int
    primary_anchor_score: float

    resolved_anchor: int = -1
    resolved_anchor_score: float = float("inf")


def resolve_joint_seed_candidates(
    seed_result: JointSeedResult,
    options: Optional[SeedResolveOptions] = None,
) -> ResolvedSeedResult:
    """
    Convert raw Phase-1 candidates into exclusive local seed patches.

    Processing per joint:

    1. Score all raw candidates relative to the joint.
    2. Select the best candidate as primary anchor.
    3. Keep only the connected candidate component containing the anchor.
    4. Limit that component by topology distance from the anchor.
    5. Resolve overlapping candidate vertices globally.
    6. Protect at least one exclusive anchor for every joint.
    """
    started = time.perf_counter()

    options = options or SeedResolveOptions()

    _validate_options(
        options
    )

    mesh_shape = seed_result.mesh_shape

    if (
        not mesh_shape
        or not cmds.objExists(mesh_shape)
    ):
        raise RuntimeError(
            "The mesh from the Phase-1 seed result no longer exists."
        )

    influences = tuple(
        seed_result.influences
    )

    if len(influences) < 2:
        raise RuntimeError(
            "At least two joints are required."
        )

    vertex_count = get_vertex_count(
        mesh_shape
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

    adjacency = get_weighted_vertex_neighbors(
        mesh_shape
    )

    diagnostic_by_joint = {
        diagnostic.joint: diagnostic
        for diagnostic in seed_result.diagnostics
    }

    work_items: List[_JointWork] = []

    for joint_index, joint in enumerate(
        influences
    ):
        raw_candidates = tuple(
            sorted(
                {
                    int(vertex_id)
                    for vertex_id
                    in seed_result.seed_vertex_ids.get(
                        joint,
                        (),
                    )
                }
            )
        )

        if not raw_candidates:
            raise RuntimeError(
                "Joint has no Phase-1 seed candidates:\n{}".format(
                    joint
                )
            )

        _validate_vertex_ids(
            raw_candidates,
            vertex_count,
            joint,
        )

        phase_one_diagnostic = diagnostic_by_joint.get(
            joint
        )

        if phase_one_diagnostic is None:
            raise RuntimeError(
                "Missing Phase-1 diagnostic for joint:\n{}".format(
                    joint
                )
            )

        local_radius = max(
            float(
                phase_one_diagnostic.local_radius
            ),
            1e-8,
        )

        joint_position = np.asarray(
            cmds.xform(
                joint,
                query=True,
                worldSpace=True,
                translation=True,
            ),
            dtype=np.float64,
        )

        scores = _score_candidates(
            candidate_ids=raw_candidates,
            positions=positions,
            normals=normals,
            joint_position=joint_position,
            local_radius=local_radius,
            distance_weight=float(
                options.distance_weight
            ),
            normal_alignment_weight=float(
                options.normal_alignment_weight
            ),
        )

        ordered_candidates = sorted(
            raw_candidates,
            key=lambda vertex_id: (
                scores[vertex_id],
                vertex_id,
            ),
        )

        primary_anchor = int(
            ordered_candidates[0]
        )

        components = _candidate_components(
            candidate_ids=raw_candidates,
            adjacency=adjacency,
        )

        anchor_component = _find_anchor_component(
            components=components,
            anchor_vertex_id=primary_anchor,
        )

        maximum_patch_distance = (
            local_radius
            * float(
                options.anchor_geodesic_radius_multiplier
            )
        )

        geodesic_patch = _build_anchor_geodesic_patch(
            anchor_vertex_id=primary_anchor,
            candidate_component=anchor_component,
            adjacency=adjacency,
            maximum_distance=maximum_patch_distance,
        )

        work_items.append(
            _JointWork(
                joint=joint,
                joint_index=joint_index,
                local_radius=local_radius,
                raw_candidates=raw_candidates,
                scores=scores,
                component_count=len(
                    components
                ),
                anchor_component=tuple(
                    sorted(
                        anchor_component
                    )
                ),
                geodesic_patch=tuple(
                    sorted(
                        geodesic_patch
                    )
                ),
                primary_anchor=primary_anchor,
                primary_anchor_score=float(
                    scores[
                        primary_anchor
                    ]
                ),
            )
        )

    # Every joint needs one protected exclusive anchor before ordinary
    # candidate conflicts are resolved.
    locked_owners = _assign_exclusive_anchors(
        work_items
    )

    claims: Dict[
        int,
        List[
            Tuple[
                float,
                int,
            ]
        ],
    ] = {}

    for work in work_items:
        for vertex_id in work.geodesic_patch:
            claims.setdefault(
                int(vertex_id),
                [],
            ).append(
                (
                    float(
                        work.scores[
                            int(vertex_id)
                        ]
                    ),
                    int(
                        work.joint_index
                    ),
                )
            )

    contested_vertex_ids = tuple(
        sorted(
            vertex_id
            for vertex_id, vertex_claims
            in claims.items()
            if len(vertex_claims) > 1
        )
    )

    resolved_by_joint: List[
        Set[int]
    ] = [
        set()
        for _ in influences
    ]

    for vertex_id, vertex_claims in claims.items():
        if vertex_id in locked_owners:
            owner_index = int(
                locked_owners[
                    vertex_id
                ]
            )

        else:
            _, owner_index = min(
                vertex_claims,
                key=lambda claim: (
                    claim[0],
                    claim[1],
                ),
            )

        resolved_by_joint[
            owner_index
        ].add(
            int(vertex_id)
        )

    diagnostics: List[
        JointResolveDiagnostic
    ] = []

    invalid_diagnostics = []

    for work in work_items:
        resolved_seed_ids = resolved_by_joint[
            work.joint_index
        ]

        raw_count = len(
            work.raw_candidates
        )

        component_size = len(
            work.anchor_component
        )

        patch_size = len(
            work.geodesic_patch
        )

        resolved_count = len(
            resolved_seed_ids
        )

        removed_disconnected = (
            raw_count
            - component_size
        )

        removed_geodesic = (
            component_size
            - patch_size
        )

        lost_to_other_joints = (
            patch_size
            - resolved_count
        )

        maximum_patch_distance = (
            work.local_radius
            * float(
                options.anchor_geodesic_radius_multiplier
            )
        )

        messages = []

        if component_size < int(
            options.minimum_anchor_component_size
        ):
            messages.append(
                "anchor component has only {} candidates".format(
                    component_size
                )
            )

        if patch_size < int(
            options.minimum_anchor_component_size
        ):
            messages.append(
                "geodesic patch has only {} candidates".format(
                    patch_size
                )
            )

        if resolved_count < int(
            options.minimum_resolved_seed_count
        ):
            messages.append(
                "only {} exclusive seeds remain after conflict "
                "resolution".format(
                    resolved_count
                )
            )

        valid = not messages

        diagnostic = JointResolveDiagnostic(
            joint=work.joint,

            raw_candidate_count=raw_count,
            component_count=work.component_count,
            anchor_component_size=component_size,
            geodesic_patch_size=patch_size,
            resolved_seed_count=resolved_count,

            removed_disconnected_count=removed_disconnected,
            removed_geodesic_count=removed_geodesic,
            lost_to_other_joints_count=lost_to_other_joints,

            primary_anchor_vertex_id=work.primary_anchor,
            resolved_anchor_vertex_id=work.resolved_anchor,

            primary_anchor_score=work.primary_anchor_score,
            resolved_anchor_score=work.resolved_anchor_score,

            local_radius=work.local_radius,
            maximum_patch_distance=maximum_patch_distance,

            valid=valid,
            messages=tuple(
                messages
            ),
        )

        diagnostics.append(
            diagnostic
        )

        if not valid:
            invalid_diagnostics.append(
                diagnostic
            )

    if (
        invalid_diagnostics
        and options.fail_on_invalid_joint
    ):
        lines = [
            "Joint seed resolution failed:",
            "",
        ]

        for diagnostic in invalid_diagnostics:
            lines.append(
                "{}: {}".format(
                    diagnostic.joint,
                    "; ".join(
                        diagnostic.messages
                    ),
                )
            )

        raise RuntimeError(
            "\n".join(
                lines
            )
        )

    resolved_seed_vertex_ids = {
        joint: tuple(
            sorted(
                resolved_by_joint[
                    joint_index
                ]
            )
        )
        for joint_index, joint
        in enumerate(influences)
    }

    anchor_vertex_ids = {
        work.joint: int(
            work.resolved_anchor
        )
        for work in work_items
    }

    raw_unique_candidates = set()

    for work in work_items:
        raw_unique_candidates.update(
            work.raw_candidates
        )

    resolved_unique = set()

    for seed_ids in resolved_seed_vertex_ids.values():
        resolved_unique.update(
            seed_ids
        )

    return ResolvedSeedResult(
        mesh_shape=mesh_shape,
        influences=influences,

        resolved_seed_vertex_ids=(
            resolved_seed_vertex_ids
        ),

        anchor_vertex_ids=(
            anchor_vertex_ids
        ),

        contested_vertex_ids=(
            contested_vertex_ids
        ),

        diagnostics=tuple(
            diagnostics
        ),

        raw_unique_candidate_count=len(
            raw_unique_candidates
        ),

        resolved_unique_seed_count=len(
            resolved_unique
        ),

        contested_vertex_count=len(
            contested_vertex_ids
        ),

        elapsed_seconds=(
            time.perf_counter()
            - started
        ),
    )


def select_resolved_joint_seeds(
    result: ResolvedSeedResult,
    joint: str,
) -> None:
    """Select exclusive Phase-2 seeds for one joint."""
    joint_path = _resolve_result_joint(
        result,
        joint,
    )

    components = _vertex_components(
        mesh_shape=result.mesh_shape,
        vertex_ids=result.resolved_seed_vertex_ids[
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


def select_joint_anchor(
    result: ResolvedSeedResult,
    joint: str,
) -> None:
    """Select the protected Phase-2 anchor for one joint."""
    joint_path = _resolve_result_joint(
        result,
        joint,
    )

    vertex_id = int(
        result.anchor_vertex_ids[
            joint_path
        ]
    )

    components = _vertex_components(
        mesh_shape=result.mesh_shape,
        vertex_ids=(
            vertex_id,
        ),
    )

    cmds.select(
        components,
        replace=True,
    )


def select_contested_seed_vertices(
    result: ResolvedSeedResult,
) -> None:
    """
    Select vertices that were claimed by more than one joint before
    exclusive conflict resolution.
    """
    components = _vertex_components(
        mesh_shape=result.mesh_shape,
        vertex_ids=result.contested_vertex_ids,
    )

    cmds.select(
        clear=True
    )

    if components:
        cmds.select(
            components,
            replace=True,
        )


def create_resolved_seed_sets(
    result: ResolvedSeedResult,
    prefix: str = "AD_v26_resolvedSeed",
) -> List[str]:
    """Create one Maya set for each joint's exclusive seeds."""
    created_sets = []

    for joint in result.influences:
        short_name = joint.split(
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
            vertex_ids=result.resolved_seed_vertex_ids[
                joint
            ],
        )

        created_sets.append(
            cmds.sets(
                components,
                name=set_name,
            )
        )

    return created_sets


def print_resolved_seed_report(
    result: ResolvedSeedResult,
) -> None:
    print(
        "\n"
        "[AD Skin Tool v2.6 Resolved Joint Seeds]"
    )

    print(
        "Mesh:",
        result.mesh_shape,
    )

    print(
        "Joints:",
        len(
            result.influences
        ),
    )

    print(
        "Raw unique candidates:",
        result.raw_unique_candidate_count,
    )

    print(
        "Contested vertices:",
        result.contested_vertex_count,
    )

    print(
        "Resolved unique seeds:",
        result.resolved_unique_seed_count,
    )

    print(
        "Elapsed seconds:",
        round(
            result.elapsed_seconds,
            3,
        ),
    )

    for diagnostic in result.diagnostics:
        print(
            "\nJoint:",
            diagnostic.joint,
        )

        print(
            "  raw candidates:",
            diagnostic.raw_candidate_count,
        )

        print(
            "  candidate components:",
            diagnostic.component_count,
        )

        print(
            "  anchor component:",
            diagnostic.anchor_component_size,
        )

        print(
            "  geodesic patch:",
            diagnostic.geodesic_patch_size,
        )

        print(
            "  exclusive resolved seeds:",
            diagnostic.resolved_seed_count,
        )

        print(
            "  removed disconnected:",
            diagnostic.removed_disconnected_count,
        )

        print(
            "  removed by geodesic limit:",
            diagnostic.removed_geodesic_count,
        )

        print(
            "  lost to other joints:",
            diagnostic.lost_to_other_joints_count,
        )

        print(
            "  primary anchor:",
            diagnostic.primary_anchor_vertex_id,
        )

        print(
            "  resolved anchor:",
            diagnostic.resolved_anchor_vertex_id,
        )

        print(
            "  local radius:",
            round(
                diagnostic.local_radius,
                6,
            ),
        )

        print(
            "  maximum patch distance:",
            round(
                diagnostic.maximum_patch_distance,
                6,
            ),
        )

        print(
            "  valid:",
            diagnostic.valid,
        )

        for message in diagnostic.messages:
            print(
                "  note:",
                message,
            )


def _score_candidates(
    candidate_ids: Tuple[int, ...],
    positions: np.ndarray,
    normals: np.ndarray,
    joint_position: np.ndarray,
    local_radius: float,
    distance_weight: float,
    normal_alignment_weight: float,
) -> Dict[int, float]:
    ids = np.asarray(
        candidate_ids,
        dtype=np.int32,
    )

    candidate_positions = positions[
        ids
    ]

    candidate_normals = normals[
        ids
    ]

    to_joint = (
        joint_position[
            np.newaxis,
            :
        ]
        - candidate_positions
    )

    distances = np.linalg.norm(
        to_joint,
        axis=1,
    )

    safe_distances = np.maximum(
        distances,
        1e-12,
    )

    directions = (
        to_joint
        / safe_distances[
            :,
            np.newaxis,
        ]
    )

    inward_dots = np.einsum(
        "ij,ij->i",
        candidate_normals,
        directions,
    )

    zero_distance = (
        distances <= 1e-12
    )

    if np.any(
        zero_distance
    ):
        inward_dots = inward_dots.copy()
        inward_dots[
            zero_distance
        ] = -1.0

    # -1 = joint directly behind outward normal, ideal.
    #  0 = tangent.
    # +1 = joint in outward direction, worst.
    alignment_error = (
        0.5
        * (
            1.0
            + np.clip(
                inward_dots,
                -1.0,
                1.0,
            )
        )
    )

    normalized_distance = (
        distances
        / max(
            local_radius,
            1e-12,
        )
    )

    scores = (
        distance_weight
        * normalized_distance
        + normal_alignment_weight
        * alignment_error
        * alignment_error
    )

    return {
        int(vertex_id): float(score)
        for vertex_id, score
        in zip(
            ids,
            scores,
        )
    }


def _candidate_components(
    candidate_ids: Tuple[int, ...],
    adjacency,
) -> List[
    Set[int]
]:
    remaining = set(
        int(vertex_id)
        for vertex_id
        in candidate_ids
    )

    components = []

    while remaining:
        start = remaining.pop()

        component = {
            start
        }

        stack = [
            start
        ]

        while stack:
            vertex_id = stack.pop()

            for neighbor_id, _ in adjacency[
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

    return components


def _find_anchor_component(
    components: List[Set[int]],
    anchor_vertex_id: int,
) -> Set[int]:
    for component in components:
        if anchor_vertex_id in component:
            return component

    raise RuntimeError(
        "Primary anchor was not found in any candidate component."
    )


def _build_anchor_geodesic_patch(
    anchor_vertex_id: int,
    candidate_component: Set[int],
    adjacency,
    maximum_distance: float,
) -> Set[int]:
    distances = {
        int(anchor_vertex_id): 0.0
    }

    heap = [
        (
            0.0,
            int(
                anchor_vertex_id
            ),
        )
    ]

    kept = set()

    while heap:
        current_distance, vertex_id = heapq.heappop(
            heap
        )

        if current_distance > maximum_distance:
            continue

        if current_distance > distances.get(
            vertex_id,
            float("inf"),
        ):
            continue

        kept.add(
            vertex_id
        )

        for neighbor_id, edge_length in adjacency[
            vertex_id
        ]:
            neighbor_id = int(
                neighbor_id
            )

            if neighbor_id not in candidate_component:
                continue

            new_distance = (
                current_distance
                + float(
                    edge_length
                )
            )

            if new_distance > maximum_distance:
                continue

            if new_distance >= distances.get(
                neighbor_id,
                float("inf"),
            ):
                continue

            distances[
                neighbor_id
            ] = new_distance

            heapq.heappush(
                heap,
                (
                    new_distance,
                    neighbor_id,
                ),
            )

    return kept


def _assign_exclusive_anchors(
    work_items: List[_JointWork],
) -> Dict[int, int]:
    """
    Reserve one exclusive candidate vertex for every joint.

    Strongest anchors are reserved first. A joint whose preferred anchor
    is already reserved chooses its next best local candidate.
    """
    locked_owners = {}

    ordered_work = sorted(
        work_items,
        key=lambda work: (
            work.primary_anchor_score,
            work.joint_index,
        ),
    )

    for work in ordered_work:
        candidates = sorted(
            work.geodesic_patch,
            key=lambda vertex_id: (
                work.scores[
                    int(vertex_id)
                ],
                int(vertex_id),
            ),
        )

        selected_anchor = None

        for vertex_id in candidates:
            vertex_id = int(
                vertex_id
            )

            if vertex_id in locked_owners:
                continue

            selected_anchor = vertex_id
            break

        if selected_anchor is None:
            raise RuntimeError(
                "Could not reserve an exclusive anchor for joint:\n"
                "{}".format(
                    work.joint
                )
            )

        locked_owners[
            selected_anchor
        ] = int(
            work.joint_index
        )

        work.resolved_anchor = selected_anchor
        work.resolved_anchor_score = float(
            work.scores[
                selected_anchor
            ]
        )

    return locked_owners


def _validate_vertex_ids(
    vertex_ids: Tuple[int, ...],
    vertex_count: int,
    joint: str,
) -> None:
    invalid = [
        vertex_id
        for vertex_id in vertex_ids
        if (
            vertex_id < 0
            or vertex_id >= vertex_count
        )
    ]

    if invalid:
        raise RuntimeError(
            "Joint contains invalid candidate vertex IDs:\n"
            "{}\n{}".format(
                joint,
                invalid[:20],
            )
        )


def _resolve_result_joint(
    result: ResolvedSeedResult,
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

    if joint_path not in result.resolved_seed_vertex_ids:
        raise RuntimeError(
            "Joint was not part of the resolved result:\n{}".format(
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


def _validate_options(
    options: SeedResolveOptions,
) -> None:
    for name in (
        "distance_weight",
        "normal_alignment_weight",
    ):
        if float(
            getattr(
                options,
                name,
            )
        ) < 0.0:
            raise ValueError(
                "{} cannot be negative.".format(
                    name
                )
            )

    if float(
        options.anchor_geodesic_radius_multiplier
    ) <= 0.0:
        raise ValueError(
            "anchor_geodesic_radius_multiplier must be positive."
        )

    if int(
        options.minimum_anchor_component_size
    ) < 1:
        raise ValueError(
            "minimum_anchor_component_size must be at least 1."
        )

    if int(
        options.minimum_resolved_seed_count
    ) < 1:
        raise ValueError(
            "minimum_resolved_seed_count must be at least 1."
        )