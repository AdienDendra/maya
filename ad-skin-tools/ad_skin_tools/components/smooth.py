"""Production Component Smooth using the shared bind diffusion kernel."""

from dataclasses import dataclass
import time
from typing import Optional, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.bind_smoothing.diffusion import (
    WeightDiffusionResult,
    diffuse_weight_matrix,
)
from ad_skin_tools.components.selection import collect_weighted_mesh_vertices
from ad_skin_tools.core.component_selection import collect_selected_mesh_vertices
from ad_skin_tools.core import mesh
from ad_skin_tools.core.compat import ensure_numpy
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


def _loaded_mesh_object_selected(
    mesh_shape: str,
    mesh_transform: str,
) -> bool:
    selection = {
        str(item)
        for item in (cmds.ls(selection=True, long=True) or [])
    }
    return mesh_shape in selection or mesh_transform in selection


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
    context_vertex_count: int
    influence_count: int
    adjacency_seconds: float
    weight_read_seconds: float
    calculation_seconds: float
    weight_write_seconds: float
    validation_seconds: float
    elapsed_seconds: float
    shared_topology_seconds: float
    shared_iteration_seconds: float
    shared_finalization_seconds: float

    @property
    def passes(self) -> int:
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


@dataclass(frozen=True)
class _SmoothCalculation:
    weights: np.ndarray
    changed_vertex_ids: np.ndarray
    skipped_empty_vertex_ids: np.ndarray
    skipped_locked_vertex_ids: np.ndarray
    diffusion_result: Optional[WeightDiffusionResult]


def smooth_skin_weights(
    scope: ComponentSmoothScope,
    blend: float,
    iterations: Optional[int] = None,
    passes: Optional[int] = None,
) -> ComponentSmoothResult:
    """Smooth selected rows through the Bind/Add Influence Jacobi kernel."""

    started = time.perf_counter()
    blend, iterations = _validated_options(
        blend=blend,
        iterations=iterations,
        passes=passes,
    )

    selection_before = cmds.ls(selection=True, long=True) or []
    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)
    influences = tuple(adapter.influences())
    active_locked = locked_influences(
        adapter.skin_cluster,
        influences,
    )

    selected_vertex_ids = np.asarray(
        scope.vertex_ids,
        dtype=np.int32,
    )
    selection_falloffs = np.clip(
        np.asarray(scope.selection_falloffs, dtype=np.float64),
        0.0,
        1.0,
    )
    if selected_vertex_ids.size != selection_falloffs.size:
        raise RuntimeError("Component Smooth selection data is inconsistent.")

    adjacency_started = time.perf_counter()
    selected_adjacency = mesh.get_vertex_neighbors(
        scope.mesh_shape,
        selected_vertex_ids,
    )
    context_vertex_ids = _build_context_vertex_ids(
        selected_vertex_ids,
        selected_adjacency,
    )
    (
        selected_context_rows,
        selected_neighbour_rows,
    ) = _build_context_row_mapping(
        mesh_shape=scope.mesh_shape,
        context_vertex_ids=context_vertex_ids,
        selected_vertex_ids=selected_vertex_ids,
        selected_adjacency=selected_adjacency,
    )
    adjacency_seconds = time.perf_counter() - adjacency_started

    read_started = time.perf_counter()
    context_data = adapter.get_weights(context_vertex_ids)
    if tuple(context_data.influences) != influences:
        raise RuntimeError(
            "Component Smooth influence order changed during the weight read."
        )
    baseline = np.asarray(
        context_data.weights,
        dtype=np.float64,
    ).copy()
    weight_read_seconds = time.perf_counter() - read_started

    locked_columns = tuple(
        influences.index(joint)
        for joint in active_locked
        if joint in influences
    )

    calculation_started = time.perf_counter()
    calculation = _solve_context_rows(
        baseline=baseline,
        selected_context_rows=selected_context_rows,
        selected_vertex_ids=selected_vertex_ids,
        selected_neighbour_rows=selected_neighbour_rows,
        selection_falloffs=selection_falloffs,
        locked_columns=locked_columns,
        blend=blend,
        iterations=iterations,
    )
    calculation_seconds = time.perf_counter() - calculation_started

    write_seconds = 0.0
    validation_seconds = 0.0
    command_applied = False
    try:
        if calculation.changed_vertex_ids.size:
            changed_context_rows = _rows_for_vertex_ids(
                context_vertex_ids,
                calculation.changed_vertex_ids,
            )
            before_weights = baseline[changed_context_rows].copy()
            after_weights = calculation.weights[changed_context_rows].copy()

            write_started = time.perf_counter()
            apply_undoable_weights(
                skin_cluster=adapter.skin_cluster,
                mesh_shape=scope.mesh_shape,
                vertex_ids=calculation.changed_vertex_ids,
                before_weights=before_weights,
                after_weights=after_weights,
            )
            write_seconds = time.perf_counter() - write_started
            command_applied = True

            validation_started = time.perf_counter()
            _validate_written_rows(
                adapter=adapter,
                vertex_ids=calculation.changed_vertex_ids,
                expected_weights=after_weights,
                locked_columns=locked_columns,
                locked_weights_before=before_weights,
            )
            validation_seconds = time.perf_counter() - validation_started
    except Exception:
        if command_applied:
            _undo_failed_smooth()
        raise
    finally:
        _restore_selection(selection_before)

    shared = calculation.diffusion_result
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
            int(value)
            for value in calculation.changed_vertex_ids.tolist()
        ),
        skipped_empty_vertex_ids=tuple(
            int(value)
            for value in calculation.skipped_empty_vertex_ids.tolist()
        ),
        skipped_locked_vertex_ids=tuple(
            int(value)
            for value in calculation.skipped_locked_vertex_ids.tolist()
        ),
        locked_influences=active_locked,
        soft_selection_enabled=scope.soft_selection_enabled,
        soft_selection_used=scope.soft_selection_used,
        context_vertex_count=int(context_vertex_ids.size),
        influence_count=len(influences),
        adjacency_seconds=float(adjacency_seconds),
        weight_read_seconds=float(weight_read_seconds),
        calculation_seconds=float(calculation_seconds),
        weight_write_seconds=float(write_seconds),
        validation_seconds=float(validation_seconds),
        elapsed_seconds=float(time.perf_counter() - started),
        shared_topology_seconds=(
            float(shared.topology_setup_seconds) if shared else 0.0
        ),
        shared_iteration_seconds=(
            float(shared.iteration_seconds) if shared else 0.0
        ),
        shared_finalization_seconds=(
            float(shared.finalization_seconds) if shared else 0.0
        ),
    )


