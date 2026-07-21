"""Topology smoothing for existing component skin weights."""

from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.components.selection import collect_weighted_mesh_vertices
from ad_skin_tools.core import mesh
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.component_selection import collect_selected_mesh_vertices
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undoable_skin_weights import apply_undoable_weights


np = ensure_numpy()

MINIMUM_COMPONENT_BLEND = 0.0
MAXIMUM_COMPONENT_BLEND = 1.0
DEFAULT_COMPONENT_BLEND = 0.25
MINIMUM_COMPONENT_ITERATIONS = 1
MAXIMUM_COMPONENT_ITERATIONS = 10
DEFAULT_COMPONENT_ITERATIONS = 1

# Compatibility aliases retained for scripts written against v9.0.
MINIMUM_COMPONENT_PASSES = MINIMUM_COMPONENT_ITERATIONS
MAXIMUM_COMPONENT_PASSES = MAXIMUM_COMPONENT_ITERATIONS
DEFAULT_COMPONENT_PASSES = DEFAULT_COMPONENT_ITERATIONS


@dataclass(frozen=True)
class ComponentSmoothScope:
    mesh_shape: str
    mesh_transform: str
    vertex_ids: Tuple[int, ...]
    selection_falloffs: Tuple[float, ...]
    whole_object: bool
    soft_selection_enabled: bool
    soft_selection_used: bool
    source_component_count: int
    ignored_component_count: int

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class ComponentSmoothResult:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    blend: float
    iterations: int
    whole_object: bool
    selected_vertex_ids: Tuple[int, ...]
    smoothed_vertex_ids: Tuple[int, ...]
    skipped_empty_vertex_ids: Tuple[int, ...]
    skipped_locked_vertex_ids: Tuple[int, ...]
    locked_influences: Tuple[str, ...]
    soft_selection_enabled: bool
    soft_selection_used: bool

    @property
    def passes(self) -> int:
        """Compatibility alias for v9.0 callers."""
        return self.iterations

    @property
    def selected_vertex_count(self) -> int:
        return len(self.selected_vertex_ids)

    @property
    def smoothed_vertex_count(self) -> int:
        return len(self.smoothed_vertex_ids)

    @property
    def skipped_vertex_count(self) -> int:
        return (
            len(self.skipped_empty_vertex_ids)
            + len(self.skipped_locked_vertex_ids)
        )


def collect_smooth_scope(
    mesh_shape: str,
    mesh_transform: str,
) -> ComponentSmoothScope:
    """Resolve component selection first, then loaded mesh object selection."""

    component_scope = collect_selected_mesh_vertices(
        mesh_shape,
        mesh_transform,
    )
    if component_scope.vertex_ids:
        weighted = collect_weighted_mesh_vertices(
            component_scope.mesh_shape,
            component_scope.mesh_transform,
            hard_scope=component_scope,
        )
        return ComponentSmoothScope(
            mesh_shape=weighted.mesh_shape,
            mesh_transform=weighted.mesh_transform,
            vertex_ids=weighted.vertex_ids,
            selection_falloffs=weighted.falloff_weights,
            whole_object=False,
            soft_selection_enabled=weighted.soft_selection_enabled,
            soft_selection_used=weighted.soft_selection_used,
            source_component_count=weighted.source_component_count,
            ignored_component_count=weighted.ignored_component_count,
        )

    if _loaded_mesh_object_selected(
        component_scope.mesh_shape,
        component_scope.mesh_transform,
    ):
        vertex_count = mesh.get_vertex_count(component_scope.mesh_shape)
        vertex_ids = tuple(range(vertex_count))
        return ComponentSmoothScope(
            mesh_shape=component_scope.mesh_shape,
            mesh_transform=component_scope.mesh_transform,
            vertex_ids=vertex_ids,
            selection_falloffs=tuple(1.0 for _ in vertex_ids),
            whole_object=True,
            soft_selection_enabled=False,
            soft_selection_used=False,
            source_component_count=0,
            ignored_component_count=component_scope.ignored_component_count,
        )

    raise RuntimeError(
        "Select vertices, edges, or faces on the loaded mesh, "
        "or select the loaded mesh object."
    )


