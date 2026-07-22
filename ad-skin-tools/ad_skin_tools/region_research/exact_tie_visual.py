"""Maya selection helpers for resolved exact-distance ties."""

from typing import Iterable

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.region_research.nearest_regions import (
    NearestRegionResearchResult,
)


def select_all_exact_ties(result: NearestRegionResearchResult) -> None:
    _select_vertex_ids(
        result.context.mesh_transform,
        result.nearest.exact_tie_vertex_ids,
    )


def select_ties_resolved_by_topology(
    result: NearestRegionResearchResult,
) -> None:
    _select_vertex_ids(
        result.context.mesh_transform,
        result.nearest.tie_resolution.resolved_by_topology_vertex_ids,
    )


def select_ties_resolved_by_fewer_owned_vertices(
    result: NearestRegionResearchResult,
) -> None:
    _select_vertex_ids(
        result.context.mesh_transform,
        result.nearest.tie_resolution.resolved_by_fewer_owned_vertices_vertex_ids,
    )


def select_ties_resolved_by_stable_joint_key(
    result: NearestRegionResearchResult,
) -> None:
    _select_vertex_ids(
        result.context.mesh_transform,
        result.nearest.tie_resolution.resolved_by_stable_joint_key_vertex_ids,
    )


def print_exact_tie_vertex(
    result: NearestRegionResearchResult,
    vertex_id: int,
) -> None:
    vertex_id = int(vertex_id)
    candidate_indices = result.nearest.exact_tie_candidate_indices.get(vertex_id)
    if candidate_indices is None:
        raise RuntimeError(
            "Vertex {} was not an original exact-distance tie.".format(vertex_id)
        )

    resolved_owner = int(result.nearest.owner_indices[vertex_id])
    print("\nExact-tie vertex:", vertex_id)
    print(
        "Candidates:",
        [
            result.context.influences[index].split("|")[-1]
            for index in candidate_indices
        ],
    )
    print(
        "Resolved owner:",
        result.context.influences[resolved_owner].split("|")[-1],
    )


def _select_vertex_ids(mesh_transform: str, vertex_ids: Iterable[int]) -> None:
    values = np.asarray(tuple(int(value) for value in vertex_ids), dtype=np.int32)
    cmds.select(clear=True)
    if not values.size:
        return

    cmds.select(
        [
            "{}.vtx[{}]".format(mesh_transform, int(vertex_id))
            for vertex_id in values.tolist()
        ],
        replace=True,
    )
