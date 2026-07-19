"""Automatic bind-smoothing algorithms for Bind Skin and Add Influence."""

from ad_skin_tools.bind_smoothing.diffusion import (
    BindDiffusionResult,
    diffuse_hard_ownership,
)
from ad_skin_tools.bind_smoothing.max_influences import (
    MaxInfluenceProjectionResult,
    enforce_maximum_influences,
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
from ad_skin_tools.bind_smoothing.v7_blocking_smoothing import (
    V7BlockingSmoothingResult,
    solve_v7_blocking_smoothing,
)

__all__ = [
    "BindDiffusionResult",
    "BindSmoothingOptions",
    "BindSmoothingResult",
    "BindWeightValidationResult",
    "MaxInfluenceProjectionResult",
    "V7BlockingSmoothingResult",
    "diffuse_hard_ownership",
    "enforce_maximum_influences",
    "solve_bind_smoothing",
    "solve_v7_blocking_smoothing",
    "validate_bind_weights",
]
