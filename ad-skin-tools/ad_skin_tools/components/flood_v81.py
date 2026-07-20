"""Stable undoable Component Flood backend for AD Skin Tool v8.1."""

from typing import Tuple

import maya.cmds as cmds

from ad_skin_tools.components import flood as legacy_flood
from ad_skin_tools.components.selection import collect_weighted_mesh_vertices
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.core.undoable_skin_weights import apply_undoable_weights


np = ensure_numpy()
ComponentFloodResult = legacy_flood.ComponentFloodResult


def flood_selected_components_to_joint(
    mesh_shape: str,
    mesh_transform: str,
    target_joint: str,
    target_locked_override: bool = False,
) -> ComponentFloodResult:
    """Apply weighted Flood through Maya's undo queue."""

    selection_before = cmds.ls(selection=True, long=True) or []
    scope = collect_weighted_mesh_vertices(mesh_shape, mesh_transform)
    resolved_joint = legacy_flood._resolve_joint(target_joint)

    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)
    influences_before = tuple(adapter.influences())
    target_is_bound = resolved_joint in influences_before
    bound_locked = locked_influences(
        adapter.skin_cluster,
        influences_before,
    )
    target_locked = bool(target_locked_override) or (
        target_is_bound and resolved_joint in bound_locked
    )

    if target_locked:
        return ComponentFloodResult(
            skin_cluster=adapter.skin_cluster,
            mesh_shape=scope.mesh_shape,
            mesh_transform=scope.mesh_transform,
            target_joint=resolved_joint,
            selected_vertex_ids=scope.vertex_ids,
            flooded_vertex_ids=tuple(),
            protected_vertex_ids=scope.vertex_ids,
            flooded_target_weights=tuple(),
            locked_influences=bound_locked,
            influence_count=len(influences_before),
            influence_added=False,
            target_locked=True,
            source_component_count=scope.source_component_count,
            ignored_component_count=scope.ignored_component_count,
            soft_selection_enabled=scope.soft_selection_enabled,
            soft_selection_used=scope.soft_selection_used,
        )

    influence_added = False
    mutation_recorded = False
    selected_vertex_ids = np.asarray(scope.vertex_ids, dtype=np.int32)
    requested_target_weights = np.asarray(
        scope.falloff_weights,
        dtype=np.float64,
    )
    flooded_vertex_ids = np.empty(0, dtype=np.int32)
    protected_vertex_ids = np.empty(0, dtype=np.int32)
    flooded_target_weights = np.empty(0, dtype=np.float64)
    active_locked_influences = tuple()

    try:
        try:
            with undo_chunk("AD Skin Tool Weighted Component Flood"):
                if not target_is_bound:
                    legacy_flood._add_influence(
                        skin_cluster=adapter.skin_cluster,
                        joint=resolved_joint,
                    )
                    influence_added = True
                    mutation_recorded = True
                    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)

                influences = tuple(adapter.influences())
                try:
                    target_column = influences.index(resolved_joint)
                except ValueError:
                    raise RuntimeError(
                        "Target joint was not found in the skinCluster after "
                        "Add Influence:\n{}".format(resolved_joint)
                    )

                active_locked_influences = tuple(
                    joint
                    for joint in locked_influences(
                        adapter.skin_cluster,
                        influences,
                    )
                    if joint != resolved_joint
                )

                before = adapter.get_weights(selected_vertex_ids)
                before_weights = np.asarray(
                    before.weights,
                    dtype=np.float64,
                ).copy()
                protected_mask = legacy_flood._protected_vertex_mask(
                    weights=before_weights,
                    influences=tuple(before.influences),
                    locked=active_locked_influences,
                )
                protected_vertex_ids = selected_vertex_ids[protected_mask]
                writable_mask = ~protected_mask
                flooded_vertex_ids = selected_vertex_ids[writable_mask]

                if flooded_vertex_ids.size:
                    writable_before = before_weights[writable_mask].copy()
                    write_weights, flooded_target_weights = (
                        legacy_flood._build_weight_rows(
                            baseline=writable_before,
                            target_column=int(target_column),
                            target_values=requested_target_weights[writable_mask],
                        )
                    )
                    apply_undoable_weights(
                        skin_cluster=adapter.skin_cluster,
                        mesh_shape=scope.mesh_shape,
                        vertex_ids=flooded_vertex_ids,
                        before_weights=writable_before,
                        after_weights=write_weights,
                    )
                    mutation_recorded = True

                    _validate_written_rows(
                        adapter=adapter,
                        vertex_ids=flooded_vertex_ids,
                        expected_weights=write_weights,
                    )

                _validate_protected_rows(
                    adapter=adapter,
                    vertex_ids=protected_vertex_ids,
                    weights_before=before_weights[protected_mask],
                )
        except Exception:
            if mutation_recorded:
                _undo_failed_flood()
            raise
    finally:
        legacy_flood._restore_selection(selection_before)

    return ComponentFloodResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=scope.mesh_shape,
        mesh_transform=scope.mesh_transform,
        target_joint=resolved_joint,
        selected_vertex_ids=scope.vertex_ids,
        flooded_vertex_ids=tuple(
            int(value) for value in flooded_vertex_ids.tolist()
        ),
        protected_vertex_ids=tuple(
            int(value) for value in protected_vertex_ids.tolist()
        ),
        flooded_target_weights=tuple(
            float(value) for value in flooded_target_weights.tolist()
        ),
        locked_influences=active_locked_influences,
        influence_count=len(adapter.influences()),
        influence_added=influence_added,
        target_locked=False,
        source_component_count=scope.source_component_count,
        ignored_component_count=scope.ignored_component_count,
        soft_selection_enabled=scope.soft_selection_enabled,
        soft_selection_used=scope.soft_selection_used,
    )


