"""Removable development-only validation for production automatic binding."""

from dataclasses import dataclass
import time
from typing import Tuple

import numpy as np

from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.smoothed_automatic_bind import AutomaticSurfaceBindResult


STORED_WEIGHT_TOLERANCE = 1e-10


@dataclass(frozen=True)
class AutomaticBindDevelopmentValidationResult:
    stored_maximum_weight_difference: float
    owner_below_maximum_count: int
    active_influence_histogram: Tuple[Tuple[int, int], ...]
    maximum_row_sum_error: float
    average_owner_distance: float
    maximum_owner_distance: float
    expected_matrix_seconds: float
    stored_weight_readback_seconds: float
    stored_weight_validation_seconds: float
    diagnostic_seconds: float
    development_validation_seconds: float


def validate_and_print(
    result: AutomaticSurfaceBindResult,
) -> AutomaticBindDevelopmentValidationResult:
    """Read Maya weights back, validate them, and print development diagnostics."""

    started = time.perf_counter()
    adapter = SkinClusterAdapter.from_mesh(result.mesh_shape)

    expected_started = time.perf_counter()
    expected, skin_influences = _expected_weights_in_skin_order(adapter, result)
    expected_matrix_seconds = time.perf_counter() - expected_started

    vertex_ids = np.arange(result.vertex_count, dtype=np.int32)
    read_started = time.perf_counter()
    actual = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )
    stored_weight_readback_seconds = time.perf_counter() - read_started

    validation_started = time.perf_counter()
    (
        maximum_difference,
        owner_below_maximum_count,
        active_histogram,
        maximum_row_sum_error,
    ) = _validate_stored_weights(
        actual=actual,
        expected=expected,
        result=result,
        skin_influences=skin_influences,
    )
    stored_weight_validation_seconds = time.perf_counter() - validation_started

    diagnostic_started = time.perf_counter()
    average_owner_distance, maximum_owner_distance = _owner_distance_diagnostics(
        result
    )
    diagnostic_seconds = time.perf_counter() - diagnostic_started

    validation_result = AutomaticBindDevelopmentValidationResult(
        stored_maximum_weight_difference=float(maximum_difference),
        owner_below_maximum_count=int(owner_below_maximum_count),
        active_influence_histogram=active_histogram,
        maximum_row_sum_error=float(maximum_row_sum_error),
        average_owner_distance=float(average_owner_distance),
        maximum_owner_distance=float(maximum_owner_distance),
        expected_matrix_seconds=float(expected_matrix_seconds),
        stored_weight_readback_seconds=float(stored_weight_readback_seconds),
        stored_weight_validation_seconds=float(stored_weight_validation_seconds),
        diagnostic_seconds=float(diagnostic_seconds),
        development_validation_seconds=float(time.perf_counter() - started),
    )
    print_development_report(result, validation_result)
    return validation_result


def print_development_report(
    result: AutomaticSurfaceBindResult,
    validation: AutomaticBindDevelopmentValidationResult,
) -> None:
    """Print detailed diagnostics without changing the production timing total."""

    pipeline = result.ownership_pipeline
    closest = pipeline.closest_ownership
    nearest = closest.closest
    global_assignment = pipeline.global_owner_assignment
    loops = pipeline.closed_loop_ownership

    print("\n[AD Skin Tool - Development Validation]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Vertices:", result.vertex_count)
    print("Influences:", result.influence_count)
    print("Exact-tie vertices:", nearest.exact_tie_vertex_count)
    print("Connected owner regions:", closest.total_region_count)
    print("Secondary regions:", closest.secondary_region_count)
    print(
        "Global Owner:",
        global_assignment.global_owner_joint.split("|")[-1]
        if global_assignment.global_owner_enabled
        else "<none>",
    )
    print(
        "Global Owner reassigned vertices:",
        global_assignment.reassigned_vertex_count,
    )
    print("Ownership boundary edges:", loops.boundary_edge_count)
    print("Relevant closed loops:", loops.discovered_loop_count)
    print("Maya polySelect calls:", loops.maya_polyselect_call_count)
    print("Applied closed loops:", loops.applied_loop_count)
    print("Closed-loop changed vertices:", loops.changed_vertex_count)
    print("Primary opposite axis:", loops.axis_context.primary_axis)
    print("Final blocking owner rows:", result.blocking_owner_indices.size)
    print("Smoothing Blend:", result.smoothing_blend)
    print("Smoothing Iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Smoothing mixed vertices:", result.smoothing_mixed_vertex_count)
    print(
        "Stored maximum weight difference:",
        validation.stored_maximum_weight_difference,
    )
    print("Owner below maximum after:", validation.owner_below_maximum_count)
    print(
        "Final active influence histogram:",
        validation.active_influence_histogram,
    )
    print(
        "Final maximum row-sum error:",
        validation.maximum_row_sum_error,
    )
    print("Average final owner distance:", validation.average_owner_distance)
    print("Maximum final owner distance:", validation.maximum_owner_distance)

    print("\nProduction timing, ending at the completed custom weight write:")
    print("  ownership:", round(result.ownership_seconds, 6))
    print(
        "  final weight calculation:",
        round(result.weight_calculation_seconds, 6),
    )
    print(
        "  skinCluster creation:",
        round(result.skin_cluster_creation_seconds, 6),
    )
    print(
        "  skin-column remap:",
        round(result.skin_column_remap_seconds, 6),
    )
    print("  custom weight write:", round(result.weight_write_seconds, 6))
    print(
        "  production total:",
        round(result.production_elapsed_seconds, 6),
    )

    print("\nDevelopment validation timing, excluded from production total:")
    print(
        "  expected matrix reconstruction:",
        round(validation.expected_matrix_seconds, 6),
    )
    print(
        "  stored-weight readback:",
        round(validation.stored_weight_readback_seconds, 6),
    )
    print(
        "  stored-weight validation:",
        round(validation.stored_weight_validation_seconds, 6),
    )
    print(
        "  diagnostics:",
        round(validation.diagnostic_seconds, 6),
    )
    print(
        "  development validation total:",
        round(validation.development_validation_seconds, 6),
    )
    print(
        "  end-to-end including development validation:",
        round(
            result.production_elapsed_seconds
            + validation.development_validation_seconds,
            6,
        ),
    )