def smooth_skin_weights(
    scope: ComponentSmoothScope,
    blend: float,
    iterations: Optional[int] = None,
    passes: Optional[int] = None,
) -> ComponentSmoothResult:
    """Smooth current skin weights inside the resolved selection scope."""

    blend = float(blend)
    if blend < MINIMUM_COMPONENT_BLEND or blend > MAXIMUM_COMPONENT_BLEND:
        raise ValueError(
            "Component Smooth Blend must be between {:.1f} and {:.1f}.".format(
                MINIMUM_COMPONENT_BLEND,
                MAXIMUM_COMPONENT_BLEND,
            )
        )

    if iterations is None:
        iterations = passes
    elif passes is not None and int(iterations) != int(passes):
        raise ValueError("Supply either iterations or passes, not conflicting values.")
    if iterations is None:
        iterations = DEFAULT_COMPONENT_ITERATIONS

    iterations = int(iterations)
    if (
        iterations < MINIMUM_COMPONENT_ITERATIONS
        or iterations > MAXIMUM_COMPONENT_ITERATIONS
    ):
        raise ValueError(
            "Component Smooth Iterations must be between {} and {}.".format(
                MINIMUM_COMPONENT_ITERATIONS,
                MAXIMUM_COMPONENT_ITERATIONS,
            )
        )

    selection_before = cmds.ls(selection=True, long=True) or []
    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)
    influences = tuple(adapter.influences())
    active_locked = locked_influences(
        adapter.skin_cluster,
        influences,
    )

    selected_vertex_ids = np.asarray(scope.vertex_ids, dtype=np.int32)
    selection_falloffs = np.clip(
        np.asarray(scope.selection_falloffs, dtype=np.float64),
        0.0,
        1.0,
    )
    if selected_vertex_ids.size != selection_falloffs.size:
        raise RuntimeError("Component Smooth selection data is inconsistent.")

    selected_adjacency = mesh.get_vertex_neighbors(
        scope.mesh_shape,
        selected_vertex_ids,
    )
    context_vertex_ids = _build_context_vertex_ids(
        selected_vertex_ids,
        selected_adjacency,
    )
    selected_context_rows = _rows_for_vertex_ids(
        context_vertex_ids,
        selected_vertex_ids,
    )
    selected_neighbour_rows = tuple(
        _rows_for_vertex_ids(context_vertex_ids, neighbours)
        for neighbours in selected_adjacency
    )

    context_data = adapter.get_weights(context_vertex_ids)
    baseline = np.asarray(context_data.weights, dtype=np.float64).copy()
    locked_columns = tuple(
        influences.index(joint)
        for joint in active_locked
        if joint in influences
    )

    (
        final_weights,
        changed_vertex_ids,
        skipped_empty_vertex_ids,
        skipped_locked_vertex_ids,
    ) = _smooth_context_rows(
        baseline=baseline,
        selected_context_rows=selected_context_rows,
        selected_vertex_ids=selected_vertex_ids,
        selected_neighbour_rows=selected_neighbour_rows,
        selection_falloffs=selection_falloffs,
        locked_columns=locked_columns,
        blend=blend,
        iterations=iterations,
    )

    command_applied = False
    try:
        if changed_vertex_ids.size:
            changed_context_rows = _rows_for_vertex_ids(
                context_vertex_ids,
                changed_vertex_ids,
            )
            before_weights = baseline[changed_context_rows].copy()
            after_weights = final_weights[changed_context_rows].copy()
            apply_undoable_weights(
                skin_cluster=adapter.skin_cluster,
                mesh_shape=scope.mesh_shape,
                vertex_ids=changed_vertex_ids,
                before_weights=before_weights,
                after_weights=after_weights,
            )
            command_applied = True
            _validate_written_rows(
                adapter=adapter,
                vertex_ids=changed_vertex_ids,
                expected_weights=after_weights,
            )
    except Exception:
        if command_applied:
            _undo_failed_smooth()
        raise
    finally:
        _restore_selection(selection_before)

    return ComponentSmoothResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=scope.mesh_shape,
        mesh_transform=scope.mesh_transform,
        blend=blend,
        iterations=iterations,
        whole_object=scope.whole_object,
        selected_vertex_ids=tuple(
            int(value) for value in selected_vertex_ids.tolist()
        ),
        smoothed_vertex_ids=tuple(
            int(value) for value in changed_vertex_ids.tolist()
        ),
        skipped_empty_vertex_ids=tuple(
            int(value) for value in skipped_empty_vertex_ids.tolist()
        ),
        skipped_locked_vertex_ids=tuple(
            int(value) for value in skipped_locked_vertex_ids.tolist()
        ),
        locked_influences=active_locked,
        soft_selection_enabled=scope.soft_selection_enabled,
        soft_selection_used=scope.soft_selection_used,
    )


