"""Maya 2023-compatible builder for the v4.1 influence tree.

Maya 2023's ``treeView`` command does not expose the newer ``emptyLabel`` flag.
Keep the row rendering and callbacks in ``component_flood_section`` while using a
creation contract that is valid in the user's Maya version.
"""

import maya.cmds as cmds


def patch(component_flood_section) -> None:
    """Install the Maya 2023-safe builder into the v4.1 composition module."""

    component_flood_section._build_joints_section = _build_joints_section


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
