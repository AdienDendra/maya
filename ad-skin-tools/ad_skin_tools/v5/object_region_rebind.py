"""v5 smoke test: re-solve Region, then apply only new-joint ownership.

The full Region solver evaluates the existing and newly selected influences
together. Existing-to-existing ownership changes are ignored. Only writable
vertices whose solved owner is a new influence are written; locked rows remain
unchanged.
"""

from dataclasses import dataclass
from typing import Dict, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region.solver import solve_region_ownership
from ad_skin_tools.v5.object_region_add import (
    _hard_owner_indices,
    _new_targets,
    _protected_mask,
    _resolve_mesh,
    _restore_selection,
    _undo_failed_operation,
    _validate,
    _write_claims,
)


np = ensure_numpy()


@dataclass(frozen=True)
class TargetProposal:
    joint: str
    proposed_vertex_ids: Tuple[int, ...]
    accepted_vertex_ids: Tuple[int, ...]
    protected_vertex_ids: Tuple[int, ...]


@dataclass(frozen=True)
class ObjectRegionRebindResult:
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


def add_object_region_influences_from_full_region(
    mesh: str,
    target_joints: Sequence[str],
) -> ObjectRegionRebindResult:
    """Run the full Region solver and write only new-influence proposals."""

    selection_before = cmds.ls(selection=True, long=True) or []
    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    existing = tuple(adapter.influences())
    targets = _new_targets(target_joints, existing)
    all_influences = existing + targets

    vertex_count = int(cmds.polyEvaluate(mesh_shape, vertex=True))
    vertex_ids = np.arange(vertex_count, dtype=np.int32)
    before = adapter.get_weights(vertex_ids)
    if tuple(before.influences) != existing:
        raise RuntimeError("skinCluster influence order changed during setup.")

    baseline_weights = np.asarray(before.weights, dtype=np.float64).copy()
    _hard_owner_indices(baseline_weights)
    locked = locked_influences(adapter.skin_cluster, existing)
    protected_mask = _protected_mask(baseline_weights, existing, locked)

    region_result = solve_region_ownership(
        mesh=mesh_transform,
        joints=all_influences,
    )
    if tuple(region_result.influences) != all_influences:
        raise RuntimeError(
            "Full Region solver returned an unexpected influence order."
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
            with undo_chunk("AD Skin Tool v5 Full Region Add"):
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

    return ObjectRegionRebindResult(
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


def print_report(result: ObjectRegionRebindResult) -> None:
    print("\n[AD Skin Tool v5.0 - Full Region Proposal Smoke Test]")
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
