"""AD Skin Tool v3 experimental pipeline.

Version 3 is intentionally isolated from ``ad_skin_tools.core``. Each solver
stage is introduced and smoke-tested independently before it becomes part of a
production bind pipeline.

Accepted baseline:
    v3.0 exact world-space joint-pivot distance ranking.

Current experiment:
    v3.2 incoming-bone-segment visibility for one selected raw owner.
"""

from ad_skin_tools.v3.distance_ranking import (
    DistanceCandidate,
    ExactDistanceRankingResult,
    format_vertex_ranking,
    rank_vertex,
    solve_exact_distance_ranking,
)
from ad_skin_tools.v3.maya_scene import (
    MayaDistanceInput,
    collect_distance_input,
)
from ad_skin_tools.v3.segment_visibility_probe import (
    SegmentVisibilityProbeResult,
    probe_source_joint_segment_visibility,
    select_probe_vertices,
)

__all__ = [
    "DistanceCandidate",
    "ExactDistanceRankingResult",
    "MayaDistanceInput",
    "SegmentVisibilityProbeResult",
    "collect_distance_input",
    "format_vertex_ranking",
    "probe_source_joint_segment_visibility",
    "rank_vertex",
    "select_probe_vertices",
    "solve_exact_distance_ranking",
]
