"""v10.2 Stage 04 smoke test: validate connectivity after Stage 03.

The production Bind Skin button and all earlier research files remain untouched.
This test expects the v10.1 Stage 03 live hard weights to be active, verifies that
Maya still stores that owner map exactly, then recomputes owner connectivity without
changing any skin weight.
"""

import builtins

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.region_research.post_reassignment_connectivity import (
    analyze_post_reassignment_connectivity,
)
from ad_skin_tools.ui import skin_operations


STORED_WEIGHT_TOLERANCE = 1e-10
STAGE_03_RESULT_SLOT = "AD_SKIN_V101_STAGE03_RESULT"
STAGE_04_RESULT_SLOT = "AD_SKIN_V102_STAGE04_RESULT"


def _active_stage_03_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v10.2 Stage 04 smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_loaded_mesh()

    state = tool_window._STATE
    if not state.get("has_skin_cluster"):
        raise RuntimeError(
            "Stage 04 requires the v10.1 Stage 03 live smoke-test skinCluster."
        )

    stage_03 = getattr(builtins, STAGE_03_RESULT_SLOT, None)
    if stage_03 is None:
        raise RuntimeError(
            "No v10.1 Stage 03 result exists in this Maya session.\n\n"
            "Run smoke_test_v101_stage03.run() first."
        )

    stage_01 = stage_03.stage_02.stage_01
    mesh_shape = state.get("mesh_shape")
    if stage_01.context.mesh_shape != mesh_shape:
        raise RuntimeError(
            "The loaded UI mesh differs from the stored Stage 03 research mesh."
        )

    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    return tool_window, adapter, stage_03


def _expected_hard_weights(adapter, stage_03):
    stage_01 = stage_03.stage_02.stage_01
    owners = np.asarray(stage_03.proposed_owner_indices, dtype=np.int32)
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
            "The active skinCluster is missing Stage 03 influences:\n{}".format(
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
    expected_columns = research_to_skin_column[owners]
    vertex_ids = np.arange(stage_01.vertex_count, dtype=np.int32)
    expected_weights = np.zeros(
        (stage_01.vertex_count, len(skin_influences)),
        dtype=np.float64,
    )
    expected_weights[vertex_ids, expected_columns] = 1.0
    return vertex_ids, expected_weights, expected_columns


def _validate_active_stage_03_weights(adapter, stage_03):
    vertex_ids, expected_weights, expected_columns = _expected_hard_weights(
        adapter,
        stage_03,
    )
    actual = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )

    if actual.shape != expected_weights.shape:
        raise RuntimeError(
            "Active Maya weight shape differs from the expected Stage 03 matrix: "
            "{} != {}".format(actual.shape, expected_weights.shape)
        )

    maximum_difference = float(np.max(np.abs(actual - expected_weights)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        raise RuntimeError(
            "Active Maya weights no longer match Stage 03. Maximum difference: {}"
            .format(maximum_difference)
        )

    actual_columns = np.argmax(actual, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad_ids = np.where(actual_columns != expected_columns)[0]
        raise RuntimeError(
            "Active Maya hard owners no longer match Stage 03. First vertex IDs: {}"
            .format(bad_ids[:20].astype(np.int32).tolist())
        )

    return maximum_difference


def run():
    """Validate the active Stage 03 skin and print post-reassignment connectivity."""

    tool_window, adapter, stage_03 = _active_stage_03_context()
    maximum_difference = _validate_active_stage_03_weights(adapter, stage_03)
    result = analyze_post_reassignment_connectivity(stage_03)

    setattr(builtins, STAGE_04_RESULT_SLOT, result)
    cmds.select(result.context.mesh_transform, replace=True)

    stage_01 = result.stage_01

    print("\n[AD Skin Tool v10.2 - Stage 04 Post-Reassignment Connectivity]")
    print("Mesh:", result.context.mesh_transform)
    print("SkinCluster:", adapter.skin_cluster)
    print("Active Stage 03 maximum weight difference:", maximum_difference)
    print("Stage 04 changed skin weights: 0")
    print("Connectivity analysis:", round(result.elapsed_seconds, 6))

    print("\nBefore / after Stage 03:")
    print(
        "  total owner regions: {} -> {}".format(
            stage_01.total_region_count,
            result.total_region_count,
        )
    )
    print(
        "  secondary regions: {} -> {}".format(
            stage_01.secondary_region_count,
            result.secondary_region_count,
        )
    )
    print(
        "  secondary vertices: {} -> {}".format(
            len(stage_01.all_secondary_vertex_ids),
            result.secondary_vertex_count,
        )
    )
    print(
        "  eliminated Stage 01 secondary vertices:",
        result.eliminated_stage_01_secondary_vertex_count,
    )
    print(
        "  residual Stage 01 secondary vertices:",
        result.residual_stage_01_secondary_vertex_count,
    )
    print(
        "  newly secondary vertices:",
        result.newly_secondary_vertex_count,
    )
    print(
        "  ambiguous primary influences:",
        result.ambiguous_primary_influence_count,
    )

    print("\nStage 03 proposal landing:")
    if not result.proposal_diagnostics:
        print("  none")
    for diagnostic in result.proposal_diagnostics:
        print(
            "  {} region {} -> {} | vertices={} | resulting_target_region={} | "
            "target_region_vertices={} | target_primary={} | "
            "contains_stage01_target_primary={}".format(
                diagnostic.source_joint.split("|")[-1],
                diagnostic.source_region_index,
                diagnostic.target_joint.split("|")[-1],
                diagnostic.vertex_count,
                diagnostic.resulting_target_region_index,
                diagnostic.resulting_target_region_vertex_count,
                diagnostic.target_region_is_primary,
                diagnostic.contains_stage_01_target_primary,
            )
        )

    print("\nPer influence changes:")
    changed_summary_count = 0
    for before, after in zip(
        stage_01.influence_summaries,
        result.influence_summaries,
    ):
        before_secondary = len(before.secondary_region_indices)
        after_secondary = len(after.secondary_region_indices)
        if (
            before.raw_vertex_count == after.raw_vertex_count
            and before.region_count == after.region_count
            and before_secondary == after_secondary
        ):
            continue

        changed_summary_count += 1
        print(
            "  {} | vertices {} -> {} | regions {} -> {} | secondary {} -> {}"
            .format(
                after.joint.split("|")[-1],
                before.raw_vertex_count,
                after.raw_vertex_count,
                before.region_count,
                after.region_count,
                before_secondary,
                after_secondary,
            )
        )

    if changed_summary_count == 0:
        print("  none")

    print(
        "\nThe viewport remains on the Stage 03 live skin. Stage 04 only validates "
        "whether those reassignments reduced or created ownership fragmentation."
    )
    tool_window._info(
        "v10.2 Stage 04: {} -> {} secondary regions.".format(
            stage_01.secondary_region_count,
            result.secondary_region_count,
        )
    )
    return result
