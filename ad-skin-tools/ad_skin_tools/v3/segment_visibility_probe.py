"""Incoming-bone-segment visibility smoke probe for AD Skin Tool v3.2.

This experiment keeps the accepted v3.0 exact pivot-distance ownership unchanged.
It inspects only the vertices raw-owned by one selected joint and asks whether
the target vertex patch is the first mesh surface reached from the closest point
on that joint's incoming parent-to-child bone segment.

The probe does not resolve final ownership, search replacement joints, process
multiple shells, use vertex normals, or write a skinCluster.
"""

from dataclasses import dataclass
from typing import Tuple
import math
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from ad_skin_tools.v3.distance_ranking import ExactDistanceRankingResult


# Maya documents 1e-6 as the default triangle-intersection tolerance. It is
# explicit only to make this smoke experiment reproducible; it is not accepted
# as a production ownership heuristic.
SMOKE_INTERSECTION_TOLERANCE = 1.0e-6


@dataclass(frozen=True)
class SegmentVisibilityProbeResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    source_joint: str
    source_influence_index: int
    segment_parent_joint: str
    segment_parent_influence_index: int
    segment_start: np.ndarray
    segment_end: np.ndarray
    raw_vertex_ids: Tuple[int, ...]
    visible_vertex_ids: Tuple[int, ...]
    rejected_vertex_ids: Tuple[int, ...]
    projection_parameters: np.ndarray
    elapsed_seconds: float

    @property
    def raw_vertex_count(self) -> int:
        return len(self.raw_vertex_ids)

    @property
    def visible_vertex_count(self) -> int:
        return len(self.visible_vertex_ids)

    @property
    def rejected_vertex_count(self) -> int:
        return len(self.rejected_vertex_ids)

    @property
    def parent_endpoint_projection_count(self) -> int:
        return int(np.count_nonzero(self.projection_parameters == 0.0))

    @property
    def source_endpoint_projection_count(self) -> int:
        return int(np.count_nonzero(self.projection_parameters == 1.0))

    @property
    def interior_projection_count(self) -> int:
        parameters = self.projection_parameters
        return int(np.count_nonzero((parameters > 0.0) & (parameters < 1.0)))


def probe_source_joint_segment_visibility(
    distance_result: ExactDistanceRankingResult,
    source_joint: str,
) -> SegmentVisibilityProbeResult:
    """Probe one raw distance owner from its finite incoming bone segment."""

    started = time.perf_counter()
    _validate_scene_state(distance_result)

    source_path = _resolve_result_joint(distance_result, source_joint)
    source_index = distance_result.influences.index(source_path)

    parent_path = _nearest_joint_ancestor(source_path)
    if parent_path not in distance_result.influences:
        raise RuntimeError(
            "The selected joint's nearest joint ancestor was not included in "
            "the v3.0 distance result.\n\n"
            "Selected joint: {}\n"
            "Incoming parent joint: {}".format(source_path, parent_path)
        )
    parent_index = distance_result.influences.index(parent_path)

    segment_start = np.asarray(
        distance_result.influence_positions[parent_index],
        dtype=np.float64,
    )
    segment_end = np.asarray(
        distance_result.influence_positions[source_index],
        dtype=np.float64,
    )
    segment_vector = segment_end - segment_start
    segment_squared_length = float(np.dot(segment_vector, segment_vector))
    if segment_squared_length == 0.0:
        raise RuntimeError(
            "The incoming bone segment has zero world-space length.\n\n"
            "Parent joint: {}\n"
            "Source joint: {}".format(parent_path, source_path)
        )

    raw_ids_array = np.where(
        distance_result.nearest_influence_indices == source_index
    )[0].astype(np.int32)
    raw_vertex_ids = tuple(raw_ids_array.tolist())

    mesh_fn, incident_faces = _mesh_intersection_context(
        distance_result.mesh_shape
    )

    visible_ids = []
    rejected_ids = []
    projection_parameters = np.empty(len(raw_vertex_ids), dtype=np.float64)

    for result_index, raw_vertex_id in enumerate(raw_vertex_ids):
        vertex_id = int(raw_vertex_id)
        target_position = distance_result.vertex_positions[vertex_id]
        origin, parameter = _closest_point_on_segment(
            point=target_position,
            segment_start=segment_start,
            segment_vector=segment_vector,
            segment_squared_length=segment_squared_length,
        )
        projection_parameters[result_index] = parameter

        if _origin_reaches_target_patch_first(
            mesh_fn=mesh_fn,
            origin=origin,
            target_position=target_position,
            target_face_ids=incident_faces[vertex_id],
        ):
            visible_ids.append(vertex_id)
        else:
            rejected_ids.append(vertex_id)

    return SegmentVisibilityProbeResult(
        mesh_shape=distance_result.mesh_shape,
        mesh_transform=distance_result.mesh_transform,
        influences=distance_result.influences,
        source_joint=source_path,
        source_influence_index=source_index,
        segment_parent_joint=parent_path,
        segment_parent_influence_index=parent_index,
        segment_start=segment_start.copy(),
        segment_end=segment_end.copy(),
        raw_vertex_ids=raw_vertex_ids,
        visible_vertex_ids=tuple(visible_ids),
        rejected_vertex_ids=tuple(rejected_ids),
        projection_parameters=projection_parameters,
        elapsed_seconds=time.perf_counter() - started,
    )


