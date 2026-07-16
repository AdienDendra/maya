from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
import math
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import (
    get_dag_path,
    get_vertex_count,
    get_vertex_positions,
)

np = ensure_numpy()


@dataclass(frozen=True)
class JointSeedOptions:
    """
    AD Skin Tool v2.6 Phase 1.

    Joint-centric radial first-hit seed capture.

    Assumptions:
    - joints are inside the mesh;
    - the mesh is closed;
    - polygon normals point outward.
    """

    # Jumlah ray radial yang ditembakkan dari setiap joint.
    ray_count: int = 8192

    # Maximum ray distance dihitung dari diagonal bounding box mesh.
    max_ray_distance_multiplier: float = 2.0

    ray_tolerance: float = 1e-6

    # Joint yang benar-benar berada di dalam closed mesh seharusnya
    # hampir selalu menemukan first intersection.
    minimum_hit_coverage: float = 0.90

    # Ray dari dalam ke luar seharusnya bergerak searah outward face normal.
    minimum_exit_dot: float = 0.05

    minimum_inside_confidence: float = 0.80

    # Estimasi ukuran lokal volume di sekitar joint.
    local_radius_percentile: float = 35.0

    # First hit yang terlalu jauh dibuang agar joint palm tidak menangkap
    # fingertip, atau joint finger tidak menangkap area jauh sepanjang jari.
    local_radius_multiplier: float = 1.75

    minimum_accepted_hits: int = 32
    minimum_unique_seeds: int = 8

    # "nearest":
    #   hanya vertex terdekat dengan ray hit.
    #
    # "all":
    #   seluruh vertex dari face yang terkena ray.
    face_vertex_mode: str = "all"

    # Mengutamakan ray yang benar-benar keluar mengikuti outward normal.
    normal_alignment_weight: float = 1.0

    # Penalti tambahan bila vertex face jauh dari posisi ray hit.
    vertex_offset_weight: float = 0.25

    # True:
    #   hentikan proses bila ada joint yang diduga berada di luar mesh
    #   atau tidak memperoleh cukup seed.
    fail_on_invalid_joint: bool = True


@dataclass(frozen=True)
class JointSeedDiagnostic:
    joint: str

    hit_coverage: float
    inside_confidence: float

    local_radius: float
    accepted_distance_limit: float

    hit_count: int
    outward_hit_count: int
    accepted_hit_count: int
    seed_count: int

    valid: bool
    messages: Tuple[str, ...]


@dataclass(frozen=True)
class JointSeedResult:
    mesh_shape: str
    influences: Tuple[str, ...]

    seed_vertex_ids: Dict[
        str,
        Tuple[int, ...],
    ]

    diagnostics: Tuple[
        JointSeedDiagnostic,
        ...,
    ]

    elapsed_seconds: float


@dataclass(frozen=True)
class _Hit:
    point: np.ndarray
    distance: float
    face_id: int
    exit_dot: float


