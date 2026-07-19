"""Closed Maya edge-loop consensus for experimental Region correction.

This module never changes the production Region solver. It inspects the final
hard owner map, asks Maya for topology edge loops, and proposes one owner for
closed loops that currently contain exactly two owners.
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
from ad_skin_tools.region.facing import (
    build_facing_mesh_context,
    classify_region_facing,
)
from ad_skin_tools.region.solver import RegionOwnershipResult


SINGLE_OWNER = "single_owner"
TWO_OWNER_PROPOSAL = "two_owner_proposal"
MULTI_OWNER_IGNORED = "multi_owner_ignored"
EXACT_COST_TIE = "exact_cost_tie"


@dataclass(frozen=True)
class ClosedLoopDiagnostic:
    edge_ids: Tuple[int, ...]
    vertex_ids: Tuple[int, ...]
    owner_indices: Tuple[int, ...]
    owner_counts: Tuple[int, ...]
    aggregate_squared_costs: Tuple[float, ...]
    proposed_owner_index: int
    classification: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)

    @property
    def changed_vertex_count(self) -> int:
        if self.proposed_owner_index < 0:
            return 0
        return self.vertex_count - self.owner_counts[
            self.owner_indices.index(self.proposed_owner_index)
        ]


@dataclass(frozen=True)
class ClosedLoopConsensusResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    original_owner_indices: np.ndarray
    corrected_owner_indices: np.ndarray
    diagnostics: Tuple[ClosedLoopDiagnostic, ...]
    open_loop_count: int
    unresolved_seed_edge_ids: Tuple[int, ...]
    applied_loop_indices: Tuple[int, ...]
    conflict_loop_indices: Tuple[int, ...]
    conflicting_vertex_ids: Tuple[int, ...]
    changed_vertex_ids: Tuple[int, ...]

    @property
    def closed_loop_count(self) -> int:
        return len(self.diagnostics)

    @property
    def applied_loop_count(self) -> int:
        return len(self.applied_loop_indices)

    @property
    def conflict_loop_count(self) -> int:
        return len(self.conflict_loop_indices)

    @property
    def changed_vertex_count(self) -> int:
        return len(self.changed_vertex_ids)


@dataclass(frozen=True)
class CorrectedOwnerValidationResult:
    connected_region_count: int
    detached_vertex_ids: Tuple[int, ...]
    ambiguous_vertex_ids: Tuple[int, ...]

    @property
    def detached_vertex_count(self) -> int:
        return len(self.detached_vertex_ids)

    @property
    def ambiguous_vertex_count(self) -> int:
        return len(self.ambiguous_vertex_ids)


@dataclass(frozen=True)
class _MeshEdgeContext:
    edge_endpoints: Tuple[Tuple[int, int], ...]

    @property
    def edge_count(self) -> int:
        return len(self.edge_endpoints)


def solve_closed_loop_consensus(
    region_result: RegionOwnershipResult,
) -> ClosedLoopConsensusResult:
    """Propose and apply one-pass owner consensus on closed Maya edge loops."""

    original = np.asarray(region_result.owner_indices, dtype=np.int32)
    if original.shape != (region_result.vertex_count,):
        raise ValueError("Region owner_indices must contain one owner per vertex.")

    context = _build_mesh_edge_context(region_result.mesh_shape)
    closed_loops, open_loop_count, unresolved = _discover_maya_edge_loops(
        mesh_transform=region_result.mesh_transform,
        context=context,
    )

    diagnostics = []
    proposals_by_vertex = {}

    for edge_ids, vertex_ids in closed_loops:
        loop_vertices = np.asarray(vertex_ids, dtype=np.int32)
        owners = original[loop_vertices]
        unique_owners, counts = np.unique(owners, return_counts=True)
        owner_indices = tuple(int(value) for value in unique_owners.tolist())
        owner_counts = tuple(int(value) for value in counts.tolist())

        costs = []
        for owner_index in owner_indices:
            delta = (
                region_result.vertex_positions[loop_vertices]
                - region_result.influence_positions[int(owner_index)][np.newaxis, :]
            )
            squared = np.einsum("vi,vi->v", delta, delta)
            costs.append(float(np.sum(squared, dtype=np.float64)))

        proposed_owner = -1
        if len(owner_indices) == 1:
            classification = SINGLE_OWNER
        elif len(owner_indices) == 2:
            if costs[0] == costs[1]:
                classification = EXACT_COST_TIE
            else:
                classification = TWO_OWNER_PROPOSAL
                proposed_owner = owner_indices[int(costs[1] < costs[0])]
                for vertex_id in vertex_ids:
                    proposals_by_vertex.setdefault(int(vertex_id), set()).add(
                        int(proposed_owner)
                    )
        else:
            classification = MULTI_OWNER_IGNORED

        diagnostics.append(
            ClosedLoopDiagnostic(
                edge_ids=edge_ids,
                vertex_ids=vertex_ids,
                owner_indices=owner_indices,
                owner_counts=owner_counts,
                aggregate_squared_costs=tuple(costs),
                proposed_owner_index=int(proposed_owner),
                classification=classification,
            )
        )

    conflicting_vertex_ids = tuple(
        sorted(
            vertex_id
            for vertex_id, proposals in proposals_by_vertex.items()
            if len(proposals) > 1
        )
    )
    conflicting_set = set(conflicting_vertex_ids)

    corrected = original.copy()
    applied_loop_indices = []
    conflict_loop_indices = []

    for loop_index, diagnostic in enumerate(diagnostics):
        if diagnostic.classification != TWO_OWNER_PROPOSAL:
            continue
        if any(vertex_id in conflicting_set for vertex_id in diagnostic.vertex_ids):
            conflict_loop_indices.append(int(loop_index))
            continue

        corrected[
            np.asarray(diagnostic.vertex_ids, dtype=np.int32)
        ] = int(diagnostic.proposed_owner_index)
        applied_loop_indices.append(int(loop_index))

    changed_vertex_ids = tuple(
        np.where(corrected != original)[0].astype(np.int32).tolist()
    )

    return ClosedLoopConsensusResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        original_owner_indices=original.copy(),
        corrected_owner_indices=corrected,
        diagnostics=tuple(diagnostics),
        open_loop_count=int(open_loop_count),
        unresolved_seed_edge_ids=tuple(sorted(unresolved)),
        applied_loop_indices=tuple(applied_loop_indices),
        conflict_loop_indices=tuple(conflict_loop_indices),
        conflicting_vertex_ids=conflicting_vertex_ids,
        changed_vertex_ids=changed_vertex_ids,
    )


def validate_corrected_owner_map(
    region_result: RegionOwnershipResult,
    corrected_owner_indices: np.ndarray,
) -> CorrectedOwnerValidationResult:
    """Run one read-only connectivity/facing validation pass on corrected owners."""

    owners = np.asarray(corrected_owner_indices, dtype=np.int32)
    if owners.shape != (region_result.vertex_count,):
        raise ValueError("Corrected owner map must contain one owner per vertex.")

    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    facing_context = build_facing_mesh_context(region_result.mesh_shape)
    detached = set()
    ambiguous = set()
    connected_region_count = 0

    for source_index in range(region_result.influence_count):
        connectivity = partition_influence_ownership(
            region_result.distance_result,
            owners,
            source_index,
            adjacency,
        )
        facing = classify_region_facing(
            region_result.distance_result,
            connectivity,
            facing_context,
        )
        connected_region_count += connectivity.region_count
        detached.update(facing.detached_vertex_ids)
        ambiguous.update(facing.ambiguous_vertex_ids)

    return CorrectedOwnerValidationResult(
        connected_region_count=int(connected_region_count),
        detached_vertex_ids=tuple(sorted(int(value) for value in detached)),
        ambiguous_vertex_ids=tuple(sorted(int(value) for value in ambiguous)),
    )


def _build_mesh_edge_context(mesh_shape):
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)
    endpoints = [None] * int(mesh_fn.numEdges)

    iterator = om.MItMeshEdge(dag_path)
    while not iterator.isDone():
        endpoints[int(iterator.index())] = (
            int(iterator.vertexId(0)),
            int(iterator.vertexId(1)),
        )
        iterator.next()

    if any(value is None for value in endpoints):
        raise RuntimeError("Failed to collect every mesh edge endpoint.")

    return _MeshEdgeContext(edge_endpoints=tuple(endpoints))


def _discover_maya_edge_loops(mesh_transform, context):
    remaining = set(range(context.edge_count))
    seen_keys = set()
    closed_loops = []
    open_loop_count = 0
    unresolved = []

    while remaining:
        seed_edge_id = min(remaining)
        raw_ids = cmds.polySelect(
            mesh_transform,
            edgeLoop=int(seed_edge_id),
            noSelection=True,
        ) or []

        unique_ids = []
        seen = set()
        for value in raw_ids:
            edge_id = int(value)
            if edge_id not in seen:
                seen.add(edge_id)
                unique_ids.append(edge_id)

        if not unique_ids or seed_edge_id not in seen:
            unresolved.append(int(seed_edge_id))
            remaining.remove(seed_edge_id)
            continue

        remaining.difference_update(unique_ids)
        key = tuple(sorted(unique_ids))
        if key in seen_keys:
            continue
        seen_keys.add(key)

        vertex_ids = _simple_closed_loop_vertices(
            key,
            context.edge_endpoints,
        )
        if not vertex_ids:
            open_loop_count += 1
            continue

        closed_loops.append((key, vertex_ids))

    closed_loops.sort(key=lambda item: item[0])
    return closed_loops, open_loop_count, tuple(unresolved)


def _simple_closed_loop_vertices(edge_ids, edge_endpoints):
    neighbours = {}
    for edge_id in edge_ids:
        first, second = edge_endpoints[int(edge_id)]
        neighbours.setdefault(first, set()).add(second)
        neighbours.setdefault(second, set()).add(first)

    if not neighbours or any(len(values) != 2 for values in neighbours.values()):
        return tuple()
    if len(edge_ids) != len(neighbours):
        return tuple()

    unseen = set(neighbours)
    stack = [min(unseen)]
    unseen.remove(stack[0])
    while stack:
        vertex_id = stack.pop()
        for neighbour in neighbours[vertex_id]:
            if neighbour in unseen:
                unseen.remove(neighbour)
                stack.append(neighbour)

    if unseen:
        return tuple()
    return tuple(sorted(int(value) for value in neighbours))
