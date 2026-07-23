"""Experimental local-domain Add Influence used only by the v11.0 smoke test."""

from dataclasses import dataclass
import time
from typing import Dict, Optional, Sequence, Tuple

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.bind_smoothing.diffusion import DEFAULT_BLEND
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.solver import BindSmoothingResult, solve_bind_smoothing
from ad_skin_tools.core import add_influence as legacy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region_research.ownership_pipeline import (
    OwnershipPipelineResult,
    solve_ownership_pipeline,
)


@dataclass(frozen=True)
class AddInfluenceV11Result:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    existing_influences: Tuple[str, ...]
    target_joints: Tuple[str, ...]
    locked_influences: Tuple[str, ...]
    claimed_vertex_ids_by_joint: Dict[str, Tuple[int, ...]]
    diagnostics: Tuple[legacy.TargetProposal, ...]
    local_domain_vertex_ids: Tuple[int, ...]
    smoothing_blend: float
    smoothing_iterations: int
    effective_maximum_influences: int
    smoothing_result: Optional[BindSmoothingResult]
    ownership_pipeline: OwnershipPipelineResult
    ownership_seconds: float
    proposal_domain_seconds: float
    local_weight_read_seconds: float
    claim_filter_seconds: float
    weight_calculation_seconds: float
    add_influence_seconds: float
    skin_column_remap_seconds: float
    weight_write_seconds: float
    production_elapsed_seconds: float

    @property
    def claimed_vertex_count(self) -> int:
        return sum(
            len(vertex_ids)
            for vertex_ids in self.claimed_vertex_ids_by_joint.values()
        )

    @property
    def local_domain_vertex_count(self) -> int:
        return len(self.local_domain_vertex_ids)


def add_influences_by_region_v11(
    mesh: str,
    target_joints: Sequence[str],
    smoothing_blend: float = DEFAULT_BLEND,
    smoothing_iterations: int = 0,
    global_owner_joint: Optional[str] = None,
) -> AddInfluenceV11Result:
    """Run production ownership and update only the local claimed domain."""

    started = time.perf_counter()
    options = BindSmoothingOptions(
        iterations=int(smoothing_iterations),
        blend=float(smoothing_blend),
    ).validated()
    selection_before = cmds.ls(selection=True, long=True) or []

    mesh_shape, mesh_transform = legacy._resolve_mesh(mesh)
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    existing = tuple(adapter.influences())
    targets = legacy._normalize_new_targets(target_joints, existing)
    all_influences = existing + targets
    locked = locked_influences(adapter.skin_cluster, existing)

    ownership_started = time.perf_counter()
    pipeline = solve_ownership_pipeline(
        mesh=mesh_transform,
        joints=all_influences,
        global_owner_joint=global_owner_joint,
    )
    ownership_seconds = time.perf_counter() - ownership_started
    context = pipeline.closest_ownership.context
    if tuple(context.influences) != all_influences:
        raise RuntimeError("Ownership pipeline returned an unexpected influence order.")

    proposal_started = time.perf_counter()
    proposed_by_joint, target_by_vertex, proposed_ids = _build_proposals(
        final_owners=pipeline.final_owner_indices,
        targets=targets,
        existing_count=len(existing),
        vertex_count=context.vertex_count,
    )
    proposed_domain_ids = _one_ring_domain(
        proposed_ids,
        context.adjacency,
        context.vertex_count,
    )
    proposal_domain_seconds = time.perf_counter() - proposal_started

    read_started = time.perf_counter()
    if proposed_domain_ids.size:
        local_skin = adapter.get_weights(proposed_domain_ids)
        if tuple(local_skin.influences) != existing:
            raise RuntimeError("skinCluster influence order changed during local read.")
        proposed_domain_weights = np.asarray(local_skin.weights, dtype=np.float64)
        legacy._validate_baseline(proposed_domain_weights)
    else:
        proposed_domain_weights = np.empty((0, len(existing)), dtype=np.float64)
    local_weight_read_seconds = time.perf_counter() - read_started

    filter_started = time.perf_counter()
    (
        claimed_by_joint,
        diagnostics,
        claimed_ids,
        claimed_target_by_vertex,
    ) = _filter_claims(
        proposed_by_joint=proposed_by_joint,
        proposed_domain_ids=proposed_domain_ids,
        proposed_domain_weights=proposed_domain_weights,
        existing=existing,
        targets=targets,
        locked=locked,
        target_by_vertex=target_by_vertex,
        vertex_count=context.vertex_count,
    )
    local_domain_ids = _one_ring_domain(
        claimed_ids,
        context.adjacency,
        context.vertex_count,
    )
    claim_filter_seconds = time.perf_counter() - filter_started

    weight_started = time.perf_counter()
    claimed_weights, smoothing_result = _calculate_claimed_weights(
        existing_weights=proposed_domain_weights,
        existing_weight_vertex_ids=proposed_domain_ids,
        claimed_ids=claimed_ids,
        target_by_vertex=claimed_target_by_vertex,
        local_domain_ids=local_domain_ids,
        all_influence_count=len(all_influences),
        context=context,
        options=options,
    )
    weight_calculation_seconds = time.perf_counter() - weight_started
    effective_maximum = options.effective_maximum_influences(len(all_influences))

    mutation_recorded = False
    add_seconds = 0.0
    remap_seconds = 0.0
    write_seconds = 0.0
    try:
        try:
            with undo_chunk("AD Skin Tool v11 Add Influence Smoke"):
                stage_started = time.perf_counter()
                for joint in targets:
                    cmds.skinCluster(
                        adapter.skin_cluster,
                        edit=True,
                        addInfluence=joint,
                        weight=0.0,
                    )
                    mutation_recorded = True
                add_seconds = time.perf_counter() - stage_started

                adapter = SkinClusterAdapter.from_mesh(mesh_shape)
                stage_started = time.perf_counter()
                weights_to_write = legacy._weights_in_skin_order(
                    adapter,
                    all_influences,
                    claimed_weights,
                )
                remap_seconds = time.perf_counter() - stage_started

                if claimed_ids.size:
                    stage_started = time.perf_counter()
                    adapter.set_weights(
                        claimed_ids,
                        weights_to_write,
                        normalize=False,
                    )
                    write_seconds = time.perf_counter() - stage_started
        except Exception:
            if mutation_recorded:
                legacy._undo_failed_operation()
            raise
    finally:
        legacy._restore_selection(selection_before)

    return AddInfluenceV11Result(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        existing_influences=existing,
        target_joints=targets,
        locked_influences=locked,
        claimed_vertex_ids_by_joint=claimed_by_joint,
        diagnostics=diagnostics,
        local_domain_vertex_ids=tuple(local_domain_ids.tolist()),
        smoothing_blend=float(options.blend),
        smoothing_iterations=int(options.iterations),
        effective_maximum_influences=int(effective_maximum),
        smoothing_result=smoothing_result,
        ownership_pipeline=pipeline,
        ownership_seconds=float(ownership_seconds),
        proposal_domain_seconds=float(proposal_domain_seconds),
        local_weight_read_seconds=float(local_weight_read_seconds),
        claim_filter_seconds=float(claim_filter_seconds),
        weight_calculation_seconds=float(weight_calculation_seconds),
        add_influence_seconds=float(add_seconds),
        skin_column_remap_seconds=float(remap_seconds),
        weight_write_seconds=float(write_seconds),
        production_elapsed_seconds=float(time.perf_counter() - started),
    )


