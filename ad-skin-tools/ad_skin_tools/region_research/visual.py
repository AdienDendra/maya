"""Maya vertex-selection helpers for Region Research stage results."""

from typing import Iterable

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.region_research.nearest_regions import (
    NearestRegionResearchResult,
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
