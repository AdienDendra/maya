"""Focused raw-ownership connectivity smoke test for AD Skin Tool v3.3.

Required state:
    1. Run test_v30_distance_ranking.py.
    2. Select exactly one source joint from that result.
    3. Execute this file in Maya's Python Script Editor.

The runner changes only component selection for inspection. It does not create a
skinCluster, write weights, use visibility rays, normals, or hierarchy.
"""

import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.v3.ownership_connectivity_probe as connectivity_probe

importlib.reload(connectivity_probe)


if not hasattr(builtins, "AD_SKIN_V30_DISTANCE_RESULT"):
    raise RuntimeError(
        "Run test_v30_distance_ranking.py before the v3.3 probe."
    )


distance_result = builtins.AD_SKIN_V30_DISTANCE_RESULT
selected_joints = cmds.ls(
    selection=True,
    long=True,
    type="joint",
) or []
selected_joints = builtins.list(
    builtins.dict.fromkeys(selected_joints)
)

if builtins.len(selected_joints) != 1:
    raise RuntimeError(
        "Select exactly one source joint to probe.\n\n"
        "Selected joints: {}".format(builtins.len(selected_joints))
    )


result = connectivity_probe.probe_source_joint_ownership_connectivity(
    distance_result=distance_result,
    source_joint=selected_joints[0],
)
builtins.AD_SKIN_V33_CONNECTIVITY_RESULT = result

source_short = result.source_joint.split("|")[-1]

builtins.print(
    "\n[AD Skin Tool v3.3 - Raw Ownership Connectivity Probe]"
)
builtins.print("Mesh:", result.mesh_transform)
builtins.print("Source joint:", source_short)
builtins.print("Raw distance-owned vertices:", result.raw_vertex_count)
builtins.print("Connected regions:", result.region_count)
builtins.print("Exact-nearest anchor vertices:", result.anchor_vertex_ids)
builtins.print("Anchor region indices:", result.anchor_region_indices)
builtins.print("Primary region unambiguous:", result.primary_is_unambiguous)
builtins.print("Primary vertices:", result.primary_vertex_count)
builtins.print("Detached vertices:", result.detached_vertex_count)
builtins.print("Elapsed seconds:", round(result.elapsed_seconds, 6))

if result.region_vertex_ids:
    builtins.print("\nRegion diagnostics:")
    for region_index, region in enumerate(result.region_vertex_ids):
        marker = ""
        if region_index in result.anchor_region_indices:
            marker = " [ANCHOR REGION]"
        builtins.print(
            "  Region {}: vertices={} | minimum squared distance={}{}".format(
                region_index,
                builtins.len(region),
                repr(result.region_minimum_squared_distances[region_index]),
                marker,
            )
        )

if not result.raw_vertex_ids:
    cmds.select(clear=True)
    builtins.print("\nThe selected joint owns no unique-nearest vertices in v3.0.")
elif not result.primary_is_unambiguous:
    connectivity_probe.select_probe_vertices(
        result,
        category="ambiguous",
    )
    builtins.print(
        "\nPrimary region is underdetermined because exact-nearest anchors "
        "occur in multiple connected regions. Selected those anchor regions."
    )
elif result.detached_vertex_ids:
    connectivity_probe.select_probe_vertices(
        result,
        category="detached",
    )
    builtins.print(
        "\nSelected {} detached vertices for visual inspection.".format(
            result.detached_vertex_count
        )
    )
else:
    cmds.select(clear=True)
    builtins.print("\nRaw ownership forms one anchor-connected region.")

builtins.print(
    "\nSaved result as builtins.AD_SKIN_V33_CONNECTIVITY_RESULT"
)
builtins.print(
    "Selection helpers:\n"
    "from ad_skin_tools.v3.ownership_connectivity_probe "
    "import select_probe_vertices\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V33_CONNECTIVITY_RESULT, category='primary')\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V33_CONNECTIVITY_RESULT, category='detached')\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V33_CONNECTIVITY_RESULT, category='anchors')"
)
