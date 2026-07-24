"""Joint-list UI, influence locks, and list-facing scene actions.

``_STATE['joints']`` is authoritative solver order. Sorting and filtering are
presentation-only concerns layered on top of Maya's native ``treeView``.
"""

import maya.cmds as cmds

from ad_skin_tools.core.influence_lock import (
    is_influence_locked,
    set_influence_locked,
)
from ad_skin_tools.core.selection import get_selected_joints
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk

CTRL_JOINT_CONTEXT_MENU = "adSkin_jointListContextMenu"

SORT_A_TO_Z = "a_to_z"
SORT_Z_TO_A = "z_to_a"
SORT_PENDING_JOINTS = "pending_joints"
VALID_SORT_MODES = frozenset(
    (SORT_A_TO_Z, SORT_Z_TO_A, SORT_PENDING_JOINTS)
)

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
_PRESENTATION_REFRESH_KEY = "joint_presentation_refresh"

_TOOL_WINDOW = None
_ICON_CACHE = {}
_PROGRAMMATIC_MULTI_SELECT = False


def configure(tool_window_module) -> None:
    """Attach the active tool-window module and initialize list state."""

    global _TOOL_WINDOW
    _TOOL_WINDOW = tool_window_module
    state = _state()
    defaults = {
        "joint_item_to_path": {},
        "joint_path_to_item": {},
        "joint_display_to_path": {},
        "joint_path_to_display": {},
        "joint_display_order": [],
        "bound_joint_paths": set(),
        "pending_locked_joints": set(),
        "joint_sort_mode": SORT_A_TO_Z,
    }
    for key, value in defaults.items():
        state.setdefault(key, value)


def build_section() -> None:
    """Build the flat influence tree and artist-facing list buttons."""

    _require_configured()
    cmds.frameLayout(
        label="Joints / Influences",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)
    _build_tree()
    _build_list_buttons()
    cmds.setParent("..")
    cmds.setParent("..")


def _build_tree() -> None:
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
        selectCommand=_allow_tree_selection_change,
    )
    cmds.popupMenu(
        CTRL_JOINT_CONTEXT_MENU,
        parent=_TOOL_WINDOW.CTRL_JOINT_LIST,
        button=3,
        postMenuCommand=_populate_joint_context_menu,
    )


def _build_list_buttons() -> None:
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


def set_joint_list(joints) -> None:
    """Rebuild rows while preserving authoritative order and UI position."""

    _require_configured()
    normalized = _TOOL_WINDOW._unique_joint_paths(joints)
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    selected_paths = set(selected_joint_paths())
    scroll_position = _query_vertical_scroll_position(control)

    state = _state()
    state["joints"] = normalized
    bound = _refresh_bound_joint_paths()
    _normalize_pending_locks(normalized, bound)

    labels = {
        joint: _TOOL_WINDOW._make_unique_joint_label(joint, normalized)
        for joint in normalized
    }
    display_order = _sorted_display_order(normalized, labels, bound)
    _reset_row_maps(display_order, labels)

    if not cmds.treeView(control, exists=True):
        return
    cmds.treeView(control, edit=True, removeAll=True)

    for index, joint in enumerate(display_order):
        item_id = "joint_{:04d}".format(index)
        _create_tree_row(control, item_id, labels[joint])
        if joint in bound:
            cmds.treeView(
                control,
                edit=True,
                textColor=(item_id,) + _BOUND_TEXT_COLOR,
            )
        _render_lock_button(item_id, joint)

    select_joint_paths(selected_paths)
    _refresh_presentation()
    _restore_vertical_scroll_position(control, scroll_position)


def bound_joint_paths(refresh=False):
    """Return current bound paths, optionally rereading the loaded skinCluster."""

    if refresh:
        return _refresh_bound_joint_paths()
    return set(_state().get("bound_joint_paths", set()))


def pending_joint_paths():
    """Return listed joints that are not current skinCluster influences."""

    bound = bound_joint_paths()
    return [
        joint
        for joint in _state().get("joints", [])
        if joint not in bound
    ]


def add_selected_joints() -> None:
    def action():
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        maya_joints = get_selected_joints()
        if not maya_joints:
            cmds.warning("No selected joints found.")
            return

        current = list(_state().get("joints", []))
        added = []
        for joint in maya_joints:
            normalized = _TOOL_WINDOW._normalize_joint_path(joint)
            if not _TOOL_WINDOW._joint_exists_in_list(normalized, current):
                current.append(normalized)
                added.append(normalized)

        set_joint_list(current)
        _TOOL_WINDOW._update_joint_count_label()
        if not added:
            cmds.warning("Selected joints already exist in the list.")
            return

        select_joint_paths(added if len(added) == 1 else [])
        if _state().get("has_skin_cluster"):
            _TOOL_WINDOW._info(
                "Added {} pending Flood target joint(s). Missing influences "
                "are added when Flood runs.".format(len(added))
            )
        else:
            _TOOL_WINDOW._info("Added {} joint(s).".format(len(added)))

    _run_action(action)


