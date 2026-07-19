"""v3.10D visual smoke for closed-loop Region owner consensus."""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import closed_loop_consensus
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


PRINT_LOOP_LIMIT = 30
STORED_WEIGHT_TOLERANCE = 1e-10


for module in (closed_loop_consensus, region_solver):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10D visual smoke test."
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
            "Maya stored weights differ from v3.10D. Maximum difference: {}".format(
                maximum_difference
            )
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad = np.where(actual_columns != expected_columns)[0][:20]
        raise RuntimeError(
            "Stored hard owners differ from v3.10D. First IDs: {}".format(
                bad.tolist()
            )
        )
    return maximum_difference


def _print_applied_loops(region_result, consensus_result):
    print("\nApplied closed-loop proposals:")
    if not consensus_result.applied_loop_indices:
        print("  None")
        return

    for loop_index in consensus_result.applied_loop_indices[:PRINT_LOOP_LIMIT]:
        diagnostic = consensus_result.diagnostics[int(loop_index)]
        owner_labels = [
            "{}={}".format(
                _short_name(region_result.influences[int(owner_index)]),
                diagnostic.owner_counts[position],
            )
            for position, owner_index in enumerate(diagnostic.owner_indices)
        ]
        cost_labels = [
            "{}={:.8f}".format(
                _short_name(region_result.influences[int(owner_index)]),
                diagnostic.aggregate_squared_costs[position],
            )
            for position, owner_index in enumerate(diagnostic.owner_indices)
        ]
        print(
            "  loop {} | vertices={} | owners: {} | costs: {} | winner={} | changed={}".format(
                int(loop_index),
                diagnostic.vertex_count,
                ", ".join(owner_labels),
                ", ".join(cost_labels),
                _short_name(
                    region_result.influences[
                        int(diagnostic.proposed_owner_index)
                    ]
                ),
                diagnostic.changed_vertex_count,
            )
        )


def run():
    mesh, joints = _loaded_unskinned_context()
    region_result = region_solver.solve_region_ownership(mesh=mesh, joints=joints)
    consensus_result = closed_loop_consensus.solve_closed_loop_consensus(
        region_result
    )
    validation_result = closed_loop_consensus.validate_corrected_owner_map(
        region_result,
        consensus_result.corrected_owner_indices,
    )

    adapter = None
    try:
        with undo_chunk("AD Skin Tool v3.10D Closed Loop Consensus Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=1,
            )
            vertex_ids, weights, expected_columns = _weights_in_skin_order(
                adapter,
                region_result,
                consensus_result.corrected_owner_indices,
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

    builtins.AD_SKIN_V310D_REGION_RESULT = region_result
    builtins.AD_SKIN_V310D_CONSENSUS_RESULT = consensus_result
    builtins.AD_SKIN_V310D_VALIDATION_RESULT = validation_result
    builtins.AD_SKIN_V310D_SKIN_CLUSTER = adapter.skin_cluster

    cmds.select(region_result.mesh_transform, replace=True)

    print("\n[AD Skin Tool v3.10D - Closed Loop Consensus Visual Bind]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Closed Maya edge loops:", consensus_result.closed_loop_count)
    print("Open/non-cycle loops ignored:", consensus_result.open_loop_count)
    print(
        "Single-owner closed loops:",
        _count(consensus_result, closed_loop_consensus.SINGLE_OWNER),
    )
    print(
        "Two-owner loop proposals:",
        _count(consensus_result, closed_loop_consensus.TWO_OWNER_PROPOSAL),
    )
    print(
        "Multi-owner loops ignored:",
        _count(consensus_result, closed_loop_consensus.MULTI_OWNER_IGNORED),
    )
    print(
        "Exact aggregate-cost ties:",
        _count(consensus_result, closed_loop_consensus.EXACT_COST_TIE),
    )
    print("Applied loops:", consensus_result.applied_loop_count)
    print("Conflict loops skipped:", consensus_result.conflict_loop_count)
    print("Conflicting vertices:", len(consensus_result.conflicting_vertex_ids))
    print("Changed owner vertices:", consensus_result.changed_vertex_count)
    print("Validation connected regions:", validation_result.connected_region_count)
    print("Validation detached vertices:", validation_result.detached_vertex_count)
    print("Validation ambiguous vertices:", validation_result.ambiguous_vertex_count)
    print("Stored maximum weight difference:", maximum_difference)
    _print_applied_loops(region_result, consensus_result)

    print(
        "\nThe mesh is bound with the corrected hard owner map. Open Paint Skin "
        "Weights Tool and inspect the same joints through color feedback."
    )
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
