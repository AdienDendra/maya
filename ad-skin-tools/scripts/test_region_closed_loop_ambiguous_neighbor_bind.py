"""v3.10G visual bind: closed-loop + facing + ambiguous neighbour assignment."""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import ambiguous_neighbor_resolution
from ad_skin_tools.region import closed_loop_consensus
from ad_skin_tools.region import closed_loop_facing_resolution
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


STORED_WEIGHT_TOLERANCE = 1e-10
PRINT_VERTEX_LIMIT = 40


for module in (
    closed_loop_consensus,
    closed_loop_facing_resolution,
    ambiguous_neighbor_resolution,
    region_solver,
):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10G visual smoke test."
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
            "Maya stored weights differ from v3.10G. Maximum difference: {}".format(
                maximum_difference
            )
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad = np.where(actual_columns != expected_columns)[0][:20]
        raise RuntimeError(
            "Stored hard owners differ from v3.10G. First IDs: {}".format(
                bad.tolist()
            )
        )
    return maximum_difference


def _print_report(region_result, consensus_result, facing_result, neighbour_result):
    print("\n[AD Skin Tool v3.10G - Ambiguous Neighbour Visual Bind]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Closed Maya edge loops:", consensus_result.closed_loop_count)
    print("v3.10D applied loops:", consensus_result.applied_loop_count)
    print("v3.10D changed owner vertices:", consensus_result.changed_vertex_count)
    print("Facing resolution passes:", facing_result.resolution_pass_count)
    print(
        "Ambiguous vertices before neighbour assignment:",
        len(facing_result.final_ambiguous_vertex_ids),
    )
    print("Ambiguous neighbour assignments:", neighbour_result.assignment_count)
    print("Vertices assigned to boundary neighbour:", neighbour_result.assigned_vertex_count)

    for assignment in neighbour_result.assignments:
        source = _short_name(
            region_result.influences[int(assignment.source_owner_index)]
        )
        target = _short_name(
            region_result.influences[int(assignment.target_owner_index)]
        )
        print(
            "  {} -> {} | region={} | vertices={} | boundary edges={} | IDs={}".format(
                source,
                target,
                assignment.source_region_index,
                assignment.vertex_count,
                assignment.boundary_edge_count,
                list(assignment.vertex_ids),
            )
        )

    print(
        "Ambiguous regions preserved due to zero/multiple neighbours:",
        neighbour_result.preserved_region_count,
    )
    for preserved in neighbour_result.preserved_regions:
        source = _short_name(
            region_result.influences[int(preserved.source_owner_index)]
        )
        neighbours = [
            _short_name(region_result.influences[int(index)])
            for index in preserved.neighbouring_owner_indices
        ]
        print(
            "  {} | region={} | vertices={} | neighbours={} | IDs={}".format(
                source,
                preserved.source_region_index,
                preserved.vertex_count,
                neighbours,
                list(preserved.vertex_ids),
            )
        )

    print(
        "Final detached vertices:",
        len(neighbour_result.final_detached_vertex_ids),
    )
    print(
        "Final ambiguous vertices:",
        len(neighbour_result.final_ambiguous_vertex_ids),
    )
    if neighbour_result.final_ambiguous_vertex_ids:
        print(
            "  final ambiguous IDs:",
            list(neighbour_result.final_ambiguous_vertex_ids[:PRINT_VERTEX_LIMIT]),
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
    neighbour_result = (
        ambiguous_neighbor_resolution.resolve_ambiguous_regions_to_boundary_neighbour(
            region_result,
            facing_result,
        )
    )

    builtins.AD_SKIN_V310G_REGION_RESULT = region_result
    builtins.AD_SKIN_V310G_CONSENSUS_RESULT = consensus_result
    builtins.AD_SKIN_V310G_FACING_RESULT = facing_result
    builtins.AD_SKIN_V310G_NEIGHBOUR_RESULT = neighbour_result

    _print_report(
        region_result,
        consensus_result,
        facing_result,
        neighbour_result,
    )

    adapter = None
    try:
        with undo_chunk("AD Skin Tool v3.10G Ambiguous Neighbour Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=1,
            )
            vertex_ids, weights, expected_columns = _weights_in_skin_order(
                adapter,
                region_result,
                neighbour_result.final_owner_indices,
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

    builtins.AD_SKIN_V310G_SKIN_CLUSTER = adapter.skin_cluster
    cmds.select(region_result.mesh_transform, replace=True)

    print("SkinCluster:", adapter.skin_cluster)
    print("Stored maximum weight difference:", maximum_difference)
    if neighbour_result.final_detached_vertex_ids:
        print(
            "Warning: final detached vertices remain, but Bind Skin was not blocked."
        )
    if neighbour_result.final_ambiguous_vertex_ids:
        print(
            "Warning: unresolved ambiguous vertices retained their current owner; "
            "Bind Skin was not blocked."
        )
    print(
        "Open Paint Skin Weights Tool and inspect pelvis_02_BND plus the left/right "
        "upperLegRibbon_002 influences."
    )
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
