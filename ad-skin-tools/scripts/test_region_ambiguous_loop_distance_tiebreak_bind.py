"""v3.10K visual bind: v3.10J plus ambiguous loop distance tie-break."""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core import opposite_axis
from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import ambiguous_loop_distance_tiebreak
from ad_skin_tools.region import closed_loop_opposite_guard
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


STORED_WEIGHT_TOLERANCE = 1e-10


for module in (
    opposite_axis,
    closed_loop_opposite_guard,
    ambiguous_loop_distance_tiebreak,
    region_solver,
):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10K visual smoke test."
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
            "Maya stored weights differ from v3.10K. Maximum difference: {}".format(
                maximum_difference
            )
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad = np.where(actual_columns != expected_columns)[0][:20]
        raise RuntimeError(
            "Stored hard owners differ from v3.10K. First IDs: {}".format(
                bad.tolist()
            )
        )
    return maximum_difference


def _print_ambiguous_diagnostics(region_result, result):
    print("\nAmbiguous island diagnostics:")
    if not result.diagnostics:
        print("  None")
        return

    for diagnostic in result.diagnostics:
        source = _short_name(
            region_result.influences[int(diagnostic.source_owner_index)]
        )
        target = "preserved"
        if diagnostic.target_owner_index >= 0:
            target = _short_name(
                region_result.influences[int(diagnostic.target_owner_index)]
            )

        print(
            "  {} | region={} | vertices={} | classification={} | target={} | "
            "IDs={}".format(
                source,
                diagnostic.source_region_index,
                diagnostic.vertex_count,
                diagnostic.classification,
                target,
                list(diagnostic.vertex_ids),
            )
        )
        for candidate in diagnostic.neighbour_candidates:
            neighbour = _short_name(
                region_result.influences[int(candidate.owner_index)]
            )
            print(
                "    neighbour={} | boundary edges={} | loop-supported edges={} | "
                "closed loops={} | island squared cost={:.8f}".format(
                    neighbour,
                    candidate.boundary_edge_count,
                    candidate.loop_supported_edge_count,
                    list(candidate.supporting_loop_indices),
                    candidate.aggregate_squared_distance,
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
    tiebreak_result = (
        ambiguous_loop_distance_tiebreak.solve_ambiguous_loop_distance_tiebreak(
            region_result,
            guarded_result,
        )
    )

    adapter = None
    try:
        with undo_chunk("AD Skin Tool v3.10K Ambiguous Loop Distance Tie-Break"):
            adapter = create_closest_skin_cluster(
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=1,
            )
            vertex_ids, weights, expected_columns = _weights_in_skin_order(
                adapter,
                region_result,
                tiebreak_result.corrected_owner_indices,
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

    builtins.AD_SKIN_V310K_REGION_RESULT = region_result
    builtins.AD_SKIN_V310K_GUARDED_RESULT = guarded_result
    builtins.AD_SKIN_V310K_TIEBREAK_RESULT = tiebreak_result
    builtins.AD_SKIN_V310K_SKIN_CLUSTER = adapter.skin_cluster

    cmds.select(region_result.mesh_transform, replace=True)

    print("\n[AD Skin Tool v3.10K - Ambiguous Loop Distance Tie-Break Visual Bind]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Primary opposite axis:", guarded_result.axis_context.primary_axis)
    print("v3.10J applied loops:", guarded_result.applied_loop_count)
    print("v3.10J changed owner vertices:", guarded_result.changed_vertex_count)
    print("Ambiguous islands before assignment:", tiebreak_result.ambiguous_region_count)
    print("Assigned ambiguous islands:", tiebreak_result.assigned_region_count)
    print(
        "  assigned by unique loop support:",
        tiebreak_result.loop_support_assignment_count,
    )
    print(
        "  assigned by distance tie-break:",
        tiebreak_result.distance_tiebreak_assignment_count,
    )
    print(
        "Vertices assigned by ambiguous resolution:",
        tiebreak_result.changed_vertex_count,
    )
    print(
        "Final detached vertices:",
        tiebreak_result.final_validation.detached_vertex_count,
    )
    print(
        "Final ambiguous vertices:",
        tiebreak_result.final_validation.ambiguous_vertex_count,
    )
    if tiebreak_result.final_validation.ambiguous_vertex_ids:
        print(
            "  final ambiguous IDs:",
            list(tiebreak_result.final_validation.ambiguous_vertex_ids),
        )
    print("SkinCluster:", adapter.skin_cluster)
    print("Stored maximum weight difference:", maximum_difference)

    _print_ambiguous_diagnostics(region_result, tiebreak_result)

    print(
        "\nThe mesh is bound with v3.10J plus the replacement v3.10K rule: "
        "closed-loop support decides first; aggregate island distance is used "
        "only when the strongest loop-support score is tied."
    )
    print(
        "Inspect pelvis_02_BND and the left/right upperLegRibbon_003 influences "
        "through Paint Skin Weights."
    )
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