def select_probe_vertices(
    result: SegmentVisibilityProbeResult,
    category: str = "rejected",
) -> None:
    """Select raw, visible, or rejected diagnostic vertices in Maya."""

    category = str(category).lower()
    if category == "raw":
        vertex_ids = result.raw_vertex_ids
    elif category == "visible":
        vertex_ids = result.visible_vertex_ids
    elif category == "rejected":
        vertex_ids = result.rejected_vertex_ids
    else:
        raise ValueError("category must be raw, visible, or rejected.")

    components = [
        "{}.vtx[{}]".format(result.mesh_transform, int(vertex_id))
        for vertex_id in vertex_ids
    ]
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)


def _closest_point_on_segment(
    point: np.ndarray,
    segment_start: np.ndarray,
    segment_vector: np.ndarray,
    segment_squared_length: float,
) -> Tuple[np.ndarray, float]:
    parameter = float(
        np.dot(
            np.asarray(point, dtype=np.float64) - segment_start,
            segment_vector,
        )
        / segment_squared_length
    )
    parameter = min(1.0, max(0.0, parameter))
    origin = segment_start + (segment_vector * parameter)
    return origin, parameter


def _origin_reaches_target_patch_first(
    mesh_fn: om.MFnMesh,
    origin: np.ndarray,
    target_position: np.ndarray,
    target_face_ids: Tuple[int, ...],
) -> bool:
    segment = np.asarray(target_position, dtype=np.float64) - np.asarray(
        origin,
        dtype=np.float64,
    )
    distance = float(np.linalg.norm(segment))
    if distance == 0.0:
        return True

    direction = segment / distance
    ray_source = om.MFloatPoint(
        float(origin[0]),
        float(origin[1]),
        float(origin[2]),
    )
    ray_direction = om.MFloatVector(
        float(direction[0]),
        float(direction[1]),
        float(direction[2]),
    )

    hit = mesh_fn.closestIntersection(
        ray_source,
        ray_direction,
        om.MSpace.kWorld,
        math.nextafter(distance, math.inf),
        False,
        tolerance=SMOKE_INTERSECTION_TOLERANCE,
    )
    if hit is None:
        return False

    return int(hit[2]) in target_face_ids


def _nearest_joint_ancestor(source_joint: str) -> str:
    current = source_joint
    while True:
        parents = cmds.listRelatives(
            current,
            parent=True,
            fullPath=True,
        ) or []
        if not parents:
            break

        current = parents[0]
        if cmds.nodeType(current) == "joint":
            return current

    raise RuntimeError(
        "The selected source joint has no incoming joint ancestor:\n{}".format(
            source_joint
        )
    )


def _mesh_intersection_context(
    mesh_shape: str,
) -> Tuple[om.MFnMesh, Tuple[Tuple[int, ...], ...]]:
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)

    incident_faces = [tuple() for _ in range(int(mesh_fn.numVertices))]
    iterator = om.MItMeshVertex(dag_path)
    while not iterator.isDone():
        incident_faces[int(iterator.index())] = tuple(
            int(face_id) for face_id in iterator.getConnectedFaces()
        )
        iterator.next()

    return mesh_fn, tuple(incident_faces)


def _resolve_result_joint(
    result: ExactDistanceRankingResult,
    joint: str,
) -> str:
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Joint does not exist:\n{}".format(joint))

    path = matches[0]
    if path not in result.influences:
        raise RuntimeError(
            "Selected joint was not part of the v3.0 distance result:\n{}".format(
                path
            )
        )
    return path


def _validate_scene_state(result: ExactDistanceRankingResult) -> None:
    if not cmds.objExists(result.mesh_shape):
        raise RuntimeError("The v3.0 distance-result mesh no longer exists.")

    current_vertex_count = int(
        cmds.polyEvaluate(result.mesh_shape, vertex=True)
    )
    if current_vertex_count != result.vertex_count:
        raise RuntimeError(
            "Mesh vertex count changed after v3.0. Run the distance test again."
        )

    current_positions = np.empty_like(result.influence_positions)
    for index, joint in enumerate(result.influences):
        if not cmds.objExists(joint):
            raise RuntimeError(
                "A v3.0 distance-result joint no longer exists:\n{}".format(
                    joint
                )
            )
        current_positions[index] = cmds.xform(
            joint,
            query=True,
            worldSpace=True,
            translation=True,
        )

    if not np.array_equal(current_positions, result.influence_positions):
        raise RuntimeError(
            "Joint positions changed after v3.0. Run the distance test again."
        )