def print_report(result: AddInfluenceV11Result) -> None:
    print("\n[AD Skin Tool - v11 Add Influence Production Timing]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Existing influences:", len(result.existing_influences))
    print("New influences:", len(result.target_joints))
    print("Locked existing influences:", len(result.locked_influences))
    print("Claimed vertices:", result.claimed_vertex_count)
    print("Local domain vertices:", result.local_domain_vertex_count)
    print("Blend:", result.smoothing_blend)
    print("Iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Ownership:", round(result.ownership_seconds, 6))
    print("Proposal + initial domain:", round(result.proposal_domain_seconds, 6))
    print("Local weight read:", round(result.local_weight_read_seconds, 6))
    print("Lock filtering + final domain:", round(result.claim_filter_seconds, 6))
    print("Claim weight calculation:", round(result.weight_calculation_seconds, 6))
    print("Add influence nodes:", round(result.add_influence_seconds, 6))
    print("Skin-column remap:", round(result.skin_column_remap_seconds, 6))
    print("Custom weight write:", round(result.weight_write_seconds, 6))
    print("Production total:", round(result.production_elapsed_seconds, 6))


def _build_proposals(final_owners, targets, existing_count, vertex_count):
    owners = np.asarray(final_owners, dtype=np.int32)
    target_by_vertex = np.full(int(vertex_count), -1, dtype=np.int32)
    proposed_by_joint = {}

    for offset, joint in enumerate(targets):
        influence_index = int(existing_count + offset)
        proposed = np.where(owners == influence_index)[0].astype(np.int32)
        proposed_by_joint[joint] = proposed
        target_by_vertex[proposed] = influence_index

    proposed_ids = np.where(target_by_vertex >= 0)[0].astype(np.int32)
    return proposed_by_joint, target_by_vertex, proposed_ids


