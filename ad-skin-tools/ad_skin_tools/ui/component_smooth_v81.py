"""Install the v8.1 Component Smooth operation."""

import builtins

import maya.cmds as cmds

from ad_skin_tools.components import smooth
from ad_skin_tools.ui import component_flood_section


CTRL_SMOOTH_BUTTON = "adSkin_smoothSelectedComponentsButton"

_TOOL_WINDOW = None
_SKIN_OPERATIONS = None
_SMOOTHING_BIND_SECTION = None


def install(
    tool_window_module,
    skin_operations_module,
    smoothing_bind_section_module,
) -> None:
    global _TOOL_WINDOW, _SKIN_OPERATIONS, _SMOOTHING_BIND_SECTION

    _TOOL_WINDOW = tool_window_module
    _SKIN_OPERATIONS = skin_operations_module
    _SMOOTHING_BIND_SECTION = smoothing_bind_section_module

    _SKIN_OPERATIONS._build_component_section = _build_component_section

    _TOOL_WINDOW.show_help = show_help
    _TOOL_WINDOW.WINDOW_LABEL = "AD Skin Weights Tool v8.1"
    _TOOL_WINDOW._V81_COMPONENT_SMOOTH_INSTALLED = True


def _build_component_section() -> None:
    cmds.frameLayout(
        label="Component",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=7)

    _SKIN_OPERATIONS._named_button_row(
        [
            (
                component_flood_section.CTRL_FLOOD_BUTTON,
                "Flood",
                lambda *_: component_flood_section.apply_component_flood(),
            ),
            (
                CTRL_SMOOTH_BUTTON,
                "Smooth",
                lambda *_: apply_component_smooth(),
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


def apply_component_smooth() -> None:
    wait_cursor_active = False

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            raise RuntimeError(
                "Component Smooth requires an existing skinCluster.\n\n"
                "Use Bind Skin first."
            )

        smoothing_level = _SMOOTHING_BIND_SECTION._query_iterations()
        if smoothing_level == 0:
            cmds.warning(
                "Component Smooth requires Smoothing Iterations of at least 1."
            )
            return

        scope = smooth.collect_smooth_scope(
            mesh_shape=_TOOL_WINDOW._STATE["mesh_shape"],
            mesh_transform=_TOOL_WINDOW._STATE["mesh_transform"],
        )

        if scope.whole_object:
            response = cmds.confirmDialog(
                title="Smooth Entire Mesh",
                message=(
                    "Smooth all current skin weights on the loaded mesh?\n\n"
                    "Smoothing Iterations: {}\n"
                    "Locked influences will remain unchanged."
                ).format(smoothing_level),
                button=["Yes", "Cancel"],
                defaultButton="Yes",
                cancelButton="Cancel",
                dismissString="Cancel",
            )
            if response != "Yes":
                return

        _set_smooth_busy(
            True,
            "Smoothing selected skin weights...",
        )
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        result = smooth.smooth_skin_weights(
            scope=scope,
            smoothing_level=smoothing_level,
        )

        builtins.AD_SKIN_V81_SMOOTH_RESULT = result
        smooth.print_component_smooth_report(result)

        mode = "entire mesh" if result.whole_object else "selection"
        suffixes = []
        if result.skipped_empty_vertex_ids:
            suffixes.append(
                "{} empty vertex/vertices skipped.".format(
                    len(result.skipped_empty_vertex_ids)
                )
            )
        if result.skipped_locked_vertex_ids:
            suffixes.append(
                "{} fully locked vertex/vertices skipped.".format(
                    len(result.skipped_locked_vertex_ids)
                )
            )
        suffix = " " + " ".join(suffixes) if suffixes else ""

        _TOOL_WINDOW._info(
            "Smooth complete: {} of {} vertices changed in {}, level {}.{}".format(
                result.smoothed_vertex_count,
                result.selected_vertex_count,
                mode,
                result.smoothing_level,
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
        _set_smooth_busy(False)


def _set_smooth_busy(busy: bool, status: str = "") -> None:
    if _TOOL_WINDOW is None:
        return

    _SKIN_OPERATIONS._set_common_enabled(not busy)
    _TOOL_WINDOW._STATE["busy"] = bool(busy)

    if cmds.button(CTRL_SMOOTH_BUTTON, exists=True):
        cmds.button(
            CTRL_SMOOTH_BUTTON,
            edit=True,
            enable=not busy,
            label="Smoothing..." if busy else "Smooth",
        )

    status_control = component_flood_section.CTRL_FLOOD_STATUS
    if cmds.text(status_control, exists=True):
        cmds.text(
            status_control,
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
        title="AD Skin Weights Tool v8.1",
        message=(
            "Binding\n"
            "- Smoothing Iterations remains the shared level from 0 to 10.\n"
            "- Bind Skin and Add Influence keep their existing Region behaviour.\n\n"
            "Component Flood\n"
            "- Soft Selection off writes target weight 1.0.\n"
            "- Soft Selection on writes Maya's per-vertex falloff.\n\n"
            "Component Smooth\n"
            "- Smoothing Iterations must be at least 1.\n"
            "- Select vertices, edges, or faces; no target joint is required.\n"
            "- Soft Selection controls smoothing strength per vertex.\n"
            "- Locked influence values remain unchanged.\n"
            "- Selecting the loaded mesh object offers whole-mesh smoothing."
        ),
        button=["OK"],
    )
