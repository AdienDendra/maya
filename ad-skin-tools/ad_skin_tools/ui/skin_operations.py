"""Compact Maya-centric binding and component operation UI."""

import builtins

import maya.cmds as cmds

from ad_skin_tools.core import add_influence
from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_list


CTRL_ADD_INFLUENCE_BUTTON = "adSkin_addInfluenceButton"

_TOOL_WINDOW = None


def install(tool_window_module) -> None:
    """Install existing behaviour, then replace the operation UI."""

    global _TOOL_WINDOW
    _TOOL_WINDOW = tool_window_module

    component_flood_section.install(tool_window_module)

    tool_window_module._build_initial_bind_section = _build_operation_sections
    tool_window_module._set_bind_busy = _set_bind_busy
    tool_window_module.show_help = show_help

    component_flood_section._set_flood_busy = _set_flood_busy

    tool_window_module.WINDOW_LABEL = "AD Skin Weights Tool v5.0"
    tool_window_module.WINDOW_HEIGHT = 650
    tool_window_module.WINDOW_WIDTH = 340
    tool_window_module._SKIN_OPERATIONS_UI_INSTALLED = True


def _build_operation_sections() -> None:
    _build_binding_section()
    _build_component_section()


def _build_binding_section() -> None:
    cmds.frameLayout(
        label="Binding",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    _named_button_row(
        [
            (
                _TOOL_WINDOW.CTRL_BIND_BUTTON,
                "Bind Skin",
                lambda *_: _TOOL_WINDOW.apply_operation(),
            ),
            (
                CTRL_ADD_INFLUENCE_BUTTON,
                "Add Influence",
                lambda *_: apply_add_influence(),
            ),
        ],
        height=38,
    )

    _TOOL_WINDOW._create_bind_progress_bar()
    cmds.text(
        _TOOL_WINDOW.CTRL_BIND_STATUS,
        label="",
        align="left",
        wordWrap=True,
        visible=False,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def _build_component_section() -> None:
    cmds.frameLayout(
        label="Component",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    _named_button_row(
        [
            (
                component_flood_section.CTRL_FLOOD_BUTTON,
                "Flood",
                lambda *_: component_flood_section.apply_component_flood(),
            ),
        ],
        height=38,
    )
    cmds.text(
        component_flood_section.CTRL_FLOOD_STATUS,
        label="",
        align="left",
        wordWrap=True,
        visible=False,
    )

    cmds.setParent("..")
    cmds.setParent("..")


def apply_add_influence() -> None:
    wait_cursor_active = False

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            raise RuntimeError(
                "Add Influence requires an existing skinCluster.\n\n"
                "Use Bind Skin first."
            )

        selected_rows = builtins.list(joint_list.selected_joint_paths())
        bound = set(_TOOL_WINDOW._STATE.get("bound_joint_paths", set()))
        targets = [joint for joint in selected_rows if joint not in bound]
        if not targets:
            raise RuntimeError(
                "Select at least one new pending joint in the influence list."
            )

        locked_targets = [
            joint for joint in targets
            if joint_list.joint_is_locked(joint)
        ]
        if locked_targets:
            raise RuntimeError(
                "Unlock the selected pending joint(s) before Add Influence:\n{}"
                .format("\n".join(locked_targets))
            )

        staged_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        staged_locks = set(
            _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
        )

        _set_add_influence_busy(
            True,
            "Calculating Region ownership for new influences...",
        )
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        result = add_influence.add_influences_by_region(
            mesh=_TOOL_WINDOW._STATE["mesh_shape"],
            target_joints=targets,
        )

        joint_list.sync_after_flood_preserving_pending(
            staged_joints,
            staged_locks,
        )
        joint_list.select_joint_paths(result.target_joints)

        builtins.AD_SKIN_ADD_INFLUENCE_RESULT = result
        add_influence.print_report(result)

        _TOOL_WINDOW._info(
            "Added {} influence(s); {} vertices claimed.".format(
                len(result.target_joints),
                result.claimed_vertex_count,
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
        _set_add_influence_busy(False)


def _set_bind_busy(busy, status="") -> None:
    _set_common_enabled(not busy)
    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    if cmds.button(_TOOL_WINDOW.CTRL_BIND_BUTTON, exists=True):
        cmds.button(
            _TOOL_WINDOW.CTRL_BIND_BUTTON,
            edit=True,
            label="Binding..." if busy else "Bind Skin",
        )

    _set_progress_status(busy, status)


def _set_add_influence_busy(busy, status="") -> None:
    _set_common_enabled(not busy)
    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    if cmds.button(CTRL_ADD_INFLUENCE_BUTTON, exists=True):
        cmds.button(
            CTRL_ADD_INFLUENCE_BUTTON,
            edit=True,
            label="Adding..." if busy else "Add Influence",
        )

    _set_progress_status(busy, status)


def _set_flood_busy(busy, status="") -> None:
    if _TOOL_WINDOW is None:
        return

    _set_common_enabled(not busy)
    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    control = component_flood_section.CTRL_FLOOD_BUTTON
    if cmds.button(control, exists=True):
        cmds.button(
            control,
            edit=True,
            label="Flooding..." if busy else "Flood",
        )

    status_control = component_flood_section.CTRL_FLOOD_STATUS
    if cmds.text(status_control, exists=True):
        cmds.text(
            status_control,
            edit=True,
            label=status if busy else "",
            visible=bool(busy),
        )

    _refresh()


def _set_common_enabled(enabled) -> None:
    for control in (
        _TOOL_WINDOW.CTRL_BIND_BUTTON,
        CTRL_ADD_INFLUENCE_BUTTON,
        component_flood_section.CTRL_FLOOD_BUTTON,
    ):
        if cmds.button(control, exists=True):
            cmds.button(control, edit=True, enable=bool(enabled))

    if cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        cmds.treeView(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            edit=True,
            enable=bool(enabled),
        )


def _set_progress_status(busy, status) -> None:
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

    _refresh()


def _named_button_row(buttons, height=28, gap=4):
    layout = cmds.formLayout(numberOfDivisions=100, height=height)
    count = len(buttons)

    for index, (name, label, callback) in enumerate(buttons):
        left_position = int(index * 100 / count)
        right_position = int((index + 1) * 100 / count)
        left_offset = 0 if index == 0 else gap // 2
        right_offset = 0 if index == count - 1 else gap // 2

        button = cmds.button(
            name,
            label=label,
            height=height,
            command=callback,
        )
        cmds.formLayout(
            layout,
            edit=True,
            attachForm=[
                (button, "top", 1),
                (button, "bottom", 1),
            ],
            attachPosition=[
                (button, "left", left_offset, left_position),
                (button, "right", right_offset, right_position),
            ],
        )

    cmds.setParent("..")
    return layout


def _refresh() -> None:
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool v5.0",
        message=(
            "Binding\n"
            "- Bind Skin: bind an unskinned loaded mesh using all listed joints.\n"
            "- Add Influence: select pending joints in the list and calculate "
            "their Region ownership on the existing skinCluster.\n\n"
            "Component\n"
            "- Flood: select one joint in the list, then select mesh components.\n\n"
            "Locked influences keep their existing ownership."
        ),
        button=["OK"],
    )
