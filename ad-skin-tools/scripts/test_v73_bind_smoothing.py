"""v7.3 visual smoke: final v3.2 blocking handed to smoothing only."""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.bind_smoothing import cutoff_projection
from ad_skin_tools.bind_smoothing import final_constraints
from ad_skin_tools.bind_smoothing import options as smoothing_options
from ad_skin_tools.bind_smoothing import solver as smoothing_solver
from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import ambiguous_loop_distance_tiebreak
from ad_skin_tools.region import closed_loop_opposite_guard
from ad_skin_tools.region import exact_tie
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.region.connectivity import build_vertex_adjacency
from ad_skin_tools.ui import skin_operations


SMOOTH_ITERATIONS = 5
RELAXATION = 0.5
MAXIMUM_INFLUENCES = 5
WEIGHT_EPSILON = 1e-12
STORED_WEIGHT_TOLERANCE = 1e-10


for module in (
    exact_tie,
    closed_loop_opposite_guard,
    ambiguous_loop_distance_tiebreak,
    smoothing_options,
    cutoff_projection,
    final_constraints,
    smoothing_solver,
    region_solver,
):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v7.3 visual smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()
    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError("Add at least two joints to the AD Skin Tool list.")
    return state["mesh_transform"], joints


def _solve_final_v32_blocking(mesh, joints):
    """Run the existing v3.2 blocking pipeline once and return its final owners."""

    region_result = region_solver.solve_region_ownership(mesh=mesh, joints=joints)
    guarded_result = closed_loop_opposite_guard.solve_closed_loop_opposite_guard(
        region_result
    )
    blocking_result = (
        ambiguous_loop_distance_tiebreak.solve_ambiguous_loop_distance_tiebreak(
            region_result,
            guarded_result,
        )
    )
    final_owners = np.asarray(
        blocking_result.corrected_owner_indices,
        dtype=np.int32,
    ).copy()
    return region_result, guarded_result, blocking_result, final_owners


def _weights_in_skin_order(adapter, region_result, region_weights):
    skin_influences = tuple(adapter.influences())
    skin_column_by_joint = {
        joint: column for column, joint in enumerate(skin_influences)
    }
    missing = [
        joint
        for joint in region_result.influences
        if joint not in skin_column_by_joint
    ]
    if missing:
        raise RuntimeError(
            "Created skinCluster is missing Region influences:\n{}".format(
                "\n".join(missing)
            )
        )

    ordered = np.zeros(
        (region_result.vertex_count, len(skin_influences)),
        dtype=np.float64,
    )
    for region_column, joint in enumerate(region_result.influences):
        ordered[:, skin_column_by_joint[joint]] = region_weights[:, region_column]
    return ordered


def _validate_stored_weights(adapter, expected, maximum_influences):
    vertex_ids = np.arange(expected.shape[0], dtype=np.int32)
    stored = adapter.get_weights(vertex_ids)
    actual = np.asarray(stored.weights, dtype=np.float64)

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
            "Maya stored weights differ from the v7.3 matrix. "
            "Maximum difference: {}. First vertex IDs: {}".format(
                maximum_difference,
                bad.tolist(),
            )
        )

    row_sums = np.sum(actual, axis=1, dtype=np.float64)
    maximum_row_sum_error = float(np.max(np.abs(row_sums - 1.0)))
    active_counts = np.count_nonzero(actual > WEIGHT_EPSILON, axis=1)
    maximum_active = int(np.max(active_counts))
    if maximum_active > int(maximum_influences):
        bad = np.where(active_counts > int(maximum_influences))[0][:20]
        raise RuntimeError(
            "Stored weights exceed Max Influences. First vertex IDs: {}".format(
                bad.tolist()
            )
        )

    return maximum_difference, maximum_row_sum_error, maximum_active


