"""Cross-version treeView compatibility for the v4.1 influence list.

The implementation intentionally uses the Maya 2023-compatible creation contract,
which also remains valid in Maya 2025 and Maya 2026. Tree items are created in one
command and configured in subsequent commands. Bulk lock actions rebuild the tree
from authoritative state so stale row IDs cannot survive a Flood refresh.
"""

import maya.cmds as cmds


# This module keeps its original filename because it was introduced as the Maya
# 2023 compatibility layer. The code path is deliberately shared by Maya 2023,
# 2025, and 2026 rather than branching on the application version.


def patch(component_flood_section) -> None:
    """Install cross-version tree construction and row-state operations."""

    component_flood_section._build_joints_section = _build_joints_section
    component_flood_section._set_joint_list = _set_joint_list
    component_flood_section._set_joint_lock_states = _set_joint_lock_states
    component_flood_section._render_lock_button = _render_lock_button
    component_flood_section._selected_item_ids = _selected_item_ids


def _build_joints_section() -> None:
    # Import inside the callback so module reloads always use the current v4.1
    # implementation rather than a stale module reference.
    from ad_skin_tools.ui import component_flood_section as section

    tool_window = section._TOOL_WINDOW
    cmds.frameLayout(
        label="Joints / Influences",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)

    cmds.treeView(
        tool_window.CTRL_JOINT_LIST,
        allowMultiSelection=True,
        allowDragAndDrop=False,
        allowReparenting=False,
        enableKeys=True,
        height=220,
        numberOfButtons=1,
        attachButtonRight=False,
        preventOverride=True,
        pressCommand=(1, section._on_lock_button_pressed),
        contextMenuCommand=section._prepare_context_menu,
    )
    cmds.popupMenu(
        section.CTRL_JOINT_CONTEXT_MENU,
        parent=tool_window.CTRL_JOINT_LIST,
        button=3,
        postMenuCommand=section._populate_joint_context_menu,
    )

    tool_window._button_row(
        [
            ("Add Joints To The List", lambda *_: section.add_selected_joints()),
            (
                "Show Joints In The List",
                lambda *_: section.show_selected_joints_in_list(),
            ),
        ],
        height=30,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _set_joint_list(joints) -> None:
    """Render stable joint rows across Maya 2023, 2025, and 2026.

    Every row follows an explicit sequence:

        create item
        set display label
        configure button
        apply colour and lock icon

    No per-joint annotation or button tooltip is installed. This keeps the list
    visually quiet while retaining lock state through the icon itself.
    """

    from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
    from ad_skin_tools.ui import component_flood_section as section

    tool_window = section._TOOL_WINDOW
    normalized_joints = tool_window._unique_joint_paths(joints)
    previous_selected_paths = set(section._selected_joint_paths())

    tool_window._STATE["joints"] = normalized_joints
    tool_window._STATE["joint_display_to_path"] = {}
    tool_window._STATE["joint_path_to_display"] = {}
    tool_window._STATE["joint_item_to_path"] = {}
    tool_window._STATE["joint_path_to_item"] = {}

    bound_paths = set()
    if tool_window._STATE.get("has_skin_cluster"):
        try:
            adapter = SkinClusterAdapter.from_mesh(
                tool_window._STATE["mesh_shape"]
            )
            bound_paths = set(adapter.influences())
            tool_window._STATE["skin_cluster"] = adapter.skin_cluster
        except Exception:
            bound_paths = set()
    tool_window._STATE["bound_joint_paths"] = bound_paths

    pending_locks = set(
        tool_window._STATE.get("pending_locked_joints", set())
    )
    pending_locks.intersection_update(normalized_joints)
    pending_locks.difference_update(bound_paths)
    tool_window._STATE["pending_locked_joints"] = pending_locks

    control = tool_window.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return

    cmds.treeView(control, edit=True, removeAll=True)

    for index, joint in enumerate(normalized_joints):
        item_id = "joint_{:04d}".format(index)
        display_label = tool_window._make_unique_joint_label(
            joint,
            normalized_joints,
        )
        tool_window._STATE["joint_display_to_path"][display_label] = joint
        tool_window._STATE["joint_path_to_display"][joint] = display_label
        tool_window._STATE["joint_item_to_path"][item_id] = joint
        tool_window._STATE["joint_path_to_item"][joint] = item_id

        # Do not combine these edits with addItem. Maya 2023 can try to resolve
        # item-dependent flags before the new item is registered. The same
        # conservative sequence remains valid in Maya 2025 and Maya 2026.
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
                textColor=(item_id,) + section._BOUND_TEXT_COLOR,
            )

        _render_lock_button(item_id, joint)

        if joint in previous_selected_paths:
            cmds.treeView(
                control,
                edit=True,
                selectItem=(item_id, True),
            )


def _set_joint_lock_states(joints, locked: bool) -> None:
    """Store lock changes, then rebuild the entire tree from authoritative state.

    A Flood may change the influence list and therefore row indices. Repainting
    individual rows from an older ``joint_path_to_item`` mapping can address an
    item that no longer exists. Rebuilding once after a bulk operation keeps the
    mapping and actual tree contents atomic.
    """

    from ad_skin_tools.core.influence_lock import set_influence_locked
    from ad_skin_tools.core.undo import undo_chunk
    from ad_skin_tools.ui import component_flood_section as section

    tool_window = section._TOOL_WINDOW
    bound = set(tool_window._STATE.get("bound_joint_paths", set()))
    pending_locks = set(
        tool_window._STATE.get("pending_locked_joints", set())
    )
    skin_cluster = tool_window._STATE.get("skin_cluster")

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

    tool_window._STATE["pending_locked_joints"] = pending_locks

    # One authoritative repaint replaces per-row edits. Selection is preserved by
    # _set_joint_list using only item IDs that still exist in the current tree.
    _set_joint_list(list(tool_window._STATE.get("joints", [])))
    tool_window._update_joint_count_label()


def _render_lock_button(item_id: str, joint: str) -> None:
    """Render only the icon/text state; intentionally install no tooltip."""

    from ad_skin_tools.ui import component_flood_section as section

    tool_window = section._TOOL_WINDOW
    control = tool_window.CTRL_JOINT_LIST
    if not _tree_item_exists(control, item_id):
        return

    locked = section._joint_is_locked(joint)
    icon = section._lock_icon(locked)
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


def _selected_item_ids():
    """Return selected rows while silently discarding stale mapping entries."""

    from ad_skin_tools.ui import component_flood_section as section

    tool_window = section._TOOL_WINDOW
    control = tool_window.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return []

    result = []
    for item_id in tool_window._STATE.get("joint_item_to_path", {}):
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
    """Query tree membership before using an item-dependent flag."""

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
