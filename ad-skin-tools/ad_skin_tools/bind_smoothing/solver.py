"""Orchestration for v6.1 in-memory automatic bind smoothing."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ad_skin_tools.bind_smoothing.diffusion import (
    BindDiffusionResult,
    diffuse_hard_ownership,
)
from ad_skin_tools.bind_smoothing.max_influences import (
    MaxInfluenceProjectionResult,
    enforce_maximum_influences,
)
from ad_skin_tools.bind_smoothing.options import (
    BindSmoothingOptions,
)
from ad_skin_tools.bind_smoothing.validation import (
    BindWeightValidationResult,
    validate_bind_weights,
)


@dataclass(frozen=True)
class BindSmoothingResult:
    """Raw diffusion, constrained output, and validation diagnostics."""

    weights: np.ndarray
    options: BindSmoothingOptions
    effective_maximum_influences: int
    diffusion_result: BindDiffusionResult
    projection_result: MaxInfluenceProjectionResult
    validation_result: BindWeightValidationResult

    @property
    def vertex_count(self) -> int:
        return int(self.weights.shape[0])

    @property
    def influence_count(self) -> int:
        return int(self.weights.shape[1])


def solve_bind_smoothing(
    owner_indices: np.ndarray,
    adjacency,
    influence_count: int,
    options: Optional[BindSmoothingOptions] = None,
) -> BindSmoothingResult:
    """Diffuse hard ownership, enforce Max Influences, and validate."""

    options = (
        options or BindSmoothingOptions()
    ).validated()
    influence_count = int(influence_count)
    effective_maximum = options.effective_maximum_influences(
        influence_count
    )

    diffusion_result = diffuse_hard_ownership(
        owner_indices=owner_indices,
        adjacency=adjacency,
        influence_count=influence_count,
        iterations=options.iterations,
        relaxation=options.relaxation,
    )
    projection_result = enforce_maximum_influences(
        weights=diffusion_result.weights,
        owner_indices=diffusion_result.owner_indices,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
    )
    validation_result = validate_bind_weights(
        weights=projection_result.weights,
        owner_indices=diffusion_result.owner_indices,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
        require_exact_one_hot=options.iterations == 0,
    )

    return BindSmoothingResult(
        weights=projection_result.weights,
        options=options,
        effective_maximum_influences=effective_maximum,
        diffusion_result=diffusion_result,
        projection_result=projection_result,
        validation_result=validation_result,
    )
