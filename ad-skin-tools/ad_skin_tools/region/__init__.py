"""Universal topology-region hard ownership for AD Skin Tool."""

from ad_skin_tools.region.connectivity import (
    OwnershipConnectivityResult,
    build_vertex_adjacency,
    partition_influence_ownership,
    probe_source_joint_ownership_connectivity,
    select_connectivity_vertices,
)
from ad_skin_tools.region.distance_ranking import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    DistanceCandidate,
    ExactDistanceRankingResult,
    ExactDistanceTables,
    build_exact_distance_tables,
    format_vertex_ranking,
    rank_vertex,
    solve_exact_distance_ranking,
)
from ad_skin_tools.region.facing import (
    AnchorFaceOrientation,
    RegionFacingDiagnostic,
    RegionFacingResult,
    classify_region_facing,
    probe_region_facing,
    select_facing_vertices,
)
from ad_skin_tools.region.maya_scene import MayaDistanceInput, collect_distance_input
from ad_skin_tools.region.solver import (
    InfluenceRegionResolution,
    RegionOwnershipResult,
    solve_region_ownership,
)

__all__ = [
    "AnchorFaceOrientation",
    "DEFAULT_DISTANCE_CHUNK_SIZE",
    "DistanceCandidate",
    "ExactDistanceRankingResult",
    "ExactDistanceTables",
    "InfluenceRegionResolution",
    "MayaDistanceInput",
    "OwnershipConnectivityResult",
    "RegionFacingDiagnostic",
    "RegionFacingResult",
    "RegionOwnershipResult",
    "build_exact_distance_tables",
    "build_vertex_adjacency",
    "classify_region_facing",
    "collect_distance_input",
    "format_vertex_ranking",
    "partition_influence_ownership",
    "probe_region_facing",
    "probe_source_joint_ownership_connectivity",
    "rank_vertex",
    "select_connectivity_vertices",
    "select_facing_vertices",
    "solve_exact_distance_ranking",
    "solve_region_ownership",
]
