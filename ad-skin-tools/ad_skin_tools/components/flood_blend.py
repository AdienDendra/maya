"""Component Flood candidate using Maya falloff as blend strength."""

from typing import Tuple

import maya.cmds as cmds

from ad_skin_tools.components import flood as baseline_flood
from ad_skin_tools.components.selection import collect_weighted_mesh_vertices
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.core.undoable_skin_weights import apply_undoable_weights


np = ensure_numpy()
ComponentFloodResult = baseline_flood.ComponentFloodResult


def flood_selected_components_to_joint(
    mesh_shape: str,
    mesh_transform: str,
    target_joint: str,
    target_locked_override: bool = False,
) -> ComponentFloodResult:
    """Blend each writable row toward the target using Maya falloff."""

    selection_before = cmds.ls(selection=True, long=True) or []
    scope = collect_weighted_mesh_vertices(mesh_shape, mesh_transform)
    resolved_joint = baseline_flood._resolve_joint(target_joint)

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
    falloff_strengths = np.asarray(
        scope.falloff_weights,
        dtype=np.float64,
    )
    flooded_vertex_ids = np.empty(0, dtype=np.int32)
    protected_vertex_ids = np.empty(0, dtype=np.int32)
    flooded_target_weights = np.empty(0, dtype=np.float64)
    active_locked_influences: Tuple[str, ...] = tuple()

    try:
        try:
            with undo_chunk("AD Skin Tool Component Flood Blend"):
                if not target_is_bound:
                    baseline_flood._add_influence(
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
                protected_mask = baseline_flood._protected_vertex_mask(
                    weights=before_weights,
                    influences=tuple(before.influences),
                    locked=active_locked_influences,
                )
                protected_vertex_ids = selected_vertex_ids[protected_mask]
                writable_mask = ~protected_mask
                flooded_vertex_ids = selected_vertex_ids[writable_mask]

                if flooded_vertex_ids.size:
                    writable_before = before_weights[writable_mask].copy()
                    write_weights, flooded_target_weights = _build_weight_rows(
                        baseline=writable_before,
                        target_column=int(target_column),
                        falloff_strengths=falloff_strengths[writable_mask],
                    )
                    apply_undoable_weights(
                        skin_cluster=adapter.skin_cluster,
                        mesh_shape=scope.mesh_shape,
                        vertex_ids=flooded_vertex_ids,
                        before_weights=writable_before,
                        after_weights=write_weights,
                    )
                    mutation_recorded = True
                    baseline_flood._validate_written_rows(
                        adapter=adapter,
                        vertex_ids=flooded_vertex_ids,
                        expected_weights=write_weights,
                    )

                baseline_flood._validate_protected_rows(
                    adapter=adapter,
                    vertex_ids=protected_vertex_ids,
                    weights_before=before_weights[protected_mask],
                )
        except Exception:
            if mutation_recorded:
                baseline_flood._undo_failed_flood()
            raise
    finally:
        baseline_flood._restore_selection(selection_before)

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
    print("\n[AD Skin Tool - Component Flood Blend]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Target influence:", result.target_joint)
    print("Falloff interpretation: blend strength toward target")
    print("Soft Selection enabled:", result.soft_selection_enabled)
    print("Soft Selection weights used:", result.soft_selection_used)
    print("Affected vertices:", result.vertex_count)
    print("Flooded vertices:", result.flooded_vertex_count)
    print("Protected vertices:", result.protected_vertex_count)
    if result.flooded_target_weights:
        print(
            "Final target weight range: {:.8f} - {:.8f}".format(
                result.minimum_target_weight,
                result.maximum_target_weight,
            )
        )
    print("Locked influences:", len(result.locked_influences))
    print("Target locked:", result.target_locked)
    print("Source components:", result.source_component_count)
    print("Ignored components:", result.ignored_component_count)
    print("Influence added:", result.influence_added)
    print("SkinCluster influences:", result.influence_count)


def _build_weight_rows(
    baseline,
    target_column: int,
    falloff_strengths,
):
    """Blend normalized skin rows toward one-hot target ownership.

    For each row and falloff alpha:

        result = baseline * (1 - alpha)
        result[target] += alpha

    Therefore alpha zero preserves the row, alpha one produces exact target
    ownership, donor weights never increase, and an existing target weight never
    decreases.
    """

    baseline = np.asarray(baseline, dtype=np.float64)
    if baseline.ndim != 2:
        raise ValueError("baseline must be a two-dimensional weight matrix.")

    target_column = int(target_column)
    if target_column < 0 or target_column >= baseline.shape[1]:
        raise ValueError("target_column is outside the weight matrix.")

    alpha = np.clip(
        np.asarray(falloff_strengths, dtype=np.float64),
        0.0,
        1.0,
    ).reshape(-1)
    if alpha.shape[0] != baseline.shape[0]:
        raise ValueError(
            "falloff_strengths must contain one value per weight row."
        )
    if not np.all(np.isfinite(baseline)):
        raise RuntimeError("Component Flood received non-finite baseline weights.")
    if not np.all(np.isfinite(alpha)):
        raise RuntimeError("Component Flood received non-finite falloff values.")

    result = baseline * (1.0 - alpha[:, None])
    result[:, target_column] += alpha

    applied_target_values = result[:, target_column].copy()
    return result, applied_target_values
