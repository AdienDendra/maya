"""Production execution path for Component Smooth."""

from typing import Optional, Tuple

import maya.cmds as cmds

from ad_skin_tools.components import smooth as solver
from ad_skin_tools.core import mesh
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undoable_skin_weights import apply_undoable_weights


np = ensure_numpy()

WRITE_TOLERANCE = 1e-8
_READBACK_VALIDATION_ENABLED = False


def collect_smooth_scope(mesh_shape: str, mesh_transform: str):
    """Resolve the current Component Smooth scope."""

    return solver.collect_smooth_scope(mesh_shape, mesh_transform)


def print_component_smooth_report(result) -> None:
    """Print the standard Component Smooth report."""

    solver.print_component_smooth_report(result)


def set_readback_validation(enabled: bool) -> None:
    """Enable expensive Maya read-back validation for diagnostic runs."""

    global _READBACK_VALIDATION_ENABLED
    _READBACK_VALIDATION_ENABLED = bool(enabled)


def readback_validation_enabled() -> bool:
    return _READBACK_VALIDATION_ENABLED


def smooth_skin_weights(
    scope,
    blend: float,
    iterations: Optional[int] = None,
    passes: Optional[int] = None,
):
    """Smooth the resolved component scope and write the result undoably."""

    blend = float(blend)
    if (
        blend < solver.MINIMUM_COMPONENT_BLEND
        or blend > solver.MAXIMUM_COMPONENT_BLEND
    ):
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

    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)
    influences = tuple(adapter.influences())
    active_locked = locked_influences(adapter.skin_cluster, influences)
    locked_columns = tuple(
        influences.index(joint)
        for joint in active_locked
        if joint in influences
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

    context_data = adapter.get_weights(context_vertex_ids)
    baseline = np.asarray(context_data.weights, dtype=np.float64).copy()

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

    command_applied = False
    try:
        if changed_vertex_ids.size:
            changed_context_rows = solver._rows_for_vertex_ids(
                context_vertex_ids,
                changed_vertex_ids,
            )
            before_weights = baseline[changed_context_rows].copy()
            after_weights = _prepare_weights_for_write(
                final_weights[changed_context_rows],
                locked_columns=locked_columns,
            )

            apply_undoable_weights(
                skin_cluster=adapter.skin_cluster,
                mesh_shape=scope.mesh_shape,
                vertex_ids=changed_vertex_ids,
                before_weights=before_weights,
                after_weights=after_weights,
            )
            command_applied = True

            if readback_validation_enabled():
                solver._validate_written_rows(
                    adapter=adapter,
                    vertex_ids=changed_vertex_ids,
                    expected_weights=after_weights,
                )
    except Exception:
        if command_applied:
            solver._undo_failed_smooth()
        raise
    finally:
        solver._restore_selection(selection_before)

    return solver.ComponentSmoothResult(
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


def _prepare_weights_for_write(
    weights,
    locked_columns: Tuple[int, ...],
):
    """Validate rows and place tiny normalization residuals deterministically."""

    prepared = np.array(weights, dtype=np.float64, copy=True, order="C")
    if prepared.ndim != 2:
        raise RuntimeError("Component Smooth write weights must be two-dimensional.")
    if not prepared.shape[0] or not prepared.shape[1]:
        raise RuntimeError("Component Smooth cannot write an empty weight matrix.")
    if not np.all(np.isfinite(prepared)):
        raise RuntimeError("Component Smooth calculated non-finite weights.")

    minimum_weight = float(np.min(prepared))
    if minimum_weight < -WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth calculated a negative weight: {:.12g}.".format(
                minimum_weight
            )
        )

    # Remove numerical noise only. Meaningful negative values are rejected above.
    np.maximum(prepared, 0.0, out=prepared)

    row_sums = np.sum(prepared, axis=1, dtype=np.float64)
    maximum_deviation = float(np.max(np.abs(row_sums - 1.0)))
    if maximum_deviation > WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth calculated weights that do not total 1.0. "
            "Maximum deviation: {:.12g}.".format(maximum_deviation)
        )

    influence_count = int(prepared.shape[1])
    locked_mask = np.zeros(influence_count, dtype=bool)
    if locked_columns:
        locked_mask[list(locked_columns)] = True

    correction_candidates = np.where(~locked_mask)[0]
    if not correction_candidates.size:
        correction_candidates = np.arange(influence_count, dtype=np.int32)

    rows = np.arange(prepared.shape[0], dtype=np.int32)
    candidate_values = prepared[:, correction_candidates]
    correction_columns = correction_candidates[
        np.argmax(candidate_values, axis=1)
    ]

    selected_values = prepared[rows, correction_columns].copy()
    corrected_values = 1.0 - (row_sums - selected_values)
    if float(np.min(corrected_values)) < -WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth normalization would create a negative weight."
        )
    prepared[rows, correction_columns] = np.maximum(corrected_values, 0.0)

    final_residual = 1.0 - np.sum(prepared, axis=1, dtype=np.float64)
    prepared[rows, correction_columns] += final_residual

    if not np.all(np.isfinite(prepared)):
        raise RuntimeError("Component Smooth prepared non-finite write weights.")
    minimum_prepared = float(np.min(prepared))
    if minimum_prepared < -WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth prepared a negative write weight: {:.12g}.".format(
                minimum_prepared
            )
        )

    final_deviation = float(
        np.max(
            np.abs(
                np.sum(prepared, axis=1, dtype=np.float64) - 1.0
            )
        )
    )
    if final_deviation > WRITE_TOLERANCE:
        raise RuntimeError(
            "Component Smooth could not prepare normalized write rows. "
            "Maximum deviation: {:.12g}.".format(final_deviation)
        )

    return prepared
