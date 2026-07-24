"""Install the v13.10 ephemeral color lifecycle into Skin Weight Visual."""

import maya.cmds as cmds

from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.ui import skin_weight_color_session


_MODE = None
_ORIGINALS = {}
_REFRESH_QUEUED = False


def prepare(skin_weight_mode_module) -> None:
    """Patch only the visual storage layer before the UI installs callbacks."""

    global _MODE
    _MODE = skin_weight_mode_module

    if skin_weight_mode_module._ensure_preview is _ensure_preview:
        return

    _ORIGINALS.clear()
    for name in (
        "request_refresh",
        "_ensure_preview",
        "_update_preview_colors",
        "_cleanup_preview",
        "_preview_color_set_exists",
        "_make_preview_current",
    ):
        _ORIGINALS[name] = getattr(skin_weight_mode_module, name)

    skin_weight_mode_module.request_refresh = request_refresh
    skin_weight_mode_module._ensure_preview = _ensure_preview
    skin_weight_mode_module._update_preview_colors = _update_preview_colors
    skin_weight_mode_module._cleanup_preview = _cleanup_preview
    skin_weight_mode_module._preview_color_set_exists = (
        skin_weight_color_session.exists
    )
    skin_weight_mode_module._make_preview_current = (
        skin_weight_color_session.make_current
    )


def shutdown() -> None:
    """Remove color data and restore the canonical v13.7 functions."""

    global _MODE, _REFRESH_QUEUED

    skin_weight_color_session.cleanup()
    if _MODE is not None:
        _MODE._PREVIEW = None
        replacements = {
            "request_refresh": request_refresh,
            "_ensure_preview": _ensure_preview,
            "_update_preview_colors": _update_preview_colors,
            "_cleanup_preview": _cleanup_preview,
            "_preview_color_set_exists": skin_weight_color_session.exists,
            "_make_preview_current": skin_weight_color_session.make_current,
        }
        for name, replacement in replacements.items():
            original = _ORIGINALS.get(name)
            if original is not None and getattr(_MODE, name, None) is replacement:
                setattr(_MODE, name, original)

    _ORIGINALS.clear()
    _MODE = None
    _REFRESH_QUEUED = False


def request_refresh(*_):
    """Coalesce refreshes, including Undo/Redo, outside the active command."""

    global _REFRESH_QUEUED

    if _MODE is None or not _MODE._mode_is_active() or _REFRESH_QUEUED:
        return
    _REFRESH_QUEUED = True
    try:
        cmds.evalDeferred(_deferred_refresh)
    except Exception as exc:
        _REFRESH_QUEUED = False
        cmds.warning(
            "Skin Weight Visual could not queue a safe refresh: {}".format(exc)
        )


def _deferred_refresh():
    global _REFRESH_QUEUED

    _REFRESH_QUEUED = False
    _refresh_now()


def _refresh_now():
    if _MODE is None or not _MODE._mode_is_active():
        return

    # Call the canonical refresh body directly. Calling the original
    # request_refresh would queue a second lowest-priority deferred callback.
    refresh = getattr(_MODE, "refresh", None)
    if callable(refresh):
        refresh()


def _ensure_preview(mesh_shape, mesh_transform):
    current = skin_weight_color_session.ensure(
        mesh_shape,
        mesh_transform,
    )
    if _MODE is not None:
        _MODE._PREVIEW = current
    return current


def _update_preview_colors(mesh_shape, joint, mode):
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    rgb = _MODE._map_weights_to_rgb(
        adapter.influence_weights(joint),
        mode,
    )
    mesh_transform = _MODE._state().get("mesh_transform")
    current = skin_weight_color_session.set_colors(
        mesh_shape,
        mesh_transform,
        rgb,
    )
    if _MODE is not None:
        _MODE._PREVIEW = current


def _cleanup_preview():
    skin_weight_color_session.cleanup()
    if _MODE is not None:
        _MODE._PREVIEW = None
