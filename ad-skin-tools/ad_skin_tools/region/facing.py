"""Region-local surface-facing classification for Region Ownership."""

from dataclasses import dataclass
from typing import Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from ad_skin_tools.region.connectivity import OwnershipConnectivityResult
from ad_skin_tools.region.distance_ranking import ExactDistanceRankingResult


PRIMARY = "primary"
AMBIGUOUS_PRIMARY = "ambiguous_primary"
CO_PRIMARY = "co_primary"
DETACHED = "detached"
AMBIGUOUS = "ambiguous"

_FLOAT64_EPSILON = float(np.finfo(np.float64).eps)
_DOT_PRODUCT_GAMMA_3 = (3.0 * _FLOAT64_EPSILON) / (
    1.0 - (3.0 * _FLOAT64_EPSILON)
)


@dataclass(frozen=True)
class FacingMeshContext:
    mesh_shape: str
    mesh_fn: object
    incident_faces: Tuple[Tuple[int, ...], ...]


@dataclass(frozen=True)
class AnchorFaceOrientation:
    anchor_vertex_id: int
    face_id: int
    dot_product: float
    numerical_zero_bound: float
    sign: int


@dataclass(frozen=True)
class RegionFacingDiagnostic:
    region_index: int
    vertex_ids: Tuple[int, ...]
    local_anchor_vertex_ids: Tuple[int, ...]
    observations: Tuple[AnchorFaceOrientation, ...]
    classification: str

    @property
    def positive_observation_count(self) -> int:
        return sum(observation.sign > 0 for observation in self.observations)

    @property
    def negative_observation_count(self) -> int:
        return sum(observation.sign < 0 for observation in self.observations)

    @property
    def unresolved_observation_count(self) -> int:
        return sum(observation.sign == 0 for observation in self.observations)


@dataclass(frozen=True)
class RegionFacingResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    source_joint: str
    source_influence_index: int
    diagnostics: Tuple[RegionFacingDiagnostic, ...]
    primary_region_indices: Tuple[int, ...]
    co_primary_region_indices: Tuple[int, ...]
    detached_region_indices: Tuple[int, ...]
    ambiguous_region_indices: Tuple[int, ...]
    primary_vertex_ids: Tuple[int, ...]
    co_primary_vertex_ids: Tuple[int, ...]
    detached_vertex_ids: Tuple[int, ...]
    ambiguous_vertex_ids: Tuple[int, ...]

    @property
    def accepted_vertex_ids(self) -> Tuple[int, ...]:
        return tuple(sorted(self.primary_vertex_ids + self.co_primary_vertex_ids))

    @property
    def accepted_vertex_count(self) -> int:
        return len(self.accepted_vertex_ids)

    @property
    def co_primary_vertex_count(self) -> int:
        return len(self.co_primary_vertex_ids)

    @property
    def detached_vertex_count(self) -> int:
        return len(self.detached_vertex_ids)

    @property
    def ambiguous_vertex_count(self) -> int:
        return len(self.ambiguous_vertex_ids)


def build_facing_mesh_context(mesh_shape: str) -> FacingMeshContext:
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)

    incident_faces = [tuple() for _ in range(int(mesh_fn.numVertices))]
    iterator = om.MItMeshVertex(dag_path)
    while not iterator.isDone():
        incident_faces[int(iterator.index())] = tuple(
            sorted(int(face_id) for face_id in iterator.getConnectedFaces())
        )
        iterator.next()

    return FacingMeshContext(
        mesh_shape=mesh_shape,
        mesh_fn=mesh_fn,
        incident_faces=tuple(incident_faces),
    )


def classify_region_facing(
    distance_result: ExactDistanceRankingResult,
    connectivity_result: OwnershipConnectivityResult,
    context: FacingMeshContext,
) -> RegionFacingResult:
    _validate_inputs(distance_result, connectivity_result, context)
    source_index = connectivity_result.source_influence_index
    source_position = np.asarray(
        distance_result.influence_positions[source_index],
        dtype=np.float64,
    )
    anchor_region_set = set(connectivity_result.anchor_region_indices)
    unique_primary = len(connectivity_result.anchor_region_indices) == 1

    diagnostics = []
    for region_index, region in enumerate(connectivity_result.region_vertex_ids):
        local_anchor_ids = _exact_local_anchor_vertex_ids(
            distance_result,
            source_position,
            region,
        )
        observations = _anchor_face_observations(
            context,
            distance_result.vertex_positions,
            source_position,
            local_anchor_ids,
        )

        if region_index in anchor_region_set:
            classification = PRIMARY if unique_primary else AMBIGUOUS_PRIMARY
        else:
            classification = _classify_non_anchor_region(observations)

        diagnostics.append(
            RegionFacingDiagnostic(
                region_index=int(region_index),
                vertex_ids=tuple(int(value) for value in region),
                local_anchor_vertex_ids=local_anchor_ids,
                observations=observations,
                classification=classification,
            )
        )

    diagnostics_tuple = tuple(diagnostics)
    primary_indices = _indices_for_classifications(diagnostics_tuple, {PRIMARY})
    co_primary_indices = _indices_for_classifications(
        diagnostics_tuple,
        {CO_PRIMARY},
    )
    detached_indices = _indices_for_classifications(diagnostics_tuple, {DETACHED})
    ambiguous_indices = _indices_for_classifications(
        diagnostics_tuple,
        {AMBIGUOUS, AMBIGUOUS_PRIMARY},
    )

    return RegionFacingResult(
        mesh_shape=distance_result.mesh_shape,
        mesh_transform=distance_result.mesh_transform,
        influences=distance_result.influences,
        source_joint=connectivity_result.source_joint,
        source_influence_index=source_index,
        diagnostics=diagnostics_tuple,
        primary_region_indices=primary_indices,
        co_primary_region_indices=co_primary_indices,
        detached_region_indices=detached_indices,
        ambiguous_region_indices=ambiguous_indices,
        primary_vertex_ids=_vertices_for_regions(diagnostics_tuple, primary_indices),
        co_primary_vertex_ids=_vertices_for_regions(
            diagnostics_tuple,
            co_primary_indices,
        ),
        detached_vertex_ids=_vertices_for_regions(
            diagnostics_tuple,
            detached_indices,
        ),
        ambiguous_vertex_ids=_vertices_for_regions(
            diagnostics_tuple,
            ambiguous_indices,
        ),
    )


