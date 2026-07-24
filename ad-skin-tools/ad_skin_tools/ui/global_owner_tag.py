"""Exclusive Global Owner tag and joint-list visual states."""

import os

import maya.cmds as cmds


_STATE_OWNER = "global_owner_joint"
_STATE_MESH = "global_owner_mesh_shape"
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
    """Install idempotent Global Owner and locked-row presentation wrappers."""

    global _TOOL_WINDOW, _JOINT_LIST
    global _ORIGINAL_SET_JOINT_LIST, _ORIGINAL_POPULATE_CONTEXT_MENU

    _TOOL_WINDOW = tool_window_module
    _JOINT_LIST = joint_list_module
    state = _state()
    state.setdefault(_STATE_OWNER, None)
    state.setdefault(_STATE_MESH, None)

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
    """Render the base list, then overlay Global Owner and lock visuals."""

    normalized = _TOOL_WINDOW._unique_joint_paths(joints)
    _normalize_global_owner_state(normalized)
    _ORIGINAL_SET_JOINT_LIST(normalized)
    _render_global_owner_text(normalized)
    _render_locked_icons(normalized)


def global_owner_joint():
    """Return the valid Global Owner for the currently loaded mesh."""

    joints = list(_state().get("joints", []))
    _normalize_global_owner_state(joints)
    return _state().get(_STATE_OWNER)


def set_selected_as_global_owner() -> None:
    def action():
        _require_editable()
        selected = list(_JOINT_LIST.selected_joint_paths())
        if len(selected) != 1:
            raise RuntimeError(
                "Select exactly one joint in the list before setting Global Owner."
            )

        joint = selected[0]
        joints = list(_state().get("joints", []))
        if joint not in joints:
            raise RuntimeError("The selected joint is no longer in the UI list.")

        _state()[_STATE_OWNER] = joint
        _state()[_STATE_MESH] = _state().get("mesh_shape")
        set_joint_list(joints)
        _JOINT_LIST.select_joint_paths([joint])
        _TOOL_WINDOW._info("Global Owner: {}".format(_short_name(joint)))

    _run_action(action)


def clear_global_owner() -> None:
    def action():
        _require_editable()
        if not _state().get(_STATE_OWNER):
            cmds.warning("No Global Owner is currently set.")
            return

        _state()[_STATE_OWNER] = None
        _state()[_STATE_MESH] = None
        set_joint_list(list(_state().get("joints", [])))
        _TOOL_WINDOW._info("Global Owner cleared.")

    _run_action(action)


def select_all_pending_joints() -> None:
    def action():
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        pending = _JOINT_LIST.pending_joint_paths()
        if not pending:
            cmds.warning("No pending joints are available in the list.")
            return
        _JOINT_LIST.select_joint_paths(pending)
        _TOOL_WINDOW._info(
            "Selected all {} pending joint(s) in the list.".format(len(pending))
        )

    _run_action(action)


def _populate_joint_context_menu(menu, *args) -> None:
    _ORIGINAL_POPULATE_CONTEXT_MENU(menu, *args)
    _insert_select_all_pending(menu)

    editable = _global_owner_is_editable()
    cmds.menuItem(divider=True, parent=menu)
    _menu_item(
        menu,
        "Set As Global Owner",
        set_selected_as_global_owner,
        enabled=editable,
    )
    _menu_item(
        menu,
        "Clear Global Owner",
        clear_global_owner,
        enabled=bool(editable and global_owner_joint()),
    )


def _insert_select_all_pending(menu) -> None:
    insert_after = _menu_item_with_label(menu, "Select Vertices")
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


def _menu_item(menu, label, callback, enabled=True):
    return cmds.menuItem(
        label=label,
        parent=menu,
        enable=bool(enabled),
        command=lambda *_: callback(),
    )


def _menu_item_with_label(menu, expected_label):
    try:
        items = cmds.popupMenu(menu, query=True, itemArray=True) or []
    except Exception:
        return None
    for item in items:
        try:
            if cmds.menuItem(item, query=True, label=True) == expected_label:
                return item
        except Exception:
            continue
    return None


def _global_owner_is_editable() -> bool:
    state = _state()
    return bool(
        state.get("mesh_shape")
        and not state.get("busy")
        and not state.get("has_skin_cluster")
    )


def _require_editable() -> None:
    _TOOL_WINDOW._require_not_busy()
    _TOOL_WINDOW._require_unskinned_mesh()


def _normalize_global_owner_state(joints) -> None:
    state = _state()
    joint = state.get(_STATE_OWNER)
    valid = bool(
        joint
        and state.get("mesh_shape")
        and state.get(_STATE_MESH) == state.get("mesh_shape")
        and joint in joints
        and cmds.objExists(joint)
    )
    if not valid:
        state[_STATE_OWNER] = None
        state[_STATE_MESH] = None


def _render_global_owner_text(joints) -> None:
    joint = _state().get(_STATE_OWNER)
    if not joint or joint not in joints:
        return
    item_id = _state().get("joint_path_to_item", {}).get(joint)
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not item_id or not _JOINT_LIST._tree_item_exists(control, item_id):
        return
    cmds.treeView(
        control,
        edit=True,
        textColor=(item_id,) + _GLOBAL_OWNER_TEXT_COLOR,
    )


def _render_locked_icons(joints) -> None:
    if not os.path.isfile(_LOCKED_ICON_PATH):
        return
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    path_to_item = _state().get("joint_path_to_item", {})
    for joint in joints:
        if not _JOINT_LIST.joint_is_locked(joint):
            continue
        item_id = path_to_item.get(joint)
        if item_id and _JOINT_LIST._tree_item_exists(control, item_id):
            cmds.treeView(
                control,
                edit=True,
                image=(item_id, 1, _LOCKED_ICON_PATH),
            )


def _run_action(callback) -> None:
    try:
        callback()
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def _state():
    return _TOOL_WINDOW._STATE


def _short_name(path):
    return str(path).rsplit("|", 1)[-1]