def print_component_smooth_report(result: ComponentSmoothResult) -> None:
    print("\n[AD Skin Tool - Component Smooth]")
    print("Mesh:", result.mesh_transform)
    print("Whole object:", result.whole_object)
    print("Soft Selection enabled:", result.soft_selection_enabled)
    print("Selected vertices:", result.selected_vertex_count)
    print("Changed vertices:", result.smoothed_vertex_count)
    print("Blend:", result.blend)
    print("Iterations:", result.iterations)
    print("Locked influences:", len(result.locked_influences))
    print("Elapsed: {:.6f} s".format(result.elapsed_seconds))


def _validated_options(blend, iterations, passes):
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
        raise ValueError(
            "Supply either iterations or passes, not conflicting values."
        )
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
    return blend, iterations


def _build_context_vertex_ids(
    selected_vertex_ids,
    selected_adjacency: Sequence[Sequence[int]],
):
    selected = np.asarray(selected_vertex_ids, dtype=np.int32)
    if selected.size != len(selected_adjacency):
        raise RuntimeError(
            "Component Smooth adjacency does not match the selection."
        )

    context = set(int(value) for value in selected.tolist())
    for neighbours in selected_adjacency:
        context.update(int(value) for value in neighbours)
    return np.asarray(sorted(context), dtype=np.int32)


def _build_context_row_mapping(
    mesh_shape,
    context_vertex_ids,
    selected_vertex_ids,
    selected_adjacency,
):
    vertex_count = mesh.get_vertex_count(mesh_shape)
    context = np.asarray(context_vertex_ids, dtype=np.int32)
    selected = np.asarray(selected_vertex_ids, dtype=np.int32)

    row_by_vertex = np.full(vertex_count, -1, dtype=np.int32)
    row_by_vertex[context] = np.arange(context.size, dtype=np.int32)

    selected_rows = row_by_vertex[selected]
    if np.any(selected_rows < 0):
        raise RuntimeError(
            "Component Smooth context is missing selected vertices."
        )

    neighbour_rows = []
    for neighbours in selected_adjacency:
        neighbour_ids = np.asarray(neighbours, dtype=np.int32)
        rows = row_by_vertex[neighbour_ids]
        if np.any(rows < 0):
            raise RuntimeError(
                "Component Smooth context is missing neighbour vertices."
            )
        neighbour_rows.append(rows)

    return selected_rows, tuple(neighbour_rows)


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
            "Component Smooth context is missing required vertex IDs. "
            "First IDs: {}".format(
                requested[invalid][:20].tolist()
            )
        )
    return rows






