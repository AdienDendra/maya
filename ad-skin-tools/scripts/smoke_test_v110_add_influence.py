"""Side-by-side v11.0 Add Influence smoke test.

The artist-facing Add Influence button is untouched. This script duplicates the
loaded skinned mesh twice, runs the current method on one clone and the v11 local
method on the other, then prints timing and weight comparisons.
"""

import builtins
import importlib
import time

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core import add_influence as legacy
from ad_skin_tools.core import add_influence_v11
from ad_skin_tools.core.skin_cluster import (
    SkinClusterAdapter,
    create_closest_skin_cluster,
)
from ad_skin_tools.ui import joint_list
from ad_skin_tools.ui import skin_operations
from ad_skin_tools.ui import smoothing_controls


importlib.reload(add_influence_v11)


def _ui_inputs():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError("Open AD Skin Tool before running the v11 smoke test.")

    tool_window._require_not_busy()
    tool_window._require_loaded_mesh()
    state = tool_window._STATE
    if not state.get("has_skin_cluster"):
        raise RuntimeError("Bind Skin before running Add Influence smoke tests.")

    bound = set(state.get("bound_joint_paths", set()))
    targets = [
        joint
        for joint in joint_list.selected_joint_paths()
        if joint not in bound
    ]
    if not targets:
        raise RuntimeError(
            "Highlight at least one pending joint in the influence list."
        )

    locked_targets = [
        joint for joint in targets
        if joint_list.joint_is_locked(joint)
    ]
    if locked_targets:
        raise RuntimeError(
            "Unlock pending smoke-test targets:\n{}".format(
                "\n".join(locked_targets)
            )
        )

    values = smoothing_controls.query_values()
    return (
        state["mesh_shape"],
        tuple(targets),
        float(values.blend),
        int(values.iterations),
    )


def _duplicate_exact_skin(source_shape, source_data, label):
    source_transform = cmds.listRelatives(
        source_shape,
        parent=True,
        fullPath=True,
    )[0]
    short_name = source_transform.split("|")[-1].split(":")[-1]
    duplicate = cmds.duplicate(
        source_transform,
        name="{}_AD_v110_{}#".format(short_name, label),
        returnRootsOnly=True,
    )[0]
    duplicate = (cmds.ls(duplicate, long=True) or [duplicate])[0]
    cmds.delete(duplicate, constructionHistory=True)

    duplicate_shape = (
        cmds.listRelatives(
            duplicate,
            shapes=True,
            noIntermediate=True,
            fullPath=True,
            type="mesh",
        )
        or []
    )[0]
    adapter = create_closest_skin_cluster(
        mesh_shape=duplicate_shape,
        mesh_transform=duplicate,
        joints=list(source_data.influences),
        max_influences=min(5, len(source_data.influences)),
    )
    ordered = _weights_in_order(
        source_data.weights,
        tuple(source_data.influences),
        tuple(adapter.influences()),
    )
    adapter.set_weights(source_data.vertex_ids, ordered, normalize=False)
    return duplicate_shape, duplicate


def _weights_in_order(weights, source_influences, target_influences):
    source_columns = {
        joint: column for column, joint in enumerate(source_influences)
    }
    missing = [
        joint for joint in target_influences
        if joint not in source_columns
    ]
    if missing:
        raise RuntimeError(
            "Cannot map smoke-test influence columns:\n{}".format(
                "\n".join(missing)
            )
        )
    permutation = np.asarray(
        [source_columns[joint] for joint in target_influences],
        dtype=np.int32,
    )
    return np.asarray(weights, dtype=np.float64)[:, permutation]


