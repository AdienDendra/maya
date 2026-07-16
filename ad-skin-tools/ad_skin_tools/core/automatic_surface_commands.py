from typing import Optional, Sequence

from ad_skin_tools.core.joint_automatic_bind import (
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
    """
    UI/command boundary for the v2.7 automatic hard-ownership solver.

    The public workflow supplies only one mesh and one complete joint list.
    """
    return _bind_object_automatic_surface(
        mesh=mesh,
        joints=joints,
        options=options,
    )


def print_report(
    result: AutomaticSurfaceBindResult,
) -> None:
    print_automatic_surface_report(result)
