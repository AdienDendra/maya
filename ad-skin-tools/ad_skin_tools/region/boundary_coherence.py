"""Read-only Maya edge-loop diagnostic for final Region Ownership boundaries.

The production distance, connectivity, facing, and Region solver modules are not
modified. This module only inspects the final hard owner map and asks Maya's
``polySelect(edgeLoop=...)`` command to traverse ownership-boundary edges.
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


SINGLE_LOOP = "single_loop"
MULTIPLE_OPEN_LOOPS = "multiple_open_loops"
MULTIPLE_MIXED_LOOPS = "multiple_mixed_loops"
UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class MayaEdgeLoopDiagnostic:
    seed_edge_id: int
    edge_ids: Tuple[int, ...]
    boundary_edge_ids: Tuple[int, ...]
    is_closed: bool


@dataclass(frozen=True)
class BoundaryContactDiagnostic:
    source_joint: str
    source_influence_index: int
    source_region_index: int
    neighbour_joint: str
    neighbour_influence_index: int
    source_region_vertex_ids: Tuple[int, ...]
    boundary_edge_ids: Tuple[int, ...]
    maya_loops: Tuple[MayaEdgeLoopDiagnostic, ...]
    unresolved_seed_edge_ids: Tuple[int, ...]
    classification: str

    @property
    def maya_loop_count(self) -> int:
        return len(self.maya_loops)

    @property
    def open_loop_count(self) -> int:
        return sum(not loop.is_closed for loop in self.maya_loops)

    @property
    def closed_loop_count(self) -> int:
        return sum(loop.is_closed for loop in self.maya_loops)

    @property
    def is_suspicious(self) -> bool:
        return self.classification == MULTIPLE_OPEN_LOOPS


@dataclass(frozen=True)
class BoundaryCoherenceResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    contacts: Tuple[BoundaryContactDiagnostic, ...]

    @property
    def suspicious_contact_indices(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, contact in enumerate(self.contacts)
            if contact.classification == MULTIPLE_OPEN_LOOPS
        )

    @property
    def unresolved_contact_indices(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, contact in enumerate(self.contacts)
            if contact.classification == UNRESOLVED
        )

    @property
    def mixed_loop_contact_indices(self) -> Tuple[int, ...]:
        return tuple(
            index
            for index, contact in enumerate(self.contacts)
            if contact.classification == MULTIPLE_MIXED_LOOPS
        )

    @property
    def suspicious_contact_count(self) -> int:
        return len(self.suspicious_contact_indices)


@dataclass(frozen=True)
class _BoundaryMeshContext:
    edge_endpoints: Tuple[Tuple[int, int], ...]
    incident_edge_ids: Tuple[Tuple[int, ...], ...]


def analyze_region_boundary_coherence(
    region_result: RegionOwnershipResult,
) -> BoundaryCoherenceResult:
    """Inspect final owner boundaries without changing the Region owner map.

    Each connected owner region is examined against each neighbouring owner.
    Maya groups the contact edges through ``polySelect(edgeLoop=...)``.

    A contact is marked suspicious only when the same owner pair is separated
    by two or more distinct open Maya edge loops. A single loop is accepted.
    Mixed open/closed multi-loop contacts and failed Maya traversals are reported
    but never corrected automatically.
    """

    owners = np.asarray(region_result.owner_indices, dtype=np.int32)
    if owners.shape != (region_result.vertex_count,):
        raise ValueError("Region owner_indices must contain one owner per vertex.")

    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    context = _build_boundary_mesh_context(region_result.mesh_shape)
    contacts = []

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

            for neighbour_index, boundary_edge_ids in sorted(
                boundary_by_neighbour.items()
            ):
                # Every physical owner-pair contact is reported once.
                if source_index > neighbour_index:
                    continue

                loops, unresolved = _maya_loops_for_boundary_edges(
                    mesh_transform=region_result.mesh_transform,
                    boundary_edge_ids=boundary_edge_ids,
                )
                classification = _classify_contact(loops, unresolved)

                contacts.append(
                    BoundaryContactDiagnostic(
                        source_joint=source_joint,
                        source_influence_index=int(source_index),
                        source_region_index=int(source_region_index),
                        neighbour_joint=region_result.influences[
                            int(neighbour_index)
                        ],
                        neighbour_influence_index=int(neighbour_index),
                        source_region_vertex_ids=tuple(
                            int(value) for value in region_vertex_ids
                        ),
                        boundary_edge_ids=tuple(
                            sorted(int(value) for value in boundary_edge_ids)
                        ),
                        maya_loops=loops,
                        unresolved_seed_edge_ids=unresolved,
                        classification=classification,
                    )
                )

    contacts.sort(
        key=lambda item: (
            item.source_influence_index,
            item.source_region_index,
            item.neighbour_influence_index,
            item.boundary_edge_ids,
        )
    )

    return BoundaryCoherenceResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        contacts=tuple(contacts),
    )


def select_boundary_contact_edges(
    result: BoundaryCoherenceResult,
    category: str = "suspicious",
) -> None:
    """Select diagnostic boundary edges for viewport inspection."""

    category = str(category).lower()
    if category == "suspicious":
        contacts = [
            result.contacts[index]
            for index in result.suspicious_contact_indices
        ]
    elif category == "unresolved":
        contacts = [
            result.contacts[index]
            for index in result.unresolved_contact_indices
        ]
    elif category == "mixed":
        contacts = [
            result.contacts[index]
            for index in result.mixed_loop_contact_indices
        ]
    elif category == "all":
        contacts = list(result.contacts)
    else:
        raise ValueError(
            "category must be suspicious, unresolved, mixed, or all."
        )

    edge_ids = sorted(
        {
            edge_id
            for contact in contacts
            for edge_id in contact.boundary_edge_ids
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


def _maya_loops_for_boundary_edges(mesh_transform, boundary_edge_ids):
    boundary_set = set(int(value) for value in boundary_edge_ids)
    remaining = set(boundary_set)
    loops = []
    unresolved = []

    while remaining:
        seed_edge_id = min(remaining)
        raw_ids = cmds.polySelect(
            mesh_transform,
            edgeLoop=int(seed_edge_id),
            noSelection=True,
        ) or []

        ordered_ids = [int(value) for value in raw_ids]
        if not ordered_ids or seed_edge_id not in ordered_ids:
            unresolved.append(seed_edge_id)
            remaining.remove(seed_edge_id)
            continue

        is_closed = (
            len(ordered_ids) > 1
            and ordered_ids[0] == ordered_ids[-1]
        )
        if is_closed:
            ordered_ids = ordered_ids[:-1]

        unique_ordered = []
        seen = set()
        for edge_id in ordered_ids:
            if edge_id not in seen:
                seen.add(edge_id)
                unique_ordered.append(edge_id)

        overlap = tuple(
            edge_id
            for edge_id in unique_ordered
            if edge_id in boundary_set
        )
        if not overlap:
            unresolved.append(seed_edge_id)
            remaining.remove(seed_edge_id)
            continue

        loops.append(
            MayaEdgeLoopDiagnostic(
                seed_edge_id=int(seed_edge_id),
                edge_ids=tuple(unique_ordered),
                boundary_edge_ids=overlap,
                is_closed=bool(is_closed),
            )
        )
        remaining.difference_update(overlap)

    loops.sort(
        key=lambda item: (
            min(item.boundary_edge_ids),
            item.seed_edge_id,
        )
    )
    return (
        tuple(loops),
        tuple(sorted(int(value) for value in unresolved)),
    )


def _classify_contact(loops, unresolved):
    if unresolved or not loops:
        return UNRESOLVED
    if len(loops) == 1:
        return SINGLE_LOOP
    if all(not loop.is_closed for loop in loops):
        return MULTIPLE_OPEN_LOOPS
    return MULTIPLE_MIXED_LOOPS