def show_selected_joints_in_list() -> None:
    def action():
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        maya_joints = get_selected_joints()
        if not maya_joints:
            cmds.warning("No selected joints found in Maya.")
            return

        path_to_item = _state().get("joint_path_to_item", {})
        normalized = [
            _TOOL_WINDOW._normalize_joint_path(joint)
            for joint in maya_joints
        ]
        matched = [joint for joint in normalized if joint in path_to_item]
        if not matched:
            cmds.warning("Selected joints were not found in the list.")
            return

        first_item = path_to_item[matched[0]]
        if _tree_item_exists(_TOOL_WINDOW.CTRL_JOINT_LIST, first_item):
            cmds.treeView(
                _TOOL_WINDOW.CTRL_JOINT_LIST,
                edit=True,
                showItem=first_item,
            )
        select_joint_paths(matched)
        _TOOL_WINDOW._info(
            "Found {} selected joint(s) in the list.".format(len(matched))
        )

    _run_action(action)


def select_vertices() -> None:
    def action():
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        selected = selected_joint_paths()
        if not selected:
            cmds.warning("No joints selected in the list.")
            return

        bound_selected = [
            joint for joint in selected if joint in bound_joint_paths()
        ]
        if not bound_selected:
            cmds.warning(
                "Selected rows are pending joints. Only bound influences "
                "can have influenced vertices."
            )
            return

        adapter = SkinClusterAdapter.from_mesh(_state()["mesh_shape"])
        vertex_ids = adapter.affected_vertex_ids(bound_selected)
        if len(vertex_ids) == 0:
            cmds.warning(
                "Selected bound influences have no non-zero vertices on "
                "the loaded mesh."
            )
            return

        mesh_transform = _state().get("mesh_transform")
        if not mesh_transform or not cmds.objExists(mesh_transform):
            raise RuntimeError("Loaded mesh transform is unavailable.")

        cmds.select(
            _vertex_component_ranges(mesh_transform, vertex_ids),
            replace=True,
        )
        _TOOL_WINDOW._info(
            "Selected {} influenced vertex(s) from {} bound joint(s).".format(
                len(vertex_ids),
                len(bound_selected),
            )
        )

    _run_action(action)


def select_joints_in_scene() -> None:
    def action():
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        selected = selected_joint_paths()
        if not selected:
            cmds.warning("No joints selected in the list.")
            return

        existing = []
        for joint in selected:
            matches = cmds.ls(joint, long=True, type="joint") or []
            if matches:
                existing.append(matches[0])
        if not existing:
            cmds.warning("Selected list joints no longer exist in the scene.")
            return

        cmds.select(existing, replace=True)
        _TOOL_WINDOW._info(
            "Selected {} joint(s) in the Maya scene.".format(len(existing))
        )

    _run_action(action)


def remove_selected_joints() -> None:
    def action():
        _require_list_editable()
        selected = set(selected_joint_paths())
        if not selected:
            cmds.warning("No joints selected in the list.")
            return

        bound = bound_joint_paths()
        removable = selected - bound
        if not removable:
            cmds.warning(
                "Existing skinCluster influences are preserved. "
                "Only pending joints can be removed from the list."
            )
            return
        skipped_count = len(selected & bound)
        suffix = (
            " {} bound influence(s) preserved.".format(skipped_count)
            if skipped_count
            else ""
        )
        _remove_pending_joints(
            removable,
            "Removed {} pending joint(s).{}".format(len(removable), suffix),
        )

    _run_action(action)


def remove_inverse_selected_joints() -> None:
    def action():
        _require_list_editable()
        selected = set(selected_joint_paths())
        if not selected:
            cmds.warning(
                "Select the joints to keep before using Remove Inverse Selected."
            )
            return

        bound = bound_joint_paths()
        removable = {
            joint
            for joint in _state().get("joints", [])
            if joint not in selected and joint not in bound
        }
        if not removable:
            cmds.warning(
                "No unselected pending joints to remove. "
                "Bound influences are preserved."
            )
            return
        _remove_pending_joints(
            removable,
            "Removed {} inverse-selected pending joint(s); selected joints "
            "and bound influences were preserved.".format(len(removable)),
        )

    _run_action(action)


def remove_all_joints() -> None:
    def action():
        _require_list_editable()
        removable = set(pending_joint_paths())
        if not removable:
            cmds.warning(
                "No pending joints to remove. Bound influences are preserved."
            )
            return
        _remove_pending_joints(
            removable,
            "Removed {} pending joint(s); bound influences were preserved.".format(
                len(removable)
            ),
        )

    _run_action(action)


