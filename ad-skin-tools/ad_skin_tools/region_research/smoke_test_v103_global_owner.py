"""v10.3 visual smoke test for Stage 01 + optional Global Owner facing.

The production Bind Skin button is not changed. This test reads the loaded unskinned
mesh, complete UI joint list, and exclusive Global Owner tag. No tag produces the
exact Stage 01 hard-owner result. A tag runs optimized facing on secondary regions
and sends only detached secondary vertices to the tagged joint.
"""

import builtins

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region_research.global_owner_override import (
    apply_global_owner_override,
)
from ad_skin_tools.region_research import runner as region_runner
from ad_skin_tools.ui import global_owner_tag
from ad_skin_tools.ui import skin_operations


STORED_WEIGHT_TOLERANCE = 1e-10


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v10.3 smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()

    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError(
            "Add at least two joints to the AD Skin Tool list before running "
            "the v10.3 smoke test."
        )

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


def _hard_weights_in_skin_order(adapter, stage_01, owner_indices):
    owners = np.asarray(owner_indices, dtype=np.int32)
    if owners.shape != (stage_01.vertex_count,):
        raise RuntimeError(
            "Final owner array shape does not match the mesh vertex count."
        )
    if np.any(owners < 0):
        bad_ids = np.where(owners < 0)[0].astype(np.int32).tolist()
        raise RuntimeError(
            "Final ownership contains unassigned vertices. First IDs: {}".format(
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
            "Created skinCluster is missing v10.3 influences:\n{}".format(
                "\n".join(missing)
            )
        )

    research_to_skin_column = np.asarray(
        [skin_column_by_joint[joint] for joint in stage_01.context.influences],
        dtype=np.int32,
    )
    expected_columns = research_to_skin_column[owners]
    vertex_ids = np.arange(stage_01.vertex_count, dtype=np.int32)
    weights = np.zeros(
        (stage_01.vertex_count, len(skin_influences)),
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
            "Stored Maya weight shape differs from the expected v10.3 matrix: "
            "{} != {}".format(actual.shape, expected_weights.shape)
        )

    maximum_difference = float(np.max(np.abs(actual - expected_weights)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        raise RuntimeError(
            "Maya stored weights differ from v10.3. Maximum difference: {}"
            .format(maximum_difference)
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad_ids = np.where(actual_columns != expected_columns)[0]
        raise RuntimeError(
            "Stored hard owners differ from v10.3. First vertex IDs: {}"
            .format(bad_ids[:20].astype(np.int32).tolist())
        )

    row_sums = np.sum(actual, axis=1)
    if np.any(np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE):
        bad_ids = np.where(
            np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE
        )[0]
        raise RuntimeError(
            "Stored v10.3 rows are not normalized. First vertex IDs: {}"
            .format(bad_ids[:20].astype(np.int32).tolist())
        )

    active_counts = np.count_nonzero(actual > STORED_WEIGHT_TOLERANCE, axis=1)
    if np.any(active_counts != 1):
        bad_ids = np.where(active_counts != 1)[0]
        raise RuntimeError(
            "Stored v10.3 rows are not hard one-owner weights. First vertex IDs: {}"
            .format(bad_ids[:20].astype(np.int32).tolist())
        )

    return maximum_difference


def run():
    """Create one real hard-weight skinCluster for the v10.3 result."""

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
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        stage_01 = region_runner.run_stage_01(
            mesh=mesh_transform,
            joints=joints,
        )
        result = apply_global_owner_override(
            stage_01=stage_01,
            global_owner_joint=tagged_joint,
        )

        with undo_chunk("AD Skin Tool v10.3 Global Owner Smoke Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=list(stage_01.context.influences),
                max_influences=1,
            )
            vertex_ids, weights, expected_columns = _hard_weights_in_skin_order(
                adapter,
                stage_01,
                result.owner_indices,
            )
            adapter.set_weights(vertex_ids, weights, normalize=False)
            maximum_difference = _validate_stored_weights(
                adapter,
                weights,
                expected_columns,
            )

        builtins.AD_SKIN_V103_STAGE01_RESULT = stage_01
        builtins.AD_SKIN_V103_GLOBAL_OWNER_RESULT = result
        builtins.AD_SKIN_V103_SKIN_CLUSTER = adapter.skin_cluster

        tool_window._sync_loaded_skin_context()
        cmds.select(mesh_transform, replace=True)

        print("\n[AD Skin Tool v10.3 - Global Owner Facing Visual Bind]")
        print("Mesh:", stage_01.context.mesh_transform)
        print("Vertices:", stage_01.vertex_count)
        print("Influences:", stage_01.influence_count)
        print("Stage 01 owner regions:", stage_01.total_region_count)
        print("Stage 01 secondary regions:", stage_01.secondary_region_count)
        print("Stage 01 secondary vertices:", len(stage_01.all_secondary_vertex_ids))
        print(
            "Global Owner:",
            result.global_owner_joint.split("|")[-1]
            if result.global_owner_enabled
            else "<none>",
        )

        if result.facing is None:
            print("Facing: skipped because no Global Owner is tagged")
            print("Final owner map: exact Stage 01 closest-distance ownership")
        else:
            facing = result.facing
            print("\nOptimized secondary facing:")
            print("  anchor vertices queried:", facing.anchor_vertex_count)
            print("  unique face normals queried:", facing.queried_face_count)
            print("  co-primary regions retained:", facing.co_primary_region_count)
            print("  detached regions sent to Global Owner:", facing.detached_region_count)
            print("  ambiguous regions retained:", facing.ambiguous_region_count)
            print("  co-primary vertices retained:", facing.co_primary_vertex_count)
            print("  detached vertices classified:", facing.detached_vertex_count)
            print("  ambiguous vertices retained:", facing.ambiguous_vertex_count)
            print("  actual vertices reassigned:", result.reassigned_vertex_count)
            print("  anchor face query:", round(facing.anchor_query_seconds, 6))
            print("  normal query:", round(facing.normal_query_seconds, 6))
            print("  total facing:", round(facing.elapsed_seconds, 6))

            print("\nPer secondary region:")
            for diagnostic in facing.diagnostics:
                print(
                    "  {} region {} | vertices={} | classification={} | "
                    "anchors={} | observations +{} -{} unresolved={}".format(
                        diagnostic.joint.split("|")[-1],
                        diagnostic.region_index,
                        diagnostic.vertex_count,
                        diagnostic.classification,
                        list(diagnostic.local_anchor_vertex_ids),
                        diagnostic.positive_observation_count,
                        diagnostic.negative_observation_count,
                        diagnostic.unresolved_observation_count,
                    )
                )

        print("\nSkinCluster:", adapter.skin_cluster)
        print("Stored maximum weight difference:", maximum_difference)
        print("Every vertex stores exactly one influence at weight 1.0.")
        print("Undo once to remove this v10.3 smoke-test skinCluster.")

        tool_window._info(
            "v10.3 bind: {} detached secondary vertices reassigned.".format(
                result.reassigned_vertex_count
            )
        )
        return result

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
