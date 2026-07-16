"""
Selection-based Maya runner for AD Skin Tool v2.7.

Usage:
    1. Select exactly one unskinned polygon mesh transform or mesh shape.
    2. Add every bind joint to the same Maya selection.
    3. Execute this file in Maya's Script Editor.

The runner contains no geometry name, joint name, shell assignment, or
body-part rule.
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

    # Maya's persistent Script Editor namespace may already contain variables
    # named list, dict, or len. Resolve these explicitly from builtins so the
    # runner remains safe when executed through exec().
    mesh_nodes = builtins.list(
        builtins.dict.fromkeys(mesh_nodes)
    )
    joints = builtins.list(
        builtins.dict.fromkeys(joints)
    )

    if builtins.len(mesh_nodes) != 1:
        raise builtins.RuntimeError(
            "Select exactly one polygon mesh transform or shape.\n\n"
            "Resolved mesh nodes: {}".format(
                builtins.len(mesh_nodes)
            )
        )

    if builtins.len(joints) < 2:
        raise builtins.RuntimeError(
            "Select at least two bind joints together with the mesh."
        )

    return mesh_nodes[0], joints


mesh, joints = _selected_mesh_and_joints()

result = joint_automatic_bind.bind_object_automatic_surface(
    mesh=mesh,
    joints=joints,
)

# Keep the result available for inspection in later Script Editor commands.
builtins.AD_SKIN_V27_RESULT = result

joint_automatic_bind.print_automatic_surface_report(
    result
)

builtins.print(
    "\nSaved result as builtins.AD_SKIN_V27_RESULT"
)