def print_component_smooth_report(result: ComponentSmoothResult) -> None:
    print("\n[AD Skin Tool Component Smooth]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Whole object:", result.whole_object)
    print("Soft Selection enabled:", result.soft_selection_enabled)
    print("Soft Selection weights used:", result.soft_selection_used)
    print("Blend:", result.blend)
    print("Iterations:", result.iterations)
    print("Selected vertices:", result.selected_vertex_count)
    print("Changed vertices:", result.smoothed_vertex_count)
    print("Skipped empty vertices:", len(result.skipped_empty_vertex_ids))
    print("Skipped fully locked vertices:", len(result.skipped_locked_vertex_ids))
    print("Locked influences:", len(result.locked_influences))


def _build_context_vertex_ids(
    selected_vertex_ids,
    selected_adjacency: Sequence[Sequence[int]],
):
    selected = np.asarray(selected_vertex_ids, dtype=np.int32)
    if selected.size != len(selected_adjacency):
        raise RuntimeError("Component Smooth adjacency does not match the selection.")

    context = set(int(value) for value in selected.tolist())
    for neighbours in selected_adjacency:
        context.update(int(value) for value in neighbours)
    return np.asarray(sorted(context), dtype=np.int32)


def _rows_for_vertex_ids(context_vertex_ids, vertex_ids):
    context = np.asarray(context_vertex_ids, dtype=np.int32)
    requested = np.asarray(vertex_ids, dtype=np.int32)
    if not requested.size:
        return np.empty(0, dtype=np.int32)

    rows = np.searchsorted(context, requested).astype(np.int32)
    invalid = (
        (rows < 0)
        | (rows >= context.size)
        | (context[np.minimum(rows, context.size - 1)] != requested)
    )
    if np.any(invalid):
        raise RuntimeError(
            "Component Smooth context is missing required vertex IDs. First IDs: {}".format(
                requested[invalid][:20].tolist()
            )
        )
    return rows


def _smooth_selected_rows(
    baseline,
    adjacency,
    selected_vertex_ids,
    selection_falloffs,
    locked_columns,
    blend,
    iterations,
):
    """Compatibility wrapper for callers that supply a full-mesh matrix."""

    selected_vertex_ids = np.asarray(selected_vertex_ids, dtype=np.int32)
    selected_neighbour_rows = tuple(
        np.asarray(adjacency[int(vertex_id)], dtype=np.int32)
        for vertex_id in selected_vertex_ids.tolist()
    )
    return _smooth_context_rows(
        baseline=baseline,
        selected_context_rows=selected_vertex_ids,
        selected_vertex_ids=selected_vertex_ids,
        selected_neighbour_rows=selected_neighbour_rows,
        selection_falloffs=selection_falloffs,
        locked_columns=locked_columns,
        blend=blend,
        iterations=iterations,
    )


