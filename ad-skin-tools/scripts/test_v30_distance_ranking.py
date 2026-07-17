"""Selection-based smoke test for AD Skin Tool v3 stage 1.

This runner only reads one mesh and selected joints, then calculates exact
world-space joint-pivot distance ranking.  It does not create a skinCluster,
write weights, inspect topology, use normals, cast visibility rays, or read the
joint hierarchy.

Usage:
    1. Select exactly one polygon mesh transform or mesh shape.
    2. Add every joint to be evaluated to the same Maya selection.
    3. Execute this file in Maya's Script Editor.
"""

import builtins
import importlib

import maya.cmds as cmds

import ad_skin_tools.v3.maya_scene as maya_scene
import ad_skin_tools.v3.distance_ranking as distance_ranking

importlib.reload(maya_scene)
importlib.reload(distance_ranking)


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

    mesh_nodes = builtins.list(builtins.dict.fromkeys(mesh_nodes))
    joints = builtins.list(builtins.dict.fromkeys(joints))

    if builtins.len(mesh_nodes) != 1:
        raise builtins.RuntimeError(
            "Select exactly one polygon mesh transform or shape.\n\n"
            "Resolved mesh nodes: {}".format(builtins.len(mesh_nodes))
        )
    if builtins.len(joints) < 2:
        raise builtins.RuntimeError(
            "Select at least two joints together with the mesh."
        )
    return mesh_nodes[0], joints


def _print_report(result):
    builtins.print("\n[AD Skin Tool v3.0 - Stage 1: Exact Distance Ranking]")
    builtins.print("Mesh:", result.mesh_transform)
    builtins.print("Vertices:", result.vertex_count)
    builtins.print("Influences:", result.influence_count)
    builtins.print("Elapsed seconds:", round(result.elapsed_seconds, 6))
    builtins.print("Exact-tie vertices:", builtins.len(result.exact_tie_vertex_ids))

    builtins.print("\nUnique nearest-vertex counts:")
    for joint in result.influences:
        builtins.print(
            "  {}: {}".format(
                joint,
                result.unique_assignment_counts[joint],
            )
        )

    zero_unique = [
        joint
        for joint in result.influences
        if result.unique_assignment_counts[joint] == 0
    ]
    if zero_unique:
        builtins.print("\nJoints with zero unique-nearest vertices:")
        for joint in zero_unique:
            builtins.print("  " + joint)

    if result.coincident_influence_groups:
        builtins.print("\nExactly coincident joint-position groups:")
        for group in result.coincident_influence_groups:
            builtins.print("  " + " | ".join(group))

    if result.exact_tie_vertex_ids:
        builtins.print(
            "\nFirst exact-tie vertex IDs:",
            result.exact_tie_vertex_ids[:20],
        )
        first_tie = result.exact_tie_vertex_ids[0]
        builtins.print(
            "\n" + distance_ranking.format_vertex_ranking(result, first_tie)
        )


mesh, joints = _selected_mesh_and_joints()
scene_input = maya_scene.collect_distance_input(
    mesh=mesh,
    joints=joints,
)
result = distance_ranking.solve_exact_distance_ranking(scene_input)

builtins.AD_SKIN_V30_DISTANCE_RESULT = result
_print_report(result)

builtins.print(
    "\nSaved result as builtins.AD_SKIN_V30_DISTANCE_RESULT"
)
builtins.print(
    "Inspect any vertex with:\n"
    "from ad_skin_tools.v3.distance_ranking import format_vertex_ranking\n"
    "print(format_vertex_ranking(builtins.AD_SKIN_V30_DISTANCE_RESULT, 0))"
)
