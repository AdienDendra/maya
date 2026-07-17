"""Cross-version treeView compatibility for the v4.1 influence list.

The implementation intentionally uses the Maya 2023-compatible creation contract,
which also remains valid in Maya 2025 and Maya 2026. Tree items are created in one
command and configured in subsequent commands. This avoids Maya's ``Item not
found`` failure when ``addItem`` and item-dependent edit flags are submitted in the
same ``treeView`` call.
"""

import maya.cmds as cmds


# This module keeps its original filename because it was introduced as the Maya
# 2023 compatibility layer. The code path is deliberately shared by Maya 2023,
# 2025, and 2026 rather than branching on the application version.


def patch(component_flood_section) -> None:
    """Install cross-version tree construction and row rendering."""

    component_flood_section._build_joints_section = _build_joints_section
    component_flood_section._set_joint_list = _set_joint_list



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

    Maya processes ``cmds`` calls synchronously, but some treeView versions do not
    resolve an item early enough when ``addItem`` and item-dependent flags are
    combined in a single call. Every row therefore follows an explicit sequence:

        create item
        set display label
        set annotation
        configure button
        apply colour and lock icon

    The sequence is slightly more verbose but deterministic across supported Maya
    versions.
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

        # Important: do not combine these edits with addItem. Maya 2023 can try
        # to resolve displayLabel/button flags before the new item is registered,
        # producing RuntimeError: Item not found. The same conservative sequence
        # is valid in Maya 2025 and Maya 2026.
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
            itemAnnotation=(
                item_id,
                "{}\n{}".format(
                    joint,
                    "Bound influence" if joint in bound_paths else "Pending joint",
                ),
            ),
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

        section._render_lock_button(item_id, joint)

        if joint in previous_selected_paths:
            cmds.treeView(
                control,
                edit=True,
                selectItem=(item_id, True),
            )
