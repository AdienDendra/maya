"""v7.0 integration: final Region blocking followed by constrained smoothing."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ad_skin_tools.bind_smoothing.diffusion import (
    BindDiffusionResult,
    diffuse_hard_ownership,
)
from ad_skin_tools.bind_smoothing.final_constraints import (
    DistanceMaxInfluenceResult,
    OwnerMaximumResult,
    enforce_maximum_influences_by_distance,
    project_region_owner_to_maximum,
)
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.validation import (
    BindWeightValidationResult,
    validate_bind_weights,
)
from ad_skin_tools.region import ambiguous_loop_distance_tiebreak
from ad_skin_tools.region import closed_loop_opposite_guard
from ad_skin_tools.region.connectivity import build_vertex_adjacency
from ad_skin_tools.region.solver import RegionOwnershipResult


@dataclass(frozen=True)
class V7BlockingSmoothingResult:
    """Final blocking data, smoothing stages, and production diagnostics."""

    weights: np.ndarray
    blocking_owner_indices: np.ndarray
    options: BindSmoothingOptions
    effective_maximum_influences: int
    guarded_result: object
    blocking_result: object
    diffusion_result: BindDiffusionResult
    distance_projection_result: DistanceMaxInfluenceResult
    owner_maximum_result: OwnerMaximumResult
    validation_result: BindWeightValidationResult

    @property
    def vertex_count(self) -> int:
        return int(self.weights.shape[0])

    @property
    def influence_count(self) -> int:
        return int(self.weights.shape[1])


def solve_v7_blocking_smoothing(
    region_result: RegionOwnershipResult,
    options: Optional[BindSmoothingOptions] = None,
) -> V7BlockingSmoothingResult:
    """Run v3.10J/K blocking, then diffuse and constrain those final owners."""

    options = (options or BindSmoothingOptions()).validated()

    guarded_result = (
        closed_loop_opposite_guard.solve_closed_loop_opposite_guard(
            region_result
        )
    )
    blocking_result = (
        ambiguous_loop_distance_tiebreak.
        solve_ambiguous_loop_distance_tiebreak(
            region_result,
            guarded_result,
        )
    )
    blocking_owners = np.asarray(
        blocking_result.corrected_owner_indices,
        dtype=np.int32,
    )
    if blocking_owners.shape != (region_result.vertex_count,):
        raise RuntimeError(
            "Final blocking owner map does not contain one owner per vertex."
        )

    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    effective_maximum = options.effective_maximum_influences(
        region_result.influence_count
    )
    diffusion_result = diffuse_hard_ownership(
        owner_indices=blocking_owners,
        adjacency=adjacency,
        influence_count=region_result.influence_count,
        iterations=options.iterations,
        relaxation=options.relaxation,
    )

    distance_projection = enforce_maximum_influences_by_distance(
        weights=diffusion_result.weights,
        owner_indices=blocking_owners,
        vertex_positions=region_result.vertex_positions,
        influence_positions=region_result.influence_positions,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
    )
    if distance_projection.unresolved_exact_tie_vertex_ids:
        raise RuntimeError(
            "v7.0 Max Influences has unresolved exact weight-and-distance ties. "
            "First vertex IDs: {}".format(
                list(
                    distance_projection.
                    unresolved_exact_tie_vertex_ids[:20]
                )
            )
        )

    owner_maximum = project_region_owner_to_maximum(
        weights=distance_projection.weights,
        owner_indices=blocking_owners,
    )
    if owner_maximum.owner_below_maximum_after:
        raise RuntimeError(
            "v7.0 final blocking owner remains below another influence. "
            "First vertex IDs: {}".format(
                list(owner_maximum.owner_below_maximum_after[:20])
            )
        )

    validation = validate_bind_weights(
        weights=owner_maximum.weights,
        owner_indices=blocking_owners,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
        require_exact_one_hot=options.iterations == 0,
    )

    return V7BlockingSmoothingResult(
        weights=owner_maximum.weights,
        blocking_owner_indices=blocking_owners.copy(),
        options=options,
        effective_maximum_influences=effective_maximum,
        guarded_result=guarded_result,
        blocking_result=blocking_result,
        diffusion_result=diffusion_result,
        distance_projection_result=distance_projection,
        owner_maximum_result=owner_maximum,
        validation_result=validation,
    )