def _solve_context_rows(
    baseline,
    selected_context_rows,
    selected_vertex_ids,
    selected_neighbour_rows,
    selection_falloffs,
    locked_columns,
    blend,
    iterations,
):
    original = np.asarray(baseline, dtype=np.float64).copy()
    selected_context_rows = np.asarray(
        selected_context_rows,
        dtype=np.int32,
    )
    selected_vertex_ids = np.asarray(
        selected_vertex_ids,
        dtype=np.int32,
    )
    selection_falloffs = np.asarray(
        selection_falloffs,
        dtype=np.float64,
    )

    if original.ndim != 2 or original.shape[1] < 1:
        raise RuntimeError(
            "Component Smooth baseline must be a two-dimensional matrix."
        )
    if selected_context_rows.size != selected_vertex_ids.size:
        raise RuntimeError(
            "Component Smooth selected row mapping is inconsistent."
        )
    if selected_vertex_ids.size != selection_falloffs.size:
        raise RuntimeError(
            "Component Smooth selection falloff is inconsistent."
        )
    if selected_vertex_ids.size != len(selected_neighbour_rows):
        raise RuntimeError(
            "Component Smooth neighbour rows are inconsistent."
        )
    if not np.all(np.isfinite(original)):
        raise RuntimeError(
            "Component Smooth received non-finite baseline weights."
        )

    influence_count = int(original.shape[1])
    tolerance = (
        float(np.finfo(np.float64).eps)
        * max(1, influence_count)
        * 64.0
    )
    if np.any(original < -tolerance):
        bad_rows = np.where(
            np.any(original < -tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Component Smooth received negative baseline weights. "
            "First context rows: {}".format(bad_rows.tolist())
        )

    row_sums = np.sum(original, axis=1, dtype=np.float64)
    selected_row_sums = row_sums[selected_context_rows]
    empty_mask = selected_row_sums <= tolerance

    locked_mask = np.zeros(influence_count, dtype=bool)
    if locked_columns:
        locked_mask[list(locked_columns)] = True
    unlocked_columns = np.where(~locked_mask)[0]

    if not unlocked_columns.size:
        locked_rows = ~empty_mask
        return _SmoothCalculation(
            weights=original,
            changed_vertex_ids=np.empty(0, dtype=np.int32),
            skipped_empty_vertex_ids=selected_vertex_ids[empty_mask],
            skipped_locked_vertex_ids=selected_vertex_ids[locked_rows],
            diffusion_result=None,
        )

    unlocked_mass = np.sum(
        original[:, unlocked_columns],
        axis=1,
        dtype=np.float64,
    )
    selected_unlocked_mass = unlocked_mass[selected_context_rows]
    fully_locked_mask = (
        (~empty_mask)
        & (selected_unlocked_mass <= tolerance)
    )

    positive_falloff_mask = selection_falloffs > 0.0
    writable_mask = ~(
        empty_mask
        | fully_locked_mask
        | (~positive_falloff_mask)
    )
    mutable_context_rows = selected_context_rows[writable_mask]

    if not mutable_context_rows.size or float(blend) <= 0.0:
        return _SmoothCalculation(
            weights=original,
            changed_vertex_ids=np.empty(0, dtype=np.int32),
            skipped_empty_vertex_ids=selected_vertex_ids[empty_mask],
            skipped_locked_vertex_ids=selected_vertex_ids[fully_locked_mask],
            diffusion_result=None,
        )

    contributor_mask = (
        (row_sums > tolerance)
        & (unlocked_mass > tolerance)
    )
    unlocked_distribution = np.zeros(
        (original.shape[0], unlocked_columns.size),
        dtype=np.float64,
    )
    valid_contributors = np.where(contributor_mask)[0]
    if valid_contributors.size:
        unlocked_distribution[valid_contributors] = (
            original[np.ix_(valid_contributors, unlocked_columns)]
            / unlocked_mass[valid_contributors, np.newaxis]
        )

    invalid_contributors = np.where(~contributor_mask)[0]
    if invalid_contributors.size:
        unlocked_distribution[invalid_contributors, 0] = 1.0

    local_adjacency = [tuple() for _ in range(original.shape[0])]
    for selection_row, context_row in enumerate(
        selected_context_rows.tolist()
    ):
        local_adjacency[int(context_row)] = tuple(
            int(value)
            for value in np.asarray(
                selected_neighbour_rows[int(selection_row)],
                dtype=np.int32,
            ).tolist()
        )

    row_blend_factors = np.zeros(
        original.shape[0],
        dtype=np.float64,
    )
    row_blend_factors[selected_context_rows] = np.clip(
        selection_falloffs,
        0.0,
        1.0,
    )

    diffusion_result = diffuse_weight_matrix(
        initial_weights=unlocked_distribution,
        adjacency=tuple(local_adjacency),
        iterations=int(iterations),
        blend=float(blend),
        mutable_vertex_ids=mutable_context_rows,
        row_blend_factors=row_blend_factors,
        contributor_mask=contributor_mask,
    )

    final_weights = original.copy()
    final_weights[np.ix_(mutable_context_rows, unlocked_columns)] = (
        diffusion_result.weights[mutable_context_rows]
        * unlocked_mass[mutable_context_rows, np.newaxis]
    )
    if np.any(locked_mask):
        locked_indices = np.where(locked_mask)[0]
        final_weights[np.ix_(mutable_context_rows, locked_indices)] = (
            original[np.ix_(mutable_context_rows, locked_indices)]
        )

    changed_local_mask = np.any(
        np.abs(
            final_weights[selected_context_rows]
            - original[selected_context_rows]
        ) > tolerance,
        axis=1,
    )
    changed_vertex_ids = selected_vertex_ids[changed_local_mask]

    _validate_calculated_rows(
        original=original,
        calculated=final_weights,
        selected_context_rows=selected_context_rows,
        locked_columns=locked_columns,
        tolerance=tolerance,
    )

    return _SmoothCalculation(
        weights=final_weights,
        changed_vertex_ids=changed_vertex_ids,
        skipped_empty_vertex_ids=selected_vertex_ids[empty_mask],
        skipped_locked_vertex_ids=selected_vertex_ids[fully_locked_mask],
        diffusion_result=diffusion_result,
    )


def _validate_calculated_rows(
    original,
    calculated,
    selected_context_rows,
    locked_columns,
    tolerance,
):
    selected = np.asarray(selected_context_rows, dtype=np.int32)
    rows = np.asarray(calculated, dtype=np.float64)[selected]
    if not np.all(np.isfinite(rows)):
        raise RuntimeError(
            "Component Smooth calculated non-finite weights."
        )
    if np.any(rows < -tolerance):
        raise RuntimeError(
            "Component Smooth calculated negative weights."
        )

    row_sums = np.sum(rows, axis=1, dtype=np.float64)
    original_sums = np.sum(
        np.asarray(original, dtype=np.float64)[selected],
        axis=1,
        dtype=np.float64,
    )
    if not np.allclose(
        row_sums,
        original_sums,
        rtol=0.0,
        atol=1e-10,
    ):
        raise RuntimeError(
            "Component Smooth changed the total weight mass."
        )

    if locked_columns:
        locked = np.asarray(locked_columns, dtype=np.int32)
        if not np.array_equal(
            np.asarray(calculated)[np.ix_(selected, locked)],
            np.asarray(original)[np.ix_(selected, locked)],
        ):
            raise RuntimeError(
                "Component Smooth changed a locked influence column."
            )


def _validate_written_rows(
    adapter,
    vertex_ids,
    expected_weights,
    locked_columns,
    locked_weights_before,
):
    if not vertex_ids.size:
        return

    actual = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )
    expected = np.asarray(expected_weights, dtype=np.float64)
    tolerance = 1e-8

    if not np.all(np.isfinite(actual)):
        raise RuntimeError(
            "Component Smooth stored non-finite weights."
        )

    differences = np.abs(actual - expected)
    if not np.allclose(
        actual,
        expected,
        rtol=0.0,
        atol=tolerance,
    ):
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

    if locked_columns:
        locked = np.asarray(locked_columns, dtype=np.int32)
        before = np.asarray(
            locked_weights_before,
            dtype=np.float64,
        )
        if not np.allclose(
            actual[:, locked],
            before[:, locked],
            rtol=0.0,
            atol=1e-12,
        ):
            changed_rows = np.where(
                np.any(
                    np.abs(actual[:, locked] - before[:, locked]) > 1e-12,
                    axis=1,
                )
            )[0][:20]
            raise RuntimeError(
                "Component Smooth changed stored locked weights. "
                "First vertex IDs: {}".format(
                    vertex_ids[changed_rows].tolist()
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
