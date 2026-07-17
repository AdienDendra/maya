"""v4.1 UI integration for component flooding and influence locks.

The v3 Region solver remains responsible for initial full-object binding.
Component Flood remains an explicit local override, now with Maya-style influence
locks, bound-state colouring, a lock button per row, and a joint context menu.
"""

import builtins

import maya.cmds as cmds

from ad_skin_tools.core import component_flood
from ad_skin_tools.core.influence_lock import (
    is_influence_locked,
    set_influence_locked,
)
from ad_skin_tools.core.selection import get_selected_joints
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk


CTRL_FLOOD_BUTTON = "adSkin_floodSelectedToJointButton"
CTRL_FLOOD_STATUS = "adSkin_floodSelectedToJointStatus"
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


def install(tool_window_module) -> None:
    """Compose v4.1 into the existing tool-window module."""

    global _TOOL_WINDOW

    _TOOL_WINDOW = tool_window_module
    if getattr(tool_window_module, "_V41_INFLUENCE_LOCKS_INSTALLED", False):
        return

    tool_window_module._build_joints_section = _build_joints_section
    tool_window_module._build_initial_bind_section = _build_bind_sections
    tool_window_module._set_joint_list = _set_joint_list
    tool_window_module.add_selected_joints = add_selected_joints
    tool_window_module.remove_selected_joints = remove_selected_joints
    tool_window_module.remove_all_joints = remove_all_joints
    tool_window_module.show_selected_joints_in_list = show_selected_joints_in_list
    tool_window_module._set_bind_busy = _set_bind_busy
    tool_window_module.show_help = show_help

    tool_window_module.WINDOW_LABEL = "AD Skin Weights Tool v4.1"
    tool_window_module.WINDOW_HEIGHT = max(
        int(tool_window_module.WINDOW_HEIGHT),
        760,
    )
    tool_window_module.WINDOW_WIDTH = 340

    tool_window_module._STATE.setdefault("joint_item_to_path", {})
    tool_window_module._STATE.setdefault("joint_path_to_item", {})
    tool_window_module._STATE.setdefault("bound_joint_paths", set())
    tool_window_module._STATE.setdefault("pending_locked_joints", set())

    tool_window_module._V4_COMPONENT_FLOOD_INSTALLED = True
    tool_window_module._V41_INFLUENCE_LOCKS_INSTALLED = True


def _build_joints_section() -> None:
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
        emptyLabel="No joints loaded",
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
            ("Show Joints In The List", lambda *_: show_selected_joints_in_list()),
        ],
        height=30,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_bind_sections() -> None:
    _build_initial_bind_section_v41()
    _build_component_flood_section()


