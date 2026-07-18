"""Joint-list UI and influence-lock actions for AD Skin Tool v4.2.

This module is the single source of truth for the flat Maya ``treeView`` used by
AD Skin Tool. It intentionally uses the Maya 2023-compatible command contract,
which is also shared by Maya 2025 and Maya 2026:

- create each item first;
- configure item-dependent flags in later commands;
- rebuild the tree after bulk lock operations;
- query item existence before addressing a row;
- do not install per-joint tooltips.
"""

import builtins

import maya.cmds as cmds

from ad_skin_tools.core.influence_lock import (
    is_influence_locked,
    set_influence_locked,
)
from ad_skin_tools.core.selection import get_selected_joints
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk


CTRL_JOINT_CONTEXT_MENU = "adSkin_jointListContextMenu"

_BOUND_TEXT_COLOR = (0.38, 0.86, 0.42)
_LOCKED_ICON_CANDIDATES = (
    "lockGeneric.png",
    "lock.png",
    "locked.png",
)
_UNLOCKED_ICON_CANDIDATES = (
    "unlockGeneric.png",
    "unlock.png",
    "unlocked.png",
)

_TOOL_WINDOW = None
_ICON_CACHE = {}


def configure(tool_window_module) -> None:
    """Attach the base tool-window module used by all callbacks."""

    global _TOOL_WINDOW
    _TOOL_WINDOW = tool_window_module

    tool_window_module._STATE.setdefault("joint_item_to_path", {})
    tool_window_module._STATE.setdefault("joint_path_to_item", {})
    tool_window_module._STATE.setdefault("bound_joint_paths", set())
    tool_window_module._STATE.setdefault("pending_locked_joints", set())


