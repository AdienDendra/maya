"""Focused first-surface visibility smoke test for AD Skin Tool v3.1.

Required state:
    1. Run test_v30_distance_ranking.py first.
    2. Select exactly one joint from that v3.0 baseline result.
    3. Execute this file in Maya's Python Script Editor.

The runner probes only the raw distance-owned vertices of the selected joint.
It changes Maya selection for inspection but does not create a skinCluster or
write skin weights.
"""

import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.v3.visibility_probe as visibility_probe

importlib.reload(visibility_probe)


if not hasattr(builtins, "AD_SKIN_V30_DISTANCE_RESULT"):
    raise RuntimeError(
        "Run test_v30_distance_ranking.py before the v3.1 visibility probe."
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
        "Select exactly one joint to probe.\n\n"
        "Selected joints: {}".format(builtins.len(selected_joints))
    )

source_joint = selected_joints[0]
result = visibility_probe.probe_source_joint_visibility(
    distance_result=distance_result,
    source_joint=source_joint,
)
builtins.AD_SKIN_V31_VISIBILITY_RESULT = result

short_source = result.source_joint.split("|")[-1]
builtins.print(
    "\n[AD Skin Tool v3.1 - Focused First-Surface Visibility Probe]"
)
builtins.print("Mesh:", result.mesh_transform)
builtins.print("Source joint:", short_source)
builtins.print("Raw distance-owned vertices:", result.raw_vertex_count)
builtins.print("First-surface visible:", result.visible_vertex_count)
builtins.print("Rejected as cross-surface:", result.rejected_vertex_count)
builtins.print(
    "Unresolved exact-visible ties:",
    builtins.len(result.unresolved_tie_vertex_ids),
)
builtins.print(
    "No visible candidate:",
    builtins.len(result.no_visible_candidate_vertex_ids),
)
builtins.print("Elapsed seconds:", round(result.elapsed_seconds, 6))

if result.transition_counts:
    builtins.print("\nNearest visible replacements:")
    for joint, count in sorted(
        result.transition_counts.items(),
        key=lambda item: (-item[1], item[0]),
    ):
        builtins.print(
            "  {} -> {}: {}".format(
                short_source,
                joint.split("|")[-1],
                count,
            )
        )

if result.rejected_vertex_ids:
    visibility_probe.select_probe_vertices(result, category="rejected")
    builtins.print(
        "\nSelected {} rejected vertices for visual inspection.".format(
            result.rejected_vertex_count
        )
    )
else:
    cmds.select(clear=True)
    builtins.print("\nNo cross-surface vertices were found for this joint.")

builtins.print(
    "\nSaved result as builtins.AD_SKIN_V31_VISIBILITY_RESULT"
)
builtins.print(
    "Selection helpers:\n"
    "from ad_skin_tools.v3.visibility_probe import select_probe_vertices\n"
    "select_probe_vertices(builtins.AD_SKIN_V31_VISIBILITY_RESULT, "
    "category='visible')\n"
    "select_probe_vertices(builtins.AD_SKIN_V31_VISIBILITY_RESULT, "
    "category='rejected')"
)
