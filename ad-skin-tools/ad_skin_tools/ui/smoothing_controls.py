"""Shared artist-facing Blend and Iterations controls."""

from dataclasses import dataclass

import maya.cmds as cmds


CTRL_BLEND = "adSkin_smoothingBlend"
CTRL_ITERATIONS = "adSkin_smoothingIterations"

STATE_BLEND = "smoothing_blend"
STATE_ITERATIONS = "smoothing_iterations"

MINIMUM_BLEND = 0.0
MAXIMUM_BLEND = 1.0
DEFAULT_BLEND = 0.25
MINIMUM_ITERATIONS = 0
MAXIMUM_ITERATIONS = 10
DEFAULT_ITERATIONS = 0

_TOOL_WINDOW = None


@dataclass(frozen=True)
class SmoothingControlValues:
    blend: float
    iterations: int


def configure(tool_window_module) -> None:
    """Attach the tool state used by the shared controls."""

    global _TOOL_WINDOW
    _TOOL_WINDOW = tool_window_module
    _TOOL_WINDOW._STATE.setdefault(STATE_BLEND, DEFAULT_BLEND)
    _TOOL_WINDOW._STATE.setdefault(STATE_ITERATIONS, DEFAULT_ITERATIONS)


def build_controls() -> None:
    """Build the shared controls in the current Maya layout."""

    _require_configured()
    cmds.separator(height=7, style="in")
    cmds.floatSliderGrp(
        CTRL_BLEND,
        label="Blend",
        field=True,
        minValue=MINIMUM_BLEND,
        maxValue=MAXIMUM_BLEND,
        fieldMinValue=MINIMUM_BLEND,
        fieldMaxValue=MAXIMUM_BLEND,
        value=_stored_blend(),
        step=0.05,
        precision=3,
        columnWidth3=(90, 52, 170),
        adjustableColumn=3,
        dragCommand=_store_blend,
        changeCommand=_store_blend,
    )
    cmds.intSliderGrp(
        CTRL_ITERATIONS,
        label="Iterations",
        field=True,
        minValue=MINIMUM_ITERATIONS,
        maxValue=MAXIMUM_ITERATIONS,
        fieldMinValue=MINIMUM_ITERATIONS,
        fieldMaxValue=MAXIMUM_ITERATIONS,
        value=_stored_iterations(),
        step=1,
        columnWidth3=(90, 52, 170),
        adjustableColumn=3,
        dragCommand=_store_iterations,
        changeCommand=_store_iterations,
    )


def query_values() -> SmoothingControlValues:
    """Read the current controls and persist their clamped values."""

    _require_configured()
    if cmds.floatSliderGrp(CTRL_BLEND, exists=True):
        _store_blend(
            cmds.floatSliderGrp(CTRL_BLEND, query=True, value=True)
        )
    if cmds.intSliderGrp(CTRL_ITERATIONS, exists=True):
        _store_iterations(
            cmds.intSliderGrp(CTRL_ITERATIONS, query=True, value=True)
        )
    return SmoothingControlValues(
        blend=_stored_blend(),
        iterations=_stored_iterations(),
    )


def set_enabled(enabled: bool) -> None:
    """Enable or disable both shared controls."""

    if cmds.floatSliderGrp(CTRL_BLEND, exists=True):
        cmds.floatSliderGrp(
            CTRL_BLEND,
            edit=True,
            enable=bool(enabled),
        )
    if cmds.intSliderGrp(CTRL_ITERATIONS, exists=True):
        cmds.intSliderGrp(
            CTRL_ITERATIONS,
            edit=True,
            enable=bool(enabled),
        )


def _stored_blend() -> float:
    value = float(_TOOL_WINDOW._STATE.get(STATE_BLEND, DEFAULT_BLEND))
    return max(MINIMUM_BLEND, min(MAXIMUM_BLEND, value))


def _store_blend(value=None, *_unused) -> None:
    if value is None:
        value = _stored_blend()
    _TOOL_WINDOW._STATE[STATE_BLEND] = max(
        MINIMUM_BLEND,
        min(MAXIMUM_BLEND, float(value)),
    )


def _stored_iterations() -> int:
    value = int(
        _TOOL_WINDOW._STATE.get(STATE_ITERATIONS, DEFAULT_ITERATIONS)
    )
    return max(MINIMUM_ITERATIONS, min(MAXIMUM_ITERATIONS, value))


def _store_iterations(value=None, *_unused) -> None:
    if value is None:
        value = _stored_iterations()
    _TOOL_WINDOW._STATE[STATE_ITERATIONS] = max(
        MINIMUM_ITERATIONS,
        min(MAXIMUM_ITERATIONS, int(value)),
    )


def _require_configured() -> None:
    if _TOOL_WINDOW is None:
        raise RuntimeError("Shared smoothing controls are not configured.")