def build_section() -> None:
    """Build the flat influence tree and the two artist-facing list buttons."""

    _require_configured()

    cmds.frameLayout(
        label="Joints / Influences",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)

    cmds.treeView(
        _TOOL_WINDOW.CTRL_JOINT_LIST,
        allowMultiSelection=True,
        allowDragAndDrop=False,
        allowReparenting=False,
        enableKeys=True,
        height=220,
        numberOfButtons=1,
        attachButtonRight=False,
        preventOverride=True,
        pressCommand=(1, _on_lock_button_pressed),
        contextMenuCommand=_prepare_context_menu,
    )
    cmds.popupMenu(
        CTRL_JOINT_CONTEXT_MENU,
        parent=_TOOL_WINDOW.CTRL_JOINT_LIST,
        button=3,
        postMenuCommand=_populate_joint_context_menu,
    )

    _TOOL_WINDOW._button_row(
        [
            ("Add Joints To The List", lambda *_: add_selected_joints()),
            (
                "Select Joints In The List",
                lambda *_: show_selected_joints_in_list(),
            ),
        ],
        height=30,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def set_joint_list(joints) -> None:
    """Render one stable row per joint with no per-row tooltip."""

    _require_configured()

    normalized_joints = _TOOL_WINDOW._unique_joint_paths(joints)
    previous_selected_paths = set(selected_joint_paths())

    _TOOL_WINDOW._STATE["joints"] = normalized_joints
    _TOOL_WINDOW._STATE["joint_display_to_path"] = {}
    _TOOL_WINDOW._STATE["joint_path_to_display"] = {}
    _TOOL_WINDOW._STATE["joint_item_to_path"] = {}
    _TOOL_WINDOW._STATE["joint_path_to_item"] = {}

    bound_paths = set()
    if _TOOL_WINDOW._STATE.get("has_skin_cluster"):
        try:
            adapter = SkinClusterAdapter.from_mesh(
                _TOOL_WINDOW._STATE["mesh_shape"]
            )
            bound_paths = set(adapter.influences())
            _TOOL_WINDOW._STATE["skin_cluster"] = adapter.skin_cluster
        except Exception:
            # Preserve v4.1 artist behaviour: an unavailable read displays rows as
            # pending instead of aborting the window refresh.
            bound_paths = set()
    _TOOL_WINDOW._STATE["bound_joint_paths"] = bound_paths

    pending_locks = set(
        _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
    )
    pending_locks.intersection_update(normalized_joints)
    pending_locks.difference_update(bound_paths)
    _TOOL_WINDOW._STATE["pending_locked_joints"] = pending_locks

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return

    cmds.treeView(control, edit=True, removeAll=True)

    for index, joint in enumerate(normalized_joints):
        item_id = "joint_{:04d}".format(index)
        display_label = _TOOL_WINDOW._make_unique_joint_label(
            joint,
            normalized_joints,
        )
        _TOOL_WINDOW._STATE["joint_display_to_path"][display_label] = joint
        _TOOL_WINDOW._STATE["joint_path_to_display"][joint] = display_label
        _TOOL_WINDOW._STATE["joint_item_to_path"][item_id] = joint
        _TOOL_WINDOW._STATE["joint_path_to_item"][joint] = item_id

        # Maya 2023 can resolve item-dependent flags before a new item is fully
        # registered when they share one command. Keep every phase separate.
        cmds.treeView(
            control,
            edit=True,
            addItem=(item_id, ""),
        )
        cmds.treeView(
            control,
            edit=True,
            displayLabel=(item_id, display_label),
        )
        cmds.treeView(
            control,
            edit=True,
            buttonStyle=(item_id, 1, "pushButton"),
        )
        cmds.treeView(
            control,
            edit=True,
            buttonVisible=(item_id, 1, True),
        )

        if joint in bound_paths:
            cmds.treeView(
                control,
                edit=True,
                textColor=(item_id,) + _BOUND_TEXT_COLOR,
            )

        _render_lock_button(item_id, joint)

        if joint in previous_selected_paths:
            cmds.treeView(
                control,
                edit=True,
                selectItem=(item_id, True),
            )


def add_selected_joints() -> None:
    """Add Maya-selected joints as bind or Flood candidates."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        maya_joints = get_selected_joints()
        if not maya_joints:
            cmds.warning("No selected joints found.")
            return

        current_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        added = []
        for joint in maya_joints:
            normalized = _TOOL_WINDOW._normalize_joint_path(joint)
            if not _TOOL_WINDOW._joint_exists_in_list(normalized, current_joints):
                current_joints.append(normalized)
                added.append(normalized)

        set_joint_list(current_joints)
        _TOOL_WINDOW._update_joint_count_label()

        if not added:
            cmds.warning("Selected joints already exist in the list.")
            return

        select_joint_paths(added if len(added) == 1 else [])
        if _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            _TOOL_WINDOW._info(
                "Added {} pending Flood target joint(s). Missing influences "
                "are added when Flood runs.".format(len(added))
            )
        else:
            _TOOL_WINDOW._info("Added {} joint(s).".format(len(added)))
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def show_selected_joints_in_list() -> None:
    """Highlight Maya-selected joints already present in the UI list."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        maya_joints = get_selected_joints()
        if not maya_joints:
            cmds.warning("No selected joints found in Maya.")
            return

        normalized = [
            _TOOL_WINDOW._normalize_joint_path(joint)
            for joint in maya_joints
        ]
        matched = [
            joint
            for joint in normalized
            if joint in _TOOL_WINDOW._STATE.get("joint_path_to_item", {})
        ]
        if not matched:
            cmds.warning("Selected joints were not found in the list.")
            return

        select_joint_paths(matched)
        first_item = _TOOL_WINDOW._STATE["joint_path_to_item"][matched[0]]
        if _tree_item_exists(_TOOL_WINDOW.CTRL_JOINT_LIST, first_item):
            cmds.treeView(
                _TOOL_WINDOW.CTRL_JOINT_LIST,
                edit=True,
                showItem=first_item,
            )
        _TOOL_WINDOW._info(
            "Found {} selected joint(s) in the list.".format(len(matched))
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def select_joints_in_scene() -> None:
    """Select the currently highlighted joint rows inside Maya."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        selected = selected_joint_paths()
        if not selected:
            cmds.warning("No joints selected in the list.")
            return

        existing_joints = []
        for joint in selected:
            matches = cmds.ls(joint, long=True, type="joint") or []
            if matches:
                existing_joints.append(matches[0])

        if not existing_joints:
            cmds.warning("Selected list joints no longer exist in the scene.")
            return

        cmds.select(existing_joints, replace=True)
        _TOOL_WINDOW._info(
            "Selected {} joint(s) in the Maya scene.".format(
                len(existing_joints)
            )
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def remove_selected_joints() -> None:
    """Remove pending rows only; real skinCluster influences are preserved."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        selected = set(selected_joint_paths())
        if not selected:
            cmds.warning("No joints selected in the list.")
            return

        bound = set(_TOOL_WINDOW._STATE.get("bound_joint_paths", set()))
        removable = selected - bound
        skipped_bound = selected & bound
        if not removable:
            cmds.warning(
                "Existing skinCluster influences are preserved. "
                "Only pending joints can be removed from the list."
            )
            return

        pending_locks = set(
            _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
        )
        pending_locks.difference_update(removable)
        _TOOL_WINDOW._STATE["pending_locked_joints"] = pending_locks

        remaining = [
            joint
            for joint in _TOOL_WINDOW._STATE.get("joints", [])
            if joint not in removable
        ]
        set_joint_list(remaining)
        _TOOL_WINDOW._update_joint_count_label()

        suffix = ""
        if skipped_bound:
            suffix = " {} bound influence(s) preserved.".format(
                len(skipped_bound)
            )
        _TOOL_WINDOW._info(
            "Removed {} pending joint(s).{}".format(len(removable), suffix)
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def remove_all_joints() -> None:
    """Clear all pending rows while retaining bound skin influences."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        bound = set(_TOOL_WINDOW._STATE.get("bound_joint_paths", set()))
        current = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        remaining = [joint for joint in current if joint in bound]
        removed_count = len(current) - len(remaining)
        if removed_count == 0:
            cmds.warning(
                "No pending joints to remove. Bound influences are preserved."
            )
            return

        _TOOL_WINDOW._STATE["pending_locked_joints"] = set()
        set_joint_list(remaining)
        _TOOL_WINDOW._update_joint_count_label()
        _TOOL_WINDOW._info(
            "Removed {} pending joint(s); bound influences were preserved.".format(
                removed_count
            )
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def lock_selected_joints(locked: bool, inverse: bool = False) -> None:
    """Apply a lock state to selected or inverse-selected list rows."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        all_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        selected = set(selected_joint_paths())
        targets = [
            joint
            for joint in all_joints
            if ((joint not in selected) if inverse else (joint in selected))
        ]
        if not targets:
            cmds.warning(
                "No inverse joints available."
                if inverse
                else "No joints selected in the list."
            )
            return

        _set_joint_lock_states(targets, bool(locked))
        _TOOL_WINDOW._info(
            "{} {} joint(s).".format(
                "Locked" if locked else "Unlocked",
                len(targets),
            )
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def sync_after_flood_preserving_pending(staged_joints, staged_locks) -> None:
    """Refresh bound influences while retaining pending rows and pending locks."""

    _TOOL_WINDOW._STATE["pending_locked_joints"] = set(staged_locks)
    _TOOL_WINDOW._sync_loaded_skin_context()
    current_influences = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
    current_set = set(current_influences)
    pending = [
        joint
        for joint in staged_joints
        if joint not in current_set and cmds.objExists(joint)
    ]
    set_joint_list(current_influences + pending)
    _TOOL_WINDOW._update_joint_count_label()


def selected_joint_paths():
    """Return selected list rows as full joint paths."""

    item_to_path = _TOOL_WINDOW._STATE.get("joint_item_to_path", {})
    return [
        item_to_path[item_id]
        for item_id in _selected_item_ids()
        if item_id in item_to_path
    ]


def select_joint_paths(joints) -> None:
    """Select the supplied full joint paths in the tree."""

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return

    cmds.treeView(control, edit=True, clearSelection=True)
    path_to_item = _TOOL_WINDOW._STATE.get("joint_path_to_item", {})
    for joint in joints:
        item_id = path_to_item.get(joint)
        if item_id and _tree_item_exists(control, item_id):
            cmds.treeView(
                control,
                edit=True,
                selectItem=(item_id, True),
            )


def joint_is_locked(joint: str) -> bool:
    """Return scene-backed or staged lock state for one listed joint."""

    if joint in _TOOL_WINDOW._STATE.get("bound_joint_paths", set()):
        skin_cluster = _TOOL_WINDOW._STATE.get("skin_cluster")
        return bool(
            skin_cluster
            and is_influence_locked(skin_cluster, joint)
        )
    return joint in _TOOL_WINDOW._STATE.get(
        "pending_locked_joints",
        set(),
    )


def _set_joint_lock_states(joints, locked: bool) -> None:
    """Store lock changes, then rebuild the tree from authoritative state."""

    bound = set(_TOOL_WINDOW._STATE.get("bound_joint_paths", set()))
    pending_locks = set(
        _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
    )
    skin_cluster = _TOOL_WINDOW._STATE.get("skin_cluster")

    with undo_chunk("AD Skin Tool Influence Locks"):
        for joint in joints:
            if joint in bound:
                if not skin_cluster:
                    raise RuntimeError("Loaded skinCluster is unavailable.")
                set_influence_locked(skin_cluster, joint, bool(locked))
            elif locked:
                pending_locks.add(joint)
            else:
                pending_locks.discard(joint)

    _TOOL_WINDOW._STATE["pending_locked_joints"] = pending_locks
    set_joint_list(builtins.list(_TOOL_WINDOW._STATE.get("joints", [])))
    _TOOL_WINDOW._update_joint_count_label()


def _render_lock_button(item_id: str, joint: str) -> None:
    """Render the lock icon without a row or button tooltip."""

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not _tree_item_exists(control, item_id):
        return

    locked = joint_is_locked(joint)
    icon = _lock_icon(locked)
    if icon:
        cmds.treeView(
            control,
            edit=True,
            image=(item_id, 1, icon),
        )
    else:
        cmds.treeView(
            control,
            edit=True,
            buttonTextIcon=(item_id, 1, "L" if locked else "U"),
        )


def _lock_icon(locked: bool):
    cache_key = "locked" if locked else "unlocked"
    if cache_key in _ICON_CACHE:
        return _ICON_CACHE[cache_key]

    candidates = (
        _LOCKED_ICON_CANDIDATES
        if locked
        else _UNLOCKED_ICON_CANDIDATES
    )
    for candidate in candidates:
        try:
            matches = cmds.resourceManager(nameFilter=candidate) or []
        except Exception:
            matches = []
        if candidate in matches:
            _ICON_CACHE[cache_key] = candidate
            return candidate

    _ICON_CACHE[cache_key] = None
    return None


def _on_lock_button_pressed(item_id, *_):
    joint = _TOOL_WINDOW._STATE.get("joint_item_to_path", {}).get(item_id)
    if not joint:
        return
    try:
        _TOOL_WINDOW._require_not_busy()
        _set_joint_lock_states([joint], not joint_is_locked(joint))
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def _prepare_context_menu(clicked_item) -> bool:
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if clicked_item and _tree_item_exists(control, clicked_item):
        selected_ids = set(_selected_item_ids())
        if clicked_item not in selected_ids:
            cmds.treeView(control, edit=True, clearSelection=True)
            cmds.treeView(
                control,
                edit=True,
                selectItem=(clicked_item, True),
            )
    return True


def _populate_joint_context_menu(menu, *_):
    cmds.popupMenu(menu, edit=True, deleteAllItems=True)
    cmds.menuItem(
        label="Lock Selected",
        parent=menu,
        command=lambda *_: lock_selected_joints(True),
    )
    cmds.menuItem(
        label="Unlock Selected",
        parent=menu,
        command=lambda *_: lock_selected_joints(False),
    )
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        label="Lock Inverse Selected",
        parent=menu,
        command=lambda *_: lock_selected_joints(True, inverse=True),
    )
    cmds.menuItem(
        label="Unlock Inverse Selected",
        parent=menu,
        command=lambda *_: lock_selected_joints(False, inverse=True),
    )
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        label="Remove Selected",
        parent=menu,
        command=lambda *_: remove_selected_joints(),
    )
    cmds.menuItem(
        label="Remove All",
        parent=menu,
        command=lambda *_: remove_all_joints(),
    )
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        label="Select Joints In The Scene",
        parent=menu,
        command=lambda *_: select_joints_in_scene(),
    )


def _selected_item_ids():
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return []

    result = []
    for item_id in _TOOL_WINDOW._STATE.get("joint_item_to_path", {}):
        if not _tree_item_exists(control, item_id):
            continue
        try:
            selected = cmds.treeView(
                control,
                query=True,
                itemSelected=item_id,
            )
        except Exception:
            selected = False
        if selected:
            result.append(item_id)
    return result


def _tree_item_exists(control: str, item_id: str) -> bool:
    if not cmds.treeView(control, exists=True):
        return False
    try:
        return bool(
            cmds.treeView(
                control,
                query=True,
                itemExists=item_id,
            )
        )
    except Exception:
        return False


def _require_configured() -> None:
    if _TOOL_WINDOW is None:
        raise RuntimeError("AD Skin Tool joint-list UI is not configured.")
