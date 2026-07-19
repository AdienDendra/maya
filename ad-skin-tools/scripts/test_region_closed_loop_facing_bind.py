"""v3.10F visual smoke: v3.10D closed loops followed by facing resolution."""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import closed_loop_consensus
from ad_skin_tools.region import closed_loop_facing_resolution
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


STORED_WEIGHT_TOLERANCE = 1e-10
PRINT_VERTEX_LIMIT = 40


for module in (
    closed_loop_consensus,
    closed_loop_facing_resolution,
    region_solver,
):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10F visual smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()
    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError("Add at least two joints to the AD Skin Tool list.")
    return state["mesh_transform"], joints


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
            "Maya stored weights differ from v3.10F. Maximum difference: {}".format(
                maximum_difference
            )
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad = np.where(actual_columns != expected_columns)[0][:20]
        raise RuntimeError(
            "Stored hard owners differ from v3.10F. First IDs: {}".format(
                bad.tolist()
            )
        )
    return maximum_difference


def _print_resolution_report(region_result, consensus_result, facing_result):
    print("\n[AD Skin Tool v3.10F - Closed Loop + Facing Resolution]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Closed Maya edge loops:", consensus_result.closed_loop_count)
    print("v3.10D applied loops:", consensus_result.applied_loop_count)
    print("v3.10D changed owner vertices:", consensus_result.changed_vertex_count)
    print("Facing resolution passes:", facing_result.resolution_pass_count)

    for pass_result in facing_result.passes:
        print(
            "  pass {} | regions={} | primary={} | co-primary={} | "
            "detached={} | ambiguous={}".format(
                pass_result.pass_index,
                pass_result.connected_region_count,
                pass_result.primary_region_count,
                pass_result.co_primary_region_count,
                pass_result.detached_vertex_count,
                pass_result.ambiguous_vertex_count,
            )
        )

    print(
        "Vertices reassigned by post-loop facing:",
        facing_result.reassigned_vertex_count,
    )
    if facing_result.reassigned_vertex_ids:
        print(
            "  first reassigned IDs:",
            list(facing_result.reassigned_vertex_ids[:PRINT_VERTEX_LIMIT]),
        )

    print(
        "Final detached vertices:",
        len(facing_result.final_detached_vertex_ids),
    )
    print(
        "Final ambiguous vertices:",
        len(facing_result.final_ambiguous_vertex_ids),
    )
    if facing_result.final_ambiguous_vertex_ids:
        print(
            "  ambiguous IDs:",
            list(
                facing_result.final_ambiguous_vertex_ids[:PRINT_VERTEX_LIMIT]
            ),
        )


def _select_unresolved_vertices(mesh_transform, facing_result):
    vertex_ids = sorted(
        set(facing_result.final_detached_vertex_ids)
        | set(facing_result.final_ambiguous_vertex_ids)
    )
    cmds.select(clear=True)
    if vertex_ids:
        cmds.select(
            [
                "{}.vtx[{}]".format(mesh_transform, int(vertex_id))
                for vertex_id in vertex_ids
            ],
            replace=True,
        )


def run():
    mesh, joints = _loaded_unskinned_context()
    region_result = region_solver.solve_region_ownership(mesh=mesh, joints=joints)
    consensus_result = closed_loop_consensus.solve_closed_loop_consensus(
        region_result
    )
    facing_result = closed_loop_facing_resolution.resolve_closed_loop_facing(
        region_result,
        consensus_result,
    )

    builtins.AD_SKIN_V310F_REGION_RESULT = region_result
    builtins.AD_SKIN_V310F_CONSENSUS_RESULT = consensus_result
    builtins.AD_SKIN_V310F_FACING_RESULT = facing_result

    _print_resolution_report(region_result, consensus_result, facing_result)

    if not facing_result.is_resolved:
        _select_unresolved_vertices(region_result.mesh_transform, facing_result)
        print("No skinCluster was created because post-loop Region is unresolved.")
        raise RuntimeError(
            "v3.10F stopped before Bind Skin. Resolve or inspect the selected "
            "ambiguous Region vertices first."
        )

    adapter = None
    try:
        with undo_chunk("AD Skin Tool v3.10F Closed Loop Facing Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=1,
            )
            vertex_ids, weights, expected_columns = _weights_in_skin_order(
                adapter,
                region_result,
                facing_result.final_owner_indices,
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

    builtins.AD_SKIN_V310F_SKIN_CLUSTER = adapter.skin_cluster
    cmds.select(region_result.mesh_transform, replace=True)

    print("SkinCluster:", adapter.skin_cluster)
    print("Stored maximum weight difference:", maximum_difference)
    print(
        "The mesh was bound only after closed-loop ownership passed the "
        "connectivity/facing resolution. Open Paint Skin Weights Tool for "
        "color feedback."
    )
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
