"""Classify only secondary owner regions from local surface-facing evidence."""

from dataclasses import dataclass
import time
from typing import Dict, Tuple

import maya.api.OpenMaya as om
import numpy as np

from ad_skin_tools.region_research.closest_region_ownership import (
    ClosestRegionOwnershipResult,
)


CO_PRIMARY = "co_primary"
DETACHED = "detached"
AMBIGUOUS = "ambiguous"

_FLOAT64_EPSILON = float(np.finfo(np.float64).eps)
_DOT_PRODUCT_GAMMA_3 = (3.0 * _FLOAT64_EPSILON) / (
    1.0 - (3.0 * _FLOAT64_EPSILON)
)


@dataclass(frozen=True)
class AnchorFaceObservation:
    anchor_vertex_id: int
    face_id: int
    dot_product: float
    numerical_zero_bound: float
    sign: int


@dataclass(frozen=True)
class SecondarySurfaceDiagnostic:
    influence_index: int
    joint: str
    region_index: int
    vertex_ids: Tuple[int, ...]
    local_anchor_vertex_ids: Tuple[int, ...]
    observations: Tuple[AnchorFaceObservation, ...]
    classification: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)

    @property
    def positive_observation_count(self) -> int:
        return sum(value.sign > 0 for value in self.observations)

    @property
    def negative_observation_count(self) -> int:
        return sum(value.sign < 0 for value in self.observations)

    @property
    def unresolved_observation_count(self) -> int:
        return sum(value.sign == 0 for value in self.observations)


@dataclass(frozen=True)
class SecondarySurfaceFacingResult:
    closest_ownership: ClosestRegionOwnershipResult
    diagnostics: Tuple[SecondarySurfaceDiagnostic, ...]
    co_primary_vertex_ids: Tuple[int, ...]
    detached_vertex_ids: Tuple[int, ...]
    ambiguous_vertex_ids: Tuple[int, ...]
    anchor_vertex_count: int
    queried_face_count: int
    anchor_query_seconds: float
    normal_query_seconds: float
    elapsed_seconds: float

    @property
    def co_primary_region_count(self) -> int:
        return sum(
            diagnostic.classification == CO_PRIMARY
            for diagnostic in self.diagnostics
        )

    @property
    def detached_region_count(self) -> int:
        return sum(
            diagnostic.classification == DETACHED
            for diagnostic in self.diagnostics
        )

    @property
    def ambiguous_region_count(self) -> int:
        return sum(
            diagnostic.classification == AMBIGUOUS
            for diagnostic in self.diagnostics
        )

    @property
    def co_primary_vertex_count(self) -> int:
        return len(self.co_primary_vertex_ids)

    @property
    def detached_vertex_count(self) -> int:
        return len(self.detached_vertex_ids)

    @property
    def ambiguous_vertex_count(self) -> int:
        return len(self.ambiguous_vertex_ids)


