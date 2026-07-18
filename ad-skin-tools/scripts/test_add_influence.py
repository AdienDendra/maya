"""UI-driven diagnostic runner for Add Influence."""

import builtins
import importlib

from ad_skin_tools.core import add_influence
from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_list


importlib.reload(add_influence)


def _loaded_mesh_and_ui_targets():
    tool_window = component_flood_section._TOOL_WINDOW
    if tool_window is None:
        raise builtins.RuntimeError(
            "AD Skin Tool UI is not installed. Open the tool before running "
            "the Add Influence diagnostic."
        )

    tool_window._require_not_busy()
    tool_window._require_loaded_mesh()

    state = tool_window._STATE
    if not state.get("has_skin_cluster"):
        raise builtins.RuntimeError(
            "Add Influence requires an existing skinCluster.\n\n"
            "Run Bind Skin first."
        )

    selected_rows = builtins.list(joint_list.selected_joint_paths())
    bound = set(state.get("bound_joint_paths", set()))
    pending_targets = [
        joint for joint in selected_rows
        if joint not in bound
    ]
    if not pending_targets:
        raise builtins.RuntimeError(
            "Highlight at least one new pending joint in the influence list."
        )

    locked_pending = [
        joint
        for joint in pending_targets
        if joint_list.joint_is_locked(joint)
    ]
    if locked_pending:
        raise builtins.RuntimeError(
            "Unlock the selected pending joint(s):\n{}".format(
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
result = add_influence.add_influences_by_region(
    mesh=mesh,
    target_joints=target_joints,
)

joint_list.sync_after_flood_preserving_pending(
    staged_joints,
    staged_locks,
)
joint_list.select_joint_paths(result.target_joints)

builtins.AD_SKIN_ADD_INFLUENCE_RESULT = result
add_influence.print_report(result)

builtins.print(
    "\nSaved result as builtins.AD_SKIN_ADD_INFLUENCE_RESULT"
)
