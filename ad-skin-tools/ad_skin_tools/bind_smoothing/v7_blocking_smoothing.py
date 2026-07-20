"""v7.1 integration: exact-tie-complete blocking then constrained smoothing."""

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ad_skin_tools.bind_smoothing.cutoff_projection import (
    GeometricMaxInfluenceResult,
    enforce_maximum_influences_by_geometry,
)
from ad_skin_tools.bind_smoothing.diffusion import (
    BindDiffusionResult,
    diffuse_hard_ownership,
)
from ad_skin_tools.bind_smoothing.final_constraints import (
    OwnerMaximumResult,
    project_region_owner_to_maximum,
)
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.validation import (
    BindWeightValidationResult,
    validate_bind_weights,
)
from ad_skin_tools.region.ambiguous_loop_distance_tiebreak import (
    AmbiguousLoopDistanceResult,
    solve_ambiguous_loop_distance_tiebreak,
)
from ad_skin_tools.region.closed_loop_opposite_guard import (
    OppositeGuardConsensusResult,
    solve_closed_loop_opposite_guard,
)
from ad_skin_tools.region.connectivity import build_vertex_adjacency
from ad_skin_tools.region.solver import RegionOwnershipResult


@dataclass(frozen=True)
class V7BlockingSmoothingResult:
    """Final blocking data, smoothing stages, and production diagnostics."""

    weights: np.ndarray
    blocking_owner_indices: np.ndarray
    options: BindSmoothingOptions
    effective_maximum_influences: int
    guarded_result: OppositeGuardConsensusResult
    blocking_result: AmbiguousLoopDistanceResult
    diffusion_result: BindDiffusionResult
    distance_projection_result: GeometricMaxInfluenceResult
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
    """Run final Region blocking, then diffuse and constrain those owners.

    ``region_result`` is expected to come from the v3.2 Region solver. Initial
    closest-distance ties have therefore already been completed before
    connectivity and facing. v3.10J/K remains the final blocking authority.
    Smoothing starts only after that final hard owner array passes structural
    validation.

    The post-correction ``final_validation`` result is retained as diagnostic
    telemetry. Its detached/ambiguous classifications come from re-running the
    generic Region facing heuristic after v3.10J/K has intentionally corrected
    closed loops. They do not mean that vertices are unowned, so they must not
    block the smoothing handoff.
    """

    options = (options or BindSmoothingOptions()).validated()

    guarded_result = solve_closed_loop_opposite_guard(region_result)
    blocking_result = solve_ambiguous_loop_distance_tiebreak(
        region_result,
        guarded_result,
    )
    blocking_owners = np.asarray(
        blocking_result.corrected_owner_indices,
        dtype=np.int32,
    )
    _validate_blocking_contract(
        region_result=region_result,
        blocking_owners=blocking_owners,
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

    distance_projection = enforce_maximum_influences_by_geometry(
        weights=diffusion_result.weights,
        owner_indices=blocking_owners,
        vertex_positions=region_result.vertex_positions,
        influence_positions=region_result.influence_positions,
        maximum_influences=effective_maximum,
        weight_epsilon=options.weight_epsilon,
    )
    if distance_projection.unresolved_coincident_vertex_ids:
        raise RuntimeError(
            "v7.1 Max Influences cannot distinguish exactly coincident cutoff "
            "joints. Their smoothed weights, vertex distances, and world "
            "positions are identical. First vertex IDs: {}".format(
                list(
                    distance_projection.unresolved_coincident_vertex_ids[:20]
                )
            )
        )

    owner_maximum = project_region_owner_to_maximum(
        weights=distance_projection.weights,
        owner_indices=blocking_owners,
    )
    if owner_maximum.owner_below_maximum_after:
        raise RuntimeError(
            "v7.1 final blocking owner remains below another influence. "
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


def _validate_blocking_contract(
    region_result,
    blocking_owners,
):
    """Validate the actual hard-owner handoff contract.

    A complete blocking map means exactly one valid influence index is stored for
    every vertex. Facing classifications are quality diagnostics, not ownership
    validity: after v3.10J/K loop corrections a fully owned region can still be
    labelled detached by the generic pre-correction heuristic.
    """

    expected_shape = (region_result.vertex_count,)
    if blocking_owners.shape != expected_shape:
        raise RuntimeError(
            "Final blocking owner map does not contain one owner per vertex."
        )
    if blocking_owners.size and (
        np.any(blocking_owners < 0)
        or np.any(blocking_owners >= region_result.influence_count)
    ):
        bad = np.where(
            (blocking_owners < 0)
            | (blocking_owners >= region_result.influence_count)
        )[0][:20]
        raise RuntimeError(
            "Final blocking owner map contains invalid influence indices. "
            "First vertex IDs: {}".format(bad.tolist())
        )
