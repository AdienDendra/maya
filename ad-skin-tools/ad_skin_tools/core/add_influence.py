"""Add pending influences by final Region ownership without rebuilding other rows.

Region evaluates existing and pending joints together. Only unlocked vertices whose
final owner is a pending joint are written. Iteration zero writes hard 1.0 claims;
positive iterations smooth those claims, while every unclaimed row stays unchanged.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.solver import BindSmoothingResult, solve_bind_smoothing
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import ambiguous_loop_distance_tiebreak
from ad_skin_tools.region import closed_loop_opposite_guard
from ad_skin_tools.region.connectivity import build_vertex_adjacency
from ad_skin_tools.region.solver import solve_region_ownership


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
    target_joints: Tuple[str, ...]
    locked_influences: Tuple[str, ...]
    unchanged_vertex_ids: Tuple[int, ...]
    claimed_vertex_ids_by_joint: Dict[str, Tuple[int, ...]]
    diagnostics: Tuple[TargetProposal, ...]
    resolution_pass_count: int
    smoothing_iterations: int
    effective_maximum_influences: int
    smoothing_result: Optional[BindSmoothingResult]

    @property
    def claimed_vertex_count(self) -> int:
        return sum(len(ids) for ids in self.claimed_vertex_ids_by_joint.values())


def add_influences_by_region(
    mesh: str,
    target_joints: Sequence[str],
    smoothing_iterations: int = 0,
) -> AddInfluenceResult:
    """Add pending joints and update only their unlocked final Region rows."""

    smooth_options = BindSmoothingOptions(
        iterations=int(smoothing_iterations)
    ).validated()
    selection_before = cmds.ls(selection=True, long=True) or []
    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    existing = tuple(adapter.influences())
    targets = _normalize_new_targets(target_joints, existing)
    all_influences = existing + targets

    vertex_count = int(cmds.polyEvaluate(mesh_shape, vertex=True))
    vertex_ids = np.arange(vertex_count, dtype=np.int32)
    before = adapter.get_weights(vertex_ids)
    if tuple(before.influences) != existing:
        raise RuntimeError("skinCluster influence order changed during setup.")
    baseline = np.asarray(before.weights, dtype=np.float64).copy()
    _validate_baseline(baseline)

    locked = locked_influences(adapter.skin_cluster, existing)
    protected_mask = _protected_mask(baseline, existing, locked)

    region_result = solve_region_ownership(
        mesh=mesh_transform,
        joints=all_influences,
    )
    if tuple(region_result.influences) != all_influences:
        raise RuntimeError("Region solver returned an unexpected influence order.")
    guarded = closed_loop_opposite_guard.solve_closed_loop_opposite_guard(
        region_result
    )
    blocking = ambiguous_loop_distance_tiebreak.solve_ambiguous_loop_distance_tiebreak(
        region_result,
        guarded,
    )
    final_owners = np.asarray(blocking.corrected_owner_indices, dtype=np.int32)

    claimed, diagnostics, target_by_vertex = _build_target_claims(
        final_owners=final_owners,
        targets=targets,
        existing_count=len(existing),
        protected_mask=protected_mask,
        vertex_count=vertex_count,
    )
    claimed_ids = np.where(target_by_vertex >= 0)[0].astype(np.int32)
    claimed_mask = target_by_vertex >= 0
    unchanged_ids = np.where(~claimed_mask)[0].astype(np.int32)

    effective_maximum = smooth_options.effective_maximum_influences(
        len(all_influences)
    )
    claimed_weights, smoothing_result = _calculate_claimed_weights(
        baseline=baseline,
        claimed_ids=claimed_ids,
        target_by_vertex=target_by_vertex,
        all_influence_count=len(all_influences),
        region_result=region_result,
        options=smooth_options,
    )

    mutation_recorded = False
    try:
        try:
            with undo_chunk("AD Skin Tool Add Influence"):
                for joint in targets:
                    cmds.skinCluster(
                        adapter.skin_cluster,
                        edit=True,
                        addInfluence=joint,
                        weight=0.0,
                    )
                    mutation_recorded = True

                adapter = SkinClusterAdapter.from_mesh(mesh_shape)
                expected_claimed = _weights_in_skin_order(
                    adapter,
                    all_influences,
                    claimed_weights,
                )
                if claimed_ids.size:
                    adapter.set_weights(
                        claimed_ids,
                        expected_claimed,
                        normalize=False,
                    )
                _validate_write(
                    adapter=adapter,
                    vertex_ids=vertex_ids,
                    existing=existing,
                    baseline=baseline,
                    targets=targets,
                    claimed_ids=claimed_ids,
                    expected_claimed=expected_claimed,
                    unchanged_ids=unchanged_ids,
                    target_by_vertex=target_by_vertex,
                    source_influences=all_influences,
                    maximum_influences=effective_maximum,
                )
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
        target_joints=targets,
        locked_influences=locked,
        unchanged_vertex_ids=tuple(int(v) for v in unchanged_ids.tolist()),
        claimed_vertex_ids_by_joint=claimed,
        diagnostics=diagnostics,
        resolution_pass_count=region_result.resolution_pass_count,
        smoothing_iterations=int(smooth_options.iterations),
        effective_maximum_influences=int(effective_maximum),
        smoothing_result=smoothing_result,
    )


def print_report(result: AddInfluenceResult) -> None:
    print("\n[AD Skin Tool - Add Influence]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("New influences:", len(result.target_joints))
    print("Locked existing influences:", len(result.locked_influences))
    print("Region resolution passes:", result.resolution_pass_count)
    print("Smoothing iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Claimed vertices:", result.claimed_vertex_count)
    print("Unchanged vertices:", len(result.unchanged_vertex_ids))
    print("\nPer target:")
    for item in result.diagnostics:
        print(
            "  {}: proposed={} | accepted={} | protected={}".format(
                item.joint.split("|")[-1],
                len(item.proposed_vertex_ids),
                len(item.accepted_vertex_ids),
                len(item.protected_vertex_ids),
            )
        )


def _build_target_claims(
    final_owners,
    targets,
    existing_count,
    protected_mask,
    vertex_count,
):
    claimed = {}
    diagnostics = []
    target_by_vertex = np.full(vertex_count, -1, dtype=np.int32)

    for target_index, joint in enumerate(targets, start=existing_count):
        proposed = np.where(final_owners == target_index)[0].astype(np.int32)
        accepted = proposed[~protected_mask[proposed]]
        protected = proposed[protected_mask[proposed]]
        if accepted.size:
            target_by_vertex[accepted] = int(target_index)

        proposed_tuple = tuple(int(v) for v in proposed.tolist())
        accepted_tuple = tuple(int(v) for v in accepted.tolist())
        protected_tuple = tuple(int(v) for v in protected.tolist())
        claimed[joint] = accepted_tuple
        diagnostics.append(
            TargetProposal(
                joint=joint,
                proposed_vertex_ids=proposed_tuple,
                accepted_vertex_ids=accepted_tuple,
                protected_vertex_ids=protected_tuple,
            )
        )

    return claimed, tuple(diagnostics), target_by_vertex


def _calculate_claimed_weights(
    baseline,
    claimed_ids,
    target_by_vertex,
    all_influence_count,
    region_result,
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

    hard_owners = np.argmax(baseline, axis=1).astype(np.int32)
    hard_owners[claimed_ids] = target_by_vertex[claimed_ids]
    result = solve_bind_smoothing(
        owner_indices=hard_owners,
        adjacency=build_vertex_adjacency(region_result.mesh_shape),
        vertex_positions=region_result.vertex_positions,
        influence_positions=region_result.influence_positions,
        options=options,
    )
    return np.asarray(result.weights[claimed_ids], dtype=np.float64).copy(), result


def _validate_baseline(weights) -> None:
    if weights.ndim != 2 or not np.all(np.isfinite(weights)):
        raise RuntimeError("Existing skin weights are invalid or non-finite.")
    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1]) * 16.0
    if np.any(weights < -tolerance):
        bad = np.where(np.any(weights < -tolerance, axis=1))[0][:20]
        raise RuntimeError(
            "Existing skin weights contain negative values. First IDs: {}".format(
                bad.tolist()
            )
        )
    empty = np.where(np.sum(weights, axis=1) <= tolerance)[0]
    if empty.size:
        raise RuntimeError(
            "Existing skin weights contain empty rows. First IDs: {}".format(
                empty[:20].tolist()
            )
        )


def _protected_mask(weights, influences, locked):
    if not locked:
        return np.zeros(weights.shape[0], dtype=bool)
    columns = {joint: index for index, joint in enumerate(influences)}
    locked_columns = [columns[joint] for joint in locked]
    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1]) * 16.0
    return np.any(np.abs(weights[:, locked_columns]) > tolerance, axis=1)


def _weights_in_skin_order(adapter, source_influences, source_weights):
    source = np.asarray(source_weights, dtype=np.float64)
    if source.ndim != 2 or source.shape[1] != len(source_influences):
        raise RuntimeError("Source Add Influence weight shape is invalid.")

    skin_influences = tuple(adapter.influences())
    skin_columns = {joint: index for index, joint in enumerate(skin_influences)}
    missing = [joint for joint in source_influences if joint not in skin_columns]
    if missing:
        raise RuntimeError(
            "skinCluster is missing influences after Add Influence:\n{}".format(
                "\n".join(missing)
            )
        )

    ordered = np.zeros((source.shape[0], len(skin_influences)), dtype=np.float64)
    for source_column, joint in enumerate(source_influences):
        ordered[:, skin_columns[joint]] = source[:, source_column]
    return ordered


def _validate_write(
    adapter,
    vertex_ids,
    existing,
    baseline,
    targets,
    claimed_ids,
    expected_claimed,
    unchanged_ids,
    target_by_vertex,
    source_influences,
    maximum_influences,
):
    stored = adapter.get_weights(vertex_ids)
    influences = tuple(stored.influences)
    weights = np.asarray(stored.weights, dtype=np.float64)
    columns = {joint: index for index, joint in enumerate(influences)}

    if unchanged_ids.size:
        existing_columns = [columns[joint] for joint in existing]
        target_columns = [columns[joint] for joint in targets]
        if not np.array_equal(
            weights[unchanged_ids][:, existing_columns],
            baseline[unchanged_ids],
        ):
            raise RuntimeError("An unclaimed existing weight row changed.")
        if np.any(weights[unchanged_ids][:, target_columns] != 0.0):
            raise RuntimeError("A new influence affected an unclaimed vertex.")

    if not claimed_ids.size:
        return

    actual = weights[claimed_ids]
    difference = np.abs(actual - expected_claimed)
    if np.any(difference > STORED_WEIGHT_TOLERANCE):
        bad = np.where(
            np.any(difference > STORED_WEIGHT_TOLERANCE, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Stored Add Influence weights differ from the local solve. First IDs: {}"
            .format(claimed_ids[bad].tolist())
        )

    row_sums = np.sum(actual, axis=1, dtype=np.float64)
    active_counts = np.count_nonzero(actual > STORED_WEIGHT_TOLERANCE, axis=1)
    if np.any(np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE):
        raise RuntimeError("Claimed Add Influence rows are not normalized.")
    if np.any(active_counts > int(maximum_influences)):
        raise RuntimeError("Claimed Add Influence rows exceed Max Influences.")

    for local_row, vertex_id in enumerate(claimed_ids.tolist()):
        target_index = int(target_by_vertex[int(vertex_id)])
        target_joint = source_influences[target_index]
        target_value = actual[local_row, columns[target_joint]]
        if target_value + STORED_WEIGHT_TOLERANCE < float(np.max(actual[local_row])):
            raise RuntimeError(
                "Pending influence is not maximum on claimed vertex {}.".format(
                    int(vertex_id)
                )
            )


def _normalize_new_targets(joints, existing):
    result = []
    seen = set(existing)
    for joint in joints:
        matches = cmds.ls(joint, long=True, type="joint") or []
        if not matches:
            raise RuntimeError("Target joint does not exist:\n{}".format(joint))
        path = matches[0]
        if path not in seen:
            seen.add(path)
            result.append(path)
    if not result:
        raise RuntimeError("Select at least one joint not already bound to the mesh.")
    return tuple(result)


def _resolve_mesh(mesh):
    matches = cmds.ls(mesh, long=True) or []
    if not matches:
        raise RuntimeError("Mesh does not exist:\n{}".format(mesh))
    node = matches[0]
    if cmds.nodeType(node) == "mesh":
        parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
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
        raise RuntimeError("Supply one polygon mesh transform or mesh shape.")
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
