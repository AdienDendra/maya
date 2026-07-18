"""v5 smoke test: add new influences by object-level Region claim.

The mesh must already have hard one-hot skin weights. New joints compete only
against each vertex's current owner. Locked ownership and rejected regions keep
their original weights. This module is intentionally separate from v4 Flood.
"""

from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region.connectivity import (
    build_vertex_adjacency,
    partition_influence_ownership,
)
from ad_skin_tools.region.distance_ranking import solve_exact_distance_ranking
from ad_skin_tools.region.facing import (
    build_facing_mesh_context,
    classify_region_facing,
)
from ad_skin_tools.region.maya_scene import collect_distance_input


np = ensure_numpy()


@dataclass(frozen=True)
class TargetClaim:
    joint: str
    candidate_vertex_ids: Tuple[int, ...]
    accepted_vertex_ids: Tuple[int, ...]
    detached_vertex_ids: Tuple[int, ...]
    ambiguous_vertex_ids: Tuple[int, ...]


@dataclass(frozen=True)
class ObjectRegionAddResult:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    target_joints: Tuple[str, ...]
    locked_influences: Tuple[str, ...]
    protected_vertex_ids: Tuple[int, ...]
    unchanged_vertex_ids: Tuple[int, ...]
    claimed_vertex_ids_by_joint: Dict[str, Tuple[int, ...]]
    diagnostics: Tuple[TargetClaim, ...]

    @property
    def claimed_vertex_count(self) -> int:
        return sum(len(ids) for ids in self.claimed_vertex_ids_by_joint.values())


def add_object_region_influences(
    mesh: str,
    target_joints: Sequence[str],
) -> ObjectRegionAddResult:
    """Add new influences at weight 0, then write accepted claims at weight 1."""

    selection_before = cmds.ls(selection=True, long=True) or []
    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    existing = tuple(adapter.influences())
    targets = _new_targets(target_joints, existing)

    scene_input = collect_distance_input(mesh_transform, existing + targets)
    distance_result = solve_exact_distance_ranking(scene_input)
    vertex_ids = np.arange(distance_result.vertex_count, dtype=np.int32)

    before = adapter.get_weights(vertex_ids)
    if tuple(before.influences) != existing:
        raise RuntimeError("skinCluster influence order changed during setup.")
    baseline_weights = np.asarray(before.weights, dtype=np.float64).copy()
    baseline_owners = _hard_owner_indices(baseline_weights)
    locked = locked_influences(adapter.skin_cluster, existing)
    protected_mask = _protected_mask(baseline_weights, existing, locked)

    tentative = _tentative_owners(
        distance_result,
        baseline_owners,
        protected_mask,
        len(existing),
        len(targets),
    )
    final_owners = baseline_owners.copy()
    adjacency = build_vertex_adjacency(mesh_shape)
    facing_context = build_facing_mesh_context(mesh_shape)
    diagnostics = []

    for offset, joint in enumerate(targets):
        target_index = len(existing) + offset
        connectivity = partition_influence_ownership(
            distance_result,
            tentative,
            target_index,
            adjacency,
        )
        facing = classify_region_facing(
            distance_result,
            connectivity,
            facing_context,
        )
        accepted = tuple(int(value) for value in facing.accepted_vertex_ids)
        if accepted:
            final_owners[np.asarray(accepted, dtype=np.int32)] = target_index
        diagnostics.append(
            TargetClaim(
                joint=joint,
                candidate_vertex_ids=tuple(connectivity.raw_vertex_ids),
                accepted_vertex_ids=accepted,
                detached_vertex_ids=tuple(facing.detached_vertex_ids),
                ambiguous_vertex_ids=tuple(facing.ambiguous_vertex_ids),
            )
        )

    claimed = {
        joint: tuple(
            np.where(final_owners == (len(existing) + offset))[0]
            .astype(np.int32)
            .tolist()
        )
        for offset, joint in enumerate(targets)
    }
    claimed_ids = tuple(sorted(vertex for ids in claimed.values() for vertex in ids))
    claimed_mask = np.zeros(distance_result.vertex_count, dtype=bool)
    if claimed_ids:
        claimed_mask[np.asarray(claimed_ids, dtype=np.int32)] = True
    unchanged_ids = tuple(np.where(~claimed_mask)[0].astype(np.int32).tolist())
    protected_ids = tuple(np.where(protected_mask)[0].astype(np.int32).tolist())

    mutation_recorded = False
    try:
        try:
            with undo_chunk("AD Skin Tool v5 Object Region Add"):
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
                _validate(
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

    return ObjectRegionAddResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        target_joints=targets,
        locked_influences=locked,
        protected_vertex_ids=protected_ids,
        unchanged_vertex_ids=unchanged_ids,
        claimed_vertex_ids_by_joint=claimed,
        diagnostics=tuple(diagnostics),
    )