def _neutral_legacy_validation(
    adapter,
    vertex_ids,
    existing,
    baseline,
    targets,
    claimed_ids,
    expected_claimed,
    unchanged_ids,
    target_by_vertex,
    source_influences,
    maximum_influences,
):
    """Keep the old full readback cost but omit the obsolete owner-max rule."""

    del target_by_vertex, source_influences
    stored = adapter.get_weights(vertex_ids)
    influences = tuple(stored.influences)
    weights = np.asarray(stored.weights, dtype=np.float64)
    columns = {joint: index for index, joint in enumerate(influences)}
    tolerance = legacy.STORED_WEIGHT_TOLERANCE

    if unchanged_ids.size:
        existing_columns = [columns[joint] for joint in existing]
        target_columns = [columns[joint] for joint in targets]
        if not np.array_equal(
            weights[unchanged_ids][:, existing_columns],
            baseline[unchanged_ids],
        ):
            raise RuntimeError("Legacy smoke changed an unclaimed existing row.")
        if np.any(weights[unchanged_ids][:, target_columns] != 0.0):
            raise RuntimeError("Legacy smoke affected an unclaimed target row.")

    if not claimed_ids.size:
        return

    actual = weights[claimed_ids]
    if np.any(np.abs(actual - expected_claimed) > tolerance):
        raise RuntimeError("Legacy smoke stored weights differ from its solve.")
    if np.any(np.abs(np.sum(actual, axis=1) - 1.0) > tolerance):
        raise RuntimeError("Legacy smoke claimed rows are not normalized.")
    if np.any(np.count_nonzero(actual > tolerance, axis=1) > maximum_influences):
        raise RuntimeError("Legacy smoke claimed rows exceed Max Influences.")


def _claimed_ids(result):
    values = {
        int(vertex_id)
        for ids in result.claimed_vertex_ids_by_joint.values()
        for vertex_id in ids
    }
    return np.asarray(sorted(values), dtype=np.int32)


def _integrity_stats(matrix, source_matrix, claimed_ids, existing_count):
    row_sums = np.sum(matrix, axis=1, dtype=np.float64)
    active_counts = np.count_nonzero(matrix > 1e-10, axis=1)
    unclaimed = np.ones(matrix.shape[0], dtype=bool)
    unclaimed[claimed_ids] = False

    if np.any(unclaimed):
        existing_difference = float(
            np.max(
                np.abs(
                    matrix[unclaimed, :existing_count]
                    - source_matrix[unclaimed]
                )
            )
        )
        target_maximum = float(
            np.max(np.abs(matrix[unclaimed, existing_count:]))
        )
    else:
        existing_difference = 0.0
        target_maximum = 0.0

    return {
        "row_sum_error": float(np.max(np.abs(row_sums - 1.0))),
        "maximum_active_influences": int(np.max(active_counts)),
        "unclaimed_existing_difference": existing_difference,
        "unclaimed_target_maximum": target_maximum,
    }


def _print_comparison(
    source_transform,
    legacy_transform,
    optimized_transform,
    legacy_result,
    optimized_result,
    legacy_seconds,
    comparison_seconds,
    legacy_weights,
    optimized_weights,
    source_weights,
):
    legacy_ids = _claimed_ids(legacy_result)
    optimized_ids = _claimed_ids(optimized_result)
    legacy_set = set(legacy_ids.tolist())
    optimized_set = set(optimized_ids.tolist())
    common = legacy_set & optimized_set

    matrix_difference = np.abs(legacy_weights - optimized_weights)
    changed_rows = np.where(
        np.any(matrix_difference > 1e-10, axis=1)
    )[0]
    dominant_difference = int(
        np.count_nonzero(
            np.argmax(legacy_weights, axis=1)
            != np.argmax(optimized_weights, axis=1)
        )
    )

    legacy_stats = _integrity_stats(
        legacy_weights,
        source_weights,
        legacy_ids,
        source_weights.shape[1],
    )
    optimized_stats = _integrity_stats(
        optimized_weights,
        source_weights,
        optimized_ids,
        source_weights.shape[1],
    )

    print("\n[AD Skin Tool - v11 Add Influence Smoke Comparison]")
    print("Source mesh:", source_transform)
    print("Legacy clone:", legacy_transform)
    print("Optimized clone:", optimized_transform)
    print("Legacy total including full validation:", round(legacy_seconds, 6))
    print(
        "Optimized production total:",
        round(optimized_result.production_elapsed_seconds, 6),
    )
    if optimized_result.production_elapsed_seconds > 0.0:
        print(
            "Measured speed-up:",
            round(
                legacy_seconds / optimized_result.production_elapsed_seconds,
                3,
            ),
            "x",
        )
    print("Comparison readback time, excluded:", round(comparison_seconds, 6))
    print("Legacy claimed vertices:", len(legacy_set))
    print("Optimized claimed vertices:", len(optimized_set))
    print("Claim overlap:", len(common))
    print("Legacy-only claims:", len(legacy_set - optimized_set))
    print("Optimized-only claims:", len(optimized_set - legacy_set))
    print(
        "Maximum full-matrix difference:",
        float(np.max(matrix_difference)),
    )
    print("Rows with different weights:", int(changed_rows.size))
    print("Rows with different dominant influence:", dominant_difference)
    print("Legacy integrity:", legacy_stats)
    print("Optimized integrity:", optimized_stats)
    print("\nThe clones overlap the source and are hidden after the test.")
    print(
        "Show legacy:\n"
        "cmds.hide({!r}, {!r}); cmds.showHidden({!r})".format(
            source_transform,
            optimized_transform,
            legacy_transform,
        )
    )
    print(
        "Show optimized:\n"
        "cmds.hide({!r}, {!r}); cmds.showHidden({!r})".format(
            source_transform,
            legacy_transform,
            optimized_transform,
        )
    )


