"""Stable UI command boundary for v7.3 final blocking and smoothing."""

import builtins
from typing import Optional, Sequence

from ad_skin_tools.core.smoothed_automatic_bind import (
    AutomaticSurfaceBindOptions,
    AutomaticSurfaceBindResult,
    bind_object_automatic_surface as _bind_object_automatic_surface,
    print_automatic_surface_report,
)


def bind_object_automatic_surface(
    mesh: str,
    joints: Sequence[str],
    options: Optional[AutomaticSurfaceBindOptions] = None,
) -> AutomaticSurfaceBindResult:
    """Bind one unskinned mesh using final v3.2 ownership and v7.3 smoothing."""

    result = _bind_object_automatic_surface(
        mesh=mesh,
        joints=joints,
        options=options,
    )
    builtins.AD_SKIN_REGION_RESULT = result
    builtins.AD_SKIN_V73_UI_RESULT = result
    return result


def print_report(result: AutomaticSurfaceBindResult) -> None:
    print_automatic_surface_report(result)
