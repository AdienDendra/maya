"""v3.10J visual bind for v3.10D plus opposite-joint guard."""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core import opposite_axis
from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import closed_loop_opposite_guard
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


PRINT_LOOP_LIMIT = 80
STORED_WEIGHT_TOLERANCE = 1e-10


for module in (opposite_axis, closed_loop_opposite_guard, region_solver):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10J visual smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()
    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError("Add at least two joints to the AD Skin Tool list.")
    return state["mesh_transform"], joints


def _short_name(path):
    return path.split("|")[-1]


def _count(result, classification):
    return sum(
        diagnostic.classification == classification
        for diagnostic in result.diagnostics
    )


def _weights_in_skin_order(adapter, region_result, owner_indices):
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

    region_to_skin_column = np.asarray(
        [skin_column_by_joint[joint] for joint in region_result.influences],
        dtype=np.int32,
    )
    owner_columns = region_to_skin_column[
        np.asarray(owner_indices, dtype=np.int32)
    ]
    vertex_ids = np.arange(region_result.vertex_count, dtype=np.int32)
    weights = np.zeros(
        (region_result.vertex_count, len(skin_influences)),
        dtype=np.float64,
    )
    weights[vertex_ids, owner_columns] = 1.0
    return vertex_ids, weights, owner_columns


def _validate_stored_weights(adapter, expected_weights, expected_columns):
    vertex_ids = np.arange(expected_weights.shape[0], dtype=np.int32)
    stored = adapter.get_weights(vertex_ids)
    actual = np.asarray(stored.weights, dtype=np.float64)
    maximum_difference = float(np.max(np.abs(actual - expected_weights)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        raise RuntimeError(
            "Maya stored weights differ from v3.10J. Maximum difference: {}".format(
                maximum_difference
            )
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad = np.where(actual_columns != expected_columns)[0][:20]
        raise RuntimeError(
            "Stored hard owners differ from v3.10J. First IDs: {}".format(
                bad.tolist()
            )
        )
    return maximum_difference


def _print_axis_context(region_result, context):
    print("\nOpposite-axis context:")
    print("  joint-set center:", context.center)
    print("  mirror tolerance:", context.tolerance)
    print("  primary axis:", context.primary_axis)

    for support in context.axis_supports:
        median_error = (
            support.median_mirror_error
            if np.isfinite(support.median_mirror_error)
            else "none"
        )
        print(
            "  axis {} | mutual pairs={} | median error={}".format(
                support.axis,
                support.pair_count,
                median_error,
            )
        )
        for pair in support.pairs:
            print(
                "    {} <-> {} | error={:.8f}".format(
                    _short_name(region_result.influences[pair.first_index]),
                    _short_name(region_result.influences[pair.second_index]),
                    pair.mirror_error,
                )
            )


def _print_two_owner_diagnostics(region_result, result):
    relevant = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.classification
        in {
            closed_loop_opposite_guard.TWO_OWNER_PROPOSAL,
            closed_loop_opposite_guard.OPPOSITE_PAIR_PRESERVED,
            closed_loop_opposite_guard.EXACT_COST_TIE,
        }
    ]

    print("\nTwo-owner loop diagnostics:")
    for diagnostic in relevant[:PRINT_LOOP_LIMIT]:
        owners = [
            _short_name(region_result.influences[int(index)])
            for index in diagnostic.owner_indices
        ]
        action = "preserved"
        if diagnostic.proposed_owner_index >= 0:
            action = _short_name(
                region_result.influences[
                    int(diagnostic.proposed_owner_index)
                ]
            )

        print(
            "  loop {} | {} | owners={} | counts={} | opposite_axis={} | "
            "action={}".format(
                diagnostic.loop_index,
                diagnostic.classification,
                owners,
                list(diagnostic.owner_counts),
                diagnostic.opposite_axis,
                action,
            )
        )


def run():
    mesh, joints = _loaded_unskinned_context()
    region_result = region_solver.solve_region_ownership(mesh=mesh, joints=joints)
    guarded_result = (
        closed_loop_opposite_guard.solve_closed_loop_opposite_guard(
            region_result
        )
    )

    adapter = None
    try:
        with undo_chunk("AD Skin Tool v3.10J Opposite Guard Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=1,
            )
            vertex_ids, weights, expected_columns = _weights_in_skin_order(
                adapter,
                region_result,
                guarded_result.corrected_owner_indices,
            )
            adapter.set_weights(vertex_ids, weights, normalize=False)
            maximum_difference = _validate_stored_weights(
                adapter,
                weights,
                expected_columns,
            )
    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            try:
                cmds.delete(adapter.skin_cluster)
            except Exception:
                pass
        raise

    builtins.AD_SKIN_V310J_REGION_RESULT = region_result
    builtins.AD_SKIN_V310J_GUARDED_RESULT = guarded_result
    builtins.AD_SKIN_V310J_SKIN_CLUSTER = adapter.skin_cluster

    cmds.select(region_result.mesh_transform, replace=True)

    print("\n[AD Skin Tool v3.10J - Closed Loop Opposite Guard Visual Bind]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Closed Maya edge loops:", guarded_result.closed_loop_count)
    print("Open/non-cycle loops ignored:", guarded_result.open_loop_count)
    print(
        "Single-owner loops:",
        _count(guarded_result, closed_loop_opposite_guard.SINGLE_OWNER),
    )
    print(
        "Standard v3.10D two-owner loops:",
        _count(
            guarded_result,
            closed_loop_opposite_guard.TWO_OWNER_PROPOSAL,
        ),
    )
    print(
        "Opposite-pair loops preserved:",
        _count(
            guarded_result,
            closed_loop_opposite_guard.OPPOSITE_PAIR_PRESERVED,
        ),
    )
    print(
        "Exact aggregate-cost ties preserved:",
        _count(guarded_result, closed_loop_opposite_guard.EXACT_COST_TIE),
    )
    print(
        "Multi-owner loops ignored:",
        _count(guarded_result, closed_loop_opposite_guard.MULTI_OWNER_IGNORED),
    )
    print("Applied v3.10D loops:", guarded_result.applied_loop_count)
    print("Conflict loops skipped:", guarded_result.conflict_loop_count)
    print("Changed owner vertices:", guarded_result.changed_vertex_count)
    print("SkinCluster:", adapter.skin_cluster)
    print("Stored maximum weight difference:", maximum_difference)

    _print_axis_context(region_result, guarded_result.axis_context)
    _print_two_owner_diagnostics(region_result, guarded_result)

    print(
        "\nThe mesh is bound with v3.10D logic plus the v3.10J "
        "opposite-joint guard only."
    )
    print(
        "Inspect L_leg_hip_BND, R_leg_hip_BND, and the ribbon influences "
        "through Paint Skin Weights."
    )
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
