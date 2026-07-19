"""v3.10C visual smoke: bind the current production Region as hard weights.

This runner does not change Region logic. It uses the existing production Region
solver and hard-bind writer so the final owner map can be inspected directly in
Maya's Paint Skin Weights Tool.

Workflow:
    1. Open AD Skin Tool.
    2. Load one unskinned polygon mesh.
    3. Add the intended joints to the UI list.
    4. Run this file from Maya's Script Editor.
"""

import builtins

import maya.cmds as cmds

from ad_skin_tools.core.joint_automatic_bind import (
    bind_object_automatic_surface,
    print_automatic_surface_report,
)
from ad_skin_tools.ui import skin_operations


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError(
            "Open AD Skin Tool before running the v3.10C visual Region bind."
        )

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()

    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError(
            "Add at least two joints to the AD Skin Tool list."
        )

    return state["mesh_transform"], joints


def run():
    mesh, joints = _loaded_unskinned_context()

    result = bind_object_automatic_surface(
        mesh=mesh,
        joints=joints,
    )

    builtins.AD_SKIN_V310C_BIND_RESULT = result
    builtins.AD_SKIN_V310C_REGION_RESULT = result.region_result
    builtins.AD_SKIN_V310C_SKIN_CLUSTER = result.skin_cluster

    cmds.select(result.mesh_transform, replace=True)

    print_automatic_surface_report(result)
    print("\n[AD Skin Tool v3.10C - Region Hard-Bind Color Feedback]")
    print("SkinCluster:", result.skin_cluster)
    print("Every vertex stores exactly one Region owner at weight 1.0.")
    print(
        "Open Paint Skin Weights Tool and select influences to inspect the "
        "Region boundary through color feedback."
    )
    print("This runner did not apply v3.10 or v3.10B boundary corrections.")
    print("Undo once to remove this visual-test skinCluster.")


if __name__ == "__main__":
    run()
