"""Fast interactive execution path for Component Smooth.

The numerical solver remains in :mod:`ad_skin_tools.components.smooth`. This
module keeps the same public UI-facing API while tightening the final weight
rows, recording stage timings, and making expensive Maya read-back validation
opt-in for debugging.
"""

from dataclasses import dataclass
import builtins
import time
from typing import Optional, Tuple

import maya.cmds as cmds

from ad_skin_tools.components import smooth as solver
from ad_skin_tools.core import mesh
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undoable_skin_weights import apply_undoable_weights


np = ensure_numpy()

READBACK_VALIDATION_FLAG = "AD_SKIN_VALIDATE_SMOOTH_WRITES"
WRITE_TOLERANCE = 1e-8


@dataclass(frozen=True)
class ComponentSmoothWriteStats:
    row_count: int
    minimum_weight_before: float
    maximum_weight_before: float
    maximum_row_sum_deviation_before: float
    minimum_weight_after: float
    maximum_weight_after: float
    maximum_row_sum_deviation_after: float


def collect_smooth_scope(mesh_shape: str, mesh_transform: str):
    started = time.perf_counter()
    scope = solver.collect_smooth_scope(mesh_shape, mesh_transform)
    builtins.AD_SKIN_SMOOTH_SCOPE_SECONDS = time.perf_counter() - started
    return scope


def print_component_smooth_report(result) -> None:
    solver.print_component_smooth_report(result)


def set_readback_validation(enabled: bool) -> None:
    """Enable expensive Maya read-back validation for diagnostic runs."""

    setattr(builtins, READBACK_VALIDATION_FLAG, bool(enabled))


def readback_validation_enabled() -> bool:
    return bool(getattr(builtins, READBACK_VALIDATION_FLAG, False))


def smooth_skin_weights(
    scope,
    blend: float,
    iterations: Optional[int] = None,
    passes: Optional[int] = None,
):
    """Run Component Smooth using compact context and fast production writes."""

    total_started = time.perf_counter()
    timings = {}

    blend = float(blend)
    if blend < solver.MINIMUM_COMPONENT_BLEND or blend > solver.MAXIMUM_COMPONENT_BLEND:
        raise ValueError(
            "Component Smooth Blend must be between {:.1f} and {:.1f}.".format(
                solver.MINIMUM_COMPONENT_BLEND,
                solver.MAXIMUM_COMPONENT_BLEND,
            )
        )

    if iterations is None:
        iterations = passes
    elif passes is not None and int(iterations) != int(passes):
        raise ValueError("Supply either iterations or passes, not conflicting values.")
    if iterations is None:
        iterations = solver.DEFAULT_COMPONENT_ITERATIONS

    iterations = int(iterations)
    if (
        iterations < solver.MINIMUM_COMPONENT_ITERATIONS
        or iterations > solver.MAXIMUM_COMPONENT_ITERATIONS
    ):
        raise ValueError(
            "Component Smooth Iterations must be between {} and {}.".format(
                solver.MINIMUM_COMPONENT_ITERATIONS,
                solver.MAXIMUM_COMPONENT_ITERATIONS,
            )
        )

    selection_before = cmds.ls(selection=True, long=True) or []

    started = time.perf_counter()
    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)
    influences = tuple(adapter.influences())
    active_locked = locked_influences(adapter.skin_cluster, influences)
    locked_columns = tuple(
        influences.index(joint)
        for joint in active_locked
        if joint in influences
    )
    timings["adapter_and_locks"] = time.perf_counter() - started

    selected_vertex_ids = np.asarray(scope.vertex_ids, dtype=np.int32)
    selection_falloffs = np.clip(
        np.asarray(scope.selection_falloffs, dtype=np.float64),
        0.0,
        1.0,
    )
    if selected_vertex_ids.size != selection_falloffs.size:
        raise RuntimeError("Component Smooth selection data is inconsistent.")

    started = time.perf_counter()
    selected_adjacency = mesh.get_vertex_neighbors(
        scope.mesh_shape,
        selected_vertex_ids,
    )
    context_vertex_ids = solver._build_context_vertex_ids(
        selected_vertex_ids,
        selected_adjacency,
    )
    selected_context_rows = solver._rows_for_vertex_ids(
        context_vertex_ids,
        selected_vertex_ids,
    )
    selected_neighbour_rows = tuple(
        solver._rows_for_vertex_ids(context_vertex_ids, neighbours)
        for neighbours in selected_adjacency
    )
    timings["local_topology_and_context"] = time.perf_counter() - started

    started = time.perf_counter()
    context_data = adapter.get_weights(context_vertex_ids)
    baseline = np.asarray(context_data.weights, dtype=np.float64).copy()
    timings["read_context_weights"] = time.perf_counter() - started

    started = time.perf_counter()
    (
        final_weights,
        changed_vertex_ids,
        skipped_empty_vertex_ids,
        skipped_locked_vertex_ids,
    ) = solver._smooth_context_rows(
        baseline=baseline,
        selected_context_rows=selected_context_rows,
        selected_vertex_ids=selected_vertex_ids,
        selected_neighbour_rows=selected_neighbour_rows,
        selection_falloffs=selection_falloffs,
        locked_columns=locked_columns,
        blend=blend,
        iterations=iterations,
    )
    timings["solve"] = time.perf_counter() - started

    command_applied = False
    try:
        if changed_vertex_ids.size:
            started = time.perf_counter()
            changed_context_rows = solver._rows_for_vertex_ids(
                context_vertex_ids,
                changed_vertex_ids,
            )
            before_weights = baseline[changed_context_rows].copy()
            after_weights, write_stats = _prepare_weights_for_write(
                final_weights[changed_context_rows],
                locked_columns=locked_columns,
            )
            builtins.AD_SKIN_SMOOTH_WRITE_STATS = write_stats
            timings["prepare_write"] = time.perf_counter() - started

            started = time.perf_counter()
            apply_undoable_weights(
                skin_cluster=adapter.skin_cluster,
                mesh_shape=scope.mesh_shape,
                vertex_ids=changed_vertex_ids,
                before_weights=before_weights,
                after_weights=after_weights,
            )
            command_applied = True
            timings["write"] = time.perf_counter() - started

            if readback_validation_enabled():
                started = time.perf_counter()
                solver._validate_written_rows(
                    adapter=adapter,
                    vertex_ids=changed_vertex_ids,
                    expected_weights=after_weights,
                )
                timings["readback_validation"] = time.perf_counter() - started
            else:
                timings["readback_validation"] = 0.0
        else:
            timings["prepare_write"] = 0.0
            timings["write"] = 0.0
            timings["readback_validation"] = 0.0
    except Exception:
        if command_applied:
            solver._undo_failed_smooth()
        raise
    finally:
        started = time.perf_counter()
        solver._restore_selection(selection_before)
        timings["restore_selection"] = time.perf_counter() - started
        timings["scope_collection"] = float(
            getattr(builtins, "AD_SKIN_SMOOTH_SCOPE_SECONDS", 0.0)
        )
        timings["smooth_function_total"] = time.perf_counter() - total_started
        builtins.AD_SKIN_SMOOTH_TIMINGS = dict(timings)

    return solver.ComponentSmoothResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=scope.mesh_shape,
        mesh_transform=scope.mesh_transform,
        blend=blend,
        iterations=iterations,
        whole_object=scope.whole_object,
        selected_vertex_ids=tuple(int(value) for value in selected_vertex_ids.tolist()),
        smoothed_vertex_ids=tuple(int(value) for value in changed_vertex_ids.tolist()),
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


