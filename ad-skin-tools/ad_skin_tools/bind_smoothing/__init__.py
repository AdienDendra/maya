"""Automatic smoothing of final AD Skin Tool blocking ownership."""

from ad_skin_tools.bind_smoothing.cutoff_projection import (
    GeometricMaxInfluenceResult,
    enforce_maximum_influences_by_geometry,
)
from ad_skin_tools.bind_smoothing.diffusion import (
    BindDiffusionResult,
    diffuse_hard_ownership,
)
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.solver import (
    BindSmoothingResult,
    solve_bind_smoothing,
)
from ad_skin_tools.bind_smoothing.validation import (
    BindWeightValidationResult,
    validate_bind_weights,
)

__all__ = [
    "BindDiffusionResult",
    "BindSmoothingOptions",
    "BindSmoothingResult",
    "BindWeightValidationResult",
    "GeometricMaxInfluenceResult",
    "diffuse_hard_ownership",
    "enforce_maximum_influences_by_geometry",
    "solve_bind_smoothing",
    "validate_bind_weights",
]