def _filter_claims(
    proposed_by_joint,
    proposed_domain_ids,
    proposed_domain_weights,
    existing,
    targets,
    locked,
    target_by_vertex,
    vertex_count,
):
    protected = np.zeros(int(vertex_count), dtype=bool)
    if proposed_domain_ids.size:
        protected[proposed_domain_ids] = legacy._protected_mask(
            proposed_domain_weights,
            existing,
            locked,
        )

    claimed_target_by_vertex = np.asarray(target_by_vertex, dtype=np.int32).copy()
    claimed_target_by_vertex[protected] = -1
    claimed_by_joint = {}
    diagnostics = []

    for joint in targets:
        proposed = proposed_by_joint[joint]
        accepted = proposed[~protected[proposed]]
        rejected = proposed[protected[proposed]]
        claimed_by_joint[joint] = tuple(accepted.tolist())
        diagnostics.append(
            legacy.TargetProposal(
                joint=joint,
                proposed_vertex_ids=tuple(proposed.tolist()),
                accepted_vertex_ids=tuple(accepted.tolist()),
                protected_vertex_ids=tuple(rejected.tolist()),
            )
        )

    claimed_ids = np.where(claimed_target_by_vertex >= 0)[0].astype(np.int32)
    return (
        claimed_by_joint,
        tuple(diagnostics),
        claimed_ids,
        claimed_target_by_vertex,
    )


def _calculate_claimed_weights(
    existing_weights,
    existing_weight_vertex_ids,
    claimed_ids,
    target_by_vertex,
    local_domain_ids,
    all_influence_count,
    context,
    options,
):
    if not claimed_ids.size:
        return np.empty((0, all_influence_count), dtype=np.float64), None

    if options.iterations == 0:
        weights = np.zeros(
            (claimed_ids.size, all_influence_count),
            dtype=np.float64,
        )
        weights[
            np.arange(claimed_ids.size, dtype=np.int32),
            target_by_vertex[claimed_ids],
        ] = 1.0
        return weights, None

    source_row_by_vertex = np.full(context.vertex_count, -1, dtype=np.int32)
    source_row_by_vertex[existing_weight_vertex_ids] = np.arange(
        existing_weight_vertex_ids.size,
        dtype=np.int32,
    )
    source_rows = source_row_by_vertex[local_domain_ids]
    if np.any(source_rows < 0):
        raise RuntimeError("Local smoothing domain was not included in the skin read.")

    baseline = np.asarray(existing_weights[source_rows], dtype=np.float64)
    baseline /= np.sum(baseline, axis=1, dtype=np.float64)[:, np.newaxis]

    initial = np.zeros(
        (local_domain_ids.size, all_influence_count),
        dtype=np.float64,
    )
    initial[:, :baseline.shape[1]] = baseline

    local_index = np.full(context.vertex_count, -1, dtype=np.int32)
    local_index[local_domain_ids] = np.arange(
        local_domain_ids.size,
        dtype=np.int32,
    )
    claimed_local_ids = local_index[claimed_ids]
    initial[claimed_local_ids] = 0.0
    initial[claimed_local_ids, target_by_vertex[claimed_ids]] = 1.0

    claimed_mask = np.zeros(context.vertex_count, dtype=bool)
    claimed_mask[claimed_ids] = True
    local_adjacency = tuple(
        tuple(
            int(local_index[neighbour_id])
            for neighbour_id in context.adjacency[vertex_id]
        )
        if claimed_mask[vertex_id]
        else tuple()
        for vertex_id in local_domain_ids.tolist()
    )
    if any(
        neighbour_id < 0
        for neighbours in local_adjacency
        for neighbour_id in neighbours
    ):
        raise RuntimeError("Local domain is missing a claimed vertex neighbour.")

    result = solve_bind_smoothing(
        owner_indices=np.argmax(initial, axis=1).astype(np.int32),
        adjacency=local_adjacency,
        vertex_positions=context.vertex_positions[local_domain_ids],
        influence_positions=context.influence_positions,
        options=options,
        initial_weights=initial,
        mutable_vertex_ids=claimed_local_ids,
        constrained_vertex_ids=claimed_local_ids,
    )
    return np.asarray(
        result.weights[claimed_local_ids],
        dtype=np.float64,
    ).copy(), result


def _one_ring_domain(seed_vertex_ids, adjacency, vertex_count):
    seeds = np.asarray(seed_vertex_ids, dtype=np.int32)
    if not seeds.size:
        return np.empty(0, dtype=np.int32)

    mask = np.zeros(int(vertex_count), dtype=bool)
    mask[seeds] = True
    for vertex_id in seeds.tolist():
        neighbours = adjacency[int(vertex_id)]
        if neighbours:
            mask[np.asarray(neighbours, dtype=np.int32)] = True
    return np.where(mask)[0].astype(np.int32)
