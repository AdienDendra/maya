"""Maya vertex-selection helpers for Region Research stage results."""

from typing import Iterable

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.region_research.boundary_contacts import (
    BoundaryContactResearchResult,
)
from ad_skin_tools.region_research.nearest_regions import (
    NearestRegionResearchResult,
)
from ad_skin_tools.region_research.single_candidate_reassignment import (
    SingleCandidateReassignmentResult,
)


def select_exact_ties(result: NearestRegionResearchResult) -> None:
    select_vertex_ids(
        result.context.mesh_transform,
        result.nearest.exact_tie_vertex_ids,
    )


def select_all_secondary_regions(result: NearestRegionResearchResult) -> None:
    select_vertex_ids(
        result.context.mesh_transform,
        result.all_secondary_vertex_ids,
    )


def select_owner(
    result: NearestRegionResearchResult,
    joint: str,
) -> None:
    summary = _summary_for_joint(result, joint)
    select_vertex_ids(result.context.mesh_transform, summary.raw_vertex_ids)


def select_primary(
    result: NearestRegionResearchResult,
    joint: str,
) -> None:
    summary = _summary_for_joint(result, joint)
    select_vertex_ids(result.context.mesh_transform, summary.primary_vertex_ids)


def select_secondary(
    result: NearestRegionResearchResult,
    joint: str,
) -> None:
    summary = _summary_for_joint(result, joint)
    select_vertex_ids(result.context.mesh_transform, summary.secondary_vertex_ids)


def select_region(
    result: NearestRegionResearchResult,
    joint: str,
    region_index: int,
) -> None:
    summary = _summary_for_joint(result, joint)
    region_index = int(region_index)
    if region_index < 0 or region_index >= summary.region_count:
        raise IndexError(
            "region_index {} is outside [0, {}).".format(
                region_index,
                summary.region_count,
            )
        )
    select_vertex_ids(
        result.context.mesh_transform,
        summary.regions[region_index].vertex_ids,
    )


def select_region_boundary(
    result: BoundaryContactResearchResult,
    joint: str,
    region_index: int,
) -> None:
    region = _secondary_boundary_for_region(result, joint, region_index)
    select_vertex_ids(
        result.stage_01.context.mesh_transform,
        region.boundary_vertex_ids,
    )


def select_contact_source_vertices(
    result: BoundaryContactResearchResult,
    joint: str,
    region_index: int,
    contact_joint: str,
) -> None:
    region = _secondary_boundary_for_region(result, joint, region_index)
    contact = _contact_for_joint(region, contact_joint)
    select_vertex_ids(
        result.stage_01.context.mesh_transform,
        contact.source_boundary_vertex_ids,
    )


def select_contact_neighbour_vertices(
    result: BoundaryContactResearchResult,
    joint: str,
    region_index: int,
    contact_joint: str,
) -> None:
    region = _secondary_boundary_for_region(result, joint, region_index)
    contact = _contact_for_joint(region, contact_joint)
    select_vertex_ids(
        result.stage_01.context.mesh_transform,
        contact.neighbour_vertex_ids,
    )


def select_contact_pair(
    result: BoundaryContactResearchResult,
    joint: str,
    region_index: int,
    contact_joint: str,
) -> None:
    region = _secondary_boundary_for_region(result, joint, region_index)
    contact = _contact_for_joint(region, contact_joint)
    select_vertex_ids(
        result.stage_01.context.mesh_transform,
        contact.source_boundary_vertex_ids + contact.neighbour_vertex_ids,
    )


def select_unique_dominant_contact_pair(
    result: BoundaryContactResearchResult,
    joint: str,
    region_index: int,
) -> None:
    region = _secondary_boundary_for_region(result, joint, region_index)
    if not region.has_unique_dominant_contact:
        raise RuntimeError(
            "Secondary region does not have one unique dominant contact.\n\n"
            "Joint: {}\nRegion: {}\nDominant influence indices: {}".format(
                joint,
                int(region_index),
                list(region.dominant_contact_influence_indices),
            )
        )
    dominant_index = region.dominant_contact_influence_indices[0]
    dominant_joint = result.stage_01.context.influences[dominant_index]
    select_contact_pair(result, joint, region_index, dominant_joint)


def select_unassigned_contact_pair(
    result: BoundaryContactResearchResult,
    joint: str,
    region_index: int,
) -> None:
    region = _secondary_boundary_for_region(result, joint, region_index)
    select_vertex_ids(
        result.stage_01.context.mesh_transform,
        region.unassigned_source_vertex_ids
        + region.unassigned_neighbour_vertex_ids,
    )


def select_stage_03_changed_vertices(
    result: SingleCandidateReassignmentResult,
) -> None:
    select_vertex_ids(
        result.stage_02.stage_01.context.mesh_transform,
        result.changed_vertex_ids,
    )


def select_stage_03_proposal(
    result: SingleCandidateReassignmentResult,
    source_joint: str,
    source_region_index: int,
) -> None:
    proposal = _stage_03_proposal(
        result,
        source_joint,
        source_region_index,
    )
    select_vertex_ids(
        result.stage_02.stage_01.context.mesh_transform,
        proposal.vertex_ids,
    )


