"""Independent Region Research package for AD Skin Tool v10.0."""

from ad_skin_tools.region_research.nearest_regions import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    NearestRegionResearchResult,
    solve_nearest_regions,
)
from ad_skin_tools.region_research.runner import (
    get_last_result,
    print_stage_01_report,
    run_stage_01,
)


__all__ = [
    "DEFAULT_DISTANCE_CHUNK_SIZE",
    "NearestRegionResearchResult",
    "get_last_result",
    "print_stage_01_report",
    "run_stage_01",
    "solve_nearest_regions",
]
