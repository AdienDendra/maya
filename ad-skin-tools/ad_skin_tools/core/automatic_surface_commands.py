"""UI command boundary for production automatic surface binding."""

import builtins
from typing import Optional, Sequence

from ad_skin_tools.core.smoothed_automatic_bind import (
    AutomaticSurfaceBindOptions,
    AutomaticSurfaceBindResult,
    bind_object_automatic_surface as _bind_object_automatic_surface,
    print_automatic_surface_report,
)
from ad_skin_tools.development import automatic_bind_validation


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
    """Print production timing, then run removable development validation."""

    print_automatic_surface_report(result)
    validation = automatic_bind_validation.validate_and_print(result)
    builtins.AD_SKIN_BIND_VALIDATION_RESULT = validation