def _expected_weights_in_skin_order(adapter, result):
    if result.smoothing_result is None:
        ownership_weights = np.zeros(
            (result.vertex_count, result.influence_count),
            dtype=np.float64,
        )
        ownership_weights[
            np.arange(result.vertex_count, dtype=np.int32),
            result.blocking_owner_indices,
        ] = 1.0
    else:
        ownership_weights = np.asarray(
            result.smoothing_result.weights,
            dtype=np.float64,
        )

    ownership_influences = tuple(result.influences)
    skin_influences = tuple(adapter.influences())
    if skin_influences == ownership_influences:
        return ownership_weights, skin_influences

    ownership_column_by_joint = {
        joint: column
        for column, joint in enumerate(ownership_influences)
    }
    missing = [
        joint
        for joint in skin_influences
        if joint not in ownership_column_by_joint
    ]
    if missing or len(skin_influences) != len(ownership_influences):
        raise RuntimeError(
            "Development validation cannot map Maya influence columns.\n{}".format(
                "\n".join(missing) if missing else "<count mismatch>"
            )
        )
    permutation = np.asarray(
        [ownership_column_by_joint[joint] for joint in skin_influences],
        dtype=np.int32,
    )
    return ownership_weights[:, permutation], skin_influences


def _validate_stored_weights(
    actual,
    expected,
    result,
    skin_influences,
):
    if actual.shape != expected.shape:
        raise RuntimeError(
            "Stored weight matrix shape differs from expected: {} != {}.".format(
                actual.shape,
                expected.shape,
            )
        )

    maximum_difference = float(np.max(np.abs(actual - expected)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        bad = np.where(
            np.any(
                np.abs(actual - expected) > STORED_WEIGHT_TOLERANCE,
                axis=1,
            )
        )[0][:20]
        raise RuntimeError(
            "Maya stored weights differ from the calculated matrix. Maximum "
            "difference: {}. First vertex IDs: {}".format(
                maximum_difference,
                bad.tolist(),
            )
        )

    row_sums = np.sum(actual, axis=1, dtype=np.float64)
    row_errors = np.abs(row_sums - 1.0)
    maximum_row_sum_error = (
        float(np.max(row_errors)) if row_errors.size else 0.0
    )
    if np.any(row_errors > STORED_WEIGHT_TOLERANCE):
        bad = np.where(row_errors > STORED_WEIGHT_TOLERANCE)[0][:20]
        raise RuntimeError(
            "Stored weights are not normalized. First vertex IDs: {}".format(
                bad.tolist()
            )
        )

    active_counts = np.count_nonzero(actual > 1e-12, axis=1).astype(np.int32)
    if np.any(active_counts > int(result.effective_maximum_influences)):
        bad = np.where(
            active_counts > int(result.effective_maximum_influences)
        )[0][:20]
        raise RuntimeError(
            "Stored weights exceed Max Influences. First vertex IDs: {}".format(
                bad.tolist()
            )
        )
    histogram_values, histogram_counts = np.unique(
        active_counts,
        return_counts=True,
    )
    active_histogram = tuple(
        (int(active_count), int(vertex_count))
        for active_count, vertex_count in zip(
            histogram_values.tolist(),
            histogram_counts.tolist(),
        )
    )

    skin_column_by_joint = {
        joint: column
        for column, joint in enumerate(skin_influences)
    }
    owner_columns = np.asarray(
        [skin_column_by_joint[joint] for joint in result.influences],
        dtype=np.int32,
    )[result.blocking_owner_indices]
    row_ids = np.arange(result.vertex_count, dtype=np.int32)
    owner_weights = actual[row_ids, owner_columns]
    row_maximums = np.max(actual, axis=1)
    owner_below_maximum_count = int(
        np.count_nonzero(
            owner_weights + STORED_WEIGHT_TOLERANCE < row_maximums
        )
    )
    if owner_below_maximum_count:
        bad = np.where(
            owner_weights + STORED_WEIGHT_TOLERANCE < row_maximums
        )[0][:20]
        raise RuntimeError(
            "Blocking owner remains below another stored influence. First vertex "
            "IDs: {}".format(bad.tolist())
        )

    return (
        maximum_difference,
        owner_below_maximum_count,
        active_histogram,
        maximum_row_sum_error,
    )


def _owner_distance_diagnostics(result):
    context = result.ownership_pipeline.closest_ownership.context
    delta = (
        np.asarray(context.vertex_positions, dtype=np.float64)
        - np.asarray(context.influence_positions, dtype=np.float64)[
            result.blocking_owner_indices
        ]
    )
    squared = np.einsum("vi,vi->v", delta, delta)
    distances = np.sqrt(squared)
    return float(np.mean(distances)), float(np.max(distances))
