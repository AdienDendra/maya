from dataclasses import dataclass
from typing import List, Optional

import maya.cmds as cmds


BIND_METHOD_CLOSEST_DISTANCE = "closest_distance"
_BIND_METHOD_ID = 0


@dataclass(frozen=True)
class NativeBindOptions:
    """
    Internal configuration for the v2.5 initial object bind.

    The bind method is intentionally fixed. AD Skin Tool v2.5 always uses
    Maya Closest Distance. No bind-method selector is exposed to the user.
    """

    max_influences: int = 5
    obey_max_influences: bool = True
    normalize_weights: int = 1
    skin_method: int = 0
    dropoff_rate: float = 4.0


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
    options: Optional[NativeBindOptions] = None,
) -> NativeBindResult:
    """
    Bind an unskinned mesh with Maya Closest Distance.

    Maya creates the initial weights through skinCluster(bindMethod=0).
    AD Skin Tool does not overwrite those weights with an object-wide custom
    ownership solver. Selected-vertex correction remains a separate workflow.
    """
    options = options or NativeBindOptions()

    if not mesh_transform or not cmds.objExists(mesh_transform):
        raise RuntimeError(
            f"Mesh transform does not exist: {mesh_transform}"
        )

    max_influences = int(options.max_influences)
    _validate_options(
        options=options,
        max_influences=max_influences,
    )

    normalized_joints = _normalize_joint_paths(joints)

    if len(normalized_joints) < 2:
        raise RuntimeError(
            "Closest Distance object bind requires at least two joints."
        )

    existing_skin = _find_skin_cluster(mesh_transform)

    if existing_skin:
        raise RuntimeError(
            "This object already has a skinCluster.\n\n"
            "Closest Distance object bind is only allowed on an "
            "unskinned mesh."
        )

    skin_cluster = None

    try:
        created = cmds.skinCluster(
            *(normalized_joints + [mesh_transform]),
            toSelectedBones=True,
            bindMethod=_BIND_METHOD_ID,
            skinMethod=int(options.skin_method),
            maximumInfluences=max_influences,
            obeyMaxInfluences=bool(options.obey_max_influences),
            normalizeWeights=int(options.normalize_weights),
            dropoffRate=float(options.dropoff_rate),
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

        influences = cmds.skinCluster(
            skin_cluster,
            query=True,
            influence=True,
        ) or []

        if len(influences) < 2:
            raise RuntimeError(
                "The created skinCluster contains fewer than two influences."
            )

        return NativeBindResult(
            skin_cluster=skin_cluster,
            mesh_transform=mesh_transform,
            method=BIND_METHOD_CLOSEST_DISTANCE,
            influence_count=len(influences),
            max_influences=max_influences,
        )

    except Exception:
        _remove_partial_skin_cluster(skin_cluster)
        raise


def _validate_options(
    options: NativeBindOptions,
    max_influences: int,
) -> None:
    if max_influences < 1:
        raise ValueError("max_influences must be at least 1.")

    normalize_weights = int(options.normalize_weights)

    if normalize_weights not in (0, 1, 2):
        raise ValueError(
            "normalize_weights must be 0 (none), 1 (interactive), "
            "or 2 (post)."
        )

    skin_method = int(options.skin_method)

    if skin_method not in (0, 1, 2):
        raise ValueError(
            "skin_method must be 0 (linear), 1 (dual quaternion), "
            "or 2 (weight blended)."
        )

    dropoff_rate = float(options.dropoff_rate)

    if not 0.1 <= dropoff_rate <= 10.0:
        raise ValueError(
            "dropoff_rate must be between 0.1 and 10.0."
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