def _build_initial_bind_section_v41() -> None:
    cmds.frameLayout(
        label="Initial Automatic Bind (Region v3)",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    cmds.text(
        label="Automatic Surface",
        align="left",
        font="boldLabelFont",
    )
    cmds.text(
        label=(
            "For an unskinned mesh: automatically calculate Region ownership "
            "across all connected and disconnected surface components."
        ),
        align="left",
        wordWrap=True,
    )
    cmds.button(
        _TOOL_WINDOW.CTRL_BIND_BUTTON,
        label="Bind Automatic Surface",
        height=38,
        command=lambda *_: _TOOL_WINDOW.apply_operation(),
        annotation=(
            "Bind the loaded unskinned mesh using all joints in the UI list. "
            "No fallback joint or manual shell assignment is required."
        ),
    )
    _TOOL_WINDOW._create_bind_progress_bar()
    cmds.text(
        _TOOL_WINDOW.CTRL_BIND_STATUS,
        label="",
        align="left",
        wordWrap=True,
        visible=False,
    )
    cmds.text(
        label=(
            "Region v3 writes exactly one influence at weight 1.0 per vertex. "
            "Use Component Flood below for explicit local overrides."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_component_flood_section() -> None:
    cmds.frameLayout(
        label="Component Flood (v4.1)",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    cmds.text(
        label="Flood Selected to Joint",
        align="left",
        font="boldLabelFont",
    )
    cmds.text(
        label=(
            "Select exactly one target joint in the list, then select vertices, "
            "edges, or faces on the loaded mesh. Locked ownership is preserved."
        ),
        align="left",
        wordWrap=True,
    )
    cmds.button(
        CTRL_FLOOD_BUTTON,
        label="Flood Selected to Joint",
        height=38,
        command=lambda *_: apply_component_flood(),
        annotation=(
            "Add the target as an influence when needed, then Replace 1.0 on "
            "writable selected vertices. Locked areas are ignored."
        ),
    )
    cmds.text(
        CTRL_FLOOD_STATUS,
        label="",
        align="left",
        wordWrap=True,
        visible=False,
    )
    cmds.text(
        label=(
            "Green joints are bound influences. The left lock protects their "
            "weights from Flood. Right-click the list for bulk lock operations."
        ),
        align="left",
        wordWrap=True,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _set_joint_list(joints) -> None:
    """Render one flat tree row per joint with scene-backed status."""

    normalized_joints = _TOOL_WINDOW._unique_joint_paths(joints)
    previous_selected_paths = set(_selected_joint_paths())

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
            bound_paths = set()
    _TOOL_WINDOW._STATE["bound_joint_paths"] = bound_paths

    pending_locks = set(
        _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
    )
    pending_locks.intersection_update(normalized_joints)
    pending_locks.difference_update(bound_paths)
    _TOOL_WINDOW._STATE["pending_locked_joints"] = pending_locks

    if not cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        return

    cmds.treeView(
        _TOOL_WINDOW.CTRL_JOINT_LIST,
        edit=True,
        removeAll=True,
    )

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

        cmds.treeView(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            edit=True,
            addItem=(item_id, ""),
            displayLabel=(item_id, display_label),
            itemAnnotation=(
                item_id,
                "{}\n{}".format(
                    joint,
                    "Bound influence" if joint in bound_paths else "Pending joint",
                ),
            ),
            buttonStyle=(item_id, 1, "pushButton"),
            buttonVisible=(item_id, 1, True),
        )

        if joint in bound_paths:
            cmds.treeView(
                _TOOL_WINDOW.CTRL_JOINT_LIST,
                edit=True,
                textColor=(item_id,) + _BOUND_TEXT_COLOR,
            )

        _render_lock_button(item_id, joint)

        if joint in previous_selected_paths:
            cmds.treeView(
                _TOOL_WINDOW.CTRL_JOINT_LIST,
                edit=True,
                selectItem=(item_id, True),
            )


def add_selected_joints() -> None:
    """Add Maya-selected joints as bind or Flood candidates."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()

        selected_joints = get_selected_joints()
        if not selected_joints:
            cmds.warning("No selected joints found.")
            return

        current_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        added = []
        for joint in selected_joints:
            normalized = _TOOL_WINDOW._normalize_joint_path(joint)
            if not _TOOL_WINDOW._joint_exists_in_list(normalized, current_joints):
                current_joints.append(normalized)
                added.append(normalized)

        _set_joint_list(current_joints)
        _TOOL_WINDOW._update_joint_count_label()

        if not added:
            cmds.warning("Selected joints already exist in the list.")
            return

        _select_joint_paths(added if len(added) == 1 else [])
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

        selected_joints = get_selected_joints()
        if not selected_joints:
            cmds.warning("No selected joints found in Maya.")
            return

        normalized = [
            _TOOL_WINDOW._normalize_joint_path(joint)
            for joint in selected_joints
        ]
        matched = [
            joint
            for joint in normalized
            if joint in _TOOL_WINDOW._STATE.get("joint_path_to_item", {})
        ]
        if not matched:
            cmds.warning("Selected joints were not found in the list.")
            return

        _select_joint_paths(matched)
        first_item = _TOOL_WINDOW._STATE["joint_path_to_item"][matched[0]]
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


def remove_selected_joints() -> None:
    """Remove pending rows only; real skinCluster influences are preserved."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        selected = set(_selected_joint_paths())
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
        _set_joint_list(remaining)
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
        _set_joint_list(remaining)
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
        selected = set(_selected_joint_paths())
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


def apply_component_flood() -> None:
    wait_cursor_active = False

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            raise RuntimeError(
                "Component Flood requires an existing skinCluster.\n\n"
                "Use Bind Automatic Surface first for an unskinned mesh."
            )

        selected_joints = _selected_joint_paths()
        if len(selected_joints) != 1:
            raise RuntimeError(
                "Select exactly one target joint in the UI influence list."
            )
        target_joint = selected_joints[0]

        _set_flood_busy(
            True,
            "Flooding writable vertices and preserving locked ownership...",
        )
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        staged_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        staged_locks = set(
            _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
        )
        result = component_flood.flood_selected_components_to_joint(
            mesh_shape=_TOOL_WINDOW._STATE["mesh_shape"],
            mesh_transform=_TOOL_WINDOW._STATE["mesh_transform"],
            target_joint=target_joint,
            target_locked_override=_joint_is_locked(target_joint),
        )

        if not result.target_locked:
            _sync_after_flood_preserving_pending(
                staged_joints,
                staged_locks,
            )
        _select_joint_paths([result.target_joint])

        builtins.AD_SKIN_V41_FLOOD_RESULT = result
        builtins.AD_SKIN_V40_FLOOD_RESULT = result
        component_flood.print_component_flood_report(result)

        short_name = result.target_joint.split("|")[-1]
        if result.target_locked:
            _TOOL_WINDOW._info(
                "Flood ignored: {} is locked.".format(short_name)
            )
            return

        suffixes = []
        if result.influence_added:
            suffixes.append("Added new influence.")
        if result.protected_vertex_count:
            suffixes.append(
                "{} locked vertex/vertices protected.".format(
                    result.protected_vertex_count
                )
            )
        if result.ignored_component_count:
            suffixes.append(
                "{} other component(s) ignored.".format(
                    result.ignored_component_count
                )
            )
        suffix = " " + " ".join(suffixes) if suffixes else ""
        _TOOL_WINDOW._info(
            "Flood complete: {} of {} selected vertices set to {} = 1.0.{}".format(
                result.flooded_vertex_count,
                result.vertex_count,
                short_name,
                suffix,
            )
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)
    finally:
        if wait_cursor_active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass
        _set_flood_busy(False)


def _sync_after_flood_preserving_pending(
    staged_joints,
    staged_locks,
) -> None:
    _TOOL_WINDOW._STATE["pending_locked_joints"] = set(staged_locks)
    _TOOL_WINDOW._sync_loaded_skin_context()
    current_influences = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
    current_set = set(current_influences)
    pending = [
        joint
        for joint in staged_joints
        if joint not in current_set and cmds.objExists(joint)
    ]
    _set_joint_list(current_influences + pending)
    _TOOL_WINDOW._update_joint_count_label()


def _set_joint_lock_states(joints, locked: bool) -> None:
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
                set_influence_locked(skin_cluster, joint, locked)
            elif locked:
                pending_locks.add(joint)
            else:
                pending_locks.discard(joint)

    _TOOL_WINDOW._STATE["pending_locked_joints"] = pending_locks
    for joint in joints:
        item_id = _TOOL_WINDOW._STATE.get("joint_path_to_item", {}).get(joint)
        if item_id:
            _render_lock_button(item_id, joint)


def _joint_is_locked(joint: str) -> bool:
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


def _render_lock_button(item_id: str, joint: str) -> None:
    locked = _joint_is_locked(joint)
    icon = _lock_icon(locked)
    kwargs = {
        "edit": True,
        "buttonTooltip": (
            item_id,
            1,
            "Unlock influence" if locked else "Lock influence",
        ),
    }
    if icon:
        kwargs["image"] = (item_id, 1, icon)
    else:
        kwargs["buttonTextIcon"] = (
            item_id,
            1,
            "L" if locked else "U",
        )
    cmds.treeView(
        _TOOL_WINDOW.CTRL_JOINT_LIST,
        **kwargs
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
        _set_joint_lock_states([joint], not _joint_is_locked(joint))
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def _prepare_context_menu(clicked_item) -> bool:
    if clicked_item:
        selected_ids = set(_selected_item_ids())
        if clicked_item not in selected_ids:
            cmds.treeView(
                _TOOL_WINDOW.CTRL_JOINT_LIST,
                edit=True,
                clearSelection=True,
            )
            cmds.treeView(
                _TOOL_WINDOW.CTRL_JOINT_LIST,
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


def _selected_item_ids():
    if not cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        return []
    result = []
    for item_id in _TOOL_WINDOW._STATE.get("joint_item_to_path", {}):
        try:
            selected = cmds.treeView(
                _TOOL_WINDOW.CTRL_JOINT_LIST,
                query=True,
                itemSelected=item_id,
            )
        except Exception:
            selected = False
        if selected:
            result.append(item_id)
    return result


def _selected_joint_paths():
    item_to_path = _TOOL_WINDOW._STATE.get("joint_item_to_path", {})
    return [
        item_to_path[item_id]
        for item_id in _selected_item_ids()
        if item_id in item_to_path
    ]


def _select_joint_paths(joints) -> None:
    if not cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        return
    cmds.treeView(
        _TOOL_WINDOW.CTRL_JOINT_LIST,
        edit=True,
        clearSelection=True,
    )
    path_to_item = _TOOL_WINDOW._STATE.get("joint_path_to_item", {})
    for joint in joints:
        item_id = path_to_item.get(joint)
        if item_id:
            cmds.treeView(
                _TOOL_WINDOW.CTRL_JOINT_LIST,
                edit=True,
                selectItem=(item_id, True),
            )


def _set_bind_busy(busy, status="") -> None:
    """Mirror the base bind busy state without assuming a textScrollList."""

    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    if cmds.button(_TOOL_WINDOW.CTRL_BIND_BUTTON, exists=True):
        cmds.button(
            _TOOL_WINDOW.CTRL_BIND_BUTTON,
            edit=True,
            enable=not busy,
            label="Binding..." if busy else "Bind Automatic Surface",
        )
    if cmds.button(CTRL_FLOOD_BUTTON, exists=True):
        cmds.button(
            CTRL_FLOOD_BUTTON,
            edit=True,
            enable=not busy,
        )
    if cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        cmds.treeView(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            edit=True,
            enable=not busy,
        )

    if cmds.progressBar(_TOOL_WINDOW.CTRL_BIND_PROGRESS, exists=True):
        kwargs = {"edit": True, "visible": bool(busy)}
        try:
            cmds.progressBar(
                _TOOL_WINDOW.CTRL_BIND_PROGRESS,
                isIndeterminate=bool(busy),
                **kwargs
            )
        except TypeError:
            cmds.progressBar(
                _TOOL_WINDOW.CTRL_BIND_PROGRESS,
                progress=50 if busy else 0,
                **kwargs
            )

    if cmds.text(_TOOL_WINDOW.CTRL_BIND_STATUS, exists=True):
        cmds.text(
            _TOOL_WINDOW.CTRL_BIND_STATUS,
            edit=True,
            label=status if busy else "",
            visible=bool(busy),
        )
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _set_flood_busy(busy: bool, status: str = "") -> None:
    if _TOOL_WINDOW is None:
        return
    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    if cmds.button(CTRL_FLOOD_BUTTON, exists=True):
        cmds.button(
            CTRL_FLOOD_BUTTON,
            edit=True,
            enable=not busy,
            label="Flooding..." if busy else "Flood Selected to Joint",
        )
    if cmds.button(_TOOL_WINDOW.CTRL_BIND_BUTTON, exists=True):
        cmds.button(
            _TOOL_WINDOW.CTRL_BIND_BUTTON,
            edit=True,
            enable=not busy,
        )
    if cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        cmds.treeView(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            edit=True,
            enable=not busy,
        )
    if cmds.text(CTRL_FLOOD_STATUS, exists=True):
        cmds.text(
            CTRL_FLOOD_STATUS,
            edit=True,
            label=status if busy else "",
            visible=bool(busy),
        )
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool v4.1",
        message=(
            "Initial Automatic Surface Bind:\n\n"
            "1. Load an unskinned mesh.\n"
            "2. Add every intended joint.\n"
            "3. Click Bind Automatic Surface.\n\n"
            "Component Flood Override:\n\n"
            "1. Load a mesh with an existing skinCluster.\n"
            "2. Add a new target joint when needed.\n"
            "3. Select exactly one target joint in the list.\n"
            "4. Select vertices, edges, or faces on the loaded mesh.\n"
            "5. Click Flood Selected to Joint.\n\n"
            "Green rows are bound influences. Click the lock icon to protect "
            "an influence. A locked target ignores Flood. Vertices carrying "
            "weight from another locked influence are skipped. Right-click "
            "the list for bulk lock, inverse lock, and pending-joint removal."
        ),
        button=["OK"],
    )
