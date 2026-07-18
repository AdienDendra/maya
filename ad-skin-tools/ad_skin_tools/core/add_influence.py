"""Add new skin influences using the existing Region ownership solver.

The current hard one-hot skin weights remain authoritative. The full Region
solver evaluates existing and newly selected influences together, but only
ownership proposed for new influences is written. Existing-to-existing changes
are ignored, and vertices protected by locked influences remain unchanged.
"""

from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region.solver import solve_region_ownership


np = ensure_numpy()


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

    @property
    def claimed_vertex_count(self) -> int:
        return sum(
            len(vertex_ids)
            for vertex_ids in self.claimed_vertex_ids_by_joint.values()
        )


def add_influences_by_region(
    mesh: str,
    target_joints: Sequence[str],
) -> AddInfluenceResult:
    """Add pending joints and write only their accepted Region ownership."""

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

    baseline_weights = np.asarray(before.weights, dtype=np.float64).copy()
    _validate_hard_weights(baseline_weights)
    locked = locked_influences(adapter.skin_cluster, existing)
    protected_mask = _protected_vertex_mask(
        baseline_weights,
        existing,
        locked,
    )

    region_result = solve_region_ownership(
        mesh=mesh_transform,
        joints=all_influences,
    )
    if tuple(region_result.influences) != all_influences:
        raise RuntimeError(
            "Region solver returned an unexpected influence order."
        )

    claimed = {}
    diagnostics = []
    for joint in targets:
        proposed = tuple(region_result.owner_vertex_ids[joint])
        proposed_array = np.asarray(proposed, dtype=np.int32)

        if proposed_array.size:
            accepted_array = proposed_array[~protected_mask[proposed_array]]
            protected_array = proposed_array[protected_mask[proposed_array]]
        else:
            accepted_array = np.asarray([], dtype=np.int32)
            protected_array = np.asarray([], dtype=np.int32)

        accepted = tuple(int(value) for value in accepted_array.tolist())
        protected = tuple(int(value) for value in protected_array.tolist())
        claimed[joint] = accepted
        diagnostics.append(
            TargetProposal(
                joint=joint,
                proposed_vertex_ids=proposed,
                accepted_vertex_ids=accepted,
                protected_vertex_ids=protected,
            )
        )

    claimed_ids = tuple(
        sorted(
            vertex_id
            for vertex_ids_for_joint in claimed.values()
            for vertex_id in vertex_ids_for_joint
        )
    )
    claimed_mask = np.zeros(vertex_count, dtype=bool)
    if claimed_ids:
        claimed_mask[np.asarray(claimed_ids, dtype=np.int32)] = True
    unchanged_ids = tuple(
        np.where(~claimed_mask)[0].astype(np.int32).tolist()
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
                _write_claims(adapter, claimed)
                _validate_stored_weights(
                    adapter,
                    vertex_ids,
                    existing,
                    baseline_weights,
                    targets,
                    claimed,
                    unchanged_ids,
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
        unchanged_vertex_ids=unchanged_ids,
        claimed_vertex_ids_by_joint=claimed,
        diagnostics=tuple(diagnostics),
        resolution_pass_count=region_result.resolution_pass_count,
    )


def print_report(result: AddInfluenceResult) -> None:
    print("\n[AD Skin Tool - Add Influence]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("New influences:", len(result.target_joints))
    print("Locked existing influences:", len(result.locked_influences))
    print("Region resolution passes:", result.resolution_pass_count)
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


def _validate_hard_weights(weights) -> None:
    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1]) * 16.0
    rows = np.arange(weights.shape[0], dtype=np.int32)
    owners = np.argmax(weights, axis=1).astype(np.int32)
    owner_values = weights[rows, owners]
    non_owner = weights.copy()
    non_owner[rows, owners] = 0.0
    invalid = (
        (np.abs(owner_values - 1.0) > tolerance)
        | (np.abs(np.sum(weights, axis=1) - 1.0) > tolerance)
        | np.any(np.abs(non_owner) > tolerance, axis=1)
    )
    if np.any(invalid):
        raise RuntimeError(
            "Add Influence requires hard one-hot existing weights.\n\n"
            "First invalid vertex IDs: {}".format(
                np.where(invalid)[0][:20].tolist()
            )
        )


def _protected_vertex_mask(weights, influences, locked):
    if not locked:
        return np.zeros(weights.shape[0], dtype=bool)
    column_by_joint = {joint: index for index, joint in enumerate(influences)}
    columns = [column_by_joint[joint] for joint in locked]
    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1]) * 16.0
    return np.any(np.abs(weights[:, columns]) > tolerance, axis=1)


def _write_claims(adapter, claimed) -> None:
    influences = tuple(adapter.influences())
    column_by_joint = {joint: index for index, joint in enumerate(influences)}
    assignments = sorted(
        (
            (int(vertex_id), int(column_by_joint[joint]))
            for joint, vertex_ids in claimed.items()
            for vertex_id in vertex_ids
        ),
        key=lambda item: item[0],
    )
    if not assignments:
        return

    ids = np.asarray([item[0] for item in assignments], dtype=np.int32)
    columns = np.asarray([item[1] for item in assignments], dtype=np.int32)
    weights = np.zeros((len(ids), len(influences)), dtype=np.float64)
    weights[np.arange(len(ids), dtype=np.int32), columns] = 1.0
    adapter.set_weights(ids, weights, normalize=False)


def _validate_stored_weights(
    adapter,
    vertex_ids,
    baseline_influences,
    baseline_weights,
    targets,
    claimed,
    unchanged_ids,
) -> None:
    stored = adapter.get_weights(vertex_ids)
    influences = tuple(stored.influences)
    weights = np.asarray(stored.weights, dtype=np.float64)
    columns = {joint: index for index, joint in enumerate(influences)}

    unchanged = np.asarray(unchanged_ids, dtype=np.int32)
    if unchanged.size:
        old_columns = [columns[joint] for joint in baseline_influences]
        target_columns = [columns[joint] for joint in targets]
        if not np.array_equal(
            weights[unchanged][:, old_columns],
            baseline_weights[unchanged],
        ):
            raise RuntimeError("An unclaimed existing weight row changed.")
        if np.any(weights[unchanged][:, target_columns] != 0.0):
            raise RuntimeError("A new influence affected an unclaimed vertex.")

    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1])
    for joint, ids in claimed.items():
        if not ids:
            continue
        rows = np.asarray(ids, dtype=np.int32)
        expected = np.zeros((len(rows), len(influences)), dtype=np.float64)
        expected[:, columns[joint]] = 1.0
        if np.any(np.abs(weights[rows] - expected) > tolerance):
            raise RuntimeError(
                "Stored claim is not exact Replace 1.0 for:\n{}".format(joint)
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
