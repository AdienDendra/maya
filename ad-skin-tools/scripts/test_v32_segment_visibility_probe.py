"""Focused incoming-bone-segment visibility smoke test for v3.2.

Required state:
    1. Run test_v30_distance_ranking.py.
    2. Select exactly one source joint from that result.
    3. Execute this file in Maya's Python Script Editor.

The runner changes only the Maya component selection for inspection. It does
not create a skinCluster or write skin weights.
"""

import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.v3.segment_visibility_probe as segment_visibility_probe

importlib.reload(segment_visibility_probe)


if not hasattr(builtins, "AD_SKIN_V30_DISTANCE_RESULT"):
    raise RuntimeError(
        "Run test_v30_distance_ranking.py before the v3.2 probe."
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

result = segment_visibility_probe.probe_source_joint_segment_visibility(
    distance_result=distance_result,
    source_joint=selected_joints[0],
)
builtins.AD_SKIN_V32_SEGMENT_VISIBILITY_RESULT = result

source_short = result.source_joint.split("|")[-1]
parent_short = result.segment_parent_joint.split("|")[-1]

builtins.print(
    "\n[AD Skin Tool v3.2 - Incoming-Bone-Segment Visibility Probe]"
)
builtins.print("Mesh:", result.mesh_transform)
builtins.print("Source joint:", source_short)
builtins.print(
    "Incoming segment: {} -> {}".format(parent_short, source_short)
)
builtins.print("Raw distance-owned vertices:", result.raw_vertex_count)
builtins.print("First-surface visible:", result.visible_vertex_count)
builtins.print("Rejected as cross-surface:", result.rejected_vertex_count)
builtins.print(
    "Projection origins at parent endpoint:",
    result.parent_endpoint_projection_count,
)
builtins.print(
    "Projection origins inside segment:",
    result.interior_projection_count,
)
builtins.print(
    "Projection origins at source endpoint:",
    result.source_endpoint_projection_count,
)
builtins.print("Elapsed seconds:", round(result.elapsed_seconds, 6))

if result.rejected_vertex_ids:
    segment_visibility_probe.select_probe_vertices(
        result,
        category="rejected",
    )
    builtins.print(
        "\nSelected {} rejected vertices for visual inspection.".format(
            result.rejected_vertex_count
        )
    )
else:
    cmds.select(clear=True)
    builtins.print("\nNo rejected vertices were found for this source joint.")

builtins.print(
    "\nSaved result as "
    "builtins.AD_SKIN_V32_SEGMENT_VISIBILITY_RESULT"
)
builtins.print(
    "Selection helpers:\n"
    "from ad_skin_tools.v3.segment_visibility_probe "
    "import select_probe_vertices\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V32_SEGMENT_VISIBILITY_RESULT, "
    "category='visible')\n"
    "select_probe_vertices("
    "builtins.AD_SKIN_V32_SEGMENT_VISIBILITY_RESULT, "
    "category='rejected')"
)