def run():
    mesh, joints = _loaded_unskinned_context()
    (
        region_result,
        guarded_result,
        blocking_result,
        final_owners,
    ) = _solve_final_v32_blocking(mesh, joints)

    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    solve_options = smoothing_options.BindSmoothingOptions(
        iterations=SMOOTH_ITERATIONS,
        relaxation=RELAXATION,
        maximum_influences=MAXIMUM_INFLUENCES,
        weight_epsilon=WEIGHT_EPSILON,
    )
    result = smoothing_solver.solve_bind_smoothing(
        owner_indices=final_owners,
        adjacency=adjacency,
        vertex_positions=region_result.vertex_positions,
        influence_positions=region_result.influence_positions,
        options=solve_options,
    )

    if not np.array_equal(result.blocking_owner_indices, final_owners):
        raise RuntimeError("Smoothing modified the final v3.2 blocking owner map.")

    adapter = None
    try:
        with undo_chunk("AD Skin Tool v7.3 Smoothing Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=result.effective_maximum_influences,
            )
            skin_weights = _weights_in_skin_order(
                adapter,
                region_result,
                result.weights,
            )
            vertex_ids = np.arange(region_result.vertex_count, dtype=np.int32)
            adapter.set_weights(vertex_ids, skin_weights, normalize=False)
            (
                maximum_difference,
                stored_row_sum_error,
                stored_maximum_active,
            ) = _validate_stored_weights(
                adapter,
                skin_weights,
                result.effective_maximum_influences,
            )
    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            try:
                cmds.delete(adapter.skin_cluster)
            except Exception:
                pass
        raise

    builtins.AD_SKIN_V73_REGION_RESULT = region_result
    builtins.AD_SKIN_V73_GUARDED_RESULT = guarded_result
    builtins.AD_SKIN_V73_BLOCKING_RESULT = blocking_result
    builtins.AD_SKIN_V73_FINAL_OWNERS = final_owners
    builtins.AD_SKIN_V73_RESULT = result
    builtins.AD_SKIN_V73_SKIN_CLUSTER = adapter.skin_cluster

    cmds.select(region_result.mesh_transform, replace=True)

    projection = result.projection_result
    owner_projection = result.owner_maximum_result
    diffusion = result.diffusion_result
    tie_result = region_result.exact_tie_result

    print("\n[AD Skin Tool v7.3 - Final Blocking to Smoothing]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("v3.2 exact-tie vertices:", tie_result.exact_tie_vertex_count)
    print("v3.2 exact-tie components:", tie_result.component_count)
    print("Final blocking owner rows:", final_owners.size)
    print("Smooth iterations:", result.options.iterations)
    print("Relaxation:", result.options.relaxation)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Diffusion changed vertices:", diffusion.changed_vertex_count)
    print("Diffusion mixed vertices:", diffusion.mixed_vertex_count)
    print("Geometry-pruned vertices:", projection.pruned_vertex_count)
    print("Equal-weight cutoff rows:", len(projection.cutoff_weight_tie_vertex_ids))
    print("Resolved by distance:", len(projection.distance_resolved_vertex_ids))
    print(
        "Resolved by spatial canonical:",
        len(projection.spatial_canonical_resolved_vertex_ids),
    )
    print(
        "Unresolved coincident cutoff rows:",
        len(projection.unresolved_coincident_vertex_ids),
    )
    print("Owner below maximum before:", len(owner_projection.owner_below_maximum_before))
    print("Owner-max projected rows:", owner_projection.projected_vertex_count)
    print("Owner below maximum after:", len(owner_projection.owner_below_maximum_after))
    print("Final active influence histogram:", result.validation_result.active_influence_histogram)
    print("Final maximum row-sum error:", result.validation_result.maximum_row_sum_error)
    print("Stored maximum active influences:", stored_maximum_active)
    print("Stored maximum weight difference:", maximum_difference)
    print("Stored maximum row-sum error:", stored_row_sum_error)
    print("SkinCluster:", adapter.skin_cluster)
    print(
        "\nThe v3.2 final blocking owner map was passed unchanged into smoothing. "
        "No Region ownership logic exists inside bind_smoothing."
    )
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