def _smooth_context_rows(
    baseline,
    selected_context_rows,
    selected_vertex_ids,
    selected_neighbour_rows,
    selection_falloffs,
    locked_columns,
    blend,
    iterations,
):
    current = np.asarray(baseline, dtype=np.float64).copy()
    original = current.copy()
    selected_context_rows = np.asarray(selected_context_rows, dtype=np.int32)
    selected_vertex_ids = np.asarray(selected_vertex_ids, dtype=np.int32)
    selection_falloffs = np.asarray(selection_falloffs, dtype=np.float64)

    if selected_context_rows.size != selected_vertex_ids.size:
        raise RuntimeError("Component Smooth selected row mapping is inconsistent.")
    if selected_vertex_ids.size != selection_falloffs.size:
        raise RuntimeError("Component Smooth selection falloff is inconsistent.")
    if selected_vertex_ids.size != len(selected_neighbour_rows):
        raise RuntimeError("Component Smooth neighbour rows are inconsistent.")

    influence_count = int(current.shape[1])
    tolerance = (
        float(np.finfo(np.float64).eps)
        * max(1, influence_count)
        * 64.0
    )

    locked_mask = np.zeros(influence_count, dtype=bool)
    if locked_columns:
        locked_mask[list(locked_columns)] = True
    unlocked_columns = np.where(~locked_mask)[0]

    selected_original = original[selected_context_rows]
    selected_row_sums = np.sum(
        selected_original,
        axis=1,
        dtype=np.float64,
    )
    empty_mask = selected_row_sums <= tolerance

    if unlocked_columns.size:
        selected_unlocked_sums = np.sum(
            selected_original[:, unlocked_columns],
            axis=1,
            dtype=np.float64,
        )
    else:
        selected_unlocked_sums = np.zeros(
            selected_vertex_ids.size,
            dtype=np.float64,
        )
    locked_mask_rows = (~empty_mask) & (selected_unlocked_sums <= tolerance)

    writable_mask = ~(empty_mask | locked_mask_rows)
    writable_selection_rows = np.where(writable_mask)[0].astype(np.int32)
    writable_context_rows = selected_context_rows[writable_mask]
    writable_falloffs = selection_falloffs[writable_mask]

    if not writable_context_rows.size or not unlocked_columns.size:
        return (
            current,
            np.empty(0, dtype=np.int32),
            selected_vertex_ids[empty_mask],
            selected_vertex_ids[locked_mask_rows],
        )

    edge_source_rows = []
    edge_neighbour_rows = []
    for writable_row, selection_row in enumerate(
        writable_selection_rows.tolist()
    ):
        neighbours = np.asarray(
            selected_neighbour_rows[int(selection_row)],
            dtype=np.int32,
        )
        if not neighbours.size:
            continue
        edge_source_rows.extend([int(writable_row)] * int(neighbours.size))
        edge_neighbour_rows.extend(int(value) for value in neighbours.tolist())

    edge_source_rows = np.asarray(edge_source_rows, dtype=np.int32)
    edge_neighbour_rows = np.asarray(edge_neighbour_rows, dtype=np.int32)

    effective_blend = float(blend) * writable_falloffs[:, np.newaxis]
    if np.any(locked_mask):
        original_locked_mass = np.sum(
            original[writable_context_rows][:, locked_mask],
            axis=1,
            dtype=np.float64,
        )
    else:
        original_locked_mass = np.zeros(
            writable_context_rows.size,
            dtype=np.float64,
        )
    available_mass = np.maximum(0.0, 1.0 - original_locked_mass)
    locked_indices = np.where(locked_mask)[0]

    for _ in range(int(iterations)):
        source = current
        next_weights = source.copy()

        neighbour_accum = np.zeros(
            (writable_context_rows.size, unlocked_columns.size),
            dtype=np.float64,
        )
        if edge_source_rows.size:
            edge_values = source[edge_neighbour_rows][:, unlocked_columns]
            edge_masses = np.sum(edge_values, axis=1, dtype=np.float64)
            valid_edges = edge_masses > tolerance
            if np.any(valid_edges):
                np.add.at(
                    neighbour_accum,
                    edge_source_rows[valid_edges],
                    edge_values[valid_edges],
                )

        neighbour_totals = np.sum(
            neighbour_accum,
            axis=1,
            dtype=np.float64,
        )
        valid_neighbour_rows = neighbour_totals > tolerance

        target_unlocked = source[writable_context_rows][:, unlocked_columns]
        target_totals = np.sum(
            target_unlocked,
            axis=1,
            dtype=np.float64,
        )
        valid_target_rows = target_totals > tolerance
        active_rows = (
            valid_neighbour_rows
            & valid_target_rows
            & (writable_falloffs > 0.0)
            & (float(blend) > 0.0)
        )
        if not np.any(active_rows):
            current = next_weights
            continue

        neighbour_distribution = np.zeros_like(neighbour_accum)
        neighbour_distribution[active_rows] = (
            neighbour_accum[active_rows]
            / neighbour_totals[active_rows, np.newaxis]
        )

        target_distribution = np.zeros_like(target_unlocked)
        target_distribution[active_rows] = (
            target_unlocked[active_rows]
            / target_totals[active_rows, np.newaxis]
        )

        blended_distribution = target_distribution[active_rows] + (
            effective_blend[active_rows]
            * (
                neighbour_distribution[active_rows]
                - target_distribution[active_rows]
            )
        )
        blended_distribution = np.maximum(blended_distribution, 0.0)
        blended_totals = np.sum(
            blended_distribution,
            axis=1,
            dtype=np.float64,
        )
        valid_blended = blended_totals > tolerance

        active_local_rows = np.where(active_rows)[0][valid_blended]
        if active_local_rows.size:
            normalized = (
                blended_distribution[valid_blended]
                / blended_totals[valid_blended, np.newaxis]
            )
            context_rows = writable_context_rows[active_local_rows]
            next_weights[np.ix_(context_rows, unlocked_columns)] = (
                normalized
                * available_mass[active_local_rows, np.newaxis]
            )
            if locked_indices.size:
                next_weights[np.ix_(context_rows, locked_indices)] = original[
                    np.ix_(context_rows, locked_indices)
                ]

        current = next_weights

    changed_local_mask = np.any(
        np.abs(
            current[selected_context_rows]
            - original[selected_context_rows]
        ) > tolerance,
        axis=1,
    )
    changed_vertex_ids = selected_vertex_ids[changed_local_mask]

    return (
        current,
        changed_vertex_ids,
        selected_vertex_ids[empty_mask],
        selected_vertex_ids[locked_mask_rows],
    )