def probe_region_facing(distance_result, connectivity_result):
    context = build_facing_mesh_context(distance_result.mesh_shape)
    return classify_region_facing(distance_result, connectivity_result, context)


def select_facing_vertices(
    result: RegionFacingResult,
    category: str = "co_primary",
    region_index: int = -1,
) -> None:
    category = str(category).lower()
    if category == "accepted":
        vertex_ids = result.accepted_vertex_ids
    elif category == "primary":
        vertex_ids = result.primary_vertex_ids
    elif category == "co_primary":
        vertex_ids = result.co_primary_vertex_ids
    elif category == "detached":
        vertex_ids = result.detached_vertex_ids
    elif category == "ambiguous":
        vertex_ids = result.ambiguous_vertex_ids
    elif category == "local_anchors":
        vertex_ids = tuple(
            sorted(
                {
                    vertex_id
                    for diagnostic in result.diagnostics
                    for vertex_id in diagnostic.local_anchor_vertex_ids
                }
            )
        )
    elif category == "region":
        region_index = int(region_index)
        if region_index < 0 or region_index >= len(result.diagnostics):
            raise IndexError(
                "region_index {} is outside [0, {}).".format(
                    region_index,
                    len(result.diagnostics),
                )
            )
        vertex_ids = result.diagnostics[region_index].vertex_ids
    else:
        raise ValueError(
            "category must be accepted, primary, co_primary, detached, "
            "ambiguous, local_anchors, or region."
        )

    components = [
        "{}.vtx[{}]".format(result.mesh_transform, int(vertex_id))
        for vertex_id in vertex_ids
    ]
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)


def _exact_local_anchor_vertex_ids(
    distance_result,
    source_position,
    region_vertex_ids,
):
    region_array = np.asarray(region_vertex_ids, dtype=np.int32)
    positions = distance_result.vertex_positions[region_array]
    delta = positions - source_position[np.newaxis, :]
    squared_distances = np.einsum("vi,vi->v", delta, delta)
    exact_minimum = float(np.min(squared_distances))
    return tuple(
        int(value)
        for value in region_array[squared_distances == exact_minimum].tolist()
    )


def _anchor_face_observations(
    context,
    vertex_positions,
    source_position,
    anchor_vertex_ids,
):
    observations = []
    for anchor_vertex_id in anchor_vertex_ids:
        radial = np.asarray(
            vertex_positions[int(anchor_vertex_id)],
            dtype=np.float64,
        ) - source_position

        for face_id in context.incident_faces[int(anchor_vertex_id)]:
            normal_value = context.mesh_fn.getPolygonNormal(
                int(face_id),
                om.MSpace.kWorld,
            )
            normal = np.asarray(
                (normal_value.x, normal_value.y, normal_value.z),
                dtype=np.float64,
            )
            dot_product, zero_bound, sign = _bounded_dot_sign(normal, radial)
            observations.append(
                AnchorFaceOrientation(
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


def _classify_non_anchor_region(observations):
    if not observations:
        return AMBIGUOUS

    signs = tuple(observation.sign for observation in observations)
    if all(sign > 0 for sign in signs):
        return CO_PRIMARY
    if all(sign < 0 for sign in signs):
        return DETACHED
    return AMBIGUOUS


def _indices_for_classifications(diagnostics, classifications):
    return tuple(
        diagnostic.region_index
        for diagnostic in diagnostics
        if diagnostic.classification in classifications
    )


def _vertices_for_regions(diagnostics, region_indices):
    selected = set(int(value) for value in region_indices)
    return tuple(
        sorted(
            vertex_id
            for diagnostic in diagnostics
            if diagnostic.region_index in selected
            for vertex_id in diagnostic.vertex_ids
        )
    )


def _validate_inputs(distance_result, connectivity_result, context):
    if connectivity_result.mesh_shape != distance_result.mesh_shape:
        raise RuntimeError("Distance and connectivity results use different meshes.")
    if connectivity_result.influences != distance_result.influences:
        raise RuntimeError(
            "Distance and connectivity results use different influence lists."
        )
    if context.mesh_shape != distance_result.mesh_shape:
        raise RuntimeError("Facing context refers to a different mesh.")

    flattened = tuple(
        sorted(
            vertex_id
            for region in connectivity_result.region_vertex_ids
            for vertex_id in region
        )
    )
    if flattened != tuple(sorted(connectivity_result.raw_vertex_ids)):
        raise RuntimeError(
            "Connected regions do not exactly partition current ownership."
        )
