"""AD Skin Tool v3 experimental pipeline.

Version 3 is intentionally isolated from ``ad_skin_tools.core``.  Each solver
stage is introduced and smoke-tested independently before it becomes part of a
production bind pipeline.

Current stage:
    1. exact world-space joint-distance ranking.
"""

from ad_skin_tools.v3.distance_ranking import (
    DistanceCandidate,
    ExactDistanceRankingResult,
    format_vertex_ranking,
    solve_exact_distance_ranking,
)
from ad_skin_tools.v3.maya_scene import (
    MayaDistanceInput,
    collect_distance_input,
)

__all__ = [
    "DistanceCandidate",
    "ExactDistanceRankingResult",
    "MayaDistanceInput",
    "collect_distance_input",
    "format_vertex_ranking",
    "solve_exact_distance_ranking",
]
