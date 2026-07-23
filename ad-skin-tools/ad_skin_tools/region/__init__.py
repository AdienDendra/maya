"""Clear ownership pipeline used to replace the legacy Region experiments."""

from ad_skin_tools.region_research.closest_region_ownership import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    ClosestRegionOwnershipResult,
    solve_closest_region_ownership,
)
from ad_skin_tools.region_research.closed_loop_ownership import (
    ClosedLoopOwnershipResult,
    resolve_closed_loop_ownership,
)
from ad_skin_tools.region_research.global_owner_assignment import (
    GlobalOwnerAssignmentResult,
    assign_detached_to_global_owner,
)
from ad_skin_tools.region_research.ownership_pipeline import (
    OwnershipPipelineResult,
    solve_ownership_pipeline,
)
from ad_skin_tools.region_research.secondary_surface_facing import (
    SecondarySurfaceFacingResult,
    classify_secondary_surface_facing,
)


__all__ = [
    "DEFAULT_DISTANCE_CHUNK_SIZE",
    "ClosestRegionOwnershipResult",
    "ClosedLoopOwnershipResult",
    "GlobalOwnerAssignmentResult",
    "OwnershipPipelineResult",
    "SecondarySurfaceFacingResult",
    "assign_detached_to_global_owner",
    "classify_secondary_surface_facing",
    "resolve_closed_loop_ownership",
    "solve_closest_region_ownership",
    "solve_ownership_pipeline",
]
