"""Binding UI using shared Blend and Iterations controls."""

import builtins

import maya.cmds as cmds

from ad_skin_tools.core import add_influence
from ad_skin_tools.core import automatic_surface_commands
from ad_skin_tools.ui import joint_list
from ad_skin_tools.ui import smoothing_controls


_TOOL_WINDOW = None
_SKIN_OPERATIONS = None
_BASE_SET_COMMON_ENABLED = None


def install(tool_window_module, skin_operations_module) -> None:
    """Install shared smoothing controls and production bind callbacks."""

    global _TOOL_WINDOW, _SKIN_OPERATIONS, _BASE_SET_COMMON_ENABLED
    _TOOL_WINDOW = tool_window_module
    _SKIN_OPERATIONS = skin_operations_module

    smoothing_controls.configure(tool_window_module)
    _TOOL_WINDOW._build_joints_section = _build_joints_section
    _SKIN_OPERATIONS._build_binding_section = _build_binding_section
    _TOOL_WINDOW.apply_operation = apply_bind_skin
    _TOOL_WINDOW.show_help = show_help

    current_enabled_callback = _SKIN_OPERATIONS._set_common_enabled
    if current_enabled_callback is not _set_common_enabled:
        _BASE_SET_COMMON_ENABLED = current_enabled_callback
    _SKIN_OPERATIONS._set_common_enabled = _set_common_enabled

    _TOOL_WINDOW.WINDOW_LABEL = "AD Skin Weights Tool"
    _TOOL_WINDOW.WINDOW_HEIGHT = 720
    _TOOL_WINDOW.WINDOW_WIDTH = 340


def _build_joints_section() -> None:
    """Build the influence tree, list actions, and shared smoothing controls."""

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
        pressCommand=(1, joint_list._on_lock_button_pressed),
        contextMenuCommand=joint_list._prepare_context_menu,
        selectCommand=joint_list._allow_tree_selection_change,
    )
    cmds.popupMenu(
        joint_list.CTRL_JOINT_CONTEXT_MENU,
        parent=_TOOL_WINDOW.CTRL_JOINT_LIST,
        button=3,
        postMenuCommand=joint_list._populate_joint_context_menu,
    )

    _TOOL_WINDOW._button_row(
        [
            (
                "Add Joints To The List",
                lambda *_: joint_list.add_selected_joints(),
            ),
            (
                "Select Joints In The List",
                lambda *_: joint_list.show_selected_joints_in_list(),
            ),
        ],
        height=30,
    )
    smoothing_controls.build_controls()

    cmds.setParent("..")
    cmds.setParent("..")


def _build_binding_section() -> None:
    cmds.frameLayout(
        label="Binding",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    _SKIN_OPERATIONS._named_button_row(
        [
            (
                _TOOL_WINDOW.CTRL_BIND_BUTTON,
                "Bind Skin",
                lambda *_: _TOOL_WINDOW.apply_operation(),
            ),
            (
                _SKIN_OPERATIONS.CTRL_ADD_INFLUENCE_BUTTON,
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


def apply_bind_skin() -> None:
    """Calculate final Region ownership and apply shared smoothing values."""

    wait_cursor_active = False
    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_unskinned_mesh()

        joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        if builtins.len(joints) < 2:
            raise RuntimeError(
                "Bind Skin requires at least two joints.\n\n"
                "Select joints in Maya and click Add Joints To The List."
            )

        values = smoothing_controls.query_values()
        status = "Calculating final blocking ownership..."
        if values.iterations > 0:
            status = (
                "Calculating final blocking and smoothing with Blend {:.3f}, "
                "Iterations {}..."
            ).format(values.blend, values.iterations)

        _TOOL_WINDOW._set_bind_busy(True, status)
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        result = automatic_surface_commands.bind_object_automatic_surface(
            mesh=_TOOL_WINDOW._STATE["mesh_transform"],
            joints=joints,
            options=automatic_surface_commands.AutomaticSurfaceBindOptions(
                smoothing_blend=values.blend,
                smoothing_iterations=values.iterations,
            ),
        )

        _TOOL_WINDOW._sync_loaded_skin_context()
        builtins.AD_SKIN_BIND_RESULT = result
        automatic_surface_commands.print_report(result)

        if values.iterations == 0:
            message = (
                "Bind complete: {} vertices with final hard blocking weights."
                .format(result.vertex_count)
            )
        else:
            message = (
                "Bind complete: Blend {:.3f}, Iterations {}, Max Influences {}."
                .format(
                    result.smoothing_blend,
                    result.smoothing_iterations,
                    result.effective_maximum_influences,
                )
            )
        _TOOL_WINDOW._info(message)
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)
    finally:
        if wait_cursor_active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass
        _TOOL_WINDOW._set_bind_busy(False)


def apply_add_influence() -> None:
    """Claim pending-joint regions and apply shared smoothing values."""

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
            joint
            for joint in targets
            if joint_list.joint_is_locked(joint)
        ]
        if locked_targets:
            raise RuntimeError(
                "Unlock the selected pending joint(s) before Add Influence:\n{}"
                .format("\n".join(locked_targets))
            )

        staged_joints = builtins.list(
            _TOOL_WINDOW._STATE.get("joints", [])
        )
        staged_locks = set(
            _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
        )
        values = smoothing_controls.query_values()
        status = "Calculating final Region ownership for new influences..."
        if values.iterations > 0:
            status = (
                "Calculating Region claims and smoothing with Blend {:.3f}, "
                "Iterations {}..."
            ).format(values.blend, values.iterations)

        _SKIN_OPERATIONS._set_add_influence_busy(True, status)
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        result = add_influence.add_influences_by_region(
            mesh=_TOOL_WINDOW._STATE["mesh_shape"],
            target_joints=targets,
            smoothing_blend=values.blend,
            smoothing_iterations=values.iterations,
        )

        joint_list.sync_after_flood_preserving_pending(
            staged_joints,
            staged_locks,
        )
        joint_list.select_joint_paths(result.target_joints)

        builtins.AD_SKIN_ADD_INFLUENCE_RESULT = result
        add_influence.print_report(result)
        _TOOL_WINDOW._info(
            (
                "Added {} influence(s); {} vertices claimed; Blend {:.3f}, "
                "Iterations {}."
            ).format(
                len(result.target_joints),
                result.claimed_vertex_count,
                result.smoothing_blend,
                result.smoothing_iterations,
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
        _SKIN_OPERATIONS._set_add_influence_busy(False)


def _set_common_enabled(enabled) -> None:
    if _BASE_SET_COMMON_ENABLED is not None:
        _BASE_SET_COMMON_ENABLED(enabled)
    smoothing_controls.set_enabled(enabled)


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool",
        message=(
            "Shared Smoothing\n"
            "Blend and Iterations are shared by Bind Skin, Add Influence, and "
            "Component Smooth. Blend controls how far one iteration moves weights "
            "toward the connected-neighbour average. Iterations is the exact number "
            "of smoothing repetitions. There is no hidden multiplier. Iterations 0 "
            "keeps Bind Skin and Add Influence in hard mode. Component Smooth "
            "requires at least 1 iteration.\n\n"
            "Binding\n"
            "Bind Skin starts from final hard Region ownership. Add Influence uses "
            "the existing skin weights as fixed boundary context and changes only "
            "the unlocked rows claimed by pending joints. Positive smoothing uses "
            "Max Influences 5, or fewer when fewer joints are available. Region "
            "remains the blocking authority."
        ),
        button=["OK"],
    )