def select_stage_03_recipient(
    result: SingleCandidateReassignmentResult,
    target_joint: str,
) -> None:
    requested = str(target_joint)
    short_name = requested.split("|")[-1]
    matches = [
        proposal
        for proposal in result.proposals
        if proposal.target_joint == requested
        or proposal.target_joint.split("|")[-1] == short_name
    ]
    if not matches:
        raise RuntimeError(
            "No Stage 03 proposal targets this joint:\n{}".format(target_joint)
        )

    full_targets = {proposal.target_joint for proposal in matches}
    if len(full_targets) != 1:
        raise RuntimeError(
            "Target joint short name is ambiguous. Use a full DAG path:\n{}".format(
                target_joint
            )
        )

    select_vertex_ids(
        result.stage_02.stage_01.context.mesh_transform,
        tuple(
            vertex_id
            for proposal in matches
            for vertex_id in proposal.vertex_ids
        ),
    )


def select_stage_03_deferred_region(
    result: SingleCandidateReassignmentResult,
    source_joint: str,
    source_region_index: int,
) -> None:
    region = _stage_03_deferred_region(
        result,
        source_joint,
        source_region_index,
    )
    select_vertex_ids(
        result.stage_02.stage_01.context.mesh_transform,
        region.vertex_ids,
    )


def select_vertex_ids(mesh_transform: str, vertex_ids: Iterable[int]) -> None:
    values = np.asarray(tuple(int(value) for value in vertex_ids), dtype=np.int32)
    cmds.select(clear=True)
    if not values.size:
        return

    components = [
        "{}.vtx[{}]".format(mesh_transform, int(vertex_id))
        for vertex_id in values.tolist()
    ]
    cmds.select(components, replace=True)


def _summary_for_joint(result, joint):
    requested = str(joint)
    exact_matches = [
        summary
        for summary in result.influence_summaries
        if summary.joint == requested
    ]
    if len(exact_matches) == 1:
        return exact_matches[0]

    short_name = requested.split("|")[-1]
    short_matches = [
        summary
        for summary in result.influence_summaries
        if summary.joint.split("|")[-1] == short_name
    ]
    if len(short_matches) == 1:
        return short_matches[0]
    if not short_matches:
        raise RuntimeError("Joint is not part of this research result:\n{}".format(joint))
    raise RuntimeError(
        "Joint short name is ambiguous. Use a full DAG path:\n{}".format(joint)
    )


def _secondary_boundary_for_region(result, joint, region_index):
    requested = str(joint)
    short_name = requested.split("|")[-1]
    region_index = int(region_index)
    matches = [
        region
        for region in result.secondary_regions
        if region.region_index == region_index
        and (
            region.joint == requested
            or region.joint.split("|")[-1] == short_name
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(
            "Secondary region is not part of this boundary result.\n\n"
            "Joint: {}\nRegion: {}".format(joint, region_index)
        )
    raise RuntimeError(
        "Joint short name is ambiguous. Use a full DAG path:\n{}".format(joint)
    )


def _contact_for_joint(region, contact_joint):
    requested = str(contact_joint)
    short_name = requested.split("|")[-1]
    matches = [
        contact
        for contact in region.owner_contacts
        if contact.joint == requested
        or contact.joint.split("|")[-1] == short_name
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(
            "Contact owner does not touch this secondary region.\n\n"
            "Contact joint: {}\nSource joint: {}\nRegion: {}".format(
                contact_joint,
                region.joint,
                region.region_index,
            )
        )
    raise RuntimeError(
        "Contact joint short name is ambiguous. Use a full DAG path:\n{}".format(
            contact_joint
        )
    )


def _stage_03_proposal(result, source_joint, source_region_index):
    requested = str(source_joint)
    short_name = requested.split("|")[-1]
    source_region_index = int(source_region_index)
    matches = [
        proposal
        for proposal in result.proposals
        if proposal.source_region_index == source_region_index
        and (
            proposal.source_joint == requested
            or proposal.source_joint.split("|")[-1] == short_name
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(
            "Region is not part of the Stage 03 proposals.\n\n"
            "Joint: {}\nRegion: {}".format(source_joint, source_region_index)
        )
    raise RuntimeError(
        "Source joint short name is ambiguous. Use a full DAG path:\n{}".format(
            source_joint
        )
    )


def _stage_03_deferred_region(result, source_joint, source_region_index):
    requested = str(source_joint)
    short_name = requested.split("|")[-1]
    source_region_index = int(source_region_index)
    matches = [
        region
        for region in result.deferred_regions
        if region.source_region_index == source_region_index
        and (
            region.source_joint == requested
            or region.source_joint.split("|")[-1] == short_name
        )
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise RuntimeError(
            "Region is not part of the Stage 03 deferred set.\n\n"
            "Joint: {}\nRegion: {}".format(source_joint, source_region_index)
        )
    raise RuntimeError(
        "Source joint short name is ambiguous. Use a full DAG path:\n{}".format(
            source_joint
        )
    )
