"""v7.3 Binding UI: integer smoothing iterations from zero to ten."""

import builtins

import maya.cmds as cmds

from ad_skin_tools.core import automatic_surface_commands


CTRL_SMOOTHING_ITERATIONS = "adSkin_smoothingIterations"
MINIMUM_ITERATIONS = 0
MAXIMUM_ITERATIONS = 10
DEFAULT_ITERATIONS = 0

_TOOL_WINDOW = None
_SKIN_OPERATIONS = None


def install(tool_window_module, skin_operations_module) -> None:
    """Install the v7.3 slider and production bind callback idempotently."""

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
        _SKIN_OPERATIONS._V73_BASE_SET_COMMON_ENABLED = (
            current_set_common_enabled
        )
    _SKIN_OPERATIONS._set_common_enabled = _set_common_enabled

    _TOOL_WINDOW.WINDOW_LABEL = "AD Skin Weights Tool v7.3"
    _TOOL_WINDOW.WINDOW_HEIGHT = 690
    _TOOL_WINDOW.WINDOW_WIDTH = 340
    _TOOL_WINDOW._V73_SMOOTHING_UI_INSTALLED = True


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
                lambda *_: _SKIN_OPERATIONS.apply_add_influence(),
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
    """Run final v3.2 blocking, then apply the selected v7.3 smoothing passes."""

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
        builtins.AD_SKIN_V73_UI_RESULT = result
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
    _SKIN_OPERATIONS._V73_BASE_SET_COMMON_ENABLED(enabled)
    if cmds.intSliderGrp(CTRL_SMOOTHING_ITERATIONS, exists=True):
        cmds.intSliderGrp(
            CTRL_SMOOTHING_ITERATIONS,
            edit=True,
            enable=bool(enabled),
        )


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool v7.3",
        message=(
            "Binding\n"
            "- Smoothing Iterations 0: preserve final v3.2 hard blocking.\n"
            "- Smoothing Iterations 1-10: diffuse weights through mesh topology.\n"
            "- Positive smoothing uses Max Influences 5, or fewer when the joint "
            "list contains fewer than five influences.\n"
            "- Bind Skin: create the initial skinCluster from all listed joints.\n"
            "- Add Influence: add selected pending influences to an existing skin.\n\n"
            "Component\n"
            "- Flood: select one influence and mesh components.\n\n"
            "Region remains the final blocking authority. Smoothing does not "
            "recalculate ownership."
        ),
        button=["OK"],
    )
