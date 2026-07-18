"""Selection-based write smoke test for AD Skin Tool v5.0.

Usage:
    1. Select exactly one already-skinned polygon mesh.
    2. Add one or two joints that are not yet skinCluster influences.
    3. Execute this file in Maya's Script Editor.

The test adds the selected joints with default weight 0.0, calculates their
object-level Region claims, and writes accepted vertices as exact weight 1.0.
Existing locked ownership and every unclaimed row must remain unchanged.
"""

import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.v5.object_region_add as object_region_add


importlib.reload(object_region_add)


def _selected_mesh_and_new_joints():
    selection = cmds.ls(
        selection=True,
        long=True,
        objectsOnly=True,
    ) or []
    joints = cmds.ls(selection, long=True, type="joint") or []
    mesh_nodes = []

    for node in selection:
        if node in joints:
            continue
        node_type = cmds.nodeType(node)
        if node_type == "mesh":
            mesh_nodes.append(node)
            continue
        if node_type != "transform":
            continue
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
        raise builtins.RuntimeError(
            "Select exactly one already-skinned polygon mesh.\n\n"
            "Resolved mesh nodes: {}".format(builtins.len(mesh_nodes))
        )
    if builtins.len(joints) not in (1, 2):
        raise builtins.RuntimeError(
            "Select one or two new joints together with the skinned mesh."
        )
    return mesh_nodes[0], joints


mesh, target_joints = _selected_mesh_and_new_joints()
result = object_region_add.add_object_region_influences(
    mesh=mesh,
    target_joints=target_joints,
)

builtins.AD_SKIN_V50_OBJECT_REGION_ADD_RESULT = result
object_region_add.print_report(result)

builtins.print(
    "\nSaved result as builtins.AD_SKIN_V50_OBJECT_REGION_ADD_RESULT"
)
