"""Add pending influences using final ownership and local-domain weight writes.

Ownership and Region resolution evaluate existing and pending joints together.
Only unlocked rows whose final owner is a pending joint are updated. Iteration
zero writes hard claims; positive iterations smooth only those claimed rows
against a fixed one-ring boundary. New influence nodes are added in one Maya
batch command before the solved weights are written.
"""

from dataclasses import dataclass
import time
from typing import Dict, Optional, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.bind_smoothing.diffusion import DEFAULT_BLEND
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.solver import BindSmoothingResult, solve_bind_smoothing
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region.ownership_pipeline import (
    OwnershipPipelineResult,
    solve_ownership_pipeline,
)


np = ensure_numpy()
STORED_WEIGHT_TOLERANCE = 1e-10


@dataclass(frozen=True)
class TargetProposal:
    joint: str
    proposed_vertex_ids: Tuple[int, ...]
    accepted_vertex_ids: Tuple[int, ...]
    protected_vertex_ids: Tuple[int, ...]


@dataclass(frozen=True)
class AddInfluenceResult:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    existing_influences: Tuple[str, ...]
    target_joints: Tuple[str, ...]
    locked_influences: Tuple[str, ...]
    claimed_vertex_ids_by_joint: Dict[str, Tuple[int, ...]]
    diagnostics: Tuple[TargetProposal, ...]
    local_domain_vertex_ids: Tuple[int, ...]
    smoothing_blend: float
    smoothing_iterations: int
    effective_maximum_influences: int
    skin_maximum_influences: int
    smoothing_result: Optional[BindSmoothingResult]
    ownership_pipeline: OwnershipPipelineResult
    ownership_seconds: float
    proposal_domain_seconds: float
    local_weight_read_seconds: float
    claim_filter_seconds: float
    weight_calculation_seconds: float
    add_influence_seconds: float
    skin_metadata_seconds: float
    skin_column_remap_seconds: float
    weight_write_seconds: float
    validation_seconds: float
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


