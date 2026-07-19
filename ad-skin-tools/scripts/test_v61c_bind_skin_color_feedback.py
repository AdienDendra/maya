"""Create a real skinCluster from the verified v6.1B in-memory weights.

This is a visual smoke test only. It does not change the production Bind Skin
command. Run the v6.1 and v6.1B smoke scripts first in the same Maya session.
"""

import builtins

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import (
    create_closest_skin_cluster,
    find_skin_cluster,
)
from ad_skin_tools.core.undo import undo_chunk


STORED_WEIGHT_TOLERANCE = 1e-10


def _require_results():
    required = (
        "AD_SKIN_V61_REGION_RESULT",
        "AD_SKIN_V61_BIND_SMOOTHING_RESULT",
        "AD_SKIN_V61B_PRODUCTION_TIE_RESULT",
        "AD_SKIN_V61B_OWNER_MAXIMUM_RESULT",
    )
    missing = [name for name in required if not hasattr(builtins, name)]
    if missing:
        raise RuntimeError(
            "Run test_v61_bind_smoothing_constraints.py and "
            "test_v61b_owner_and_ties.py first.\nMissing: {}".format(
                ", ".join(missing)
            )
        )

    region = builtins.AD_SKIN_V61_REGION_RESULT
    smooth = builtins.AD_SKIN_V61_BIND_SMOOTHING_RESULT
    tie_result = builtins.AD_SKIN_V61B_PRODUCTION_TIE_RESULT
    owner_result = builtins.AD_SKIN_V61B_OWNER_MAXIMUM_RESULT

    if tie_result.unresolved_exact_tie_vertex_ids:
        raise RuntimeError(
            "Production Max Influence still has unresolved exact ties. "
            "No skinCluster was created. First IDs: {}".format(
                list(tie_result.unresolved_exact_tie_vertex_ids[:20])
            )
        )
    if owner_result.owner_below_maximum_after:
        raise RuntimeError(
            "Region Owner is still below another influence. "
            "No skinCluster was created. First IDs: {}".format(
                list(owner_result.owner_below_maximum_after[:20])
            )
        )

    weights = np.asarray(owner_result.weights, dtype=np.float64)
    expected_shape = (region.vertex_count, region.influence_count)
    if weights.shape != expected_shape:
        raise RuntimeError(
            "Final weight matrix shape does not match Region data: "
            "{} != {}.".format(weights.shape, expected_shape)
        )

    return region, smooth, weights


def _weights_in_skin_order(adapter, region, region_weights):
    skin_influences = tuple(adapter.influences())
    skin_column_by_joint = {
        joint: column for column, joint in enumerate(skin_influences)
    }

    missing = [
        joint
        for joint in region.influences
        if joint not in skin_column_by_joint
    ]
    if missing:
        raise RuntimeError(
            "Created skinCluster is missing Region influences:\n{}".format(
                "\n".join(missing)
            )
        )

    ordered = np.zeros(
        (region.vertex_count, len(skin_influences)),
        dtype=np.float64,
    )
    for region_column, joint in enumerate(region.influences):
        ordered[:, skin_column_by_joint[joint]] = region_weights[:, region_column]

    return ordered


def _validate_stored_weights(adapter, expected, maximum_influences):
    vertex_ids = np.arange(expected.shape[0], dtype=np.int32)
    stored = adapter.get_weights(vertex_ids)
    actual = np.asarray(stored.weights, dtype=np.float64)

    if actual.shape != expected.shape:
        raise RuntimeError(
            "Stored weight matrix shape differs from expected: "
            "{} != {}.".format(actual.shape, expected.shape)
        )

    maximum_difference = float(np.max(np.abs(actual - expected)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        bad = np.where(
            np.any(
                np.abs(actual - expected) > STORED_WEIGHT_TOLERANCE,
                axis=1,
            )
        )[0][:20]
        raise RuntimeError(
            "Maya stored weights differ from the v6.1B matrix. "
            "Maximum difference: {}. First vertex IDs: {}".format(
                maximum_difference,
                bad.tolist(),
            )
        )

    row_sums = np.sum(actual, axis=1, dtype=np.float64)
    maximum_row_sum_error = float(np.max(np.abs(row_sums - 1.0)))
    active_counts = np.count_nonzero(actual > 1e-12, axis=1)
    maximum_active = int(np.max(active_counts))

    if maximum_active > int(maximum_influences):
        bad = np.where(active_counts > int(maximum_influences))[0][:20]
        raise RuntimeError(
            "Stored weights exceed Max Influences. First vertex IDs: {}".format(
                bad.tolist()
            )
        )

    return maximum_difference, maximum_row_sum_error, maximum_active


def run():
    region, smooth, final_weights = _require_results()

    if find_skin_cluster(region.mesh_shape, required=False):
        raise RuntimeError(
            "The mesh already has a skinCluster. Undo or delete the existing "
            "skinCluster before running this visual smoke test."
        )

    vertex_ids = np.arange(region.vertex_count, dtype=np.int32)
    adapter = None

    try:
        with undo_chunk("AD Skin Tool v6.1C Visual Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=region.mesh_shape,
                mesh_transform=region.mesh_transform,
                joints=list(region.influences),
                max_influences=smooth.effective_maximum_influences,
            )
            skin_order_weights = _weights_in_skin_order(
                adapter,
                region,
                final_weights,
            )
            adapter.set_weights(
                vertex_ids,
                skin_order_weights,
                normalize=False,
            )
            (
                maximum_difference,
                maximum_row_sum_error,
                maximum_active,
            ) = _validate_stored_weights(
                adapter,
                skin_order_weights,
                smooth.effective_maximum_influences,
            )
    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            try:
                cmds.delete(adapter.skin_cluster)
            except Exception:
                pass
        raise

    builtins.AD_SKIN_V61C_SKIN_CLUSTER = adapter.skin_cluster
    builtins.AD_SKIN_V61C_FINAL_WEIGHTS = final_weights.copy()

    cmds.select(region.mesh_transform, replace=True)

    print("\n[AD Skin Tool v6.1C - Visual Bind]")
    print("SkinCluster:", adapter.skin_cluster)
    print("Mesh:", region.mesh_transform)
    print("Vertices:", region.vertex_count)
    print("Influences:", region.influence_count)
    print("Smooth iterations:", smooth.options.iterations)
    print(
        "Effective Max Influences:",
        smooth.effective_maximum_influences,
    )
    print("Stored maximum active influences:", maximum_active)
    print("Maximum stored weight difference:", maximum_difference)
    print("Maximum stored row-sum error:", maximum_row_sum_error)
    print(
        "\nThe mesh is now skinned with the v6.1B final weights. "
        "Open Paint Skin Weights Tool and select influences to inspect "
        "the color feedback."
    )
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
