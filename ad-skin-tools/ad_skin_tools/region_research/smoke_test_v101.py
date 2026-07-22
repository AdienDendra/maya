"""v10.1 smoke test: bind the UI mesh from Region Research Stage 01 owners.

This module is deliberately separate from the production Bind Skin button. It reads
the currently loaded unskinned mesh and complete flat joint list from AD Skin Tool,
runs Region Research Stage 01, creates a real skinCluster, and writes one hard 1.0
owner per vertex so the Stage 01 result can be inspected directly in Maya.

Undo once to remove the smoke-test skinCluster.
"""

import builtins

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region_research import runner as region_runner
from ad_skin_tools.ui import skin_operations


STORED_WEIGHT_TOLERANCE = 1e-10


def _loaded_unskinned_context():
    """Read the mesh and full flat joint list already staged in AD Skin Tool."""

    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v10.1 Stage 01 smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()

    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError(
            "Add at least two joints to the AD Skin Tool list before running "
            "the v10.1 Stage 01 smoke test."
        )

    mesh_shape = state.get("mesh_shape")
    mesh_transform = state.get("mesh_transform")
    if not mesh_shape or not mesh_transform:
        raise RuntimeError("AD Skin Tool does not currently contain a loaded mesh.")

    return tool_window, mesh_shape, mesh_transform, joints


def _hard_weights_in_skin_order(adapter, stage_01):
    """Convert Stage 01 owner indices into a Maya skin-influence weight matrix."""

    owners = np.asarray(stage_01.nearest.owner_indices, dtype=np.int32)
    if owners.shape != (stage_01.vertex_count,):
        raise RuntimeError(
            "Stage 01 owner array shape does not match the mesh vertex count."
        )
    if np.any(owners < 0):
        bad_ids = np.where(owners < 0)[0].astype(np.int32).tolist()
        raise RuntimeError(
            "Stage 01 produced unassigned owners. First vertex IDs: {}".format(
                bad_ids[:20]
            )
        )

    skin_influences = tuple(adapter.influences())
    skin_column_by_joint = {
        joint: column for column, joint in enumerate(skin_influences)
    }

    missing = [
        joint
        for joint in stage_01.context.influences
        if joint not in skin_column_by_joint
    ]
    if missing:
        raise RuntimeError(
            "Created skinCluster is missing Stage 01 influences:\n{}".format(
                "\n".join(missing)
            )
        )

    research_to_skin_column = np.asarray(
        [
            skin_column_by_joint[joint]
            for joint in stage_01.context.influences
        ],
        dtype=np.int32,
    )
    owner_columns = research_to_skin_column[owners]
    vertex_ids = np.arange(stage_01.vertex_count, dtype=np.int32)

    weights = np.zeros(
        (stage_01.vertex_count, len(skin_influences)),
        dtype=np.float64,
    )
    weights[vertex_ids, owner_columns] = 1.0
    return vertex_ids, weights, owner_columns


def _validate_stored_weights(adapter, expected_weights, expected_columns):
    """Confirm Maya stored exactly the Stage 01 hard-owner matrix."""

    vertex_ids = np.arange(expected_weights.shape[0], dtype=np.int32)
    stored = adapter.get_weights(vertex_ids)
    actual = np.asarray(stored.weights, dtype=np.float64)

    if actual.shape != expected_weights.shape:
        raise RuntimeError(
            "Stored Maya weight shape differs from the expected Stage 01 matrix: "
            "{} != {}".format(actual.shape, expected_weights.shape)
        )

    maximum_difference = float(np.max(np.abs(actual - expected_weights)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        raise RuntimeError(
            "Maya stored weights differ from Stage 01. Maximum difference: {}"
            .format(maximum_difference)
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad_ids = np.where(actual_columns != expected_columns)[0]
        raise RuntimeError(
            "Stored hard owners differ from Stage 01. First vertex IDs: {}"
            .format(bad_ids[:20].astype(np.int32).tolist())
        )

    row_sums = np.sum(actual, axis=1)
    maximum_sum_difference = float(np.max(np.abs(row_sums - 1.0)))
    if maximum_sum_difference > STORED_WEIGHT_TOLERANCE:
        raise RuntimeError(
            "Stored Stage 01 weights are not normalized. Maximum row-sum "
            "difference: {}".format(maximum_sum_difference)
        )

    active_counts = np.count_nonzero(actual > STORED_WEIGHT_TOLERANCE, axis=1)
    if np.any(active_counts != 1):
        bad_ids = np.where(active_counts != 1)[0]
        raise RuntimeError(
            "Stored Stage 01 weights are not hard one-owner rows. First vertex "
            "IDs: {}".format(bad_ids[:20].astype(np.int32).tolist())
        )

    return maximum_difference


def run():
    """Create a real hard-weight skinCluster from the v10.1 Stage 01 owner map."""

    tool_window, mesh_shape, mesh_transform, joints = _loaded_unskinned_context()

    wait_cursor_active = False
    adapter = None
    try:
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        stage_01 = region_runner.run_stage_01(
            mesh=mesh_transform,
            joints=joints,
        )

        with undo_chunk("AD Skin Tool v10.1 Stage 01 Smoke Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=list(stage_01.context.influences),
                max_influences=1,
            )
            vertex_ids, weights, expected_columns = _hard_weights_in_skin_order(
                adapter,
                stage_01,
            )
            adapter.set_weights(vertex_ids, weights, normalize=False)
            maximum_difference = _validate_stored_weights(
                adapter,
                weights,
                expected_columns,
            )

        builtins.AD_SKIN_V101_STAGE01_RESULT = stage_01
        builtins.AD_SKIN_V101_STAGE01_SKIN_CLUSTER = adapter.skin_cluster

        # Refresh only the current UI state after the separate smoke-test operation.
        # The production Bind Skin callback remains untouched.
        tool_window._sync_loaded_skin_context()
        cmds.select(mesh_transform, replace=True)

        print("\n[AD Skin Tool v10.1 - Region Research Stage 01 Visual Bind]")
        print("Mesh:", stage_01.context.mesh_transform)
        print("Vertices:", stage_01.vertex_count)
        print("Influences:", stage_01.influence_count)
        print("Raw exact-tie vertices:", stage_01.nearest.exact_tie_vertex_count)
        print(
            "Remaining unassigned vertices:",
            stage_01.nearest.remaining_unassigned_vertex_count,
        )
        print("Connected owner regions:", stage_01.total_region_count)
        print("Secondary regions:", stage_01.secondary_region_count)
        print("SkinCluster:", adapter.skin_cluster)
        print("Stored maximum weight difference:", maximum_difference)
        print("Every vertex stores exactly one influence at weight 1.0.")
        print("Undo once to remove this v10.1 smoke-test skinCluster.")
        print("After undo, click Load Mesh once to refresh the AD Skin Tool UI state.")

        return stage_01

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