def _prepare_weights_for_write(
    weights,
    locked_columns: Tuple[int, ...],
):
    """Validate rows and place the tiny normalization residual deterministically."""

    prepared = np.array(weights, dtype=np.float64, copy=True, order="C")
    if prepared.ndim != 2:
        raise RuntimeError("Component Smooth write weights must be two-dimensional.")
    if not prepared.shape[0] or not prepared.shape[1]:
        raise RuntimeError("Component Smooth cannot write an empty weight matrix.")
    if not np.all(np.isfinite(prepared)):
        raise RuntimeError("Component Smooth calculated non-finite weights.")

    minimum_before = float(np.min(prepared))
    maximum_before = float(np.max(prepared))
    if minimum_before < -WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth calculated a negative weight: {:.12g}.".format(
                minimum_before
            )
        )

    # Only remove numerical noise. A meaningful negative value is rejected above.
    np.maximum(prepared, 0.0, out=prepared)

    row_sums_before = np.sum(prepared, axis=1, dtype=np.float64)
    deviation_before = np.abs(row_sums_before - 1.0)
    maximum_deviation_before = float(np.max(deviation_before))
    if maximum_deviation_before > WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth calculated weights that do not total 1.0. "
            "Maximum deviation: {:.12g}.".format(maximum_deviation_before)
        )

    influence_count = int(prepared.shape[1])
    locked_mask = np.zeros(influence_count, dtype=bool)
    if locked_columns:
        locked_mask[list(locked_columns)] = True
    correction_candidates = np.where(~locked_mask)[0]
    if not correction_candidates.size:
        correction_candidates = np.arange(influence_count, dtype=np.int32)

    candidate_values = prepared[:, correction_candidates]
    local_columns = np.argmax(candidate_values, axis=1)
    correction_columns = correction_candidates[local_columns]
    rows = np.arange(prepared.shape[0], dtype=np.int32)

    # Recompute the selected value from all other columns instead of merely
    # adding a residual. This gives Maya the tightest possible row total while
    # keeping every locked column unchanged.
    selected_values = prepared[rows, correction_columns].copy()
    other_sums = row_sums_before - selected_values
    corrected_values = 1.0 - other_sums
    if float(np.min(corrected_values)) < -WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth normalization would create a negative weight."
        )
    prepared[rows, correction_columns] = np.maximum(corrected_values, 0.0)

    # One final floating-point residual pass handles cancellation in wide rows.
    final_residual = 1.0 - np.sum(prepared, axis=1, dtype=np.float64)
    prepared[rows, correction_columns] += final_residual

    if not np.all(np.isfinite(prepared)):
        raise RuntimeError("Component Smooth prepared non-finite write weights.")
    minimum_after = float(np.min(prepared))
    if minimum_after < -WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth prepared a negative write weight: {:.12g}.".format(
                minimum_after
            )
        )

    row_sums_after = np.sum(prepared, axis=1, dtype=np.float64)
    maximum_deviation_after = float(
        np.max(np.abs(row_sums_after - 1.0))
    )
    if maximum_deviation_after > WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth could not prepare normalized write rows. "
            "Maximum deviation: {:.12g}.".format(maximum_deviation_after)
        )

    stats = ComponentSmoothWriteStats(
        row_count=int(prepared.shape[0]),
        minimum_weight_before=minimum_before,
        maximum_weight_before=maximum_before,
        maximum_row_sum_deviation_before=maximum_deviation_before,
        minimum_weight_after=minimum_after,
        maximum_weight_after=float(np.max(prepared)),
        maximum_row_sum_deviation_after=maximum_deviation_after,
    )
    return prepared, stats
