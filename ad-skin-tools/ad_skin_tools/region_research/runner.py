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
from ad_skin_tools.region_research.single_candidate_reassignment import (
    SingleCandidateReassignmentResult,
    propose_single_candidate_reassignments,
)


STAGE_01_RESULT_SLOT = "AD_SKIN_REGION_RESEARCH_STAGE_01"
STAGE_02_RESULT_SLOT = "AD_SKIN_REGION_RESEARCH_STAGE_02"
STAGE_03_RESULT_SLOT = "AD_SKIN_REGION_RESEARCH_STAGE_03"
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


def run_stage_03(
    mesh: str,
    joints: Sequence[str],
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> SingleCandidateReassignmentResult:
    """Run stages one through three from fresh scene data."""

    stage_02 = run_stage_02(
        mesh=mesh,
        joints=joints,
        distance_chunk_size=int(distance_chunk_size),
    )
    return run_stage_03_from_stage_02(stage_02)


def run_stage_03_from_stage_02(
    stage_02: BoundaryContactResearchResult,
) -> SingleCandidateReassignmentResult:
    """Build conservative single-candidate proposals from Stage 02."""

    result = propose_single_candidate_reassignments(stage_02)
    setattr(builtins, STAGE_01_RESULT_SLOT, stage_02.stage_01)
    setattr(builtins, STAGE_02_RESULT_SLOT, stage_02)
    setattr(builtins, STAGE_03_RESULT_SLOT, result)
    print_stage_03_report(result)
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


def get_last_stage_03_result() -> SingleCandidateReassignmentResult:
    result = getattr(builtins, STAGE_03_RESULT_SLOT, None)
    if result is None:
        raise RuntimeError(
            "No Region Research stage 03 result exists. Run run_stage_03 or "
            "run_stage_03_from_stage_02 first."
        )
    return result


def print_stage_01_report(result: NearestRegionResearchResult) -> None:
    nearest = result.nearest
    resolution = nearest.tie_resolution

    print("\n[AD Skin Tool - Region Research / Stage 01]")
    print("Mesh:", result.context.mesh_transform)
    print("Vertices:", result.context.vertex_count)
    print("Faces:", result.context.face_count)
    print("Edges:", result.context.edge_count)
    print("Influences:", result.context.influence_count)
    print("Raw exact-tie vertices:", nearest.exact_tie_vertex_count)
    print(
        "  resolved by topology:",
        len(resolution.resolved_by_topology_vertex_ids),
    )
    print(
        "  resolved by fewer owned vertices:",
        len(resolution.resolved_by_fewer_owned_vertices_vertex_ids),
    )
    print(
        "  resolved by stable joint key:",
        len(resolution.resolved_by_stable_joint_key_vertex_ids),
    )
    print("  topology propagation passes:", resolution.topology_pass_count)
    print("Remaining unassigned vertices:", nearest.remaining_unassigned_vertex_count)
    print("Connected owner regions:", result.total_region_count)
    print("Secondary regions:", result.secondary_region_count)
    print(
        "Influences with ambiguous primary anchor:",
        result.ambiguous_primary_influence_count,
    )
    print("\nTimings:")
    print("  scene capture:", round(result.context.scene_capture_seconds, 6))
    print("  adjacency build:", round(result.context.adjacency_seconds, 6))
    print("  exact nearest distance:", round(nearest.distance_seconds, 6))
    print("  exact-tie resolution:", round(resolution.elapsed_seconds, 6))
    print("  exact nearest total:", round(nearest.elapsed_seconds, 6))
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


def print_stage_03_report(result: SingleCandidateReassignmentResult) -> None:
    print("\n[AD Skin Tool - Region Research / Stage 03]")
    print("Single-candidate proposals:", result.proposal_count)
    print("Deferred secondary regions:", result.deferred_region_count)
    print("Proposed changed vertices:", result.changed_vertex_count)
    print("Proposal analysis:", round(result.elapsed_seconds, 6))

    print("\nProposals:")
    if not result.proposals:
        print("  none")
    for proposal in result.proposals:
        print(
            "  {} region {} -> {} | vertices={} | contact_edges={}".format(
                proposal.source_joint.split("|")[-1],
                proposal.source_region_index,
                proposal.target_joint.split("|")[-1],
                proposal.vertex_count,
                proposal.contact_edge_count,
            )
        )

    print("\nDeferred:")
    if not result.deferred_regions:
        print("  none")
    for region in result.deferred_regions:
        candidate_names = [
            joint.split("|")[-1]
            for joint in region.candidate_joints
        ]
        print(
            "  {} region {} | vertices={} | reason={} | candidates={} | "
            "unassigned_edges={}".format(
                region.source_joint.split("|")[-1],
                region.source_region_index,
                region.vertex_count,
                region.reason,
                candidate_names,
                region.unassigned_edge_count,
            )
        )
