"""AD Skin Tool v3 experimental pipeline.

Version 3 is intentionally isolated from ``ad_skin_tools.core``. The accepted
v3.0 exact-distance baseline remains unchanged while later hypotheses are
smoke-tested on separate branches.

Current focused smoke stage:
    v3.3 raw-ownership connectivity for one selected influence.
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

__all__ = [
    "DistanceCandidate",
    "ExactDistanceRankingResult",
    "MayaDistanceInput",
    "OwnershipConnectivityProbeResult",
    "collect_distance_input",
    "format_vertex_ranking",
    "probe_source_joint_ownership_connectivity",
    "rank_vertex",
    "select_probe_vertices",
    "solve_exact_distance_ranking",
]
