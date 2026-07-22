"""v10.1 live smoke test for Region Research Stage 03.

The production Bind Skin button is not changed. This module expects the real
Stage 01 smoke-test skinCluster to already exist, runs Stage 02 and Stage 03 from
the stored Stage 01 result, then replaces the current hard weights with the Stage
03 proposal owner map so the correction can be inspected directly in Maya.

Use ``restore_stage_01()`` to return to the Stage 01 hard-weight result.
"""

import builtins

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region_research import runner as region_runner
from ad_skin_tools.ui import skin_operations


STORED_WEIGHT_TOLERANCE = 1e-10
STAGE_01_RESULT_SLOT = "AD_SKIN_V101_STAGE01_RESULT"
STAGE_03_RESULT_SLOT = "AD_SKIN_V101_STAGE03_RESULT"
STAGE_03_SKIN_CLUSTER_SLOT = "AD_SKIN_V101_STAGE03_SKIN_CLUSTER"


def _loaded_stage_01_context():
    """Return the active UI, skin adapter, and stored v10.1 Stage 01 result."""

    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v10.1 Stage 03 smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_loaded_mesh()

    state = tool_window._STATE
    if not state.get("has_skin_cluster"):
        raise RuntimeError(
            "Stage 03 live testing requires the Stage 01 smoke-test skinCluster.\n\n"
            "Run ad_skin_tools.region_research.smoke_test_v101.run() first."
        )

    stage_01 = getattr(builtins, STAGE_01_RESULT_SLOT, None)
    if stage_01 is None:
        raise RuntimeError(
            "The v10.1 Stage 01 result is not stored in this Maya session.\n\n"
            "Run ad_skin_tools.region_research.smoke_test_v101.run() first."
        )

    mesh_shape = state.get("mesh_shape")
    mesh_transform = state.get("mesh_transform")
    if not mesh_shape or not mesh_transform:
        raise RuntimeError("AD Skin Tool does not currently contain a loaded mesh.")

    if stage_01.context.mesh_shape != mesh_shape:
        raise RuntimeError(
            "The loaded UI mesh differs from the stored Stage 01 smoke-test mesh."
        )

    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    return tool_window, adapter, stage_01


