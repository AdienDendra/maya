from dataclasses import dataclass
from typing import Dict, List

import maya.cmds as cmds


BIND_METHOD_CLOSEST_DISTANCE = "closest_distance"
BIND_METHOD_CLOSEST_HIERARCHY = "closest_hierarchy"
BIND_METHOD_HEAT_MAP = "heat_map"
BIND_METHOD_GEODESIC_VOXEL = "geodesic_voxel"


_BIND_METHOD_IDS: Dict[str, int] = {
    BIND_METHOD_CLOSEST_DISTANCE: 0,
    BIND_METHOD_CLOSEST_HIERARCHY: 1,
    BIND_METHOD_HEAT_MAP: 2,
    BIND_METHOD_GEODESIC_VOXEL: 3,
}


@dataclass(frozen=True)
class NativeBindOptions:
    """Configuration for Maya's native initial skin binding."""

    method: str = BIND_METHOD_GEODESIC_VOXEL
    max_influences: int = 5
    obey_max_influences: bool = True
    normalize_weights: int = 1
    skin_method: int = 0
    dropoff_rate: float = 4.0
    heatmap_falloff: float = 0.0
    geodesic_falloff: float = 0.0
    voxel_resolution: int = 256
    validate_voxels: bool = True


@dataclass(frozen=True)
class NativeBindResult:
    skin_cluster: str
    mesh_transform: str
    method: str
    influence_count: int
    max_influences: int


def create_native_bind(
    mesh_transform: str,
    joints: List[str],
    options: NativeBindOptions | None = None,
) -> NativeBindResult:
    """
    Bind an unskinned mesh using Maya's native binding implementation.

    Version 2.5 deliberately keeps initial object binding inside Maya.
    Custom ownership maths remains available for selected-vertex editing,
    but it is no longer the default source of initial object weights.
    """
    options = options or NativeBindOptions()

    if not mesh_transform or not cmds.objExists(mesh_transform):
        raise RuntimeError(
            f"Mesh transform does not exist: {mesh_transform}"
        )

    method_id = _resolve_bind_method(options.method)
    max_influences = int(options.max_influences)

    if max_influences < 1:
        raise ValueError("max_influences must be at least 1.")

    if int(options.voxel_resolution) < 1:
        raise ValueError("voxel_resolution must be at least 1.")

    normalized_joints = _normalize_joint_paths(joints)

    if len(normalized_joints) < 2:
        raise RuntimeError(
            "Native object bind requires at least two joints."
        )

    existing_skin = _find_skin_cluster(mesh_transform)

    if existing_skin:
        raise RuntimeError(
            "This object already has a skinCluster.\n\n"
            "Native object bind is only allowed on an unskinned mesh."
        )

    skin_cluster = None

    try:
        create_kwargs = {
            "toSelectedBones": True,
            "bindMethod": method_id,
            "skinMethod": int(options.skin_method),
            "maximumInfluences": max_influences,
            "obeyMaxInfluences": bool(options.obey_max_influences),
            "normalizeWeights": int(options.normalize_weights),
            "dropoffRate": float(options.dropoff_rate),
        }

        if method_id == 2:
            create_kwargs["heatmapFalloff"] = float(
                options.heatmap_falloff
            )

        created = cmds.skinCluster(
            *(normalized_joints + [mesh_transform]),
            **create_kwargs,
        )

        skin_cluster = (
            created[0]
            if isinstance(created, (list, tuple))
            else created
        )

        if not skin_cluster or not cmds.objExists(skin_cluster):
            raise RuntimeError(
                "Maya did not return a valid skinCluster."
            )

        if method_id == 3:
            _run_geodesic_voxel_bind(
                skin_cluster=skin_cluster,
                options=options,
            )

        influence_count = len(
            cmds.skinCluster(
                skin_cluster,
                query=True,
                influence=True,
            )
            or []
        )

        return NativeBindResult(
            skin_cluster=skin_cluster,
            mesh_transform=mesh_transform,
            method=options.method,
            influence_count=influence_count,
            max_influences=max_influences,
        )

    except Exception:
        _remove_partial_skin_cluster(skin_cluster)
        raise


def _run_geodesic_voxel_bind(
    skin_cluster: str,
    options: NativeBindOptions,
) -> None:
    """
    Ask Maya to calculate Geodesic Voxel weights for an existing skinCluster.

    geomBind is Maya's native geodesic binding command. It requires an
    interactive Maya session and may be unavailable in batch/headless mode.
    """
    if not hasattr(cmds, "geomBind"):
        raise RuntimeError(
            "This Maya session does not expose cmds.geomBind. "
            "Use Closest In Hierarchy as the fallback bind method."
        )

    cmds.geomBind(
        skin_cluster,
        bindMethod=3,
        falloff=float(options.geodesic_falloff),
        maxInfluences=int(options.max_influences),
        geodesicVoxelParams=(
            int(options.voxel_resolution),
            bool(options.validate_voxels),
        ),
    )


def _resolve_bind_method(method: str) -> int:
    try:
        return _BIND_METHOD_IDS[method]
    except KeyError as exc:
        supported = ", ".join(sorted(_BIND_METHOD_IDS))
        raise ValueError(
            f"Unsupported bind method: {method}. "
            f"Supported methods: {supported}"
        ) from exc


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

        path = matches[0]

        if path in seen:
            continue

        seen.add(path)
        result.append(path)

    return result


def _find_skin_cluster(mesh_transform: str):
    history = cmds.listHistory(
        mesh_transform,
        pruneDagObjects=True,
    ) or []

    for node in history:
        if cmds.nodeType(node) == "skinCluster":
            return node

    return None


def _remove_partial_skin_cluster(skin_cluster) -> None:
    if not skin_cluster or not cmds.objExists(skin_cluster):
        return

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
