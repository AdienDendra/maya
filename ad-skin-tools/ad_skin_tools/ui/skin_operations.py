"""Binding and component operation UI."""

import builtins
from contextlib import contextmanager

import maya.cmds as cmds

from ad_skin_tools.core import add_influence
from ad_skin_tools.ui import joint_list

CTRL_ADD_INFLUENCE_BUTTON = "adSkin_addInfluenceButton"

_TOOL_WINDOW = None
_ORIGINAL_LOAD_SKIN_WEIGHT = None
_ORIGINAL_SYNC_LOADED_SKIN_CONTEXT = None


def install(tool_window_module) -> None:
    """Configure operation UI and wrap the canonical mesh-context refresh."""

    global _TOOL_WINDOW
    global _ORIGINAL_LOAD_SKIN_WEIGHT, _ORIGINAL_SYNC_LOADED_SKIN_CONTEXT

    _TOOL_WINDOW = tool_window_module
    joint_list.configure(tool_window_module)
    _register_tool_window_api(tool_window_module)

    if tool_window_module.load_skin_weight is not load_skin_weight:
        _ORIGINAL_LOAD_SKIN_WEIGHT = tool_window_module.load_skin_weight
        _ORIGINAL_SYNC_LOADED_SKIN_CONTEXT = (
            tool_window_module._sync_loaded_skin_context
        )
        tool_window_module.load_skin_weight = load_skin_weight
        tool_window_module._sync_loaded_skin_context = _sync_loaded_skin_context

    tool_window_module.WINDOW_LABEL = "AD Skin Weights Tool"
    tool_window_module.WINDOW_HEIGHT = 665
    tool_window_module.WINDOW_WIDTH = 340


def _register_tool_window_api(tool_window_module) -> None:
    assignments = {
        "_build_skin_cluster_section": _build_skin_cluster_section,
        "_build_joints_section": joint_list.build_section,
        "_build_initial_bind_section": _build_operation_sections,
        "_set_joint_list": joint_list.set_joint_list,
        "add_selected_joints": joint_list.add_selected_joints,
        "remove_selected_joints": joint_list.remove_selected_joints,
        "remove_all_joints": joint_list.remove_all_joints,
        "show_selected_joints_in_list": joint_list.show_selected_joints_in_list,
        "_set_bind_busy": _set_bind_busy,
        "show_help": show_help,
        "_set_option_menu_items": _set_skin_cluster_field_items,
    }
    for name, callback in assignments.items():
        setattr(tool_window_module, name, callback)


def _build_skin_cluster_section() -> None:
    cmds.frameLayout(
        label="Mesh / Skin Context",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)

    _TOOL_WINDOW._label_control_row(
        "Skin Cluster",
        lambda: cmds.textField(
            _TOOL_WINDOW.CTRL_SKIN_MENU,
            text="<no skinCluster>",
            editable=False,
        ),
    )
    cmds.text(
        _TOOL_WINDOW.CTRL_MESH_LABEL,
        label="Mesh: <none>",
        align="left",
    )
    cmds.text(
        _TOOL_WINDOW.CTRL_MODE_LABEL,
        label="",
        visible=False,
        manage=False,
    )
    cmds.text(
        _TOOL_WINDOW.CTRL_JOINT_LABEL,
        label="Joints: 0",
        align="left",
    )
    _TOOL_WINDOW._button_row(
        [("Load Mesh", lambda *_: _TOOL_WINDOW.load_skin_weight())],
        height=30,
    )
    cmds.setParent("..")
    cmds.setParent("..")


def load_skin_weight(silent=False):
    """Run the canonical loader, then apply compact display labels."""

    if _ORIGINAL_LOAD_SKIN_WEIGHT is None:
        return None
    result = _ORIGINAL_LOAD_SKIN_WEIGHT(silent=silent)
    _refresh_mesh_context_labels()
    return result


def _sync_loaded_skin_context():
    """Run the canonical skin refresh, then apply compact display labels."""

    if _ORIGINAL_SYNC_LOADED_SKIN_CONTEXT is None:
        raise RuntimeError("Skin-context refresh is unavailable.")
    result = _ORIGINAL_SYNC_LOADED_SKIN_CONTEXT()
    _refresh_mesh_context_labels()
    return result


def _set_skin_cluster_field_items(control_name, items) -> None:
    value = items[0] if items else "<no skinCluster>"
    if cmds.textField(control_name, exists=True):
        cmds.textField(control_name, edit=True, text=str(value))


def _refresh_mesh_context_labels() -> None:
    if _TOOL_WINDOW is None:
        return
    state = _state()
    _set_text_field(
        _TOOL_WINDOW.CTRL_SKIN_MENU,
        state.get("skin_cluster") or "<no skinCluster>",
    )
    mesh_transform = state.get("mesh_transform")
    mesh_label = _short_dag_name(mesh_transform) if mesh_transform else "<none>"
    _set_text(
        _TOOL_WINDOW.CTRL_MESH_LABEL,
        "Mesh: {}".format(mesh_label),
    )


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
    from ad_skin_tools.ui import component_section

    component_section.build_section()