source_shape, targets, blend, iterations = _ui_inputs()
source_adapter = SkinClusterAdapter.from_mesh(source_shape)
vertex_count = int(cmds.polyEvaluate(source_shape, vertex=True))
vertex_ids = np.arange(vertex_count, dtype=np.int32)

setup_started = time.perf_counter()
source_data = source_adapter.get_weights(vertex_ids)
legacy_shape = None
optimized_shape = None
try:
    legacy_shape, legacy_transform = _duplicate_exact_skin(
        source_shape,
        source_data,
        "LEGACY",
    )
    optimized_shape, optimized_transform = _duplicate_exact_skin(
        source_shape,
        source_data,
        "OPTIMIZED",
    )
    clone_setup_seconds = time.perf_counter() - setup_started

    original_validation = legacy._validate_write
    legacy._validate_write = _neutral_legacy_validation
    try:
        legacy_started = time.perf_counter()
        legacy_result = legacy.add_influences_by_region(
            mesh=legacy_shape,
            target_joints=targets,
            smoothing_blend=blend,
            smoothing_iterations=iterations,
        )
        legacy_seconds = time.perf_counter() - legacy_started
    finally:
        legacy._validate_write = original_validation

    optimized_result = add_influence_v11.add_influences_by_region_v11(
        mesh=optimized_shape,
        target_joints=targets,
        smoothing_blend=blend,
        smoothing_iterations=iterations,
        global_owner_joint=None,
    )

    comparison_started = time.perf_counter()
    expected_influences = tuple(source_data.influences) + tuple(targets)
    legacy_data = SkinClusterAdapter.from_mesh(legacy_shape).get_weights(vertex_ids)
    optimized_data = SkinClusterAdapter.from_mesh(optimized_shape).get_weights(
        vertex_ids
    )
    legacy_weights = _weights_in_order(
        legacy_data.weights,
        tuple(legacy_data.influences),
        expected_influences,
    )
    optimized_weights = _weights_in_order(
        optimized_data.weights,
        tuple(optimized_data.influences),
        expected_influences,
    )
    comparison_seconds = time.perf_counter() - comparison_started

    source_transform = cmds.listRelatives(
        source_shape,
        parent=True,
        fullPath=True,
    )[0]
    print("\nClone setup, excluded from method timing:", round(clone_setup_seconds, 6))
    legacy.print_report(legacy_result)
    add_influence_v11.print_report(optimized_result)
    _print_comparison(
        source_transform=source_transform,
        legacy_transform=legacy_transform,
        optimized_transform=optimized_transform,
        legacy_result=legacy_result,
        optimized_result=optimized_result,
        legacy_seconds=legacy_seconds,
        comparison_seconds=comparison_seconds,
        legacy_weights=legacy_weights,
        optimized_weights=optimized_weights,
        source_weights=np.asarray(source_data.weights, dtype=np.float64),
    )

    cmds.hide(legacy_transform, optimized_transform)
    builtins.AD_SKIN_V110_SMOKE_RESULT = {
        "source_mesh": source_transform,
        "legacy_mesh": legacy_transform,
        "optimized_mesh": optimized_transform,
        "legacy_result": legacy_result,
        "optimized_result": optimized_result,
        "legacy_seconds": legacy_seconds,
        "clone_setup_seconds": clone_setup_seconds,
        "comparison_seconds": comparison_seconds,
    }
    print("\nSaved as builtins.AD_SKIN_V110_SMOKE_RESULT")
except Exception:
    for shape in (legacy_shape, optimized_shape):
        if shape and cmds.objExists(shape):
            parent = cmds.listRelatives(shape, parent=True, fullPath=True) or []
            cmds.delete(parent[0] if parent else shape)
    raise
