"""v3.10E visual smoke for local closed-loop owner runs."""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import local_closed_loop_runs
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


PRINT_PROPOSAL_LIMIT = 50
STORED_WEIGHT_TOLERANCE = 1e-10


for module in (local_closed_loop_runs, region_solver):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10E visual smoke test."
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


def _proposal_count(result, kind):
    return sum(proposal.kind == kind for proposal in result.proposals)


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
            "Maya stored weights differ from v3.10E. Maximum difference: {}".format(
                maximum_difference
            )
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad = np.where(actual_columns != expected_columns)[0][:20]
        raise RuntimeError(
            "Stored hard owners differ from v3.10E. First IDs: {}".format(
                bad.tolist()
            )
        )
    return maximum_difference


def _print_applied_proposals(region_result, consensus_result):
    print("\nApplied proposals:")
    if not consensus_result.applied_proposal_indices:
        print("  None")
        return

    for proposal_index in consensus_result.applied_proposal_indices[
        :PRINT_PROPOSAL_LIMIT
    ]:
        proposal = consensus_result.proposals[int(proposal_index)]
        diagnostic = consensus_result.diagnostics[int(proposal.loop_index)]
        target = _short_name(
            region_result.influences[int(proposal.target_owner_index)]
        )

        if proposal.kind == local_closed_loop_runs.WHOLE_LOOP_PROPOSAL:
            owner_labels = [
                "{}={}".format(
                    _short_name(region_result.influences[int(owner_index)]),
                    diagnostic.owner_counts[position],
                )
                for position, owner_index in enumerate(
                    diagnostic.owner_indices
                )
            ]
            cost_labels = [
                "{}={:.8f}".format(
                    _short_name(region_result.influences[int(owner_index)]),
                    diagnostic.aggregate_squared_costs[position],
                )
                for position, owner_index in enumerate(
                    diagnostic.owner_indices
                )
            ]
            print(
                "  proposal {} | whole loop {} | vertices={} | owners: {} | "
                "costs: {} | winner={}".format(
                    int(proposal_index),
                    int(proposal.loop_index),
                    len(proposal.vertex_ids),
                    ", ".join(owner_labels),
                    ", ".join(cost_labels),
                    target,
                )
            )
        else:
            source = _short_name(
                region_result.influences[int(proposal.source_owner_index)]
            )
            print(
                "  proposal {} | local run | loop={} run={} | {} -> {} | "
                "vertices={} | IDs={}".format(
                    int(proposal_index),
                    int(proposal.loop_index),
                    int(proposal.run_index),
                    source,
                    target,
                    len(proposal.vertex_ids),
                    list(proposal.vertex_ids),
                )
            )


def run():
    mesh, joints = _loaded_unskinned_context()
    region_result = region_solver.solve_region_ownership(mesh=mesh, joints=joints)
    consensus_result = local_closed_loop_runs.solve_local_closed_loop_runs(
        region_result
    )
    validation_result = local_closed_loop_runs.validate_corrected_owner_map(
        region_result,
        consensus_result.corrected_owner_indices,
    )

    adapter = None
    try:
        with undo_chunk("AD Skin Tool v3.10E Local Closed Loop Runs Bind"):
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

    builtins.AD_SKIN_V310E_REGION_RESULT = region_result
    builtins.AD_SKIN_V310E_CONSENSUS_RESULT = consensus_result
    builtins.AD_SKIN_V310E_VALIDATION_RESULT = validation_result
    builtins.AD_SKIN_V310E_SKIN_CLUSTER = adapter.skin_cluster

    cmds.select(region_result.mesh_transform, replace=True)

    print("\n[AD Skin Tool v3.10E - Local Closed Loop Runs Visual Bind]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Closed Maya edge loops:", consensus_result.closed_loop_count)
    print("Open/non-cycle loops ignored:", consensus_result.open_loop_count)
    print(
        "Single-owner closed loops:",
        _count(consensus_result, local_closed_loop_runs.SINGLE_OWNER),
    )
    print(
        "Two-owner whole-loop cases:",
        _count(
            consensus_result,
            local_closed_loop_runs.TWO_OWNER_WHOLE_LOOP,
        ),
    )
    print(
        "Multi-owner loops with local A-B-A runs:",
        _count(
            consensus_result,
            local_closed_loop_runs.MULTI_OWNER_LOCAL_RUNS,
        ),
    )
    print(
        "Multi-owner loops without local runs:",
        _count(
            consensus_result,
            local_closed_loop_runs.MULTI_OWNER_NO_LOCAL_RUN,
        ),
    )
    print(
        "Exact aggregate-cost ties:",
        _count(consensus_result, local_closed_loop_runs.EXACT_COST_TIE),
    )
    print(
        "Whole-loop proposals:",
        _proposal_count(
            consensus_result,
            local_closed_loop_runs.WHOLE_LOOP_PROPOSAL,
        ),
    )
    print(
        "Local-run proposals:",
        _proposal_count(
            consensus_result,
            local_closed_loop_runs.LOCAL_RUN_PROPOSAL,
        ),
    )
    print("Applied proposals:", consensus_result.applied_proposal_count)
    print(
        "Conflict proposals skipped:",
        consensus_result.conflict_proposal_count,
    )
    print(
        "Conflicting vertices:",
        len(consensus_result.conflicting_vertex_ids),
    )
    print("Changed owner vertices:", consensus_result.changed_vertex_count)
    print("Validation connected regions:", validation_result.connected_region_count)
    print("Validation detached vertices:", validation_result.detached_vertex_count)
    print("Validation ambiguous vertices:", validation_result.ambiguous_vertex_count)
    print("Stored maximum weight difference:", maximum_difference)
    _print_applied_proposals(region_result, consensus_result)

    print(
        "\nThe mesh is bound with the v3.10E corrected hard owner map. "
        "Open Paint Skin Weights Tool and inspect pelvis_02_BND, "
        "L_leg_upperLegRibbon_002_BND, and R_leg_upperLegRibbon_002_BND."
    )
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
