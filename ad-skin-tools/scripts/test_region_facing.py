"""Run test_region_distance_ranking.py, then select exactly one source joint."""

import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.region.connectivity as connectivity
import ad_skin_tools.region.facing as facing

importlib.reload(connectivity)
importlib.reload(facing)

if not hasattr(builtins, "AD_SKIN_REGION_DISTANCE_RESULT"):
    raise RuntimeError("Run test_region_distance_ranking.py first.")

selected_joints = cmds.ls(selection=True, long=True, type="joint") or []
selected_joints = builtins.list(builtins.dict.fromkeys(selected_joints))
if builtins.len(selected_joints) != 1:
    raise RuntimeError("Select exactly one source joint.")

distance_result = builtins.AD_SKIN_REGION_DISTANCE_RESULT
connectivity_result = connectivity.probe_source_joint_ownership_connectivity(
    distance_result,
    selected_joints[0],
)
result = facing.probe_region_facing(distance_result, connectivity_result)
builtins.AD_SKIN_REGION_CONNECTIVITY_RESULT = connectivity_result
builtins.AD_SKIN_REGION_FACING_RESULT = result

builtins.print("\n[AD Skin Tool Region - Local Facing]")
builtins.print("Mesh:", result.mesh_transform)
builtins.print("Source joint:", result.source_joint.split("|")[-1])
builtins.print("Connected regions:", connectivity_result.region_count)
builtins.print("Primary regions:", result.primary_region_indices)
builtins.print("Co-primary regions:", result.co_primary_region_indices)
builtins.print("Detached regions:", result.detached_region_indices)
builtins.print("Ambiguous regions:", result.ambiguous_region_indices)
builtins.print("Accepted vertices:", result.accepted_vertex_count)
builtins.print("Detached vertices:", result.detached_vertex_count)
builtins.print("Ambiguous vertices:", result.ambiguous_vertex_count)

for diagnostic in result.diagnostics:
    builtins.print(
        "  Region {}: vertices={} | anchors={} | signs +{}/-{}/0{} | {}".format(
            diagnostic.region_index,
            len(diagnostic.vertex_ids),
            diagnostic.local_anchor_vertex_ids,
            diagnostic.positive_observation_count,
            diagnostic.negative_observation_count,
            diagnostic.unresolved_observation_count,
            diagnostic.classification.upper(),
        )
    )

if result.co_primary_vertex_ids:
    facing.select_facing_vertices(result, category="co_primary")
elif result.ambiguous_vertex_ids:
    facing.select_facing_vertices(result, category="ambiguous")
elif result.detached_vertex_ids:
    facing.select_facing_vertices(result, category="detached")
else:
    cmds.select(clear=True)

builtins.print("Saved as builtins.AD_SKIN_REGION_FACING_RESULT")