def lock_selected_joints(locked: bool, inverse: bool = False) -> None:
    def action():
        _require_list_editable()
        selected = set(selected_joint_paths())
        targets = [
            joint
            for joint in _state().get("joints", [])
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

    _run_action(action)


def sync_after_flood_preserving_pending(staged_joints, staged_locks) -> None:
    """Refresh bound influences while retaining still-valid pending rows."""

    _state()["pending_locked_joints"] = set(staged_locks)
    _TOOL_WINDOW._sync_loaded_skin_context()
    current_influences = list(_state().get("joints", []))
    current_set = set(current_influences)
    pending = [
        joint
        for joint in staged_joints
        if joint not in current_set and cmds.objExists(joint)
    ]
    set_joint_list(current_influences + pending)
    _TOOL_WINDOW._update_joint_count_label()


def selected_joint_paths():
    item_to_path = _state().get("joint_item_to_path", {})
    return [
        item_to_path[item_id]
        for item_id in _selected_item_ids()
        if item_id in item_to_path
    ]


def select_joint_paths(joints) -> None:
    global _PROGRAMMATIC_MULTI_SELECT

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return
    path_to_item = _state().get("joint_path_to_item", {})
    item_ids = [
        path_to_item[joint]
        for joint in joints
        if joint in path_to_item
        and _tree_item_exists(control, path_to_item[joint])
    ]

    cmds.treeView(control, edit=True, clearSelection=True)
    _PROGRAMMATIC_MULTI_SELECT = True
    try:
        for item_id in item_ids:
            _set_tree_item_selected(control, item_id, True)
    finally:
        _PROGRAMMATIC_MULTI_SELECT = False


def joint_is_locked(joint: str) -> bool:
    if joint in bound_joint_paths():
        skin_cluster = _state().get("skin_cluster")
        return bool(skin_cluster and is_influence_locked(skin_cluster, joint))
    return joint in _state().get("pending_locked_joints", set())


def _remove_pending_joints(removable, message) -> None:
    removable = set(removable)
    pending_locks = set(_state().get("pending_locked_joints", set()))
    pending_locks.difference_update(removable)
    _state()["pending_locked_joints"] = pending_locks
    remaining = [
        joint for joint in _state().get("joints", []) if joint not in removable
    ]
    set_joint_list(remaining)
    _TOOL_WINDOW._update_joint_count_label()
    _TOOL_WINDOW._info(message)


def _set_joint_lock_states(joints, locked: bool) -> None:
    bound = bound_joint_paths()
    pending_locks = set(_state().get("pending_locked_joints", set()))
    skin_cluster = _state().get("skin_cluster")

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

    _state()["pending_locked_joints"] = pending_locks
    set_joint_list(list(_state().get("joints", [])))
    _TOOL_WINDOW._update_joint_count_label()


def _render_lock_button(item_id: str, joint: str) -> None:
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not _tree_item_exists(control, item_id):
        return
    locked = joint_is_locked(joint)
    icon = _lock_icon(locked)
    if icon:
        cmds.treeView(control, edit=True, image=(item_id, 1, icon))
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

    candidates = _LOCKED_ICON_CANDIDATES if locked else _UNLOCKED_ICON_CANDIDATES
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
    joint = _state().get("joint_item_to_path", {}).get(item_id)
    if not joint:
        return

    def action():
        _TOOL_WINDOW._require_not_busy()
        _set_joint_lock_states([joint], not joint_is_locked(joint))

    _run_action(action)


def _prepare_context_menu(clicked_item) -> bool:
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if clicked_item and _tree_item_exists(control, clicked_item):
        if clicked_item not in set(_selected_item_ids()):
            cmds.treeView(control, edit=True, clearSelection=True)
            _set_tree_item_selected(control, clicked_item, True)
    return True


def _populate_joint_context_menu(menu, *_):
    cmds.popupMenu(menu, edit=True, deleteAllItems=True)
    groups = (
        (
            ("Lock Selected", lambda: lock_selected_joints(True)),
            ("Unlock Selected", lambda: lock_selected_joints(False)),
        ),
        (
            (
                "Lock Inverse Selected",
                lambda: lock_selected_joints(True, inverse=True),
            ),
            (
                "Unlock Inverse Selected",
                lambda: lock_selected_joints(False, inverse=True),
            ),
        ),
        (
            ("Remove Selected", remove_selected_joints),
            ("Remove Inverse Selected", remove_inverse_selected_joints),
            ("Remove All", remove_all_joints),
        ),
        (
            ("Select Vertices", select_vertices),
            ("Select Joints In The Scene", select_joints_in_scene),
        ),
    )
    for group_index, group in enumerate(groups):
        if group_index:
            cmds.menuItem(divider=True, parent=menu)
        for label, callback in group:
            cmds.menuItem(
                label=label,
                parent=menu,
                command=lambda *_args, action=callback: action(),
            )


def _allow_tree_selection_change(_item_id, selected) -> bool:
    return not (_PROGRAMMATIC_MULTI_SELECT and not bool(selected))


def _set_tree_item_selected(control: str, item_id: str, selected: bool) -> None:
    cmds.treeView(
        control,
        edit=True,
        selectItem=(item_id, bool(selected)),
    )


def _selected_item_ids():
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return []
    result = []
    for item_id in _state().get("joint_item_to_path", {}):
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


def _sorted_display_order(joints, display_labels, bound=None):
    mode = _state().get("joint_sort_mode", SORT_A_TO_Z)
    bound = set(bound if bound is not None else bound_joint_paths())

    def label_key(joint):
        return (
            str(display_labels[joint]).casefold(),
            str(joint).casefold(),
        )

    if mode == SORT_Z_TO_A:
        return sorted(joints, key=label_key, reverse=True)
    if mode == SORT_PENDING_JOINTS:
        return sorted(
            joints,
            key=lambda joint: (
                0 if joint not in bound else 1,
                *label_key(joint),
            ),
        )
    return sorted(joints, key=label_key)


def _refresh_bound_joint_paths():
    state = _state()
    bound = set()
    if state.get("has_skin_cluster") and state.get("mesh_shape"):
        try:
            adapter = SkinClusterAdapter.from_mesh(state["mesh_shape"])
            bound = set(adapter.influences())
            state["skin_cluster"] = adapter.skin_cluster
        except Exception:
            bound = set()
    state["bound_joint_paths"] = bound
    return bound


def _normalize_pending_locks(joints, bound) -> None:
    pending_locks = set(_state().get("pending_locked_joints", set()))
    pending_locks.intersection_update(joints)
    pending_locks.difference_update(bound)
    _state()["pending_locked_joints"] = pending_locks


def _reset_row_maps(display_order, labels) -> None:
    state = _state()
    state["joint_display_order"] = list(display_order)
    state["joint_display_to_path"] = {}
    state["joint_path_to_display"] = {}
    state["joint_item_to_path"] = {}
    state["joint_path_to_item"] = {}
    for index, joint in enumerate(display_order):
        item_id = "joint_{:04d}".format(index)
        label = labels[joint]
        state["joint_display_to_path"][label] = joint
        state["joint_path_to_display"][joint] = label
        state["joint_item_to_path"][item_id] = joint
        state["joint_path_to_item"][joint] = item_id


def _create_tree_row(control, item_id, display_label) -> None:
    cmds.treeView(control, edit=True, addItem=(item_id, ""))
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


def _refresh_presentation() -> None:
    callback = _state().get(_PRESENTATION_REFRESH_KEY)
    if callable(callback):
        try:
            callback()
        except Exception:
            pass


def _query_vertical_scroll_position(control):
    if not cmds.treeView(control, exists=True):
        return None
    try:
        return int(
            cmds.treeView(
                control,
                query=True,
                verticalScrollPosition=True,
            )
        )
    except Exception:
        return None


def _restore_vertical_scroll_position(control, position) -> None:
    if position is None or not cmds.treeView(control, exists=True):
        return
    try:
        cmds.treeView(
            control,
            edit=True,
            verticalScrollPosition=max(0, int(position)),
        )
    except Exception:
        pass


def _vertex_component_ranges(mesh_transform: str, vertex_ids):
    ids = sorted({int(vertex_id) for vertex_id in vertex_ids})
    if not ids:
        return []

    components = []
    start = previous = ids[0]
    for vertex_id in ids[1:]:
        if vertex_id == previous + 1:
            previous = vertex_id
            continue
        components.append(_format_vertex_range(mesh_transform, start, previous))
        start = previous = vertex_id
    components.append(_format_vertex_range(mesh_transform, start, previous))
    return components


def _format_vertex_range(mesh_transform: str, start: int, end: int) -> str:
    if start == end:
        return "{}.vtx[{}]".format(mesh_transform, start)
    return "{}.vtx[{}:{}]".format(mesh_transform, start, end)


def _require_list_editable() -> None:
    _TOOL_WINDOW._require_not_busy()
    _TOOL_WINDOW._require_loaded_mesh()


def _run_action(callback) -> None:
    try:
        callback()
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def _state():
    return _TOOL_WINDOW._STATE


def _require_configured() -> None:
    if _TOOL_WINDOW is None:
        raise RuntimeError("AD Skin Tool joint-list UI is not configured.")
