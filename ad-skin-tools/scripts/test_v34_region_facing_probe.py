"""Focused multi-primary region-facing smoke test for AD Skin Tool v3.4.

Required state:
    1. Run test_v30_distance_ranking.py.
    2. Select exactly one source joint from that result.
    3. Execute this file in Maya's Python Script Editor.

The runner recomputes the v3.3 connectivity result for the selected joint, then
classifies detached regions as co-primary, detached, or ambiguous. It changes
only Maya component selection and does not write skin weights.
"""
import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.v3.ownership_connectivity_probe as connectivity_probe
import ad_skin_tools.v3.region_facing_probe as region_facing_probe

importlib.reload(connectivity_probe)
importlib.reload(region_facing_probe)


if not hasattr(builtins, "AD_SKIN_V30_DISTANCE_RESULT"):
    raise RuntimeError(
        "Run test_v30_distance_ranking.py before the v3.4 probe."
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


connectivity_result = (
    connectivity_probe.probe_source_joint_ownership_connectivity(
        distance_result=distance_result,
        source_joint=selected_joints[0],
    )
)
builtins.AD_SKIN_V33_CONNECTIVITY_RESULT = connectivity_result

result = region_facing_probe.probe_region_facing(
    distance_result=distance_result,
    connectivity_result=connectivity_result,
)
builtins.AD_SKIN_V34_REGION_FACING_RESULT = result

source_short = result.source_joint.split("|")[-1]

builtins.print(
    "\n[AD Skin Tool v3.4 - Region-Local Facing Probe]"
)
builtins.print("Mesh:", result.mesh_transform)
builtins.print("Source joint:", source_short)
builtins.print(
    "Raw distance-owned vertices:",
    connectivity_result.raw_vertex_count,
)
builtins.print("Connected regions:", connectivity_result.region_count)
builtins.print(
    "v3.3 primary unambiguous:",
    connectivity_result.primary_is_unambiguous,
)
builtins.print("Primary region indices:", result.primary_region_indices)
builtins.print("Co-primary region indices:", result.co_primary_region_indices)
builtins.print("Detached region indices:", result.detached_region_indices)
builtins.print("Ambiguous region indices:", result.ambiguous_region_indices)
builtins.print("Accepted vertices:", result.accepted_vertex_count)
builtins.print("Co-primary vertices:", result.co_primary_vertex_count)
builtins.print("Detached vertices:", result.detached_vertex_count)
builtins.print("Ambiguous vertices:", result.ambiguous_vertex_count)
builtins.print("Elapsed seconds:", round(result.elapsed_seconds, 6))

if result.diagnostics:
    builtins.print("\nRegion diagnostics:")
    for diagnostic in result.diagnostics:
        builtins.print(
            "  Region {}: vertices={} | local anchors={} | "
            "face signs +{}/-{}/0{} | {}".format(
                diagnostic.region_index,
                builtins.len(diagnostic.vertex_ids),
                diagnostic.local_anchor_vertex_ids,
                diagnostic.positive_observation_count,
                diagnostic.negative_observation_count,
                diagnostic.unresolved_observation_count,
                diagnostic.classification.upper(),
            )
        )

if result.co_primary_vertex_ids:
    region_facing_probe.select_probe_vertices(
        result,
        category="co_primary",
    )
    builtins.print(
        "\nSelected {} co-primary vertices for visual inspection.".format(
            result.co_primary_vertex_count
        )
    )
elif result.ambiguous_vertex_ids:
    region_facing_probe.select_probe_vertices(
        result,
        category="ambiguous",
    )
    builtins.print(
        "\nNo co-primary region was proven. Selected {} ambiguous vertices.".format(
            result.ambiguous_vertex_count
        )
    )
elif result.detached_vertex_ids:
    region_facing_probe.select_probe_vertices(
        result,
        category="detached",
    )
    builtins.print(
        "\nNo co-primary or ambiguous region was found. "
        "Selected {} detached vertices.".format(
            result.detached_vertex_count
        )
    )
else:
    cmds.select(clear=True)
    builtins.print("\nThe raw ownership contains no secondary region.")

builtins.print(
    "\nSaved connectivity as builtins.AD_SKIN_V33_CONNECTIVITY_RESULT"
)
builtins.print(
    "Saved facing result as builtins.AD_SKIN_V34_REGION_FACING_RESULT"
)
builtins.print(
    "Selection helpers:\n"
    "from ad_skin_tools.v3.region_facing_probe "
    "import select_probe_vertices\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V34_REGION_FACING_RESULT, category='accepted')\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V34_REGION_FACING_RESULT, category='co_primary')\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V34_REGION_FACING_RESULT, category='detached')\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V34_REGION_FACING_RESULT, category='ambiguous')"
)
