"""Visual validation bind for the complete v10.4 hard ownership pipeline."""

import builtins

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region_research.closed_loop_ownership import (
    CONFLICT_PRESERVED,
    EXACT_COST_TIE_PRESERVED,
    MULTI_OWNER_PRESERVED,
    OPPOSITE_PAIR_PRESERVED,
    SINGLE_OWNER,
    TWO_OWNER_PROPOSAL,
)
from ad_skin_tools.region_research.ownership_pipeline import (
    solve_ownership_pipeline,
)
from ad_skin_tools.ui import global_owner_tag
from ad_skin_tools.ui import skin_operations


STORED_WEIGHT_TOLERANCE = 1e-10


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError("Open AD Skin Tool before running the visual bind.")

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()
    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError("Add at least two joints to the AD Skin Tool list.")

    mesh_shape = state.get("mesh_shape")
    mesh_transform = state.get("mesh_transform")
    if not mesh_shape or not mesh_transform:
        raise RuntimeError("AD Skin Tool does not currently contain a loaded mesh.")

    return (
        tool_window,
        mesh_shape,
        mesh_transform,
        joints,
        global_owner_tag.global_owner_joint(),
    )


def _hard_weights_in_skin_order(adapter, pipeline):
    closest = pipeline.closest_ownership
    owners = np.asarray(pipeline.final_owner_indices, dtype=np.int32)
    skin_influences = tuple(adapter.influences())
    skin_column_by_joint = {
        joint: column for column, joint in enumerate(skin_influences)
    }
    missing = [
        joint
        for joint in closest.context.influences
        if joint not in skin_column_by_joint
    ]
    if missing:
        raise RuntimeError(
            "Created skinCluster is missing ownership influences:\n{}".format(
                "\n".join(missing)
            )
        )

    ownership_to_skin = np.asarray(
        [skin_column_by_joint[joint] for joint in closest.context.influences],
        dtype=np.int32,
    )
    expected_columns = ownership_to_skin[owners]
    vertex_ids = np.arange(closest.vertex_count, dtype=np.int32)
    weights = np.zeros(
        (closest.vertex_count, len(skin_influences)),
        dtype=np.float64,
    )
    weights[vertex_ids, expected_columns] = 1.0
    return vertex_ids, weights, expected_columns