def print_component_flood_report(result: ComponentFloodResult) -> None:
    legacy_flood.print_component_flood_report(result)


def _validate_written_rows(adapter, vertex_ids, expected_weights) -> None:
    if not vertex_ids.size:
        return

    stored = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )
    expected = np.asarray(expected_weights, dtype=np.float64)
    tolerance = 1e-8

    if not np.all(np.isfinite(stored)):
        raise RuntimeError("Component Flood stored non-finite skin weights.")
    if np.any(stored < -tolerance):
        raise RuntimeError("Component Flood stored negative skin weights.")
    if not np.allclose(
        stored,
        expected,
        rtol=0.0,
        atol=tolerance,
    ):
        changed = np.where(
            np.any(np.abs(stored - expected) > tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Component Flood did not store the calculated weights. "
            "First vertex IDs: {}".format(vertex_ids[changed].tolist())
        )

    row_sums = np.sum(stored, axis=1, dtype=np.float64)
    bad_sums = np.where(np.abs(row_sums - 1.0) > tolerance)[0]
    if bad_sums.size:
        raise RuntimeError(
            "Component Flood produced invalid normalized rows. "
            "First vertex IDs: {}".format(
                vertex_ids[bad_sums[:20]].tolist()
            )
        )


def _validate_protected_rows(adapter, vertex_ids, weights_before) -> None:
    if not vertex_ids.size:
        return

    after = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )
    before = np.asarray(weights_before, dtype=np.float64)
    if not np.allclose(after, before, rtol=0.0, atol=1e-12):
        changed = np.where(
            np.any(np.abs(after - before) > 1e-12, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Component Flood changed weights protected by a locked influence. "
            "First vertex IDs: {}".format(vertex_ids[changed].tolist())
        )


def _undo_failed_flood() -> None:
    try:
        cmds.undo()
    except Exception:
        cmds.warning(
            "Component Flood failed after modifying the skinCluster, and the "
            "automatic rollback also failed. Use Maya Undo before continuing."
        )