def print_report(result: ObjectRegionAddResult) -> None:
    print("\n[AD Skin Tool v5.0 - Object Region Add Smoke Test]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("New influences:", len(result.target_joints))
    print("Locked existing influences:", len(result.locked_influences))
    print("Protected vertices:", len(result.protected_vertex_ids))
    print("Claimed vertices:", result.claimed_vertex_count)
    print("Unchanged vertices:", len(result.unchanged_vertex_ids))
    print("\nPer target:")
    for item in result.diagnostics:
        print(
            "  {}: candidates={} | accepted={} | detached={} | ambiguous={}".format(
                item.joint.split("|")[-1],
                len(item.candidate_vertex_ids),
                len(item.accepted_vertex_ids),
                len(item.detached_vertex_ids),
                len(item.ambiguous_vertex_ids),
            )
        )


def _tentative_owners(result, baseline, protected, existing_count, target_count):
    baseline_delta = (
        result.vertex_positions
        - result.influence_positions[np.asarray(baseline, dtype=np.int32)]
    )
    baseline_squared = np.einsum("vi,vi->v", baseline_delta, baseline_delta)

    target_positions = result.influence_positions[
        existing_count : existing_count + target_count
    ]
    delta = result.vertex_positions[:, np.newaxis, :] - target_positions[np.newaxis]
    squared = np.einsum("vji,vji->vj", delta, delta)
    minimum = np.min(squared, axis=1)
    local_owner = np.argmin(squared, axis=1).astype(np.int32)
    unique = np.count_nonzero(squared == minimum[:, np.newaxis], axis=1) == 1
    claimable = (~protected) & unique & (minimum < baseline_squared)

    owners = np.asarray(baseline, dtype=np.int32).copy()
    owners[claimable] = existing_count + local_owner[claimable]
    return owners


def _hard_owner_indices(weights):
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
            "v5.0 smoke test requires hard one-hot existing weights.\n\n"
            "First invalid vertex IDs: {}".format(
                np.where(invalid)[0][:20].tolist()
            )
        )
    return owners


def _protected_mask(weights, influences, locked):
    if not locked:
        return np.zeros(weights.shape[0], dtype=bool)
    column_by_joint = {joint: index for index, joint in enumerate(influences)}
    columns = [column_by_joint[joint] for joint in locked]
    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1]) * 16.0
    return np.any(np.abs(weights[:, columns]) > tolerance, axis=1)


def _write_claims(adapter, claimed):
    influences = tuple(adapter.influences())
    column_by_joint = {joint: index for index, joint in enumerate(influences)}
    ids = []
    columns = []
    for joint, vertex_ids in claimed.items():
        for vertex_id in vertex_ids:
            ids.append(vertex_id)
            columns.append(column_by_joint[joint])
    if not ids:
        return

    ids = np.asarray(ids, dtype=np.int32)
    columns = np.asarray(columns, dtype=np.int32)
    weights = np.zeros((len(ids), len(influences)), dtype=np.float64)
    weights[np.arange(len(ids), dtype=np.int32), columns] = 1.0
    adapter.set_weights(ids, weights, normalize=False)


def _validate(
    adapter,
    vertex_ids,
    baseline_influences,
    baseline_weights,
    targets,
    claimed,
    unchanged_ids,
):
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


def _new_targets(joints, existing):
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


def _undo_failed_operation():
    try:
        cmds.undo()
    except Exception:
        cmds.warning(
            "v5 Object Region Add failed after changing the skinCluster. "
            "Automatic rollback also failed; use Maya Undo."
        )


def _restore_selection(selection_before):
    cmds.select(clear=True)
    if selection_before:
        try:
            cmds.select(selection_before, replace=True)
        except Exception:
            pass
