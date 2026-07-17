"""Focused first-surface visibility smoke probe for AD Skin Tool v3.

This module deliberately operates on one source influence at a time. It reads
an accepted Stage-1A exact-distance result, inspects only the vertices whose raw
unique-nearest owner is the selected source influence, and asks one geometric
question:

    Is the target vertex patch the first mesh surface reached by the segment
    from the candidate joint pivot to that vertex?

No topology-component partition, vertex-normal rule, hierarchy constraint,
skinCluster write, body-part name, ownership quota, or production correction is
introduced here.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple
import math
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from ad_skin_tools.v3.distance_ranking import ExactDistanceRankingResult


# Maya documents 1e-6 as the default triangle-intersection tolerance. It is
# kept explicit only for this smoke probe so results are reproducible. This is
# not accepted as a production ownership parameter.
SMOKE_INTERSECTION_TOLERANCE = 1.0e-6


@dataclass(frozen=True)
class FirstSurfaceVisibilityProbeResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    source_joint: str
    source_influence_index: int
    raw_vertex_ids: Tuple[int, ...]
    visible_vertex_ids: Tuple[int, ...]
    rejected_vertex_ids: Tuple[int, ...]
    replacement_owner_indices: np.ndarray
    unresolved_tie_vertex_ids: Tuple[int, ...]
    no_visible_candidate_vertex_ids: Tuple[int, ...]
    transition_counts: Dict[str, int]
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


def probe_source_joint_visibility(
    distance_result: ExactDistanceRankingResult,
    source_joint: str,
) -> FirstSurfaceVisibilityProbeResult:
    """Probe first-surface visibility for one raw distance owner."""

    started = time.perf_counter()
    source_path = _resolve_result_joint(distance_result, source_joint)
    source_index = distance_result.influences.index(source_path)
    _validate_scene_state(distance_result)

    raw_ids_array = np.where(
        distance_result.nearest_influence_indices == source_index
    )[0].astype(np.int32)
    raw_vertex_ids = tuple(raw_ids_array.tolist())

    mesh_fn, incident_faces = _mesh_intersection_context(
        distance_result.mesh_shape
    )

    visible_ids = []
    rejected_ids = []
    replacement_indices = []
    unresolved_ties = []
    no_visible = []

    for raw_vertex_id in raw_vertex_ids:
        vertex_id = int(raw_vertex_id)
        target_faces = incident_faces[vertex_id]
        source_position = distance_result.influence_positions[source_index]
        target_position = distance_result.vertex_positions[vertex_id]

        if _candidate_reaches_target_patch_first(
            mesh_fn=mesh_fn,
            source_position=source_position,
            target_position=target_position,
            target_face_ids=target_faces,
        ):
            visible_ids.append(vertex_id)
            continue

        rejected_ids.append(vertex_id)
        replacement = _nearest_visible_candidate(
            mesh_fn=mesh_fn,
            distance_result=distance_result,
            vertex_id=vertex_id,
            target_face_ids=target_faces,
        )
        replacement_indices.append(replacement)

        if replacement == -2:
            unresolved_ties.append(vertex_id)
        elif replacement < 0:
            no_visible.append(vertex_id)

    replacement_array = np.asarray(replacement_indices, dtype=np.int32)
    transitions = _build_transition_counts(
        influences=distance_result.influences,
        replacement_owner_indices=replacement_array,
    )

    return FirstSurfaceVisibilityProbeResult(
        mesh_shape=distance_result.mesh_shape,
        mesh_transform=distance_result.mesh_transform,
        influences=distance_result.influences,
        source_joint=source_path,
        source_influence_index=source_index,
        raw_vertex_ids=raw_vertex_ids,
        visible_vertex_ids=tuple(visible_ids),
        rejected_vertex_ids=tuple(rejected_ids),
        replacement_owner_indices=replacement_array,
        unresolved_tie_vertex_ids=tuple(unresolved_ties),
        no_visible_candidate_vertex_ids=tuple(no_visible),
        transition_counts=transitions,
        elapsed_seconds=time.perf_counter() - started,
    )


def select_probe_vertices(
    result: FirstSurfaceVisibilityProbeResult,
    category: str = "rejected",
    replacement_joint: Optional[str] = None,
) -> None:
    """Select one diagnostic vertex category in Maya."""

    category = str(category).lower()
    if replacement_joint is not None:
        replacement_path = _resolve_probe_joint(result, replacement_joint)
        replacement_index = result.influences.index(replacement_path)
        ids = [
            vertex_id
            for vertex_id, owner_index in zip(
                result.rejected_vertex_ids,
                result.replacement_owner_indices.tolist(),
            )
            if int(owner_index) == replacement_index
        ]
    elif category == "raw":
        ids = list(result.raw_vertex_ids)
    elif category == "visible":
        ids = list(result.visible_vertex_ids)
    elif category == "rejected":
        ids = list(result.rejected_vertex_ids)
    elif category == "unresolved":
        ids = list(result.unresolved_tie_vertex_ids)
    elif category == "no_visible":
        ids = list(result.no_visible_candidate_vertex_ids)
    else:
        raise ValueError(
            "category must be raw, visible, rejected, unresolved, or no_visible."
        )

    components = [
        "{}.vtx[{}]".format(result.mesh_transform, int(vertex_id))
        for vertex_id in ids
    ]
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)


def _nearest_visible_candidate(
    mesh_fn: om.MFnMesh,
    distance_result: ExactDistanceRankingResult,
    vertex_id: int,
    target_face_ids: Tuple[int, ...],
) -> int:
    """Return nearest visible influence, -1 for none, or -2 for exact tie."""

    target = distance_result.vertex_positions[int(vertex_id)]
    delta = distance_result.influence_positions - target[np.newaxis, :]
    squared = np.einsum("ji,ji->j", delta, delta)
    order = np.argsort(squared, kind="mergesort")

    start = 0
    influence_count = int(order.size)
    while start < influence_count:
        distance_value = float(squared[int(order[start])])
        stop = start + 1
        while (
            stop < influence_count
            and float(squared[int(order[stop])]) == distance_value
        ):
            stop += 1

        visible_group = []
        for position in range(start, stop):
            influence_index = int(order[position])
            if _candidate_reaches_target_patch_first(
                mesh_fn=mesh_fn,
                source_position=distance_result.influence_positions[
                    influence_index
                ],
                target_position=target,
                target_face_ids=target_face_ids,
            ):
                visible_group.append(influence_index)

        if len(visible_group) == 1:
            return int(visible_group[0])
        if len(visible_group) > 1:
            return -2
        start = stop

    return -1


def _candidate_reaches_target_patch_first(
    mesh_fn: om.MFnMesh,
    source_position: np.ndarray,
    target_position: np.ndarray,
    target_face_ids: Tuple[int, ...],
) -> bool:
    """Return whether the target vertex patch is the first segment hit."""

    segment = np.asarray(target_position, dtype=np.float64) - np.asarray(
        source_position,
        dtype=np.float64,
    )
    distance = float(np.linalg.norm(segment))
    if distance == 0.0:
        return True

    direction = segment / distance
    ray_source = om.MFloatPoint(
        float(source_position[0]),
        float(source_position[1]),
        float(source_position[2]),
    )
    ray_direction = om.MFloatVector(
        float(direction[0]),
        float(direction[1]),
        float(direction[2]),
    )
    maximum_parameter = math.nextafter(distance, math.inf)

    hit = mesh_fn.closestIntersection(
        ray_source,
        ray_direction,
        om.MSpace.kWorld,
        maximum_parameter,
        False,
        tolerance=SMOKE_INTERSECTION_TOLERANCE,
    )
    if hit is None:
        return False

    hit_face_id = int(hit[2])
    return hit_face_id in target_face_ids


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


def _build_transition_counts(
    influences: Sequence[str],
    replacement_owner_indices: np.ndarray,
) -> Dict[str, int]:
    result = {}
    for raw_index in replacement_owner_indices.tolist():
        owner_index = int(raw_index)
        if owner_index < 0:
            continue
        joint = influences[owner_index]
        result[joint] = result.get(joint, 0) + 1
    return result


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
            "Selected joint was not part of the Stage-1A result:\n{}".format(path)
        )
    return path


def _resolve_probe_joint(
    result: FirstSurfaceVisibilityProbeResult,
    joint: str,
) -> str:
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Joint does not exist:\n{}".format(joint))
    path = matches[0]
    if path not in result.influences:
        raise RuntimeError("Joint was not part of this probe:\n{}".format(path))
    return path


def _validate_scene_state(result: ExactDistanceRankingResult) -> None:
    if not cmds.objExists(result.mesh_shape):
        raise RuntimeError("The Stage-1A mesh no longer exists.")
    current_vertex_count = int(cmds.polyEvaluate(result.mesh_shape, vertex=True))
    if current_vertex_count != result.vertex_count:
        raise RuntimeError(
            "Mesh vertex count changed after Stage 1A. Run Stage 1A again."
        )

    current_positions = np.empty_like(result.influence_positions)
    for index, joint in enumerate(result.influences):
        if not cmds.objExists(joint):
            raise RuntimeError("A Stage-1A joint no longer exists:\n{}".format(joint))
        current_positions[index] = cmds.xform(
            joint,
            query=True,
            worldSpace=True,
            translation=True,
        )
    if not np.array_equal(current_positions, result.influence_positions):
        raise RuntimeError(
            "Joint positions changed after Stage 1A. Run Stage 1A again."
        )
