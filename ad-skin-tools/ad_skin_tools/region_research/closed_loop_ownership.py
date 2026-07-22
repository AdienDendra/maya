"""Relevant closed-loop consensus with an integrated opposite-pair guard."""

from dataclasses import dataclass
import time
from typing import Optional, Tuple

import maya.api.OpenMaya as om
import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core import opposite_axis
from ad_skin_tools.region_research.mesh_context import MeshOwnershipContext


SINGLE_OWNER = "single_owner"
TWO_OWNER_PROPOSAL = "two_owner_proposal"
OPPOSITE_PAIR_PRESERVED = "opposite_pair_preserved"
MULTI_OWNER_PRESERVED = "multi_owner_preserved"
EXACT_COST_TIE_PRESERVED = "exact_cost_tie_preserved"
CONFLICT_PRESERVED = "conflict_preserved"


@dataclass(frozen=True)
class ClosedLoopDiagnostic:
    loop_index: int
    seed_edge_id: int
    edge_ids: Tuple[int, ...]
    vertex_ids: Tuple[int, ...]
    owner_indices: Tuple[int, ...]
    owner_counts: Tuple[int, ...]
    aggregate_squared_costs: Tuple[float, ...]
    proposed_owner_index: int
    opposite_axis: Optional[str]
    classification: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class ClosedLoopOwnershipResult:
    context: MeshOwnershipContext
    original_owner_indices: np.ndarray
    corrected_owner_indices: np.ndarray
    axis_context: opposite_axis.OppositeAxisContext
    diagnostics: Tuple[ClosedLoopDiagnostic, ...]
    boundary_edge_ids: Tuple[int, ...]
    unresolved_seed_edge_ids: Tuple[int, ...]
    open_loop_seed_edge_ids: Tuple[int, ...]
    conflicting_vertex_ids: Tuple[int, ...]
    applied_loop_indices: Tuple[int, ...]
    changed_vertex_ids: Tuple[int, ...]
    edge_scan_seconds: float
    loop_query_seconds: float
    consensus_seconds: float
    maya_polyselect_call_count: int
    elapsed_seconds: float

    @property
    def boundary_edge_count(self) -> int:
        return len(self.boundary_edge_ids)

    @property
    def discovered_loop_count(self) -> int:
        return len(self.diagnostics)

    @property
    def applied_loop_count(self) -> int:
        return len(self.applied_loop_indices)

    @property
    def changed_vertex_count(self) -> int:
        return len(self.changed_vertex_ids)

    @property
    def opposite_pair_preserved_count(self) -> int:
        return sum(
            value.classification == OPPOSITE_PAIR_PRESERVED
            for value in self.diagnostics
        )

    @property
    def two_owner_proposal_count(self) -> int:
        return sum(
            value.classification in {TWO_OWNER_PROPOSAL, CONFLICT_PRESERVED}
            for value in self.diagnostics
        )


