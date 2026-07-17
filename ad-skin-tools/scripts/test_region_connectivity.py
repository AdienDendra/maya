"""Run test_region_distance_ranking.py, then select exactly one source joint."""

import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.region.connectivity as connectivity

importlib.reload(connectivity)

if not hasattr(builtins, "AD_SKIN_REGION_DISTANCE_RESULT"):
    raise RuntimeError("Run test_region_distance_ranking.py first.")

selected_joints = cmds.ls(selection=True, long=True, type="joint") or []
selected_joints = builtins.list(builtins.dict.fromkeys(selected_joints))
if builtins.len(selected_joints) != 1:
    raise RuntimeError("Select exactly one source joint.")

result = connectivity.probe_source_joint_ownership_connectivity(
    builtins.AD_SKIN_REGION_DISTANCE_RESULT,
    selected_joints[0],
)
builtins.AD_SKIN_REGION_CONNECTIVITY_RESULT = result

builtins.print("\n[AD Skin Tool Region - Ownership Connectivity]")
builtins.print("Mesh:", result.mesh_transform)
builtins.print("Source joint:", result.source_joint.split("|")[-1])
builtins.print("Raw-owned vertices:", result.raw_vertex_count)
builtins.print("Connected regions:", result.region_count)
builtins.print("Anchor vertices:", result.anchor_vertex_ids)
builtins.print("Primary unambiguous:", result.primary_is_unambiguous)
builtins.print("Primary vertices:", result.primary_vertex_count)
builtins.print("Secondary vertices:", result.detached_vertex_count)

if result.detached_vertex_ids:
    connectivity.select_connectivity_vertices(result, category="detached")
else:
    cmds.select(clear=True)

builtins.print("Saved as builtins.AD_SKIN_REGION_CONNECTIVITY_RESULT")
