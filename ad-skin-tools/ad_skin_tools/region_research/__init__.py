"""Independent Region Research package for AD Skin Tool."""

from ad_skin_tools.region_research.boundary_contacts import (
    BoundaryContactResearchResult,
    BoundaryOwnerContact,
    SecondaryRegionBoundary,
    analyze_secondary_region_boundaries,
)
from ad_skin_tools.region_research.nearest_regions import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    NearestRegionResearchResult,
    solve_nearest_regions,
)
from ad_skin_tools.region_research.runner import (
    get_last_result,
    get_last_stage_01_result,
    get_last_stage_02_result,
    print_stage_01_report,
    print_stage_02_report,
    run_stage_01,
    run_stage_02,
    run_stage_02_from_stage_01,
)


__all__ = [
    "BoundaryContactResearchResult",
    "BoundaryOwnerContact",
    "DEFAULT_DISTANCE_CHUNK_SIZE",
    "NearestRegionResearchResult",
    "SecondaryRegionBoundary",
    "analyze_secondary_region_boundaries",
    "get_last_result",
    "get_last_stage_01_result",
    "get_last_stage_02_result",
    "print_stage_01_report",
    "print_stage_02_report",
    "run_stage_01",
    "run_stage_02",
    "run_stage_02_from_stage_01",
    "solve_nearest_regions",
]
