"""v7.4 Binding UI: integer smoothing iterations from zero to ten."""

import builtins

import maya.cmds as cmds

from ad_skin_tools.core import add_influence
from ad_skin_tools.core import automatic_surface_commands
from ad_skin_tools.ui import joint_list


CTRL_SMOOTHING_ITERATIONS = "adSkin_smoothingIterations"
MINIMUM_ITERATIONS = 0
MAXIMUM_ITERATIONS = 10
DEFAULT_ITERATIONS = 0

_TOOL_WINDOW = None
_SKIN_OPERATIONS = None


def install(tool_window_module, skin_operations_module) -> None:
    """Install the v7.4 slider and production bind callback idempotently."""

    global _TOOL_WINDOW, _SKIN_OPERATIONS
    _TOOL_WINDOW = tool_window_module
    _SKIN_OPERATIONS = skin_operations_module

    _TOOL_WINDOW._STATE.setdefault(
        "smoothing_iterations",
        DEFAULT_ITERATIONS,
    )
    _SKIN_OPERATIONS._build_binding_section = _build_binding_section
    _TOOL_WINDOW.apply_operation = apply_bind_skin
    _TOOL_WINDOW.show_help = show_help

    current_set_common_enabled = _SKIN_OPERATIONS._set_common_enabled
    if current_set_common_enabled is not _set_common_enabled:
        _SKIN_OPERATIONS._V74_BASE_SET_COMMON_ENABLED = (
            current_set_common_enabled
        )
    _SKIN_OPERATIONS._set_common_enabled = _set_common_enabled

    _TOOL_WINDOW.WINDOW_LABEL = "AD Skin Weights Tool v7.4"
    _TOOL_WINDOW.WINDOW_HEIGHT = 690
    _TOOL_WINDOW.WINDOW_WIDTH = 340
    _TOOL_WINDOW._V74_SMOOTHING_UI_INSTALLED = True


def _build_binding_section() -> None:
    cmds.frameLayout(
        label="Binding",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    cmds.intSliderGrp(
        CTRL_SMOOTHING_ITERATIONS,
        label="Smoothing Iterations",
        field=True,
        minValue=MINIMUM_ITERATIONS,
        maxValue=MAXIMUM_ITERATIONS,
        fieldMinValue=MINIMUM_ITERATIONS,
        fieldMaxValue=MAXIMUM_ITERATIONS,
        value=_stored_iterations(),
        step=1,
        columnWidth3=(125, 42, 145),
        adjustableColumn=3,
        dragCommand=_store_iterations,
        changeCommand=_store_iterations,
        annotation=(
            "0 preserves final v3.2 hard blocking. Values 1-10 apply topology "
            "smoothing and use Max Influences 5, or the total joint count when "
            "fewer than five joints are listed."
        ),
    )
    cmds.text(
        label=(
            "0 = hard blocking. 1-10 = smoothed weights, up to 5 influences "
            "per vertex."
        ),
        align="left",
        wordWrap=True,
    )

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
    """Run final v3.2 blocking, then apply the selected v7.4 smoothing passes."""

    wait_cursor_active = False
    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_unskinned_mesh()

        joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        if builtins.len(joints) < 2:
            raise RuntimeError(
                "Bind Skin requires at least two joints.\n\n"
                "Select joints in Maya and click Add Selected."
            )

        iterations = _query_iterations()
        status = "Calculating final blocking ownership..."
        if iterations > 0:
            status = (
                "Calculating final blocking and smoothing {} iteration(s)..."
                .format(iterations)
            )

        _TOOL_WINDOW._set_bind_busy(True, status)
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        result = automatic_surface_commands.bind_object_automatic_surface(
            mesh=_TOOL_WINDOW._STATE["mesh_transform"],
            joints=joints,
            options=automatic_surface_commands.AutomaticSurfaceBindOptions(
                smoothing_iterations=iterations,
            ),
        )

        _TOOL_WINDOW._sync_loaded_skin_context()
        builtins.AD_SKIN_V74_UI_RESULT = result
        automatic_surface_commands.print_report(result)

        if iterations == 0:
            message = (
                "Bind complete: {} vertices with final hard blocking weights."
                .format(result.vertex_count)
            )
        else:
            message = (
                "Bind complete: {} smoothing iteration(s), Max Influences {}."
                .format(
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
    """Claim pending-joint regions, then optionally smooth only claimed rows."""

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

        staged_joints = builtins.list(_TOOL_WINDOW._STATE.get("joints", []))
        staged_locks = set(
            _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
        )
        iterations = _query_iterations()
        status = "Calculating final Region ownership for new influences..."
        if iterations > 0:
            status = (
                "Calculating Region claims and smoothing {} iteration(s)..."
                .format(iterations)
            )

        _SKIN_OPERATIONS._set_add_influence_busy(True, status)
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        result = add_influence.add_influences_by_region(
            mesh=_TOOL_WINDOW._STATE["mesh_shape"],
            target_joints=targets,
            smoothing_iterations=iterations,
        )

        joint_list.sync_after_flood_preserving_pending(
            staged_joints,
            staged_locks,
        )
        joint_list.select_joint_paths(result.target_joints)

        builtins.AD_SKIN_ADD_INFLUENCE_RESULT = result
        add_influence.print_report(result)
        _TOOL_WINDOW._info(
            "Added {} influence(s); {} vertices claimed; smoothing {}.".format(
                len(result.target_joints),
                result.claimed_vertex_count,
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


def _stored_iterations() -> int:
    value = int(
        _TOOL_WINDOW._STATE.get(
            "smoothing_iterations",
            DEFAULT_ITERATIONS,
        )
    )
    return max(MINIMUM_ITERATIONS, min(MAXIMUM_ITERATIONS, value))


def _store_iterations(value=None, *_unused) -> None:
    if value is None:
        value = _stored_iterations()
    value = max(
        MINIMUM_ITERATIONS,
        min(MAXIMUM_ITERATIONS, int(value)),
    )
    _TOOL_WINDOW._STATE["smoothing_iterations"] = value


def _query_iterations() -> int:
    if cmds.intSliderGrp(CTRL_SMOOTHING_ITERATIONS, exists=True):
        value = cmds.intSliderGrp(
            CTRL_SMOOTHING_ITERATIONS,
            query=True,
            value=True,
        )
        _store_iterations(value)
    return _stored_iterations()


def _set_common_enabled(enabled) -> None:
    _SKIN_OPERATIONS._V74_BASE_SET_COMMON_ENABLED(enabled)
    if cmds.intSliderGrp(CTRL_SMOOTHING_ITERATIONS, exists=True):
        cmds.intSliderGrp(
            CTRL_SMOOTHING_ITERATIONS,
            edit=True,
            enable=bool(enabled),
        )


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool v7.4",
        message=(
            "Binding\n"
            "- Smoothing Iterations 0: preserve final v3.2 hard blocking.\n"
            "- Smoothing Iterations 1-10: diffuse weights through mesh topology.\n"
            "- Positive smoothing uses Max Influences 5, or fewer when the joint "
            "list contains fewer than five influences.\n"
            "- Bind Skin: create the initial skinCluster from all listed joints.\n"
            "- Add Influence: calculate final Region ownership for selected pending "
            "joints and modify only their unlocked claimed rows.\n\n"
            "Component\n"
            "- Flood: select one influence and mesh components.\n\n"
            "Region remains the final blocking authority. Smoothing does not "
            "recalculate ownership."
        ),
        button=["OK"],
    )
