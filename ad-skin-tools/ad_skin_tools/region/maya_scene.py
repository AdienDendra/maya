"""Maya scene reads for the AD Skin Tool Region Ownership solver.

This module resolves one polygon mesh, normalizes the supplied joint paths, and
captures immutable world-space input arrays. It does not calculate ownership or
create a skinCluster.
"""

from dataclasses import dataclass
from typing import Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np


@dataclass(frozen=True)
class MayaDistanceInput:
    """World-space data required by exact joint-pivot distance ranking."""

    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    vertex_positions: np.ndarray
    influence_positions: np.ndarray


def collect_distance_input(
    mesh: str,
    joints: Sequence[str],
) -> MayaDistanceInput:
    """Read one mesh and one complete joint list from the Maya scene."""

    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    influences = _normalize_joint_paths(joints)
    if len(influences) < 2:
        raise RuntimeError("Region ownership requires at least two joints.")

    vertex_positions = _mesh_vertex_positions(mesh_shape)
    if vertex_positions.shape[0] == 0:
        raise RuntimeError("The mesh contains no vertices.")

    influence_positions = _joint_world_positions(influences)
    return MayaDistanceInput(
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        influences=influences,
        vertex_positions=vertex_positions,
        influence_positions=influence_positions,
    )


def validate_scene_state(scene_input: MayaDistanceInput) -> None:
    """Reject stale captured data before it is reused for ownership solving."""

    if not cmds.objExists(scene_input.mesh_shape):
        raise RuntimeError("The captured mesh no longer exists.")

    current_vertices = _mesh_vertex_positions(scene_input.mesh_shape)
    if not np.array_equal(current_vertices, scene_input.vertex_positions):
        raise RuntimeError(
            "Mesh vertex positions changed after Region input capture. "
            "Run the bind again."
        )

    current_joints = _joint_world_positions(scene_input.influences)
    if not np.array_equal(current_joints, scene_input.influence_positions):
        raise RuntimeError(
            "Joint positions changed after Region input capture. Run the bind again."
        )


def _resolve_mesh(mesh: str) -> Tuple[str, str]:
    if not mesh:
        raise RuntimeError("No mesh was supplied.")

    matches = cmds.ls(mesh, long=True) or []
    if not matches:
        raise RuntimeError("Mesh does not exist:\n{}".format(mesh))

    node = matches[0]
    node_type = cmds.nodeType(node)

    if node_type == "mesh":
        parents = cmds.listRelatives(
            node,
            parent=True,
            fullPath=True,
        ) or []
        if not parents:
            raise RuntimeError("Mesh shape has no transform parent:\n{}".format(node))
        return node, parents[0]

    if node_type != "transform":
        raise RuntimeError(
            "Node is not a polygon mesh or mesh transform:\n{}".format(node)
        )

    shapes = cmds.listRelatives(
        node,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
        type="mesh",
    ) or []
    if len(shapes) != 1:
        raise RuntimeError(
            "Geometry transform must contain exactly one non-intermediate "
            "polygon mesh shape.\n\nTransform: {}\nMesh shapes: {}".format(
                node,
                len(shapes),
            )
        )
    return shapes[0], node


def _normalize_joint_paths(joints: Sequence[str]) -> Tuple[str, ...]:
    result = []
    seen = set()

    for joint in joints:
        matches = cmds.ls(joint, long=True, type="joint") or []
        if not matches:
            raise RuntimeError("Joint does not exist:\n{}".format(joint))

        path = matches[0]
        if path in seen:
            continue
        seen.add(path)
        result.append(path)

    return tuple(result)


def _mesh_vertex_positions(mesh_shape: str) -> np.ndarray:
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)
    points = mesh_fn.getPoints(om.MSpace.kWorld)

    positions = np.empty((len(points), 3), dtype=np.float64)
    for index, point in enumerate(points):
        positions[index] = (point.x, point.y, point.z)
    return positions


def _joint_world_positions(influences: Tuple[str, ...]) -> np.ndarray:
    positions = np.empty((len(influences), 3), dtype=np.float64)
    for index, joint in enumerate(influences):
        value = cmds.xform(
            joint,
            query=True,
            worldSpace=True,
            translation=True,
        )
        positions[index] = value
    return positions
