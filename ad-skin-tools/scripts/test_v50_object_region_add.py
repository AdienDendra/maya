"""UI-driven write smoke test for AD Skin Tool v5.0.

Workflow:
    1. Load an already-skinned mesh in the AD Skin Tool.
    2. Add one or two new joints to the UI joint list.
    3. Highlight those pending joint rows in the UI list.
    4. Execute this file in Maya's Script Editor.

The Maya scene selection is not used to find target joints. The loaded mesh and
selected target joints come from the existing AD Skin Tool UI state.
"""

import builtins
import importlib

from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_list
import ad_skin_tools.v5.object_region_add as object_region_add


importlib.reload(object_region_add)


def _loaded_mesh_and_ui_targets():
    tool_window = component_flood_section._TOOL_WINDOW
    if tool_window is None:
        raise builtins.RuntimeError(
            "AD Skin Tool UI is not installed. Open the tool before running "
            "the v5 smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_loaded_mesh()

    state = tool_window._STATE
    if not state.get("has_skin_cluster"):
        raise builtins.RuntimeError(
            "v5 Object Region Add requires an existing skinCluster.\n\n"
            "Run Bind Automatic Surface first."
        )

    selected_rows = builtins.list(joint_list.selected_joint_paths())
    bound = set(state.get("bound_joint_paths", set()))
    pending_targets = [joint for joint in selected_rows if joint not in bound]

    if builtins.len(pending_targets) not in (1, 2):
        raise builtins.RuntimeError(
            "Highlight one or two NEW pending joints in the AD Skin Tool joint "
            "list.\n\n"
            "Selected UI rows: {}\n"
            "Selected pending targets: {}".format(
                builtins.len(selected_rows),
                builtins.len(pending_targets),
            )
        )

    locked_pending = [
        joint for joint in pending_targets if joint_list.joint_is_locked(joint)
    ]
    if locked_pending:
        raise builtins.RuntimeError(
            "Unlock the selected pending target joint(s) before the smoke test:\n{}"
            .format("\n".join(locked_pending))
        )

    return (
        state["mesh_shape"],
        pending_targets,
        builtins.list(state.get("joints", [])),
        set(state.get("pending_locked_joints", set())),
    )


mesh, target_joints, staged_joints, staged_locks = _loaded_mesh_and_ui_targets()
result = object_region_add.add_object_region_influences(
    mesh=mesh,
    target_joints=target_joints,
)

# Refresh the existing v4 list without changing its implementation. The newly
# added targets become bound rows, while unrelated pending rows remain staged.
joint_list.sync_after_flood_preserving_pending(
    staged_joints,
    staged_locks,
)
joint_list.select_joint_paths(result.target_joints)

builtins.AD_SKIN_V50_OBJECT_REGION_ADD_RESULT = result
object_region_add.print_report(result)

builtins.print(
    "\nSaved result as builtins.AD_SKIN_V50_OBJECT_REGION_ADD_RESULT"
)