def _validate_stored_weights(adapter, expected_weights, expected_columns):
    vertex_ids = np.arange(expected_weights.shape[0], dtype=np.int32)
    actual = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )
    if actual.shape != expected_weights.shape:
        raise RuntimeError(
            "Stored weight shape differs from the final hard-weight matrix."
        )

    maximum_difference = float(np.max(np.abs(actual - expected_weights)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        raise RuntimeError(
            "Maya stored weights differ from v10.4. Maximum difference: {}"
            .format(maximum_difference)
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad_ids = np.where(actual_columns != expected_columns)[0]
        raise RuntimeError(
            "Stored hard owners differ from v10.4. First IDs: {}".format(
                bad_ids[:20].astype(np.int32).tolist()
            )
        )

    row_sums = np.sum(actual, axis=1)
    active_counts = np.count_nonzero(actual > STORED_WEIGHT_TOLERANCE, axis=1)
    if np.any(np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE):
        raise RuntimeError("One or more stored hard-weight rows are not normalized.")
    if np.any(active_counts != 1):
        raise RuntimeError("One or more vertices do not store exactly one owner.")
    return maximum_difference


def run():
    """Solve once, create one skinCluster, and write one final hard-owner map."""

    (
        tool_window,
        mesh_shape,
        mesh_transform,
        joints,
        tagged_joint,
    ) = _loaded_unskinned_context()

    wait_cursor_active = False
    adapter = None
    try:
        tool_window._set_bind_busy(True, "Solving final Region ownership...")
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        pipeline = solve_ownership_pipeline(
            mesh=mesh_transform,
            joints=joints,
            global_owner_joint=tagged_joint,
        )

        with undo_chunk("AD Skin Tool v10.4 Ownership Visual Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=list(pipeline.closest_ownership.context.influences),
                max_influences=1,
            )
            vertex_ids, weights, expected_columns = _hard_weights_in_skin_order(
                adapter,
                pipeline,
            )
            adapter.set_weights(vertex_ids, weights, normalize=False)
            maximum_difference = _validate_stored_weights(
                adapter,
                weights,
                expected_columns,
            )

        builtins.AD_SKIN_OWNERSHIP_PIPELINE_RESULT = pipeline
        builtins.AD_SKIN_OWNERSHIP_SKIN_CLUSTER = adapter.skin_cluster
        tool_window._sync_loaded_skin_context()
        cmds.select(mesh_transform, replace=True)
        _print_report(pipeline, adapter.skin_cluster, maximum_difference)
        tool_window._info(
            "v10.4 final hard ownership written to {}.".format(
                adapter.skin_cluster
            )
        )
        return pipeline

    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            try:
                cmds.delete(adapter.skin_cluster)
            except Exception:
                pass
        raise
    finally:
        if wait_cursor_active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass
        tool_window._set_bind_busy(False)


def _print_report(pipeline, skin_cluster, maximum_difference):
    closest = pipeline.closest_ownership
    nearest = closest.closest
    tie = nearest.tie_resolution
    global_assignment = pipeline.global_owner_assignment
    loops = pipeline.closed_loop_ownership

    print("\n[AD Skin Tool v10.4 - Final Hard Ownership Visual Bind]")
    print("Mesh:", closest.context.mesh_transform)
    print("Vertices:", closest.vertex_count)
    print("Influences:", closest.influence_count)

    print("\nClosest region ownership:")
    print("  exact-tie vertices:", nearest.exact_tie_vertex_count)
    print("  resolved by topology:", len(tie.resolved_by_topology_vertex_ids))
    print(
        "  resolved by fewer owned vertices:",
        len(tie.resolved_by_fewer_owned_vertices_vertex_ids),
    )
    print(
        "  resolved by stable joint key:",
        len(tie.resolved_by_stable_joint_key_vertex_ids),
    )
    print("  connected owner regions:", closest.total_region_count)
    print("  secondary regions:", closest.secondary_region_count)
    print("  secondary vertices:", len(closest.all_secondary_vertex_ids))
    print(
        "  ambiguous primary influences:",
        closest.ambiguous_primary_influence_count,
    )

    print("\nGlobal Owner:")
    print(
        "  joint:",
        global_assignment.global_owner_joint.split("|")[-1]
        if global_assignment.global_owner_enabled
        else "<none>",
    )
    if global_assignment.facing is None:
        print("  secondary facing: skipped")
        print("  detached vertices reassigned: 0")
    else:
        facing = global_assignment.facing
        print("  anchor vertices queried:", facing.anchor_vertex_count)
        print("  unique face normals queried:", facing.queried_face_count)
        print("  co-primary regions retained:", facing.co_primary_region_count)
        print("  detached regions:", facing.detached_region_count)
        print("  ambiguous regions retained:", facing.ambiguous_region_count)
        print(
            "  detached vertices reassigned:",
            global_assignment.reassigned_vertex_count,
        )
        print("  facing time:", round(facing.elapsed_seconds, 6))

    counts = {
        SINGLE_OWNER: 0,
        TWO_OWNER_PROPOSAL: 0,
        OPPOSITE_PAIR_PRESERVED: 0,
        MULTI_OWNER_PRESERVED: 0,
        EXACT_COST_TIE_PRESERVED: 0,
        CONFLICT_PRESERVED: 0,
    }
    for diagnostic in loops.diagnostics:
        counts[diagnostic.classification] = (
            counts.get(diagnostic.classification, 0) + 1
        )

    print("\nRelevant closed loops:")
    print("  total mesh edges:", closest.context.edge_count)
    print("  ownership boundary edges:", loops.boundary_edge_count)
    print("  Maya polySelect calls:", loops.maya_polyselect_call_count)
    print("  closed loops discovered:", loops.discovered_loop_count)
    print("  unresolved seeds:", len(loops.unresolved_seed_edge_ids))
    print("  open/non-simple loops:", len(loops.open_loop_seed_edge_ids))
    print("  single-owner loops:", counts[SINGLE_OWNER])
    print("  two-owner proposals:", counts[TWO_OWNER_PROPOSAL])
    print("  opposite pairs preserved:", counts[OPPOSITE_PAIR_PRESERVED])
    print("  multi-owner loops preserved:", counts[MULTI_OWNER_PRESERVED])
    print("  exact cost ties preserved:", counts[EXACT_COST_TIE_PRESERVED])
    print("  conflict loops preserved:", counts[CONFLICT_PRESERVED])
    print("  applied loops:", loops.applied_loop_count)
    print("  changed vertices:", loops.changed_vertex_count)
    print("  primary opposite axis:", loops.axis_context.primary_axis)
    print("  edge scan:", round(loops.edge_scan_seconds, 6))
    print("  loop queries:", round(loops.loop_query_seconds, 6))
    print("  consensus:", round(loops.consensus_seconds, 6))
    print("  closed-loop total:", round(loops.elapsed_seconds, 6))

    print("\nFinal:")
    print("  pipeline total:", round(pipeline.elapsed_seconds, 6))
    print("  SkinCluster:", skin_cluster)
    print("  stored maximum weight difference:", maximum_difference)
    print("  every vertex stores exactly one influence at weight 1.0")
    print("  undo once to remove this visual-test skinCluster")
