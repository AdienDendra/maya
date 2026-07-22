"""Maya selection helpers for Region Research Stage 04."""

from ad_skin_tools.region_research.multiple_candidate_reassignment import (
    MultipleCandidateReassignmentResult,
)
from ad_skin_tools.region_research.visual import select_vertex_ids


def select_changed_vertices(
    result: MultipleCandidateReassignmentResult,
) -> None:
    """Select every vertex changed by the combined Stage 03 + Stage 04 proposal."""

    select_vertex_ids(
        result.stage_03.stage_02.stage_01.context.mesh_transform,
        result.changed_vertex_ids,
    )


def select_proposal(
    result: MultipleCandidateReassignmentResult,
    source_joint: str,
    source_region_index: int,
) -> None:
    """Select one multiple-candidate source region resolved by Stage 04."""

    proposal = _proposal_for_region(
        result,
        source_joint,
        source_region_index,
    )
    select_vertex_ids(
        result.stage_03.stage_02.stage_01.context.mesh_transform,
        proposal.vertex_ids,
    )


def select_recipient(
    result: MultipleCandidateReassignmentResult,
    target_joint: str,
) -> None:
    """Select all Stage 04 source regions proposed for one recipient joint."""

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
            "No Stage 04 proposal targets this joint:\n{}".format(target_joint)
        )

    full_targets = {proposal.target_joint for proposal in matches}
    if len(full_targets) != 1:
        raise RuntimeError(
            "Target joint short name is ambiguous. Use a full DAG path:\n{}".format(
                target_joint
            )
        )

    select_vertex_ids(
        result.stage_03.stage_02.stage_01.context.mesh_transform,
        tuple(
            vertex_id
            for proposal in matches
            for vertex_id in proposal.vertex_ids
        ),
    )


def select_unresolved_region(
    result: MultipleCandidateReassignmentResult,
    source_joint: str,
    source_region_index: int,
) -> None:
    """Select one topology-isolated region that remains unresolved."""

    region = _unresolved_for_region(
        result,
        source_joint,
        source_region_index,
    )
    select_vertex_ids(
        result.stage_03.stage_02.stage_01.context.mesh_transform,
        region.vertex_ids,
    )


def _proposal_for_region(result, source_joint, source_region_index):
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
            "Region is not part of the Stage 04 proposals.\n\n"
            "Joint: {}\nRegion: {}".format(source_joint, source_region_index)
        )
    raise RuntimeError(
        "Source joint short name is ambiguous. Use a full DAG path:\n{}".format(
            source_joint
        )
    )


def _unresolved_for_region(result, source_joint, source_region_index):
    requested = str(source_joint)
    short_name = requested.split("|")[-1]
    source_region_index = int(source_region_index)
    matches = [
        region
        for region in result.unresolved_regions
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
            "Region is not part of the Stage 04 unresolved set.\n\n"
            "Joint: {}\nRegion: {}".format(source_joint, source_region_index)
        )
    raise RuntimeError(
        "Source joint short name is ambiguous. Use a full DAG path:\n{}".format(
            source_joint
        )
    )