def apply_add_influence() -> None:
    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _state().get("has_skin_cluster"):
            raise RuntimeError(
                "Add Influence requires an existing skinCluster.\n\n"
                "Use Bind Skin first."
            )

        targets = _selected_pending_targets()
        staged_joints = list(_state().get("joints", []))
        staged_locks = set(_state().get("pending_locked_joints", set()))

        _set_add_influence_busy(
            True,
            "Calculating Region ownership for new influences...",
        )
        with _wait_cursor():
            result = add_influence.add_influences_by_region(
                mesh=_state()["mesh_shape"],
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
        _request_weight_preview_refresh()
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)
    finally:
        _set_add_influence_busy(False)


def _selected_pending_targets():
    selected = list(joint_list.selected_joint_paths())
    bound = joint_list.bound_joint_paths()
    targets = [joint for joint in selected if joint not in bound]
    if not targets:
        raise RuntimeError(
            "Select at least one new pending joint in the influence list."
        )

    locked = [joint for joint in targets if joint_list.joint_is_locked(joint)]
    if locked:
        raise RuntimeError(
            "Unlock the selected pending joint(s) before Add Influence:\n{}".format(
                "\n".join(locked)
            )
        )
    return targets


def _set_bind_busy(busy, status="") -> None:
    _set_operation_busy(
        control=_TOOL_WINDOW.CTRL_BIND_BUTTON,
        busy=busy,
        busy_label="Binding...",
        idle_label="Bind Skin",
        status=status,
    )


def _set_add_influence_busy(busy, status="") -> None:
    _set_operation_busy(
        control=CTRL_ADD_INFLUENCE_BUTTON,
        busy=busy,
        busy_label="Adding...",
        idle_label="Add Influence",
        status=status,
    )


def _set_operation_busy(control, busy, busy_label, idle_label, status=""):
    busy = bool(busy)
    _set_common_enabled(not busy)
    _state()["busy"] = busy
    if cmds.button(control, exists=True):
        cmds.button(
            control,
            edit=True,
            label=busy_label if busy else idle_label,
        )
    _set_progress_status(busy, status)


def _set_common_enabled(enabled) -> None:
    from ad_skin_tools.ui import component_section

    enabled = bool(enabled)
    for control in (
        _TOOL_WINDOW.CTRL_BIND_BUTTON,
        CTRL_ADD_INFLUENCE_BUTTON,
        component_section.CTRL_FLOOD_BUTTON,
        component_section.CTRL_SMOOTH_BUTTON,
    ):
        if cmds.button(control, exists=True):
            cmds.button(control, edit=True, enable=enabled)
    if cmds.treeView(_TOOL_WINDOW.CTRL_JOINT_LIST, exists=True):
        cmds.treeView(
            _TOOL_WINDOW.CTRL_JOINT_LIST,
            edit=True,
            enable=enabled,
        )


def _set_progress_status(busy, status) -> None:
    busy = bool(busy)
    if cmds.progressBar(_TOOL_WINDOW.CTRL_BIND_PROGRESS, exists=True):
        kwargs = {"edit": True, "visible": busy}
        try:
            cmds.progressBar(
                _TOOL_WINDOW.CTRL_BIND_PROGRESS,
                isIndeterminate=busy,
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
            visible=busy,
        )
    _refresh()


def _named_button_row(buttons, height=28, gap=4):
    layout = cmds.formLayout(numberOfDivisions=100, height=height)
    count = len(buttons)
    for index, (name, label, callback) in enumerate(buttons):
        left_position = int(index * 100 / count)
        right_position = int((index + 1) * 100 / count)
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
                (
                    button,
                    "left",
                    0 if index == 0 else gap // 2,
                    left_position,
                ),
                (
                    button,
                    "right",
                    0 if index == count - 1 else gap // 2,
                    right_position,
                ),
            ],
        )
    cmds.setParent("..")
    return layout


@contextmanager
def _wait_cursor():
    active = False
    try:
        cmds.waitCursor(state=True)
        active = True
        cmds.refresh(force=True)
        yield
    finally:
        if active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass


def _request_weight_preview_refresh() -> None:
    callback = _state().get("skin_weight_mode_refresh")
    if callable(callback):
        callback()


def _set_text_field(control, value) -> None:
    if cmds.textField(control, exists=True):
        cmds.textField(control, edit=True, text=str(value))


def _set_text(control, value) -> None:
    if cmds.text(control, exists=True):
        cmds.text(control, edit=True, label=str(value))


def _short_dag_name(node) -> str:
    return str(node).rsplit("|", 1)[-1]


def _refresh() -> None:
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _state():
    return _TOOL_WINDOW._STATE


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool",
        message=(
            "Binding\n"
            "- Bind Skin binds an unskinned loaded mesh using all listed joints.\n"
            "- Add Influence calculates Region ownership for selected pending joints.\n\n"
            "Component\n"
            "- Flood assigns selected mesh components to one selected influence.\n"
            "- Smooth diffuses existing weights inside the selected scope.\n\n"
            "Locked influence values remain unchanged."
        ),
        button=["OK"],
    )
