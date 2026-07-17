"""AD Skin Tool v3 experimental pipeline.

Version 3 is intentionally isolated from ``ad_skin_tools.core``. The accepted
v3.0 exact-distance baseline and preserved v3.3 connectivity checkpoint remain
unchanged while v3.4 tests multi-primary region qualification.
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
from ad_skin_tools.v3.ownership_connectivity_probe import (
    OwnershipConnectivityProbeResult,
    probe_source_joint_ownership_connectivity,
    select_probe_vertices,
)
from ad_skin_tools.v3.region_facing_probe import (
    AnchorFaceOrientation,
    RegionFacingDiagnostic,
    RegionFacingProbeResult,
    probe_region_facing,
    select_probe_vertices as select_region_facing_vertices,
)

__all__ = [
    "AnchorFaceOrientation",
    "DistanceCandidate",
    "ExactDistanceRankingResult",
    "MayaDistanceInput",
    "OwnershipConnectivityProbeResult",
    "RegionFacingDiagnostic",
    "RegionFacingProbeResult",
    "collect_distance_input",
    "format_vertex_ranking",
    "probe_region_facing",
    "probe_source_joint_ownership_connectivity",
    "rank_vertex",
    "select_probe_vertices",
    "select_region_facing_vertices",
    "solve_exact_distance_ranking",
]