def _hard_weights_in_skin_order(adapter, stage_01, owner_indices):
    """Convert one research owner array into the active skin influence order."""

    owners = np.asarray(owner_indices, dtype=np.int32)
    if owners.shape != (stage_01.vertex_count,):
        raise RuntimeError(
            "Research owner array shape does not match the mesh vertex count."
        )
    if np.any(owners < 0):
        bad_ids = np.where(owners < 0)[0].astype(np.int32).tolist()
        raise RuntimeError(
            "Research ownership contains unassigned vertices. First IDs: {}"
            .format(bad_ids[:20])
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
            "The active skinCluster is missing Region Research influences:\n{}"
            .format("\n".join(missing))
        )

    research_to_skin_column = np.asarray(
        [skin_column_by_joint[joint] for joint in stage_01.context.influences],
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
    """Verify the hard owner matrix actually stored by Maya."""

    vertex_ids = np.arange(expected_weights.shape[0], dtype=np.int32)
    actual = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )

    if actual.shape != expected_weights.shape:
        raise RuntimeError(
            "Stored Maya weight shape differs from the expected Stage 03 matrix: "
            "{} != {}".format(actual.shape, expected_weights.shape)
        )

    maximum_difference = float(np.max(np.abs(actual - expected_weights)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        raise RuntimeError(
            "Maya stored weights differ from Stage 03. Maximum difference: {}"
            .format(maximum_difference)
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad_ids = np.where(actual_columns != expected_columns)[0]
        raise RuntimeError(
            "Stored hard owners differ from Stage 03. First vertex IDs: {}"
            .format(bad_ids[:20].astype(np.int32).tolist())
        )

    row_sums = np.sum(actual, axis=1)
    if np.any(np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE):
        bad_ids = np.where(
            np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE
        )[0]
        raise RuntimeError(
            "Stored Stage 03 rows are not normalized. First vertex IDs: {}"
            .format(bad_ids[:20].astype(np.int32).tolist())
        )

    active_counts = np.count_nonzero(actual > STORED_WEIGHT_TOLERANCE, axis=1)
    if np.any(active_counts != 1):
        bad_ids = np.where(active_counts != 1)[0]
        raise RuntimeError(
            "Stored Stage 03 rows are not hard one-owner weights. First vertex "
            "IDs: {}".format(bad_ids[:20].astype(np.int32).tolist())
        )

    return maximum_difference


def _write_owner_map(adapter, stage_01, owner_indices, undo_name):
    """Write and validate one complete hard owner map on the active skinCluster."""

    vertex_ids, weights, expected_columns = _hard_weights_in_skin_order(
        adapter,
        stage_01,
        owner_indices,
    )

    previous_weights = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    ).copy()

    try:
        with undo_chunk(undo_name):
            adapter.set_weights(vertex_ids, weights, normalize=False)
            maximum_difference = _validate_stored_weights(
                adapter,
                weights,
                expected_columns,
            )
    except Exception:
        try:
            adapter.set_weights(vertex_ids, previous_weights, normalize=False)
        except Exception:
            pass
        raise

    return maximum_difference


def run():
    """Run Stage 02/03 and show the Stage 03 owner map as real skin weights."""

    tool_window, adapter, stage_01 = _loaded_stage_01_context()

    wait_cursor_active = False
    try:
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        # Stage 02 is diagnostic and cheap. Rebuilding it from the stored Stage 01
        # result avoids accidentally using stale boundary data from another test.
        stage_02 = region_runner.run_stage_02_from_stage_01(stage_01)
        stage_03 = region_runner.run_stage_03_from_stage_02(stage_02)

        maximum_difference = _write_owner_map(
            adapter=adapter,
            stage_01=stage_01,
            owner_indices=stage_03.proposed_owner_indices,
            undo_name="AD Skin Tool v10.1 Stage 03 Smoke Weights",
        )

        setattr(builtins, STAGE_03_RESULT_SLOT, stage_03)
        setattr(builtins, STAGE_03_SKIN_CLUSTER_SLOT, adapter.skin_cluster)

        cmds.select(stage_01.context.mesh_transform, replace=True)
        tool_window._info(
            "v10.1 Stage 03 live weights: {} vertices changed from Stage 01."
            .format(stage_03.changed_vertex_count)
        )

        print("\n[AD Skin Tool v10.1 - Region Research Stage 03 Live Weights]")
        print("Mesh:", stage_01.context.mesh_transform)
        print("SkinCluster:", adapter.skin_cluster)
        print("Stage 03 reassignment proposals:", stage_03.proposal_count)
        print("Preserved source-owner regions:", stage_03.preserved_region_count)
        print("Changed vertices from Stage 01:", stage_03.changed_vertex_count)
        print("Stored maximum weight difference:", maximum_difference)
        print("Every vertex stores exactly one influence at weight 1.0.")
        print(
            "Run smoke_test_v101_stage03.restore_stage_01() to return to "
            "the Stage 01 weights."
        )

        return stage_03
    finally:
        if wait_cursor_active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass


def restore_stage_01():
    """Restore the real skinCluster to the stored v10.1 Stage 01 owner map."""

    tool_window, adapter, stage_01 = _loaded_stage_01_context()
    maximum_difference = _write_owner_map(
        adapter=adapter,
        stage_01=stage_01,
        owner_indices=stage_01.nearest.owner_indices,
        undo_name="AD Skin Tool v10.1 Restore Stage 01 Weights",
    )

    cmds.select(stage_01.context.mesh_transform, replace=True)
    tool_window._info("Restored v10.1 Stage 01 hard weights.")

    print("\n[AD Skin Tool v10.1 - Stage 01 Weights Restored]")
    print("Mesh:", stage_01.context.mesh_transform)
    print("SkinCluster:", adapter.skin_cluster)
    print("Stored maximum weight difference:", maximum_difference)

    return stage_01
