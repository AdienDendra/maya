"""UI-driven full Region proposal smoke test for AD Skin Tool v5.0.

Workflow:
    1. Load an already-skinned mesh in the AD Skin Tool.
    2. Add one or more new joints to the UI joint list.
    3. Highlight those pending joint rows.
    4. Execute this file in Maya's Script Editor.
"""

import builtins
import importlib

from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_list
import ad_skin_tools.v5.object_region_rebind as object_region_rebind


importlib.reload(object_region_rebind)


def _loaded_mesh_and_ui_targets():
    tool_window = component_flood_section._TOOL_WINDOW
    if tool_window is None:
        raise builtins.RuntimeError(
            "AD Skin Tool UI is not installed. Open the tool before running "
            "the v5 full Region smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_loaded_mesh()

    state = tool_window._STATE
    if not state.get("has_skin_cluster"):
        raise builtins.RuntimeError(
            "v5 Full Region Add requires an existing skinCluster.\n\n"
            "Run Bind Automatic Surface first."
        )

    selected_rows = builtins.list(joint_list.selected_joint_paths())
    bound = set(state.get("bound_joint_paths", set()))
    pending_targets = [
        joint for joint in selected_rows
        if joint not in bound
    ]
    if not pending_targets:
        raise builtins.RuntimeError(
            "Highlight at least one NEW pending joint in the AD Skin Tool list."
        )

    locked_pending = [
        joint
        for joint in pending_targets
        if joint_list.joint_is_locked(joint)
    ]
    if locked_pending:
        raise builtins.RuntimeError(
            "Unlock the selected pending target joint(s):\n{}".format(
                "\n".join(locked_pending)
            )
        )

    return (
        state["mesh_shape"],
        pending_targets,
        builtins.list(state.get("joints", [])),
        set(state.get("pending_locked_joints", set())),
    )


mesh, target_joints, staged_joints, staged_locks = _loaded_mesh_and_ui_targets()
result = object_region_rebind.add_object_region_influences_from_full_region(
    mesh=mesh,
    target_joints=target_joints,
)

joint_list.sync_after_flood_preserving_pending(
    staged_joints,
    staged_locks,
)
joint_list.select_joint_paths(result.target_joints)

builtins.AD_SKIN_V50_OBJECT_REGION_REBIND_RESULT = result
object_region_rebind.print_report(result)

builtins.print(
    "\nSaved result as "
    "builtins.AD_SKIN_V50_OBJECT_REGION_REBIND_RESULT"
)