def classify_secondary_surface_facing(
    closest_ownership: ClosestRegionOwnershipResult,
) -> SecondarySurfaceFacingResult:
    """Query only exact anchors of secondary regions and cache face normals once."""

    started = time.perf_counter()
    context = closest_ownership.context

    pending = []
    all_anchor_ids = set()
    for summary in closest_ownership.influence_summaries:
        source_position = np.asarray(
            context.influence_positions[summary.influence_index],
            dtype=np.float64,
        )
        for region_index in summary.secondary_region_indices:
            region = summary.regions[int(region_index)]
            anchor_ids = _exact_local_anchor_vertex_ids(
                vertex_positions=context.vertex_positions,
                source_position=source_position,
                region_vertex_ids=region.vertex_ids,
            )
            all_anchor_ids.update(anchor_ids)
            pending.append(
                (
                    int(summary.influence_index),
                    summary.joint,
                    int(region.region_index),
                    region.vertex_ids,
                    anchor_ids,
                )
            )

    anchor_started = time.perf_counter()
    mesh_fn, incident_faces = _query_anchor_incident_faces(
        context.mesh_shape,
        tuple(sorted(all_anchor_ids)),
    )
    anchor_query_seconds = time.perf_counter() - anchor_started

    unique_face_ids = tuple(
        sorted(
            {
                face_id
                for face_ids in incident_faces.values()
                for face_id in face_ids
            }
        )
    )
    normal_started = time.perf_counter()
    face_normals = _query_world_face_normals(mesh_fn, unique_face_ids)
    normal_query_seconds = time.perf_counter() - normal_started

    diagnostics = []
    for influence_index, joint, region_index, vertex_ids, anchor_ids in pending:
        source_position = np.asarray(
            context.influence_positions[influence_index],
            dtype=np.float64,
        )
        observations = _build_observations(
            vertex_positions=context.vertex_positions,
            source_position=source_position,
            anchor_vertex_ids=anchor_ids,
            incident_faces=incident_faces,
            face_normals=face_normals,
        )
        diagnostics.append(
            SecondarySurfaceDiagnostic(
                influence_index=influence_index,
                joint=joint,
                region_index=region_index,
                vertex_ids=vertex_ids,
                local_anchor_vertex_ids=anchor_ids,
                observations=observations,
                classification=_classify_observations(observations),
            )
        )

    diagnostics_tuple = tuple(diagnostics)
    return SecondarySurfaceFacingResult(
        closest_ownership=closest_ownership,
        diagnostics=diagnostics_tuple,
        co_primary_vertex_ids=_vertices_for_classification(
            diagnostics_tuple,
            CO_PRIMARY,
        ),
        detached_vertex_ids=_vertices_for_classification(
            diagnostics_tuple,
            DETACHED,
        ),
        ambiguous_vertex_ids=_vertices_for_classification(
            diagnostics_tuple,
            AMBIGUOUS,
        ),
        anchor_vertex_count=len(all_anchor_ids),
        queried_face_count=len(unique_face_ids),
        anchor_query_seconds=float(anchor_query_seconds),
        normal_query_seconds=float(normal_query_seconds),
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _exact_local_anchor_vertex_ids(
    vertex_positions,
    source_position,
    region_vertex_ids,
) -> Tuple[int, ...]:
    ids = np.asarray(region_vertex_ids, dtype=np.int32)
    positions = vertex_positions[ids]
    delta = positions - source_position[np.newaxis, :]
    squared = np.einsum("vi,vi->v", delta, delta)
    exact_minimum = float(np.min(squared))
    return tuple(
        int(value)
        for value in ids[squared == exact_minimum].tolist()
    )


def _query_anchor_incident_faces(mesh_shape, anchor_vertex_ids):
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)

    incident_faces: Dict[int, Tuple[int, ...]] = {}
    iterator = om.MItMeshVertex(dag_path)
    for vertex_id in anchor_vertex_ids:
        iterator.setIndex(int(vertex_id))
        incident_faces[int(vertex_id)] = tuple(
            sorted(int(face_id) for face_id in iterator.getConnectedFaces())
        )
    return mesh_fn, incident_faces


def _query_world_face_normals(mesh_fn, face_ids):
    normals = {}
    for face_id in face_ids:
        value = mesh_fn.getPolygonNormal(int(face_id), om.MSpace.kWorld)
        normals[int(face_id)] = np.asarray(
            (value.x, value.y, value.z),
            dtype=np.float64,
        )
    return normals


def _build_observations(
    vertex_positions,
    source_position,
    anchor_vertex_ids,
    incident_faces,
    face_normals,
) -> Tuple[AnchorFaceObservation, ...]:
    observations = []
    for anchor_vertex_id in anchor_vertex_ids:
        radial = np.asarray(
            vertex_positions[int(anchor_vertex_id)],
            dtype=np.float64,
        ) - source_position
        for face_id in incident_faces.get(int(anchor_vertex_id), tuple()):
            dot_product, zero_bound, sign = _bounded_dot_sign(
                face_normals[int(face_id)],
                radial,
            )
            observations.append(
                AnchorFaceObservation(
                    anchor_vertex_id=int(anchor_vertex_id),
                    face_id=int(face_id),
                    dot_product=dot_product,
                    numerical_zero_bound=zero_bound,
                    sign=sign,
                )
            )
    observations.sort(key=lambda value: (value.anchor_vertex_id, value.face_id))
    return tuple(observations)


def _bounded_dot_sign(first, second):
    products = np.asarray(first, dtype=np.float64) * np.asarray(
        second,
        dtype=np.float64,
    )
    dot_product = float(np.sum(products, dtype=np.float64))
    zero_bound = float(
        _DOT_PRODUCT_GAMMA_3
        * np.sum(np.abs(products), dtype=np.float64)
    )
    if dot_product > zero_bound:
        sign = 1
    elif dot_product < -zero_bound:
        sign = -1
    else:
        sign = 0
    return dot_product, zero_bound, sign


def _classify_observations(observations):
    if not observations:
        return AMBIGUOUS
    signs = tuple(value.sign for value in observations)
    if all(sign > 0 for sign in signs):
        return CO_PRIMARY
    if all(sign < 0 for sign in signs):
        return DETACHED
    return AMBIGUOUS


def _vertices_for_classification(diagnostics, classification):
    return tuple(
        sorted(
            vertex_id
            for diagnostic in diagnostics
            if diagnostic.classification == classification
            for vertex_id in diagnostic.vertex_ids
        )
    )
