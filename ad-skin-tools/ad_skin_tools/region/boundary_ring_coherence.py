"""Read-only v3.10B Region boundary diagnostic using Maya edge rings.

The existing Region solver remains untouched. This module inspects each connected
owner region after the production Region solve.

A region is analysed only when it touches exactly one neighbouring owner. The
ownership-crossing edges are then grouped with Maya ``polySelect(edgeRing=...)``.
One Maya edge ring is treated as a normal segment boundary. Two or more distinct
rings are reported as a suspicious circumferential ownership split.
"""

from dataclasses import dataclass
from typing import Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from ad_skin_tools.region.connectivity import (
    build_vertex_adjacency,
    partition_influence_ownership,
)
from ad_skin_tools.region.solver import RegionOwnershipResult


SINGLE_RING = "single_ring"
MULTIPLE_RINGS = "multiple_rings"
JUNCTION_IGNORED = "junction_ignored"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class MayaEdgeRingDiagnostic:
    seed_edge_id: int
    edge_ids: Tuple[int, ...]
    boundary_edge_ids: Tuple[int, ...]

    @property
    def boundary_overlap_count(self) -> int:
        return len(self.boundary_edge_ids)


@dataclass(frozen=True)
class RegionBoundaryRingDiagnostic:
    source_joint: str
    source_influence_index: int
    source_region_index: int
    source_region_vertex_ids: Tuple[int, ...]
    neighbour_joints: Tuple[str, ...]
    neighbour_influence_indices: Tuple[int, ...]
    boundary_edge_ids: Tuple[int, ...]
    maya_rings: Tuple[MayaEdgeRingDiagnostic, ...]
    unresolved_seed_edge_ids: Tuple[int, ...]
    classification: str

    @property
    def neighbour_count(self) -> int:
        return len(self.neighbour_influence_indices)

    @property
    def maya_ring_count(self) -> int:
        return len(self.maya_rings)

    @property
    def is_suspicious(self) -> bool:
        return self.classification == MULTIPLE_RINGS


@dataclass(frozen=True)
class BoundaryRingCoherenceResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    diagnostics: Tuple[RegionBoundaryRingDiagnostic, ...]

    @property
    def suspicious_diagnostic_indices(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, diagnostic in enumerate(self.diagnostics)
            if diagnostic.classification == MULTIPLE_RINGS
        )

    @property
    def unresolved_diagnostic_indices(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, diagnostic in enumerate(self.diagnostics)
            if diagnostic.classification == UNRESOLVED
        )

    @property
    def junction_diagnostic_indices(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, diagnostic in enumerate(self.diagnostics)
            if diagnostic.classification == JUNCTION_IGNORED
        )

    @property
    def suspicious_diagnostic_count(self) -> int:
        return len(self.suspicious_diagnostic_indices)


@dataclass(frozen=True)
class _BoundaryMeshContext:
    edge_endpoints: Tuple[Tuple[int, int], ...]
    incident_edge_ids: Tuple[Tuple[int, ...], ...]


def analyze_region_boundary_rings(
    region_result: RegionOwnershipResult,
) -> BoundaryRingCoherenceResult:
    """Inspect final Region ownership without changing any owner assignment."""

    owners = np.asarray(region_result.owner_indices, dtype=np.int32)
    if owners.shape != (region_result.vertex_count,):
        raise ValueError("Region owner_indices must contain one owner per vertex.")

    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    context = _build_boundary_mesh_context(region_result.mesh_shape)
    diagnostics = []
    seen_single_neighbour_contacts = set()

    for source_index, source_joint in enumerate(region_result.influences):
        connectivity = partition_influence_ownership(
            region_result.distance_result,
            owners,
            source_index,
            adjacency,
        )

        for source_region_index, region_vertex_ids in enumerate(
            connectivity.region_vertex_ids
        ):
            boundary_by_neighbour = _boundary_edges_by_neighbour(
                region_vertex_ids=region_vertex_ids,
                source_influence_index=source_index,
                owner_indices=owners,
                context=context,
            )
            if not boundary_by_neighbour:
                continue

            neighbour_indices = tuple(sorted(boundary_by_neighbour))
            neighbour_joints = tuple(
                region_result.influences[int(index)]
                for index in neighbour_indices
            )
            all_boundary_edge_ids = tuple(
                sorted(
                    {
                        edge_id
                        for edge_ids in boundary_by_neighbour.values()
                        for edge_id in edge_ids
                    }
                )
            )

            if len(neighbour_indices) != 1:
                diagnostics.append(
                    RegionBoundaryRingDiagnostic(
                        source_joint=source_joint,
                        source_influence_index=int(source_index),
                        source_region_index=int(source_region_index),
                        source_region_vertex_ids=tuple(
                            int(value) for value in region_vertex_ids
                        ),
                        neighbour_joints=neighbour_joints,
                        neighbour_influence_indices=tuple(
                            int(value) for value in neighbour_indices
                        ),
                        boundary_edge_ids=all_boundary_edge_ids,
                        maya_rings=tuple(),
                        unresolved_seed_edge_ids=tuple(),
                        classification=JUNCTION_IGNORED,
                    )
                )
                continue

            neighbour_index = int(neighbour_indices[0])
            boundary_edge_ids = tuple(
                sorted(boundary_by_neighbour[neighbour_index])
            )
            canonical_contact = (
                min(int(source_index), neighbour_index),
                max(int(source_index), neighbour_index),
                boundary_edge_ids,
            )
            if canonical_contact in seen_single_neighbour_contacts:
                continue
            seen_single_neighbour_contacts.add(canonical_contact)

            rings, unresolved = _maya_rings_for_boundary_edges(
                mesh_transform=region_result.mesh_transform,
                boundary_edge_ids=boundary_edge_ids,
            )
            classification = _classify_region(rings, unresolved)

            diagnostics.append(
                RegionBoundaryRingDiagnostic(
                    source_joint=source_joint,
                    source_influence_index=int(source_index),
                    source_region_index=int(source_region_index),
                    source_region_vertex_ids=tuple(
                        int(value) for value in region_vertex_ids
                    ),
                    neighbour_joints=neighbour_joints,
                    neighbour_influence_indices=(neighbour_index,),
                    boundary_edge_ids=boundary_edge_ids,
                    maya_rings=rings,
                    unresolved_seed_edge_ids=unresolved,
                    classification=classification,
                )
            )

    diagnostics.sort(
        key=lambda item: (
            item.source_influence_index,
            item.source_region_index,
            item.neighbour_influence_indices,
            item.boundary_edge_ids,
        )
    )

    return BoundaryRingCoherenceResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        diagnostics=tuple(diagnostics),
    )


