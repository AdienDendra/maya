"""
Selection-based Maya runner for AD Skin Tool v2.8.

Usage:
    1. Select exactly one unskinned polygon mesh transform or mesh shape.
    2. Add every intended bind joint to the same Maya selection.
    3. Execute this file in Maya's Script Editor.

This is a smoke runner only. It contains no geometry name, joint name, body-part
rule, shell assignment, ownership percentage, or tuned solver value.
"""

import builtins
import importlib

import maya.cmds as cmds

from ad_skin_tools.core import joint_automatic_bind

importlib.reload(joint_automatic_bind)


def _selected_mesh_and_joints():
    selection = cmds.ls(
        selection=True,
        long=True,
        objectsOnly=True,
    ) or []
    joints = cmds.ls(
        selection,
        long=True,
        type="joint",
    ) or []
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

    mesh_nodes = list(dict.fromkeys(mesh_nodes))
    joints = list(dict.fromkeys(joints))

    if len(mesh_nodes) != 1:
        raise RuntimeError(
            "Select exactly one polygon mesh transform or shape.\n\n"
            "Resolved mesh nodes: {}".format(len(mesh_nodes))
        )
    if len(joints) < 2:
        raise RuntimeError(
            "Select at least two bind joints together with the mesh."
        )
    return mesh_nodes[0], joints


mesh, joints = _selected_mesh_and_joints()
result = joint_automatic_bind.bind_object_automatic_surface(
    mesh=mesh,
    joints=joints,
)

builtins.AD_SKIN_V28_RESULT = result
joint_automatic_bind.print_automatic_surface_report(result)

print("\nSaved result as builtins.AD_SKIN_V28_RESULT")
