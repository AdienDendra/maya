"""Exclusive Global Owner tag for the AD Skin Tool joint list.

The tag is UI state only in v10.3. It does not change the production Bind Skin
button. The v10.3 smoke test reads this state and uses it as the destination for
secondary regions classified as detached by facing.
"""

import builtins

import maya.cmds as cmds


_GLOBAL_OWNER_TEXT_COLOR = (1.0, 0.78, 0.15)

_TOOL_WINDOW = None
_JOINT_LIST = None
_ORIGINAL_SET_JOINT_LIST = None
_ORIGINAL_POPULATE_CONTEXT_MENU = None


def install(tool_window_module, joint_list_module) -> None:
    """Install one idempotent wrapper around the active joint-list module."""

    global _TOOL_WINDOW
    global _JOINT_LIST
    global _ORIGINAL_SET_JOINT_LIST
    global _ORIGINAL_POPULATE_CONTEXT_MENU

    _TOOL_WINDOW = tool_window_module
    _JOINT_LIST = joint_list_module

    state = tool_window_module._STATE
    state.setdefault("global_owner_joint", None)
    state.setdefault("global_owner_mesh_shape", None)

    if joint_list_module.set_joint_list is set_joint_list:
        tool_window_module._set_joint_list = set_joint_list
        return

    _ORIGINAL_SET_JOINT_LIST = joint_list_module.set_joint_list
    _ORIGINAL_POPULATE_CONTEXT_MENU = (
        joint_list_module._populate_joint_context_menu
    )

    joint_list_module.set_joint_list = set_joint_list
    joint_list_module._populate_joint_context_menu = _populate_joint_context_menu
    tool_window_module._set_joint_list = set_joint_list


def set_joint_list(joints) -> None:
    """Render the normal list, then paint the exclusive Global Owner yellow."""

    normalized = _TOOL_WINDOW._unique_joint_paths(joints)
    _normalize_global_owner_state(normalized)
    _ORIGINAL_SET_JOINT_LIST(normalized)
    _render_global_owner_color()


def global_owner_joint():
    """Return the valid Global Owner for the currently loaded mesh, or ``None``."""

    joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
    _normalize_global_owner_state(joints)
    return _TOOL_WINDOW._STATE.get("global_owner_joint")


def set_selected_as_global_owner() -> None:
    """Set exactly one selected row as the exclusive Global Owner."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        selected = builtins.list(_JOINT_LIST.selected_joint_paths())
        if len(selected) != 1:
            raise RuntimeError(
                "Select exactly one joint in the list before setting Global Owner."
            )

        joint = selected[0]
        current_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        if joint not in current_joints:
            raise RuntimeError("The selected joint is no longer in the UI list.")

        _TOOL_WINDOW._STATE["global_owner_joint"] = joint
        _TOOL_WINDOW._STATE["global_owner_mesh_shape"] = (
            _TOOL_WINDOW._STATE.get("mesh_shape")
        )

        set_joint_list(current_joints)
        _JOINT_LIST.select_joint_paths([joint])
        _TOOL_WINDOW._info(
            "Global Owner: {}".format(joint.split("|")[-1])
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def clear_global_owner() -> None:
    """Clear the Global Owner tag for the current loaded mesh."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        if not _TOOL_WINDOW._STATE.get("global_owner_joint"):
            cmds.warning("No Global Owner is currently set.")
            return

        _TOOL_WINDOW._STATE["global_owner_joint"] = None
        _TOOL_WINDOW._STATE["global_owner_mesh_shape"] = None
        set_joint_list(
            builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        )
        _TOOL_WINDOW._info("Global Owner cleared.")
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def _populate_joint_context_menu(menu, *args) -> None:
    """Append Global Owner actions to the existing joint context menu."""

    _ORIGINAL_POPULATE_CONTEXT_MENU(menu, *args)
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        label="Set As Global Owner",
        parent=menu,
        command=lambda *_: set_selected_as_global_owner(),
    )
    cmds.menuItem(
        label="Clear Global Owner",
        parent=menu,
        enable=bool(global_owner_joint()),
        command=lambda *_: clear_global_owner(),
    )


def _normalize_global_owner_state(normalized_joints) -> None:
    state = _TOOL_WINDOW._STATE
    joint = state.get("global_owner_joint")
    tagged_mesh = state.get("global_owner_mesh_shape")
    current_mesh = state.get("mesh_shape")

    valid = bool(
        joint
        and current_mesh
        and tagged_mesh == current_mesh
        and joint in normalized_joints
        and cmds.objExists(joint)
    )
    if valid:
        return

    state["global_owner_joint"] = None
    state["global_owner_mesh_shape"] = None


def _render_global_owner_color() -> None:
    joint = _TOOL_WINDOW._STATE.get("global_owner_joint")
    if not joint:
        return

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    item_id = _TOOL_WINDOW._STATE.get("joint_path_to_item", {}).get(joint)
    if not item_id:
        return
    if not _JOINT_LIST._tree_item_exists(control, item_id):
        return

    cmds.treeView(
        control,
        edit=True,
        textColor=(item_id,) + _GLOBAL_OWNER_TEXT_COLOR,
    )
