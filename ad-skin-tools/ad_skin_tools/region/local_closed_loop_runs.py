"""Local closed-loop owner runs for experimental Region correction.

The production Region solver is not modified. This module starts from its final
hard owner map, discovers simple closed Maya edge loops, preserves cyclic vertex
order, and proposes two kinds of corrections:

- two-owner loops keep the v3.10D whole-loop aggregate-distance consensus;
- multi-owner loops collapse only a local A-B-A run, where the middle B run is
  bordered by the same owner A on both sides.

All proposals are collected before application. Conflicting target owners for
the same vertex are reported and skipped.
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
TWO_OWNER_WHOLE_LOOP = "two_owner_whole_loop"
MULTI_OWNER_LOCAL_RUNS = "multi_owner_local_runs"
MULTI_OWNER_NO_LOCAL_RUN = "multi_owner_no_local_run"
EXACT_COST_TIE = "exact_cost_tie"

WHOLE_LOOP_PROPOSAL = "whole_loop"
LOCAL_RUN_PROPOSAL = "local_run"


@dataclass(frozen=True)
class LocalOwnerRun:
    owner_index: int
    vertex_ids: Tuple[int, ...]


@dataclass(frozen=True)
class LocalRunProposal:
    loop_index: int
    run_index: int
    kind: str
    source_owner_index: int
    target_owner_index: int
    vertex_ids: Tuple[int, ...]

    @property
    def changed_vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class LocalClosedLoopDiagnostic:
    edge_ids: Tuple[int, ...]
    vertex_ids: Tuple[int, ...]
    owner_indices: Tuple[int, ...]
    owner_counts: Tuple[int, ...]
    aggregate_squared_costs: Tuple[float, ...]
    whole_loop_target_owner_index: int
    runs: Tuple[LocalOwnerRun, ...]
    proposal_indices: Tuple[int, ...]
    classification: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class LocalClosedLoopConsensusResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    original_owner_indices: np.ndarray
    corrected_owner_indices: np.ndarray
    diagnostics: Tuple[LocalClosedLoopDiagnostic, ...]
    proposals: Tuple[LocalRunProposal, ...]
    open_loop_count: int
    unresolved_seed_edge_ids: Tuple[int, ...]
    applied_proposal_indices: Tuple[int, ...]
    conflict_proposal_indices: Tuple[int, ...]
    conflicting_vertex_ids: Tuple[int, ...]
    changed_vertex_ids: Tuple[int, ...]

    @property
    def closed_loop_count(self) -> int:
        return len(self.diagnostics)

    @property
    def applied_proposal_count(self) -> int:
        return len(self.applied_proposal_indices)

    @property
    def conflict_proposal_count(self) -> int:
        return len(self.conflict_proposal_indices)

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


def solve_local_closed_loop_runs(
    region_result: RegionOwnershipResult,
) -> LocalClosedLoopConsensusResult:
    """Apply one-pass whole-loop and local-run proposals in memory."""

    original = np.asarray(region_result.owner_indices, dtype=np.int32)
    if original.shape != (region_result.vertex_count,):
        raise ValueError("Region owner_indices must contain one owner per vertex.")

    context = _build_mesh_edge_context(region_result.mesh_shape)
    closed_loops, open_loop_count, unresolved = _discover_maya_edge_loops(
        mesh_transform=region_result.mesh_transform,
        context=context,
    )

    diagnostics = []
    proposals = []

    for loop_index, (edge_ids, vertex_ids) in enumerate(closed_loops):
        loop_vertices = np.asarray(vertex_ids, dtype=np.int32)
        loop_owners = original[loop_vertices]
        unique_owners, counts = np.unique(loop_owners, return_counts=True)
        owner_indices = tuple(int(value) for value in unique_owners.tolist())
        owner_counts = tuple(int(value) for value in counts.tolist())
        costs = _aggregate_owner_costs(
            region_result,
            loop_vertices,
            owner_indices,
        )
        runs = _cyclic_owner_runs(vertex_ids, loop_owners)
        loop_proposal_indices = []
        whole_loop_target = -1

        if len(owner_indices) == 1:
            classification = SINGLE_OWNER

        elif len(owner_indices) == 2:
            if costs[0] == costs[1]:
                classification = EXACT_COST_TIE
            else:
                classification = TWO_OWNER_WHOLE_LOOP
                whole_loop_target = owner_indices[int(costs[1] < costs[0])]
                proposal_index = len(proposals)
                proposals.append(
                    LocalRunProposal(
                        loop_index=int(loop_index),
                        run_index=-1,
                        kind=WHOLE_LOOP_PROPOSAL,
                        source_owner_index=-1,
                        target_owner_index=int(whole_loop_target),
                        vertex_ids=tuple(int(value) for value in vertex_ids),
                    )
                )
                loop_proposal_indices.append(proposal_index)

        else:
            for run_index, run in enumerate(runs):
                previous_run = runs[(run_index - 1) % len(runs)]
                next_run = runs[(run_index + 1) % len(runs)]
                if (
                    previous_run.owner_index == next_run.owner_index
                    and run.owner_index != previous_run.owner_index
                ):
                    proposal_index = len(proposals)
                    proposals.append(
                        LocalRunProposal(
                            loop_index=int(loop_index),
                            run_index=int(run_index),
                            kind=LOCAL_RUN_PROPOSAL,
                            source_owner_index=int(run.owner_index),
                            target_owner_index=int(previous_run.owner_index),
                            vertex_ids=tuple(
                                int(value) for value in run.vertex_ids
                            ),
                        )
                    )
                    loop_proposal_indices.append(proposal_index)

            classification = (
                MULTI_OWNER_LOCAL_RUNS
                if loop_proposal_indices
                else MULTI_OWNER_NO_LOCAL_RUN
            )

        diagnostics.append(
            LocalClosedLoopDiagnostic(
                edge_ids=tuple(int(value) for value in edge_ids),
                vertex_ids=tuple(int(value) for value in vertex_ids),
                owner_indices=owner_indices,
                owner_counts=owner_counts,
                aggregate_squared_costs=tuple(costs),
                whole_loop_target_owner_index=int(whole_loop_target),
                runs=runs,
                proposal_indices=tuple(loop_proposal_indices),
                classification=classification,
            )
        )

    proposals_by_vertex = {}
    for proposal in proposals:
        for vertex_id in proposal.vertex_ids:
            proposals_by_vertex.setdefault(int(vertex_id), set()).add(
                int(proposal.target_owner_index)
            )

    conflicting_vertex_ids = tuple(
        sorted(
            vertex_id
            for vertex_id, targets in proposals_by_vertex.items()
            if len(targets) > 1
        )
    )
    conflicting_set = set(conflicting_vertex_ids)

    corrected = original.copy()
    applied_proposal_indices = []
    conflict_proposal_indices = []

    for proposal_index, proposal in enumerate(proposals):
        if any(vertex_id in conflicting_set for vertex_id in proposal.vertex_ids):
            conflict_proposal_indices.append(int(proposal_index))
            continue

        corrected[
            np.asarray(proposal.vertex_ids, dtype=np.int32)
        ] = int(proposal.target_owner_index)
        applied_proposal_indices.append(int(proposal_index))

    changed_vertex_ids = tuple(
        np.where(corrected != original)[0].astype(np.int32).tolist()
    )

    return LocalClosedLoopConsensusResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        original_owner_indices=original.copy(),
        corrected_owner_indices=corrected,
        diagnostics=tuple(diagnostics),
        proposals=tuple(proposals),
        open_loop_count=int(open_loop_count),
        unresolved_seed_edge_ids=tuple(sorted(int(value) for value in unresolved)),
        applied_proposal_indices=tuple(applied_proposal_indices),
        conflict_proposal_indices=tuple(conflict_proposal_indices),
        conflicting_vertex_ids=conflicting_vertex_ids,
        changed_vertex_ids=changed_vertex_ids,
    )


def validate_corrected_owner_map(
    region_result: RegionOwnershipResult,
    corrected_owner_indices: np.ndarray,
) -> CorrectedOwnerValidationResult:
    """Run one read-only connectivity/facing pass on corrected owners."""

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


def _aggregate_owner_costs(region_result, loop_vertices, owner_indices):
    costs = []
    points = region_result.vertex_positions[loop_vertices]
    for owner_index in owner_indices:
        delta = (
            points
            - region_result.influence_positions[int(owner_index)][np.newaxis, :]
        )
        squared = np.einsum("vi,vi->v", delta, delta)
        costs.append(float(np.sum(squared, dtype=np.float64)))
    return tuple(costs)


def _cyclic_owner_runs(vertex_ids, owner_indices):
    runs = []
    for vertex_id, owner_index in zip(vertex_ids, owner_indices.tolist()):
        owner_index = int(owner_index)
        vertex_id = int(vertex_id)
        if runs and runs[-1][0] == owner_index:
            runs[-1][1].append(vertex_id)
        else:
            runs.append([owner_index, [vertex_id]])

    if len(runs) > 1 and runs[0][0] == runs[-1][0]:
        runs[0][1] = runs[-1][1] + runs[0][1]
        runs.pop()

    return tuple(
        LocalOwnerRun(
            owner_index=int(owner_index),
            vertex_ids=tuple(int(value) for value in run_vertices),
        )
        for owner_index, run_vertices in runs
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

        vertex_ids = _ordered_simple_cycle_vertices(
            key,
            context.edge_endpoints,
        )
        if not vertex_ids:
            open_loop_count += 1
            continue

        closed_loops.append((key, vertex_ids))

    closed_loops.sort(key=lambda item: item[0])
    return closed_loops, open_loop_count, tuple(unresolved)


def _ordered_simple_cycle_vertices(edge_ids, edge_endpoints):
    neighbours = {}
    for edge_id in edge_ids:
        first, second = edge_endpoints[int(edge_id)]
        neighbours.setdefault(first, set()).add(second)
        neighbours.setdefault(second, set()).add(first)

    if not neighbours or any(len(values) != 2 for values in neighbours.values()):
        return tuple()
    if len(edge_ids) != len(neighbours):
        return tuple()

    start = min(neighbours)
    ordered = [start]
    previous = None
    current = start

    while True:
        candidates = set(neighbours[current])
        if previous is not None:
            candidates.discard(previous)

        if previous is None:
            next_vertex = min(candidates)
        elif len(candidates) == 1:
            next_vertex = next(iter(candidates))
        else:
            return tuple()

        if next_vertex == start:
            if len(ordered) != len(neighbours):
                return tuple()
            break

        if next_vertex in ordered:
            return tuple()

        ordered.append(int(next_vertex))
        previous, current = current, int(next_vertex)

        if len(ordered) > len(neighbours):
            return tuple()

    return tuple(int(value) for value in ordered)