def _loaded_mesh_object_selected(
    mesh_shape: str,
    mesh_transform: str,
) -> bool:
    selection = {
        str(item)
        for item in (cmds.ls(selection=True, long=True) or [])
    }
    return mesh_shape in selection or mesh_transform in selection


def _validate_written_rows(
    adapter: SkinClusterAdapter,
    vertex_ids,
    expected_weights,
) -> None:
    if not vertex_ids.size:
        return

    actual = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )
    expected = np.asarray(expected_weights, dtype=np.float64)
    tolerance = 1e-8

    if not np.all(np.isfinite(actual)):
        raise RuntimeError("Component Smooth stored non-finite weights.")

    differences = np.abs(actual - expected)
    if not np.allclose(actual, expected, rtol=0.0, atol=tolerance):
        changed_rows = np.where(
            np.any(differences > tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Component Smooth did not store the calculated weights. "
            "Maximum difference: {:.12g}. First vertex IDs: {}".format(
                float(np.max(differences)),
                vertex_ids[changed_rows].tolist(),
            )
        )

    row_sums = np.sum(actual, axis=1, dtype=np.float64)
    bad_rows = np.where(np.abs(row_sums - 1.0) > tolerance)[0]
    if bad_rows.size:
        raise RuntimeError(
            "Component Smooth stored weights that do not total 1.0. "
            "First vertex IDs: {}".format(
                vertex_ids[bad_rows[:20]].tolist()
            )
        )


def _undo_failed_smooth() -> None:
    try:
        cmds.undo()
    except Exception:
        cmds.warning(
            "Component Smooth failed after modifying the skinCluster. "
            "Use Maya Undo before continuing."
        )


def _restore_selection(selection_before) -> None:
    cmds.select(clear=True)
    if not selection_before:
        return
    try:
        cmds.select(selection_before, replace=True)
    except Exception:
        pass