def solve_joint_surface_seeds(
    mesh_shape: str,
    joints: Sequence[str],
    options: Optional[JointSeedOptions] = None,
) -> JointSeedResult:
    """
    Capture a local surface patch independently for every joint.

    No hierarchy, parent, child, or bone segment is used.

    No skinCluster is created in this phase.
    """
    started = time.perf_counter()

    options = options or JointSeedOptions()

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

    influences = tuple(
        _normalize_joint_paths(
            joints
        )
    )

    if len(influences) < 2:
        raise RuntimeError(
            "At least two joints are required."
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

    vertex_positions = get_vertex_positions(
        mesh_shape,
        vertex_ids,
    )

    directions = _fibonacci_sphere(
        int(options.ray_count)
    )

    max_distance = (
        _world_bbox_diagonal(
            mesh_shape
        )
        * float(
            options.max_ray_distance_multiplier
        )
    )

    mesh_fn = om.MFnMesh(
        get_dag_path(
            mesh_shape
        )
    )

    accel_params = (
        mesh_fn.autoUniformGridParams()
    )

    # Ribuan ray biasanya mengenai face yang sama berkali-kali.
    # Cache menghindari pembacaan normal dan vertex face berulang.
    face_cache: Dict[
        int,
        Tuple[
            np.ndarray,
            np.ndarray,
        ],
    ] = {}

    seeds_by_joint: Dict[
        str,
        Tuple[int, ...],
    ] = {}

    diagnostics: List[
        JointSeedDiagnostic
    ] = []

    try:
        for joint in influences:
            joint_position = np.asarray(
                cmds.xform(
                    joint,
                    query=True,
                    worldSpace=True,
                    translation=True,
                ),
                dtype=np.float64,
            )

            seeds, diagnostic = _solve_one_joint(
                mesh_fn=mesh_fn,
                accel_params=accel_params,
                face_cache=face_cache,
                vertex_positions=vertex_positions,
                joint=joint,
                joint_position=joint_position,
                directions=directions,
                max_distance=max_distance,
                options=options,
            )

            seeds_by_joint[
                joint
            ] = tuple(
                seeds
            )

            diagnostics.append(
                diagnostic
            )

    finally:
        try:
            mesh_fn.freeCachedIntersectionAccelerator()

        except Exception:
            pass

    invalid = [
        item
        for item in diagnostics
        if not item.valid
    ]

    if (
        invalid
        and options.fail_on_invalid_joint
    ):
        lines = [
            "Joint seed validation failed:",
            "",
        ]

        for item in invalid:
            lines.append(
                "{}: {}".format(
                    item.joint,
                    "; ".join(
                        item.messages
                    ),
                )
            )

        raise RuntimeError(
            "\n".join(
                lines
            )
        )

    return JointSeedResult(
        mesh_shape=mesh_shape,
        influences=influences,
        seed_vertex_ids=seeds_by_joint,
        diagnostics=tuple(
            diagnostics
        ),
        elapsed_seconds=(
            time.perf_counter()
            - started
        ),
    )


def select_joint_seeds(
    result: JointSeedResult,
    joint: str,
) -> None:
    """
    Select seed vertices milik satu joint.

    Gunakan ini untuk memastikan joint hanya menangkap surface pada
    volume tempat joint tersebut berada.
    """
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

    if joint_path not in result.seed_vertex_ids:
        raise RuntimeError(
            "Joint was not part of this solve: {}".format(
                joint_path
            )
        )

    parents = cmds.listRelatives(
        result.mesh_shape,
        parent=True,
        fullPath=True,
    ) or []

    mesh_name = (
        parents[0]
        if parents
        else result.mesh_shape
    )

    components = [
        "{}.vtx[{}]".format(
            mesh_name,
            vertex_id,
        )
        for vertex_id
        in result.seed_vertex_ids[
            joint_path
        ]
    ]

    cmds.select(
        clear=True
    )

    if components:
        cmds.select(
            components,
            replace=True,
        )


def create_joint_seed_sets(
    result: JointSeedResult,
    prefix: str = "AD_v26_seed",
) -> List[str]:
    """
    Create satu Maya set untuk setiap joint.

    Ini memudahkan pemeriksaan seed setiap joint melalui Outliner.
    Existing set dengan nama sama akan diganti.
    """
    parents = cmds.listRelatives(
        result.mesh_shape,
        parent=True,
        fullPath=True,
    ) or []

    mesh_name = (
        parents[0]
        if parents
        else result.mesh_shape
    )

    created = []

    for joint in result.influences:
        short_name = (
            joint.split("|")[-1]
        )

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

        components = [
            "{}.vtx[{}]".format(
                mesh_name,
                vertex_id,
            )
            for vertex_id
            in result.seed_vertex_ids[
                joint
            ]
        ]

        created.append(
            cmds.sets(
                components,
                name=set_name,
            )
        )

    return created


def print_joint_seed_report(
    result: JointSeedResult,
) -> None:
    print(
        "\n"
        "[AD Skin Tool v2.6 Joint Surface Seeds]"
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
        "Elapsed seconds:",
        round(
            result.elapsed_seconds,
            3,
        ),
    )

    for item in result.diagnostics:
        print(
            "\nJoint:",
            item.joint,
        )

        print(
            "  hit coverage:",
            round(
                item.hit_coverage,
                4,
            ),
        )

        print(
            "  inside confidence:",
            round(
                item.inside_confidence,
                4,
            ),
        )

        print(
            "  local radius:",
            round(
                item.local_radius,
                6,
            ),
        )

        print(
            "  accepted distance limit:",
            round(
                item.accepted_distance_limit,
                6,
            ),
        )

        print(
            "  hits:",
            item.hit_count,
        )

        print(
            "  outward hits:",
            item.outward_hit_count,
        )

        print(
            "  accepted hits:",
            item.accepted_hit_count,
        )

        print(
            "  seeds:",
            item.seed_count,
        )

        print(
            "  valid:",
            item.valid,
        )

        for message in item.messages:
            print(
                "  note:",
                message,
            )


def _solve_one_joint(
    mesh_fn,
    accel_params,
    face_cache,
    vertex_positions,
    joint,
    joint_position,
    directions,
    max_distance,
    options,
):
    hits: List[
        _Hit
    ] = []

    for direction in directions:
        intersection = _closest_intersection(
            mesh_fn=mesh_fn,
            accel_params=accel_params,
            source=joint_position,
            direction=direction,
            max_distance=max_distance,
            tolerance=float(
                options.ray_tolerance
            ),
        )

        if intersection is None:
            continue

        (
            hit_point,
            hit_distance,
            face_id,
        ) = intersection

        (
            face_normal,
            _,
        ) = _face_data(
            mesh_fn,
            face_id,
            face_cache,
        )

        # Joint berada di dalam mesh:
        # ray yang keluar seharusnya searah outward face normal.
        exit_dot = float(
            np.dot(
                face_normal,
                direction,
            )
        )

        hits.append(
            _Hit(
                point=hit_point,
                distance=hit_distance,
                face_id=face_id,
                exit_dot=exit_dot,
            )
        )

    ray_count = int(
        directions.shape[0]
    )

    hit_count = len(
        hits
    )

    hit_coverage = (
        hit_count
        / float(ray_count)
    )

    outward_hits = [
        hit
        for hit in hits
        if hit.exit_dot
        >= float(
            options.minimum_exit_dot
        )
    ]

    outward_count = len(
        outward_hits
    )

    inside_confidence = (
        outward_count
        / float(hit_count)
        if hit_count
        else 0.0
    )

    messages: List[
        str
    ] = []

    if hit_coverage < float(
        options.minimum_hit_coverage
    ):
        messages.append(
            "hit coverage {:.3f} is below {:.3f}".format(
                hit_coverage,
                float(
                    options.minimum_hit_coverage
                ),
            )
        )

    if inside_confidence < float(
        options.minimum_inside_confidence
    ):
        messages.append(
            "inside confidence {:.3f} is below {:.3f}".format(
                inside_confidence,
                float(
                    options.minimum_inside_confidence
                ),
            )
        )

    if not outward_hits:
        return (
            [],
            JointSeedDiagnostic(
                joint=joint,
                hit_coverage=hit_coverage,
                inside_confidence=inside_confidence,
                local_radius=0.0,
                accepted_distance_limit=0.0,
                hit_count=hit_count,
                outward_hit_count=0,
                accepted_hit_count=0,
                seed_count=0,
                valid=False,
                messages=tuple(
                    messages
                    + [
                        "no outward first-hit rays"
                    ]
                ),
            ),
        )

    distances = np.asarray(
        [
            hit.distance
            for hit in outward_hits
        ],
        dtype=np.float64,
    )

    # Lower percentile dipakai sebagai estimasi ketebalan lokal.
    # Ray yang berjalan sepanjang jari/palm cenderung jauh dan tidak
    # mendominasi radius ini.
    local_radius = float(
        np.percentile(
            distances,
            float(
                options.local_radius_percentile
            ),
        )
    )

    accepted_limit = (
        local_radius
        * float(
            options.local_radius_multiplier
        )
    )

    accepted_hits = [
        hit
        for hit in outward_hits
        if hit.distance
        <= accepted_limit
    ]

    if len(accepted_hits) < int(
        options.minimum_accepted_hits
    ):
        messages.append(
            "accepted hit count {} is below {}".format(
                len(
                    accepted_hits
                ),
                int(
                    options.minimum_accepted_hits
                ),
            )
        )

    # Satu vertex dapat terkena banyak ray.
    # Simpan score terbaik saja untuk joint ini.
    best_by_vertex: Dict[
        int,
        Tuple[
            float,
            float,
            float,
        ],
    ] = {}

    for hit in accepted_hits:
        (
            _,
            face_vertex_ids,
        ) = _face_data(
            mesh_fn,
            hit.face_id,
            face_cache,
        )

        if (
            options.face_vertex_mode
            == "nearest"
        ):
            face_positions = (
                vertex_positions[
                    face_vertex_ids
                ]
            )

            delta = (
                face_positions
                - hit.point[
                    np.newaxis,
                    :
                ]
            )

            local_index = int(
                np.argmin(
                    np.einsum(
                        "ij,ij->i",
                        delta,
                        delta,
                    )
                )
            )

            candidate_ids = [
                int(
                    face_vertex_ids[
                        local_index
                    ]
                )
            ]

        else:
            candidate_ids = [
                int(
                    vertex_id
                )
                for vertex_id
                in face_vertex_ids
            ]

        for vertex_id in candidate_ids:
            vertex_offset = float(
                np.linalg.norm(
                    vertex_positions[
                        vertex_id
                    ]
                    - hit.point
                )
            )

            alignment_error = (
                1.0
                - min(
                    max(
                        hit.exit_dot,
                        0.0,
                    ),
                    1.0,
                )
            )

            score = (
                hit.distance
                / max(
                    local_radius,
                    1e-12,
                )
                + float(
                    options.normal_alignment_weight
                )
                * alignment_error
                * alignment_error
                + float(
                    options.vertex_offset_weight
                )
                * vertex_offset
                / max(
                    local_radius,
                    1e-12,
                )
            )

            key = (
                float(score),
                float(
                    hit.distance
                ),
                -float(
                    hit.exit_dot
                ),
            )

            current = (
                best_by_vertex.get(
                    vertex_id
                )
            )

            if (
                current is None
                or key < current
            ):
                best_by_vertex[
                    vertex_id
                ] = key

    ordered = sorted(
        best_by_vertex.items(),
        key=lambda item: (
            item[1],
            item[0],
        ),
    )

    seeds = [
        int(
            vertex_id
        )
        for vertex_id, _
        in ordered
    ]

    if len(seeds) < int(
        options.minimum_unique_seeds
    ):
        messages.append(
            "unique seed count {} is below {}".format(
                len(
                    seeds
                ),
                int(
                    options.minimum_unique_seeds
                ),
            )
        )

    valid = (
        hit_coverage
        >= float(
            options.minimum_hit_coverage
        )
        and inside_confidence
        >= float(
            options.minimum_inside_confidence
        )
        and len(
            accepted_hits
        )
        >= int(
            options.minimum_accepted_hits
        )
        and len(
            seeds
        )
        >= int(
            options.minimum_unique_seeds
        )
    )

    return (
        seeds,
        JointSeedDiagnostic(
            joint=joint,
            hit_coverage=hit_coverage,
            inside_confidence=inside_confidence,
            local_radius=local_radius,
            accepted_distance_limit=accepted_limit,
            hit_count=hit_count,
            outward_hit_count=outward_count,
            accepted_hit_count=len(
                accepted_hits
            ),
            seed_count=len(
                seeds
            ),
            valid=valid,
            messages=tuple(
                messages
            ),
        ),
    )


def _closest_intersection(
    mesh_fn,
    accel_params,
    source,
    direction,
    max_distance,
    tolerance,
):
    try:
        hit = mesh_fn.closestIntersection(
            om.MFloatPoint(
                float(
                    source[0]
                ),
                float(
                    source[1]
                ),
                float(
                    source[2]
                ),
            ),
            om.MFloatVector(
                float(
                    direction[0]
                ),
                float(
                    direction[1]
                ),
                float(
                    direction[2]
                ),
            ),
            om.MSpace.kWorld,
            float(
                max_distance
            ),
            False,
            accelParams=accel_params,
            tolerance=float(
                tolerance
            ),
        )

    except RuntimeError:
        return None

    if not hit:
        return None

    point = hit[0]

    return (
        np.asarray(
            [
                point.x,
                point.y,
                point.z,
            ],
            dtype=np.float64,
        ),
        float(
            hit[1]
        ),
        int(
            hit[2]
        ),
    )


def _face_data(
    mesh_fn,
    face_id,
    cache,
):
    cached = cache.get(
        face_id
    )

    if cached is not None:
        return cached

    normal = mesh_fn.getPolygonNormal(
        int(
            face_id
        ),
        om.MSpace.kWorld,
    )

    normal_array = np.asarray(
        [
            normal.x,
            normal.y,
            normal.z,
        ],
        dtype=np.float64,
    )

    normal_length = float(
        np.linalg.norm(
            normal_array
        )
    )

    if normal_length > 1e-12:
        normal_array /= normal_length

    vertex_ids = np.asarray(
        mesh_fn.getPolygonVertices(
            int(
                face_id
            )
        ),
        dtype=np.int32,
    )

    cache[
        face_id
    ] = (
        normal_array,
        vertex_ids,
    )

    return cache[
        face_id
    ]


def _fibonacci_sphere(
    count: int,
) -> np.ndarray:
    """
    Generate deterministic, approximately uniform directions on a sphere.
    """
    index = np.arange(
        count,
        dtype=np.float64,
    )

    golden_angle = (
        math.pi
        * (
            3.0
            - math.sqrt(
                5.0
            )
        )
    )

    y = (
        1.0
        - 2.0
        * (
            index + 0.5
        )
        / float(
            count
        )
    )

    radius = np.sqrt(
        np.maximum(
            0.0,
            1.0
            - y
            * y,
        )
    )

    theta = (
        golden_angle
        * index
    )

    directions = np.column_stack(
        (
            np.cos(
                theta
            )
            * radius,
            y,
            np.sin(
                theta
            )
            * radius,
        )
    ).astype(
        np.float64
    )

    lengths = np.linalg.norm(
        directions,
        axis=1,
    )

    directions /= lengths[
        :,
        np.newaxis,
    ]

    return directions


def _world_bbox_diagonal(
    mesh_shape: str,
) -> float:
    bounds = cmds.exactWorldBoundingBox(
        mesh_shape
    )

    minimum = np.asarray(
        bounds[:3],
        dtype=np.float64,
    )

    maximum = np.asarray(
        bounds[3:],
        dtype=np.float64,
    )

    diagonal = float(
        np.linalg.norm(
            maximum
            - minimum
        )
    )

    if diagonal <= 1e-8:
        raise RuntimeError(
            "Mesh bounding box is too small."
        )

    return diagonal


def _normalize_joint_paths(
    joints: Sequence[str],
) -> List[str]:
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
                "Joint does not exist: {}".format(
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

    return result


def _validate_options(
    options: JointSeedOptions,
) -> None:
    if int(
        options.ray_count
    ) < 128:
        raise ValueError(
            "ray_count must be at least 128."
        )

    if float(
        options.max_ray_distance_multiplier
    ) <= 0.0:
        raise ValueError(
            "max_ray_distance_multiplier must be positive."
        )

    if float(
        options.ray_tolerance
    ) <= 0.0:
        raise ValueError(
            "ray_tolerance must be positive."
        )

    for name in (
        "minimum_hit_coverage",
        "minimum_inside_confidence",
    ):
        value = float(
            getattr(
                options,
                name,
            )
        )

        if not 0.0 <= value <= 1.0:
            raise ValueError(
                "{} must be between 0.0 and 1.0.".format(
                    name
                )
            )

    if not -1.0 <= float(
        options.minimum_exit_dot
    ) <= 1.0:
        raise ValueError(
            "minimum_exit_dot must be between -1.0 and 1.0."
        )

    if not (
        0.0
        < float(
            options.local_radius_percentile
        )
        <= 100.0
    ):
        raise ValueError(
            "local_radius_percentile must be in (0, 100]."
        )

    if float(
        options.local_radius_multiplier
    ) <= 0.0:
        raise ValueError(
            "local_radius_multiplier must be positive."
        )

    if int(
        options.minimum_accepted_hits
    ) < 1:
        raise ValueError(
            "minimum_accepted_hits must be at least 1."
        )

    if int(
        options.minimum_unique_seeds
    ) < 1:
        raise ValueError(
            "minimum_unique_seeds must be at least 1."
        )

    if options.face_vertex_mode not in (
        "nearest",
        "all",
    ):
        raise ValueError(
            "face_vertex_mode must be 'nearest' or 'all'."
        )

    for name in (
        "normal_alignment_weight",
        "vertex_offset_weight",
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