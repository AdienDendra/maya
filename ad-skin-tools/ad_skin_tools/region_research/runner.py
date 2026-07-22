"""Interactive runners for Region Research stages."""

import builtins
from typing import Sequence

from ad_skin_tools.region_research.boundary_contacts import (
    BoundaryContactResearchResult,
    analyze_secondary_region_boundaries,
)
from ad_skin_tools.region_research.nearest_regions import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    NearestRegionResearchResult,
    solve_nearest_regions,
)


STAGE_01_RESULT_SLOT = "AD_SKIN_REGION_RESEARCH_STAGE_01"
STAGE_02_RESULT_SLOT = "AD_SKIN_REGION_RESEARCH_STAGE_02"
RESULT_SLOT = STAGE_01_RESULT_SLOT


def run_stage_01(
    mesh: str,
    joints: Sequence[str],
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> NearestRegionResearchResult:
    """Run exact-nearest regions, print diagnostics, and store the result."""

    result = solve_nearest_regions(
        mesh=mesh,
        joints=joints,
        distance_chunk_size=int(distance_chunk_size),
    )
    setattr(builtins, STAGE_01_RESULT_SLOT, result)
    print_stage_01_report(result)
    return result


def run_stage_02(
    mesh: str,
    joints: Sequence[str],
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> BoundaryContactResearchResult:
    """Run stages one and two from fresh scene data."""

    stage_01 = solve_nearest_regions(
        mesh=mesh,
        joints=joints,
        distance_chunk_size=int(distance_chunk_size),
    )
    setattr(builtins, STAGE_01_RESULT_SLOT, stage_01)
    print_stage_01_report(stage_01)
    return run_stage_02_from_stage_01(stage_01)


def run_stage_02_from_stage_01(
    stage_01: NearestRegionResearchResult,
) -> BoundaryContactResearchResult:
    """Analyze boundary contacts without repeating scene or distance work."""

    result = analyze_secondary_region_boundaries(stage_01)
    setattr(builtins, STAGE_01_RESULT_SLOT, stage_01)
    setattr(builtins, STAGE_02_RESULT_SLOT, result)
    print_stage_02_report(result)
    return result


def get_last_result() -> NearestRegionResearchResult:
    """Backward-compatible alias for the last stage-one result."""

    return get_last_stage_01_result()


def get_last_stage_01_result() -> NearestRegionResearchResult:
    result = getattr(builtins, STAGE_01_RESULT_SLOT, None)
    if result is None:
        raise RuntimeError(
            "No Region Research stage 01 result exists. Run run_stage_01 first."
        )
    return result


def get_last_stage_02_result() -> BoundaryContactResearchResult:
    result = getattr(builtins, STAGE_02_RESULT_SLOT, None)
    if result is None:
        raise RuntimeError(
            "No Region Research stage 02 result exists. Run run_stage_02 or "
            "run_stage_02_from_stage_01 first."
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


def print_stage_02_report(result: BoundaryContactResearchResult) -> None:
    print("\n[AD Skin Tool - Region Research / Stage 02]")
    print("Secondary regions analyzed:", result.secondary_region_count)
    print(
        "Regions with unique dominant contact:",
        result.unique_dominant_contact_region_count,
    )
    print(
        "Regions touching multiple owners:",
        result.multiple_contact_owner_region_count,
    )
    print(
        "Regions with no external topology contact:",
        result.no_external_contact_region_count,
    )
    print("Boundary analysis:", round(result.elapsed_seconds, 6))
    print("\nPer secondary region:")

    for region in result.secondary_regions:
        source_name = region.joint.split("|")[-1]
        if region.owner_contacts:
            contact_text = ", ".join(
                "{}={} edges".format(
                    contact.joint.split("|")[-1],
                    contact.edge_count,
                )
                for contact in region.owner_contacts
            )
        else:
            contact_text = "none"

        if region.unassigned_edge_count:
            contact_text += ", unassigned={} edges".format(
                region.unassigned_edge_count
            )

        dominant_names = [
            result.stage_01.context.influences[index].split("|")[-1]
            for index in region.dominant_contact_influence_indices
        ]
        print(
            "  {} region {} | vertices={} | boundary={} | contacts=[{}] | "
            "dominant={}".format(
                source_name,
                region.region_index,
                region.region_vertex_count,
                region.boundary_vertex_count,
                contact_text,
                dominant_names,
            )
        )
