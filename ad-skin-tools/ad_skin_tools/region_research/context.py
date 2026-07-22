"""Immutable Maya scene capture for Region Research.

This package intentionally does not import anything from ``ad_skin_tools.region``.
The research branch owns its scene reads and topology context so the old Region
package can eventually be removed without leaving hidden dependencies.
"""

from dataclasses import dataclass
import time
from typing import Sequence, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np


@dataclass(frozen=True)
class ResearchMeshContext:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    influence_uuids: Tuple[str, ...]
    vertex_positions: np.ndarray
    influence_positions: np.ndarray
    adjacency: Tuple[Tuple[int, ...], ...]
    face_count: int
    edge_count: int
    scene_capture_seconds: float
    adjacency_seconds: float
    elapsed_seconds: float

    @property
    def vertex_count(self) -> int:
        return int(self.vertex_positions.shape[0])

    @property
    def influence_count(self) -> int:
        return int(self.influence_positions.shape[0])


def build_research_mesh_context(
    mesh: str,
    joints: Sequence[str],
) -> ResearchMeshContext:
    """Capture one mesh, joint pivots, and direct vertex adjacency exactly once."""

    started = time.perf_counter()
    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    influences = _normalize_joint_paths(joints)
    if len(influences) < 2:
        raise RuntimeError("Region Research requires at least two joints.")

    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)

    capture_started = time.perf_counter()
    points = mesh_fn.getPoints(om.MSpace.kWorld)
    vertex_positions = np.empty((len(points), 3), dtype=np.float64)
    for index, point in enumerate(points):
        vertex_positions[index] = (point.x, point.y, point.z)

    influence_positions = np.empty((len(influences), 3), dtype=np.float64)
    influence_uuids = []
    for index, joint in enumerate(influences):
        value = cmds.xform(
            joint,
            query=True,
            worldSpace=True,
            translation=True,
        )
        influence_positions[index] = value

        uuid_matches = cmds.ls(joint, uuid=True) or []
        if len(uuid_matches) != 1:
            raise RuntimeError(
                "Unable to resolve one stable Maya UUID for joint:\n{}".format(joint)
            )
        influence_uuids.append(str(uuid_matches[0]))

    scene_capture_seconds = time.perf_counter() - capture_started

    adjacency_started = time.perf_counter()
    adjacency_sets = [set() for _ in range(int(mesh_fn.numVertices))]
    edge_iterator = om.MItMeshEdge(dag_path)
    while not edge_iterator.isDone():
        first = int(edge_iterator.vertexId(0))
        second = int(edge_iterator.vertexId(1))
        adjacency_sets[first].add(second)
        adjacency_sets[second].add(first)
        edge_iterator.next()

    adjacency = tuple(
        tuple(sorted(int(value) for value in neighbours))
        for neighbours in adjacency_sets
    )
    adjacency_seconds = time.perf_counter() - adjacency_started

    return ResearchMeshContext(
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        influences=influences,
        influence_uuids=tuple(influence_uuids),
        vertex_positions=vertex_positions,
        influence_positions=influence_positions,
        adjacency=adjacency,
        face_count=int(mesh_fn.numPolygons),
        edge_count=int(mesh_fn.numEdges),
        scene_capture_seconds=float(scene_capture_seconds),
        adjacency_seconds=float(adjacency_seconds),
        elapsed_seconds=float(time.perf_counter() - started),
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
            "Geometry transform must contain exactly one non-intermediate mesh.\n\n"
            "Transform: {}\nMesh shapes: {}".format(node, len(shapes))
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
