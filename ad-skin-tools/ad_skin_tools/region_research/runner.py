"""Interactive runner for Region Research stage 01."""

import builtins
from typing import Sequence

from ad_skin_tools.region_research.nearest_regions import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    NearestRegionResearchResult,
    solve_nearest_regions,
)


RESULT_SLOT = "AD_SKIN_REGION_RESEARCH_STAGE_01"


def run_stage_01(
    mesh: str,
    joints: Sequence[str],
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> NearestRegionResearchResult:
    """Run stage 01, print diagnostics, and store the result in ``builtins``."""

    result = solve_nearest_regions(
        mesh=mesh,
        joints=joints,
        distance_chunk_size=int(distance_chunk_size),
    )
    setattr(builtins, RESULT_SLOT, result)
    print_stage_01_report(result)
    return result


def get_last_result() -> NearestRegionResearchResult:
    result = getattr(builtins, RESULT_SLOT, None)
    if result is None:
        raise RuntimeError(
            "No Region Research stage 01 result exists. Run run_stage_01 first."
        )
    return result


def print_stage_01_report(result: NearestRegionResearchResult) -> None:
    print("\n[AD Skin Tool - Region Research / Stage 01]")
    print("Mesh:", result.context.mesh_transform)
    print("Vertices:", result.context.vertex_count)
    print("Faces:", result.context.face_count)
    print("Edges:", result.context.edge_count)
    print("Influences:", result.context.influence_count)
    print("Exact-tie vertices:", result.nearest.exact_tie_vertex_count)
    print("Connected owner regions:", result.total_region_count)
    print("Secondary regions:", result.secondary_region_count)
    print(
        "Influences with ambiguous primary anchor:",
        result.ambiguous_primary_influence_count,
    )
    print("\nTimings:")
    print("  scene capture:", round(result.context.scene_capture_seconds, 6))
    print("  adjacency build:", round(result.context.adjacency_seconds, 6))
    print("  exact nearest:", round(result.nearest.elapsed_seconds, 6))
    print("  connected regions:", round(result.connectivity_seconds, 6))
    print("  total:", round(result.elapsed_seconds, 6))
    print("\nPer influence:")

    for summary in result.influence_summaries:
        short_name = summary.joint.split("|")[-1]
        print(
            "  {}: vertices={} | regions={} | primary={} | secondary={}".format(
                short_name,
                summary.raw_vertex_count,
                summary.region_count,
                list(summary.primary_region_indices),
                len(summary.secondary_region_indices),
            )
        )
