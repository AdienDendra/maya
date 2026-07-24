"""Connect Skin Weight Visual to a Viewport 2.0 overlay."""

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ad_skin_tools.core import skin_weight_events
from ad_skin_tools.ui import skin_weight_visual_draw


_MODE = None
_TOOL_WINDOW = None
_EVENT_CALLBACK_ID = None

_ORIGINALS = {}


def prepare(skin_weight_mode_module, tool_window_module) -> None:
    """Replace color-set preview plumbing before the visual installs."""

    global _MODE, _TOOL_WINDOW
    _MODE = skin_weight_mode_module
    _TOOL_WINDOW = tool_window_module

    if skin_weight_mode_module._ensure_preview is _ensure_preview:
        return

    _ORIGINALS.clear()
    for name in (
        "request_refresh",
        "refresh",
        "_ensure_preview",
        "_update_preview_colors",
        "_cleanup_preview",
        "_preview_color_set_exists",
        "_make_preview_current",
        "_install_script_jobs",
    ):
        _ORIGINALS[name] = getattr(skin_weight_mode_module, name)

    skin_weight_mode_module.request_refresh = request_refresh
    skin_weight_mode_module._ensure_preview = _ensure_preview
    skin_weight_mode_module._update_preview_colors = _update_preview_colors
    skin_weight_mode_module._cleanup_preview = _cleanup_preview
    skin_weight_mode_module._preview_color_set_exists = (
        skin_weight_visual_draw.exists
    )
    skin_weight_mode_module._make_preview_current = _make_preview_current
    skin_weight_mode_module._install_script_jobs = _install_script_jobs


def install() -> None:
    """Listen for undoable weight writes without using Undo/Redo scriptJobs."""

    global _EVENT_CALLBACK_ID

    _remove_event_callback()
    _EVENT_CALLBACK_ID = skin_weight_events.add_callback(
        _weights_changed,
    )


def shutdown() -> None:
    """Remove callbacks, drawable state, and runtime monkey patches."""

    _remove_event_callback()
    _cleanup_preview()
    skin_weight_visual_draw.shutdown(unload_plugin=True)
    _restore_originals()


def request_refresh(*_):
    """Refresh immediately; viewport draw data performs the expensive read."""

    original = _ORIGINALS.get("refresh")
    if original is None or _MODE is None:
        return
    if not _MODE._mode_is_active():
        return
    original()


def _ensure_preview(mesh_shape, mesh_transform):
    current = skin_weight_visual_draw.ensure(
        mesh_shape,
        mesh_transform,
    )
    if _MODE is not None:
        _MODE._PREVIEW = current
    return current


def _update_preview_colors(mesh_shape, joint, mode):
    if _TOOL_WINDOW is None:
        return
    mesh_transform = _TOOL_WINDOW._STATE.get("mesh_transform")
    skin_weight_visual_draw.update_context(
        mesh_shape,
        mesh_transform,
        joint,
        mode,
    )
    try:
        cmds.refresh(force=True)
    except Exception:
        pass


def _cleanup_preview():
    skin_weight_visual_draw.cleanup()
    if _MODE is not None:
        _MODE._PREVIEW = None


def _make_preview_current():
    if not skin_weight_visual_draw.exists():
        raise RuntimeError("Skin Weight Visual viewport overlay is unavailable.")


def _install_script_jobs():
    """Keep lifecycle jobs, but never observe Undo or Redo globally."""

    if _MODE is None or _TOOL_WINDOW is None:
        return

    _MODE._remove_script_jobs()
    _MODE._SCRIPT_JOBS = []
    parent = _TOOL_WINDOW.WINDOW_NAME

    try:
        _MODE._SCRIPT_JOBS.append(
            cmds.scriptJob(
                event=("ToolChanged", _MODE._tool_changed),
                parent=parent,
                protected=True,
            )
        )
    except Exception:
        pass

    try:
        _MODE._SCRIPT_JOBS.append(
            cmds.scriptJob(
                uiDeleted=(parent, _MODE.shutdown),
                runOnce=True,
            )
        )
    except Exception:
        pass


def _weights_changed(mesh_shape):
    """Undo/redo callback: mark draw data dirty without reading or writing DG."""

    skin_weight_visual_draw.mark_colors_dirty(mesh_shape)


def _remove_event_callback():
    global _EVENT_CALLBACK_ID

    if _EVENT_CALLBACK_ID is not None:
        try:
            om.MMessage.removeCallback(_EVENT_CALLBACK_ID)
        except Exception:
            pass
    _EVENT_CALLBACK_ID = None


def _restore_originals():
    if _MODE is None:
        _ORIGINALS.clear()
        return

    replacements = {
        "request_refresh": request_refresh,
        "_ensure_preview": _ensure_preview,
        "_update_preview_colors": _update_preview_colors,
        "_cleanup_preview": _cleanup_preview,
        "_preview_color_set_exists": skin_weight_visual_draw.exists,
        "_make_preview_current": _make_preview_current,
        "_install_script_jobs": _install_script_jobs,
    }
    for name, replacement in replacements.items():
        original = _ORIGINALS.get(name)
        if original is not None and getattr(_MODE, name, None) is replacement:
            setattr(_MODE, name, original)
    _ORIGINALS.clear()
