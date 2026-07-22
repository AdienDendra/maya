"""UI command boundary for production automatic surface binding."""

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
    """Bind one unskinned mesh using final ownership and optional smoothing."""

    result = _bind_object_automatic_surface(
        mesh=mesh,
        joints=joints,
        options=options,
    )
    builtins.AD_SKIN_REGION_RESULT = result
    builtins.AD_SKIN_OWNERSHIP_BIND_RESULT = result
    return result


def print_report(result: AutomaticSurfaceBindResult) -> None:
    print_automatic_surface_report(result)