def add_influences_by_region(
    mesh: str,
    target_joints: Sequence[str],
    smoothing_blend: float = DEFAULT_BLEND,
    smoothing_iterations: int = 0,
    global_owner_joint: Optional[str] = None,
) -> AddInfluenceResult:
    """Add pending joints and update only their unlocked final-owner rows."""

    started = time.perf_counter()
    options = BindSmoothingOptions(
        iterations=int(smoothing_iterations),
        blend=float(smoothing_blend),
    ).validated()
    selection_before = cmds.ls(selection=True, long=True) or []

    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    existing = tuple(adapter.influences())
    targets = _normalize_new_targets(target_joints, existing)
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
        raise RuntimeError(
            "Ownership pipeline returned an unexpected influence order."
        )

    proposal_started = time.perf_counter()
    proposed_by_joint, target_by_vertex, proposed_ids = _build_proposals(
        final_owners=pipeline.final_owner_indices,
        targets=targets,
        existing_count=len(existing),
        vertex_count=context.vertex_count,
    )
    proposed_domain_ids = (
        proposed_ids
        if options.iterations == 0
        else _one_ring_domain(
            proposed_ids,
            context.adjacency,
            context.vertex_count,
        )
    )
    proposal_domain_seconds = time.perf_counter() - proposal_started

    read_started = time.perf_counter()
    if proposed_domain_ids.size:
        local_skin = adapter.get_weights(proposed_domain_ids)
        if tuple(local_skin.influences) != existing:
            raise RuntimeError(
                "skinCluster influence order changed during local read."
            )
        proposed_domain_weights = np.asarray(
            local_skin.weights,
            dtype=np.float64,
        )
        _validate_baseline(proposed_domain_weights)
    else:
        proposed_domain_weights = np.empty(
            (0, len(existing)),
            dtype=np.float64,
        )
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
    local_domain_ids = (
        claimed_ids
        if options.iterations == 0
        else _one_ring_domain(
            claimed_ids,
            context.adjacency,
            context.vertex_count,
        )
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
    effective_maximum = options.effective_maximum_influences(
        len(all_influences)
    )

    mutation_recorded = False
    add_seconds = 0.0
    metadata_seconds = 0.0
    remap_seconds = 0.0
    write_seconds = 0.0
    validation_seconds = 0.0
    skin_maximum = 0
    production_elapsed_seconds = 0.0
    try:
        try:
            with undo_chunk("AD Skin Tool Add Influence"):
                stage_started = time.perf_counter()
                cmds.skinCluster(
                    adapter.skin_cluster,
                    edit=True,
                    addInfluence=list(targets),
                    weight=0.0,
                )
                mutation_recorded = True
                add_seconds = time.perf_counter() - stage_started

                adapter = SkinClusterAdapter.from_mesh(mesh_shape)

                stage_started = time.perf_counter()
                skin_maximum = _ensure_maximum_influences_metadata(
                    adapter.skin_cluster,
                    effective_maximum,
                )
                metadata_seconds = time.perf_counter() - stage_started

                stage_started = time.perf_counter()
                weights_to_write = _weights_in_skin_order(
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

                production_elapsed_seconds = time.perf_counter() - started

                if claimed_ids.size:
                    stage_started = time.perf_counter()
                    _validate_local_write(
                        adapter=adapter,
                        claimed_ids=claimed_ids,
                        expected_weights=weights_to_write,
                        maximum_influences=effective_maximum,
                    )
                    validation_seconds = time.perf_counter() - stage_started
        except Exception:
            if mutation_recorded:
                _undo_failed_operation()
            raise
    finally:
        _restore_selection(selection_before)

    return AddInfluenceResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        existing_influences=existing,
        target_joints=targets,
        locked_influences=locked,
        claimed_vertex_ids_by_joint=claimed_by_joint,
        diagnostics=diagnostics,
        local_domain_vertex_ids=tuple(
            int(vertex_id)
            for vertex_id in local_domain_ids.tolist()
        ),
        smoothing_blend=float(options.blend),
        smoothing_iterations=int(options.iterations),
        effective_maximum_influences=int(effective_maximum),
        skin_maximum_influences=int(skin_maximum),
        smoothing_result=smoothing_result,
        ownership_pipeline=pipeline,
        ownership_seconds=float(ownership_seconds),
        proposal_domain_seconds=float(proposal_domain_seconds),
        local_weight_read_seconds=float(local_weight_read_seconds),
        claim_filter_seconds=float(claim_filter_seconds),
        weight_calculation_seconds=float(weight_calculation_seconds),
        add_influence_seconds=float(add_seconds),
        skin_metadata_seconds=float(metadata_seconds),
        skin_column_remap_seconds=float(remap_seconds),
        weight_write_seconds=float(write_seconds),
        validation_seconds=float(validation_seconds),
        production_elapsed_seconds=float(production_elapsed_seconds),
    )


def print_report(result: AddInfluenceResult) -> None:
    global_owner = (
        result.ownership_pipeline.global_owner_assignment.global_owner_joint
        or "<none>"
    )
    influence_count = len(result.existing_influences) + len(result.target_joints)
    mixed_vertex_count = (
        result.smoothing_result.diffusion_result.mixed_vertex_count
        if result.smoothing_result is not None
        else 0
    )
    print("\n[AD Skin Tool - Add Influence]")
    print("Mesh:", result.mesh_transform)
    print("Global Owner:", global_owner)
    print("Vertices:", result.ownership_pipeline.vertex_count)
    print("Influences:", influence_count)
    print("New influences:", len(result.target_joints))
    print("Claimed vertices:", result.claimed_vertex_count)
    print("Smoothing Blend:", result.smoothing_blend)
    print("Smoothing Iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Smoothing mixed vertices:", mixed_vertex_count)
    print("Locked influences:", len(result.locked_influences))
    print("Elapsed: {:.6f} s".format(result.production_elapsed_seconds))


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
        protected[proposed_domain_ids] = _protected_mask(
            proposed_domain_weights,
            existing,
            locked,
        )

    claimed_target_by_vertex = np.asarray(
        target_by_vertex,
        dtype=np.int32,
    ).copy()
    claimed_target_by_vertex[protected] = -1
    claimed_by_joint = {}
    diagnostics = []

    for joint in targets:
        proposed = proposed_by_joint[joint]
        accepted = proposed[~protected[proposed]]
        rejected = proposed[protected[proposed]]
        claimed_by_joint[joint] = tuple(
            int(vertex_id)
            for vertex_id in accepted.tolist()
        )
        diagnostics.append(
            TargetProposal(
                joint=joint,
                proposed_vertex_ids=tuple(
                    int(vertex_id)
                    for vertex_id in proposed.tolist()
                ),
                accepted_vertex_ids=tuple(
                    int(vertex_id)
                    for vertex_id in accepted.tolist()
                ),
                protected_vertex_ids=tuple(
                    int(vertex_id)
                    for vertex_id in rejected.tolist()
                ),
            )
        )

    claimed_ids = np.where(
        claimed_target_by_vertex >= 0
    )[0].astype(np.int32)
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

    source_row_by_vertex = np.full(
        context.vertex_count,
        -1,
        dtype=np.int32,
    )
    source_row_by_vertex[existing_weight_vertex_ids] = np.arange(
        existing_weight_vertex_ids.size,
        dtype=np.int32,
    )
    source_rows = source_row_by_vertex[local_domain_ids]
    if np.any(source_rows < 0):
        raise RuntimeError(
            "Local smoothing domain was not included in the skin read."
        )

    baseline = np.asarray(
        existing_weights[source_rows],
        dtype=np.float64,
    )
    baseline /= np.sum(
        baseline,
        axis=1,
        dtype=np.float64,
    )[:, np.newaxis]

    initial = np.zeros(
        (local_domain_ids.size, all_influence_count),
        dtype=np.float64,
    )
    initial[:, :baseline.shape[1]] = baseline

    local_index = np.full(
        context.vertex_count,
        -1,
        dtype=np.int32,
    )
    local_index[local_domain_ids] = np.arange(
        local_domain_ids.size,
        dtype=np.int32,
    )
    claimed_local_ids = local_index[claimed_ids]
    initial[claimed_local_ids] = 0.0
    initial[
        claimed_local_ids,
        target_by_vertex[claimed_ids],
    ] = 1.0

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
        raise RuntimeError(
            "Local domain is missing a claimed vertex neighbour."
        )

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


def _validate_baseline(weights) -> None:
    matrix = np.asarray(weights, dtype=np.float64)
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
        raise RuntimeError(
            "Existing skin weights are invalid or non-finite."
        )

    tolerance = (
        float(np.finfo(np.float64).eps)
        * max(1, matrix.shape[1])
        * 16.0
    )
    if np.any(matrix < -tolerance):
        bad = np.where(
            np.any(matrix < -tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Existing skin weights contain negative values. "
            "First local row IDs: {}".format(bad.tolist())
        )

    empty = np.where(
        np.sum(matrix, axis=1, dtype=np.float64) <= tolerance
    )[0]
    if empty.size:
        raise RuntimeError(
            "Existing skin weights contain empty rows. "
            "First local row IDs: {}".format(empty[:20].tolist())
        )


def _protected_mask(weights, influences, locked):
    if not locked:
        return np.zeros(weights.shape[0], dtype=bool)

    columns = {
        joint: index
        for index, joint in enumerate(influences)
    }
    locked_columns = [columns[joint] for joint in locked]
    tolerance = (
        float(np.finfo(np.float64).eps)
        * max(1, weights.shape[1])
        * 16.0
    )
    return np.any(
        np.abs(weights[:, locked_columns]) > tolerance,
        axis=1,
    )


def _ensure_maximum_influences_metadata(skin_cluster, required_maximum):
    """Raise Maya's stored max-influence metadata without rebuilding weights."""

    attribute = "{}.maxInfluences".format(skin_cluster)
    if not cmds.objExists(attribute):
        raise RuntimeError(
            "skinCluster Max Influences attribute is unavailable:\n{}".format(
                attribute
            )
        )

    current = int(cmds.getAttr(attribute))
    required = int(required_maximum)
    target = max(current, required)
    if current < target:
        cmds.setAttr(attribute, target)

    stored = int(cmds.getAttr(attribute))
    if stored < target:
        raise RuntimeError(
            "Maya did not store the required Max Influences metadata. "
            "Required: {} | Stored: {}".format(target, stored)
        )
    return stored


def _weights_in_skin_order(adapter, source_influences, source_weights):
    source = np.asarray(source_weights, dtype=np.float64)
    if source.ndim != 2 or source.shape[1] != len(source_influences):
        raise RuntimeError(
            "Source Add Influence weight shape is invalid."
        )

    skin_influences = tuple(adapter.influences())
    skin_columns = {
        joint: index
        for index, joint in enumerate(skin_influences)
    }
    missing = [
        joint
        for joint in source_influences
        if joint not in skin_columns
    ]
    if missing:
        raise RuntimeError(
            "skinCluster is missing influences after Add Influence:\n{}".format(
                "\n".join(missing)
            )
        )

    ordered = np.zeros(
        (source.shape[0], len(skin_influences)),
        dtype=np.float64,
    )
    for source_column, joint in enumerate(source_influences):
        ordered[:, skin_columns[joint]] = source[:, source_column]
    return ordered


def _validate_local_write(
    adapter,
    claimed_ids,
    expected_weights,
    maximum_influences,
):
    stored = adapter.get_weights(claimed_ids)
    actual = np.asarray(stored.weights, dtype=np.float64)
    expected = np.asarray(expected_weights, dtype=np.float64)

    if actual.shape != expected.shape:
        raise RuntimeError(
            "Stored Add Influence weight shape differs from the local solve."
        )

    difference = np.abs(actual - expected)
    if np.any(difference > STORED_WEIGHT_TOLERANCE):
        bad_rows = np.where(
            np.any(difference > STORED_WEIGHT_TOLERANCE, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Stored Add Influence weights differ from the local solve. "
            "First vertex IDs: {}".format(
                claimed_ids[bad_rows].tolist()
            )
        )

    row_sums = np.sum(actual, axis=1, dtype=np.float64)
    if np.any(
        np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE
    ):
        raise RuntimeError(
            "Claimed Add Influence rows are not normalized."
        )

    active_counts = np.count_nonzero(
        actual > STORED_WEIGHT_TOLERANCE,
        axis=1,
    )
    if np.any(active_counts > int(maximum_influences)):
        raise RuntimeError(
            "Claimed Add Influence rows exceed Max Influences."
        )


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


def _normalize_new_targets(joints, existing):
    result = []
    seen = set(existing)
    for joint in joints:
        matches = cmds.ls(joint, long=True, type="joint") or []
        if not matches:
            raise RuntimeError(
                "Target joint does not exist:\n{}".format(joint)
            )
        path = matches[0]
        if path not in seen:
            seen.add(path)
            result.append(path)

    if not result:
        raise RuntimeError(
            "Select at least one joint not already bound to the mesh."
        )
    return tuple(result)


def _resolve_mesh(mesh):
    matches = cmds.ls(mesh, long=True) or []
    if not matches:
        raise RuntimeError("Mesh does not exist:\n{}".format(mesh))

    node = matches[0]
    if cmds.nodeType(node) == "mesh":
        parent = cmds.listRelatives(
            node,
            parent=True,
            fullPath=True,
        ) or []
        if not parent:
            raise RuntimeError("Mesh shape has no transform parent.")
        return node, parent[0]

    shapes = cmds.listRelatives(
        node,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
        type="mesh",
    ) or []
    if cmds.nodeType(node) != "transform" or len(shapes) != 1:
        raise RuntimeError(
            "Supply one polygon mesh transform or mesh shape."
        )
    return shapes[0], node


def _undo_failed_operation() -> None:
    try:
        cmds.undo()
    except Exception:
        cmds.warning(
            "Add Influence failed after changing the skinCluster. "
            "Automatic rollback also failed; use Maya Undo."
        )


def _restore_selection(selection_before) -> None:
    cmds.select(clear=True)
    if selection_before:
        try:
            cmds.select(selection_before, replace=True)
        except Exception:
            pass
