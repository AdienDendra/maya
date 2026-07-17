"""Select one polygon mesh and every intended joint, then execute in Maya."""

import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.region.maya_scene as maya_scene
import ad_skin_tools.region.distance_ranking as distance_ranking

importlib.reload(maya_scene)
importlib.reload(distance_ranking)

selection = cmds.ls(selection=True, long=True) or []
joints = cmds.ls(selection=True, long=True, type="joint") or []
mesh_nodes = []
for node in selection:
    if cmds.nodeType(node) == "mesh":
        mesh_nodes.append(node)
    elif cmds.nodeType(node) == "transform":
        shapes = cmds.listRelatives(
            node,
            shapes=True,
            noIntermediate=True,
            fullPath=True,
            type="mesh",
        ) or []
        if shapes:
            mesh_nodes.append(node)

mesh_nodes = builtins.list(builtins.dict.fromkeys(mesh_nodes))
joints = builtins.list(builtins.dict.fromkeys(joints))
if builtins.len(mesh_nodes) != 1:
    raise RuntimeError("Select exactly one polygon mesh plus the joint list.")
if builtins.len(joints) < 2:
    raise RuntimeError("Select at least two joints with the mesh.")

scene_input = maya_scene.collect_distance_input(mesh_nodes[0], joints)
result = distance_ranking.solve_exact_distance_ranking(scene_input)
builtins.AD_SKIN_REGION_DISTANCE_RESULT = result

builtins.print("\n[AD Skin Tool Region - Exact Distance Ranking]")
builtins.print("Mesh:", result.mesh_transform)
builtins.print("Vertices:", result.vertex_count)
builtins.print("Influences:", result.influence_count)
builtins.print("Exact-tie vertices:", len(result.exact_tie_vertex_ids))
builtins.print("Elapsed seconds:", round(result.elapsed_seconds, 6))
builtins.print("Saved as builtins.AD_SKIN_REGION_DISTANCE_RESULT")