def select_boundary_ring_diagnostics(
    result: BoundaryRingCoherenceResult,
    category: str = "suspicious",
) -> None:
    """Select ownership-crossing edges for viewport inspection."""

    category = str(category).lower()
    if category == "suspicious":
        indices = result.suspicious_diagnostic_indices
    elif category == "unresolved":
        indices = result.unresolved_diagnostic_indices
    elif category == "junction":
        indices = result.junction_diagnostic_indices
    elif category == "all":
        indices = tuple(range(len(result.diagnostics)))
    else:
        raise ValueError(
            "category must be suspicious, unresolved, junction, or all."
        )

    edge_ids = sorted(
        {
            edge_id
            for index in indices
            for edge_id in result.diagnostics[int(index)].boundary_edge_ids
        }
    )
    cmds.select(clear=True)
    if edge_ids:
        cmds.select(
            [
                "{}.e[{}]".format(result.mesh_transform, int(edge_id))
                for edge_id in edge_ids
            ],
            replace=True,
        )


def _build_boundary_mesh_context(mesh_shape):
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)

    endpoints = [None] * int(mesh_fn.numEdges)
    incident = [set() for _ in range(int(mesh_fn.numVertices))]

    iterator = om.MItMeshEdge(dag_path)
    while not iterator.isDone():
        edge_id = int(iterator.index())
        first = int(iterator.vertexId(0))
        second = int(iterator.vertexId(1))
        endpoints[edge_id] = (first, second)
        incident[first].add(edge_id)
        incident[second].add(edge_id)
        iterator.next()

    if any(value is None for value in endpoints):
        raise RuntimeError("Failed to collect every mesh edge endpoint.")

    return _BoundaryMeshContext(
        edge_endpoints=tuple(endpoints),
        incident_edge_ids=tuple(
            tuple(sorted(edge_ids)) for edge_ids in incident
        ),
    )


def _boundary_edges_by_neighbour(
    region_vertex_ids,
    source_influence_index,
    owner_indices,
    context,
):
    source_index = int(source_influence_index)
    region_set = set(int(value) for value in region_vertex_ids)
    result = {}

    for vertex_id in region_set:
        if int(owner_indices[vertex_id]) != source_index:
            raise RuntimeError(
                "Connected Region contains a vertex owned by another influence."
            )

        for edge_id in context.incident_edge_ids[vertex_id]:
            first, second = context.edge_endpoints[int(edge_id)]
            other_vertex = second if first == vertex_id else first
            if other_vertex in region_set:
                continue

            neighbour_index = int(owner_indices[int(other_vertex)])
            if neighbour_index == source_index:
                continue
            result.setdefault(neighbour_index, set()).add(int(edge_id))

    return result


def _maya_rings_for_boundary_edges(mesh_transform, boundary_edge_ids):
    boundary_set = set(int(value) for value in boundary_edge_ids)
    remaining = set(boundary_set)
    rings = []
    unresolved = []

    while remaining:
        seed_edge_id = min(remaining)
        raw_ids = cmds.polySelect(
            mesh_transform,
            edgeRing=int(seed_edge_id),
            noSelection=True,
        ) or []

        ordered_ids = []
        seen = set()
        for value in raw_ids:
            edge_id = int(value)
            if edge_id not in seen:
                seen.add(edge_id)
                ordered_ids.append(edge_id)

        if not ordered_ids or seed_edge_id not in seen:
            unresolved.append(seed_edge_id)
            remaining.remove(seed_edge_id)
            continue

        overlap = tuple(
            edge_id
            for edge_id in ordered_ids
            if edge_id in boundary_set
        )
        if not overlap:
            unresolved.append(seed_edge_id)
            remaining.remove(seed_edge_id)
            continue

        rings.append(
            MayaEdgeRingDiagnostic(
                seed_edge_id=int(seed_edge_id),
                edge_ids=tuple(ordered_ids),
                boundary_edge_ids=overlap,
            )
        )
        remaining.difference_update(overlap)

    rings.sort(
        key=lambda item: (
            min(item.boundary_edge_ids),
            item.seed_edge_id,
        )
    )
    return (
        tuple(rings),
        tuple(sorted(int(value) for value in unresolved)),
    )


def _classify_region(rings, unresolved):
    if unresolved or not rings:
        return UNRESOLVED
    if len(rings) == 1:
        return SINGLE_RING
    return MULTIPLE_RINGS
