"""Exclusive Global Owner tag and joint-list visual states."""

import builtins
import os

import maya.cmds as cmds


_GLOBAL_OWNER_TEXT_COLOR = (1.0, 0.78, 0.15)
_LOCKED_ICON_PATH = os.path.normpath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "resources",
        "lock_yellow.png",
    )
).replace("\\", "/")

_TOOL_WINDOW = None
_JOINT_LIST = None
_ORIGINAL_SET_JOINT_LIST = None
_ORIGINAL_POPULATE_CONTEXT_MENU = None


def install(tool_window_module, joint_list_module) -> None:
    """Install idempotent wrappers for Global Owner and lock visualization."""

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
    """Render the normal list, then apply distinct Global Owner and lock visuals."""

    normalized = _TOOL_WINDOW._unique_joint_paths(joints)
    _normalize_global_owner_state(normalized)
    _ORIGINAL_SET_JOINT_LIST(normalized)
    _render_global_owner_text(normalized)
    _render_locked_icons(normalized)


def global_owner_joint():
    """Return the valid Global Owner for the currently loaded mesh, or ``None``."""

    joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
    _normalize_global_owner_state(joints)
    return _TOOL_WINDOW._STATE.get("global_owner_joint")


def set_selected_as_global_owner() -> None:
    """Set exactly one selected row as the exclusive Global Owner."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_unskinned_mesh()

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
        _TOOL_WINDOW._info("Global Owner: {}".format(joint.split("|")[-1]))
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def clear_global_owner() -> None:
    """Clear the Global Owner tag while the loaded mesh is still unskinned."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_unskinned_mesh()

        if not _TOOL_WINDOW._STATE.get("global_owner_joint"):
            cmds.warning("No Global Owner is currently set.")
            return

        _TOOL_WINDOW._STATE["global_owner_joint"] = None
        _TOOL_WINDOW._STATE["global_owner_mesh_shape"] = None
        set_joint_list(builtins.list(_TOOL_WINDOW._STATE.get("joints", [])))
        _TOOL_WINDOW._info("Global Owner cleared.")
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def select_all_pending_joints() -> None:
    """Select every pending row, regardless of the current list selection."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        bound = set(_TOOL_WINDOW._STATE.get("bound_joint_paths", set()))
        pending = [joint for joint in joints if joint not in bound]
        if not pending:
            cmds.warning("No pending joints are available in the list.")
            return

        _JOINT_LIST.select_joint_paths(pending)
        _TOOL_WINDOW._info(
            "Selected all {} pending joint(s) in the list.".format(len(pending))
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def _populate_joint_context_menu(menu, *args) -> None:
    """Insert pending selection, then append Global Owner actions."""

    _ORIGINAL_POPULATE_CONTEXT_MENU(menu, *args)
    _insert_select_all_pending(menu)

    editable = _global_owner_is_editable()
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        label="Set As Global Owner",
        parent=menu,
        enable=editable,
        command=lambda *_: set_selected_as_global_owner(),
    )
    cmds.menuItem(
        label="Clear Global Owner",
        parent=menu,
        enable=bool(editable and global_owner_joint()),
        command=lambda *_: clear_global_owner(),
    )


def _insert_select_all_pending(menu) -> None:
    """Insert immediately after Select Vertices when Maya supports insertion."""

    insert_after = None
    try:
        items = cmds.popupMenu(menu, query=True, itemArray=True) or []
        for item in items:
            try:
                label = cmds.menuItem(item, query=True, label=True)
            except Exception:
                continue
            if label == "Select Vertices":
                insert_after = item
                break
    except Exception:
        insert_after = None

    kwargs = {
        "label": "Select All Pending Joints",
        "parent": menu,
        "command": lambda *_: select_all_pending_joints(),
    }
    if insert_after:
        kwargs["insertAfter"] = insert_after

    try:
        cmds.menuItem(**kwargs)
    except Exception:
        kwargs.pop("insertAfter", None)
        cmds.menuItem(**kwargs)


def _global_owner_is_editable() -> bool:
    state = _TOOL_WINDOW._STATE
    return bool(
        state.get("mesh_shape")
        and not state.get("busy")
        and not state.get("has_skin_cluster")
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


def _render_global_owner_text(joints) -> None:
    joint = _TOOL_WINDOW._STATE.get("global_owner_joint")
    if not joint or joint not in joints:
        return

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    item_id = _TOOL_WINDOW._STATE.get("joint_path_to_item", {}).get(joint)
    if not item_id or not _JOINT_LIST._tree_item_exists(control, item_id):
        return

    cmds.treeView(
        control,
        edit=True,
        textColor=(item_id,) + _GLOBAL_OWNER_TEXT_COLOR,
    )


def _render_locked_icons(joints) -> None:
    """Replace only locked button images; row text remains in its normal state."""

    if not os.path.isfile(_LOCKED_ICON_PATH):
        return

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    path_to_item = _TOOL_WINDOW._STATE.get("joint_path_to_item", {})
    for joint in joints:
        if not _JOINT_LIST.joint_is_locked(joint):
            continue
        item_id = path_to_item.get(joint)
        if not item_id or not _JOINT_LIST._tree_item_exists(control, item_id):
            continue
        cmds.treeView(
            control,
            edit=True,
            image=(item_id, 1, _LOCKED_ICON_PATH),
        )