def resolve_closed_loop_ownership(
    context: MeshOwnershipContext,
    owner_indices: np.ndarray,
) -> ClosedLoopOwnershipResult:
    """Inspect only loops reached from ownership-boundary edges, then apply once."""

    started = time.perf_counter()
    original = np.asarray(owner_indices, dtype=np.int32).copy()
    _validate_owner_map(context, original)

    edge_scan_started = time.perf_counter()
    edge_endpoints, boundary_edge_ids = _scan_edges(
        context.mesh_shape,
        original,
    )
    edge_scan_seconds = time.perf_counter() - edge_scan_started

    loop_query_started = time.perf_counter()
    discovered, unresolved, open_seeds, polyselect_calls = _discover_relevant_loops(
        mesh_transform=context.mesh_transform,
        boundary_edge_ids=boundary_edge_ids,
        edge_endpoints=edge_endpoints,
    )
    loop_query_seconds = time.perf_counter() - loop_query_started

    consensus_started = time.perf_counter()
    axis_context = opposite_axis.build_opposite_axis_context(
        context.influence_positions
    )
    diagnostics = []
    proposals_by_vertex = {}

    for loop_index, (seed_edge_id, edge_ids, vertex_ids) in enumerate(discovered):
        loop_vertices = np.asarray(vertex_ids, dtype=np.int32)
        loop_owners = original[loop_vertices]
        unique_owners, counts = np.unique(loop_owners, return_counts=True)
        owner_tuple = tuple(int(value) for value in unique_owners.tolist())
        count_tuple = tuple(int(value) for value in counts.tolist())
        costs = _aggregate_owner_costs(
            context,
            loop_vertices,
            owner_tuple,
        )

        proposed_owner = -1
        detected_axis = None
        if len(owner_tuple) == 1:
            classification = SINGLE_OWNER
        elif len(owner_tuple) > 2:
            classification = MULTI_OWNER_PRESERVED
        else:
            first_owner, second_owner = owner_tuple
            detected_axis = opposite_axis.detect_opposite_axis(
                first_owner,
                second_owner,
                axis_context,
            )
            if detected_axis is not None:
                classification = OPPOSITE_PAIR_PRESERVED
            elif float(costs[0]) == float(costs[1]):
                classification = EXACT_COST_TIE_PRESERVED
            else:
                classification = TWO_OWNER_PROPOSAL
                proposed_owner = owner_tuple[int(costs[1] < costs[0])]
                for vertex_id in vertex_ids:
                    proposals_by_vertex.setdefault(int(vertex_id), set()).add(
                        int(proposed_owner)
                    )

        diagnostics.append(
            ClosedLoopDiagnostic(
                loop_index=int(loop_index),
                seed_edge_id=int(seed_edge_id),
                edge_ids=edge_ids,
                vertex_ids=vertex_ids,
                owner_indices=owner_tuple,
                owner_counts=count_tuple,
                aggregate_squared_costs=costs,
                proposed_owner_index=int(proposed_owner),
                opposite_axis=detected_axis,
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
    finalized_diagnostics = []
    for diagnostic in diagnostics:
        if diagnostic.classification != TWO_OWNER_PROPOSAL:
            finalized_diagnostics.append(diagnostic)
            continue
        if any(
            vertex_id in conflicting_set
            for vertex_id in diagnostic.vertex_ids
        ):
            finalized_diagnostics.append(
                ClosedLoopDiagnostic(
                    loop_index=diagnostic.loop_index,
                    seed_edge_id=diagnostic.seed_edge_id,
                    edge_ids=diagnostic.edge_ids,
                    vertex_ids=diagnostic.vertex_ids,
                    owner_indices=diagnostic.owner_indices,
                    owner_counts=diagnostic.owner_counts,
                    aggregate_squared_costs=diagnostic.aggregate_squared_costs,
                    proposed_owner_index=diagnostic.proposed_owner_index,
                    opposite_axis=diagnostic.opposite_axis,
                    classification=CONFLICT_PRESERVED,
                )
            )
            continue
        corrected[np.asarray(diagnostic.vertex_ids, dtype=np.int32)] = int(
            diagnostic.proposed_owner_index
        )
        applied_loop_indices.append(int(diagnostic.loop_index))
        finalized_diagnostics.append(diagnostic)

    changed_vertex_ids = tuple(
        np.where(corrected != original)[0].astype(np.int32).tolist()
    )
    consensus_seconds = time.perf_counter() - consensus_started

    return ClosedLoopOwnershipResult(
        context=context,
        original_owner_indices=original,
        corrected_owner_indices=corrected,
        axis_context=axis_context,
        diagnostics=tuple(finalized_diagnostics),
        boundary_edge_ids=boundary_edge_ids,
        unresolved_seed_edge_ids=tuple(sorted(unresolved)),
        open_loop_seed_edge_ids=tuple(sorted(open_seeds)),
        conflicting_vertex_ids=conflicting_vertex_ids,
        applied_loop_indices=tuple(applied_loop_indices),
        changed_vertex_ids=changed_vertex_ids,
        edge_scan_seconds=float(edge_scan_seconds),
        loop_query_seconds=float(loop_query_seconds),
        consensus_seconds=float(consensus_seconds),
        maya_polyselect_call_count=int(polyselect_calls),
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _validate_owner_map(context, owners):
    if owners.shape != (context.vertex_count,):
        raise ValueError("owner_indices must contain one owner per mesh vertex.")
    if np.any(owners < 0) or np.any(owners >= context.influence_count):
        bad_ids = np.where(
            (owners < 0) | (owners >= context.influence_count)
        )[0].astype(np.int32).tolist()
        raise RuntimeError(
            "Closed-loop ownership received invalid owner indices. First IDs: {}"
            .format(bad_ids[:20])
        )


def _scan_edges(mesh_shape, owners):
    selection = om.MSelectionList()
    selection.add(mesh_shape)
    dag_path = selection.getDagPath(0)
    mesh_fn = om.MFnMesh(dag_path)
    endpoints = [None] * int(mesh_fn.numEdges)
    boundary = []

    iterator = om.MItMeshEdge(dag_path)
    while not iterator.isDone():
        edge_id = int(iterator.index())
        first = int(iterator.vertexId(0))
        second = int(iterator.vertexId(1))
        endpoints[edge_id] = (first, second)
        if int(owners[first]) != int(owners[second]):
            boundary.append(edge_id)
        iterator.next()

    if any(value is None for value in endpoints):
        raise RuntimeError("Failed to capture every mesh edge endpoint.")
    return tuple(endpoints), tuple(boundary)


def _discover_relevant_loops(
    mesh_transform,
    boundary_edge_ids,
    edge_endpoints,
):
    pending = set(int(value) for value in boundary_edge_ids)
    seen_loop_keys = set()
    discovered = []
    unresolved = []
    open_seeds = []
    calls = 0

    while pending:
        seed = min(pending)
        calls += 1
        raw_ids = cmds.polySelect(
            mesh_transform,
            edgeLoop=int(seed),
            noSelection=True,
        ) or []
        unique_ids = tuple(sorted({int(value) for value in raw_ids}))
        if not unique_ids or seed not in unique_ids:
            unresolved.append(seed)
            pending.remove(seed)
            continue

        pending.difference_update(unique_ids)
        if unique_ids in seen_loop_keys:
            continue
        seen_loop_keys.add(unique_ids)

        vertex_ids = _simple_closed_loop_vertices(unique_ids, edge_endpoints)
        if not vertex_ids:
            open_seeds.append(seed)
            continue
        discovered.append((int(seed), unique_ids, vertex_ids))

    discovered.sort(key=lambda value: value[1])
    return tuple(discovered), tuple(unresolved), tuple(open_seeds), calls


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
        for neighbour_id in neighbours[vertex_id]:
            if neighbour_id in unseen:
                unseen.remove(neighbour_id)
                stack.append(neighbour_id)
    if unseen:
        return tuple()
    return tuple(sorted(int(value) for value in neighbours))


def _aggregate_owner_costs(context, loop_vertices, owner_indices):
    positions = context.vertex_positions[loop_vertices]
    costs = []
    for owner_index in owner_indices:
        delta = (
            positions
            - context.influence_positions[int(owner_index)][np.newaxis, :]
        )
        squared = np.einsum("vi,vi->v", delta, delta)
        costs.append(float(np.sum(squared, dtype=np.float64)))
    return tuple(costs)
