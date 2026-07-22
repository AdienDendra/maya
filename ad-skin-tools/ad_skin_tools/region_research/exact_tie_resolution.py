"""Resolve exact nearest-distance ties before Region connectivity is built.

The resolver is intentionally pragmatic and deterministic. It first uses local
mesh topology, then a frozen global owner-count balance, and finally a stable
joint key. No joint hierarchy, joint name, or input ordering participates.
"""

from dataclasses import dataclass
import time
from typing import Dict, Mapping, Tuple

import numpy as np

from ad_skin_tools.region_research.context import ResearchMeshContext


RESOLVED_BY_TOPOLOGY = "topology_neighbour"
RESOLVED_BY_FEWER_OWNED_VERTICES = "fewer_owned_vertices"
RESOLVED_BY_STABLE_JOINT_KEY = "stable_joint_key"


@dataclass(frozen=True)
class ExactTieResolutionResult:
    owner_indices: np.ndarray
    candidate_indices_by_vertex: Dict[int, Tuple[int, ...]]
    resolved_by_topology_vertex_ids: Tuple[int, ...]
    resolved_by_fewer_owned_vertices_vertex_ids: Tuple[int, ...]
    resolved_by_stable_joint_key_vertex_ids: Tuple[int, ...]
    topology_pass_count: int
    elapsed_seconds: float

    @property
    def resolved_vertex_count(self) -> int:
        return len(self.candidate_indices_by_vertex)

    @property
    def remaining_unassigned_vertex_count(self) -> int:
        return int(np.count_nonzero(self.owner_indices < 0))


def resolve_exact_ties(
    context: ResearchMeshContext,
    raw_owner_indices: np.ndarray,
    candidate_indices_by_vertex: Mapping[int, Tuple[int, ...]],
) -> ExactTieResolutionResult:
    """Resolve every raw exact tie before connected-owner regions are discovered."""

    started = time.perf_counter()
    owners = np.asarray(raw_owner_indices, dtype=np.int32).copy()
    candidates = {
        int(vertex_id): tuple(int(value) for value in values)
        for vertex_id, values in candidate_indices_by_vertex.items()
    }

    expected_unassigned = set(candidates)
    actual_unassigned = set(
        int(value)
        for value in np.where(owners < 0)[0].astype(np.int32).tolist()
    )
    if actual_unassigned != expected_unassigned:
        raise RuntimeError(
            "Raw exact-tie owner map does not match the captured tie candidates."
        )

    influence_count = context.influence_count
    assigned_owners = owners[owners >= 0]
    frozen_owner_counts = np.bincount(
        assigned_owners,
        minlength=influence_count,
    ).astype(np.int64)

    unresolved = set(expected_unassigned)
    topology_resolved = []
    topology_pass_count = 0

    # Proposals are applied simultaneously per pass so vertex traversal order does
    # not affect propagation through a connected exact-tie strip.
    while unresolved:
        proposals = {}

        for vertex_id in sorted(unresolved):
            candidate_set = set(candidates[vertex_id])
            support_counts = {}

            for neighbour_id in context.adjacency[vertex_id]:
                neighbour_owner = int(owners[int(neighbour_id)])
                if neighbour_owner not in candidate_set:
                    continue
                support_counts[neighbour_owner] = (
                    support_counts.get(neighbour_owner, 0) + 1
                )

            if not support_counts:
                continue

            maximum_support = max(support_counts.values())
            winners = tuple(
                owner_index
                for owner_index, support in support_counts.items()
                if support == maximum_support
            )
            if len(winners) == 1:
                proposals[vertex_id] = int(winners[0])

        if not proposals:
            break

        topology_pass_count += 1
        for vertex_id in sorted(proposals):
            owners[vertex_id] = int(proposals[vertex_id])
            topology_resolved.append(int(vertex_id))
            unresolved.remove(vertex_id)

    fewer_owned_resolved = []
    stable_key_resolved = []

    # The counts remain frozen throughout this fallback. Updating them per vertex
    # would make the result depend on which tied vertex happened to be visited first.
    for vertex_id in sorted(unresolved):
        candidate_indices = candidates[vertex_id]
        minimum_count = min(
            int(frozen_owner_counts[owner_index])
            for owner_index in candidate_indices
        )
        count_winners = tuple(
            owner_index
            for owner_index in candidate_indices
            if int(frozen_owner_counts[owner_index]) == minimum_count
        )

        if len(count_winners) == 1:
            selected_owner = int(count_winners[0])
            fewer_owned_resolved.append(int(vertex_id))
        else:
            selected_owner = min(
                count_winners,
                key=lambda owner_index: _stable_joint_key(context, owner_index),
            )
            stable_key_resolved.append(int(vertex_id))

        owners[vertex_id] = selected_owner

    if np.any(owners < 0):
        unresolved_ids = np.where(owners < 0)[0].astype(np.int32).tolist()
        raise RuntimeError(
            "Exact-tie resolution left unassigned vertices: {}".format(
                unresolved_ids[:20]
            )
        )

    return ExactTieResolutionResult(
        owner_indices=owners,
        candidate_indices_by_vertex=candidates,
        resolved_by_topology_vertex_ids=tuple(sorted(topology_resolved)),
        resolved_by_fewer_owned_vertices_vertex_ids=tuple(
            sorted(fewer_owned_resolved)
        ),
        resolved_by_stable_joint_key_vertex_ids=tuple(sorted(stable_key_resolved)),
        topology_pass_count=int(topology_pass_count),
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _stable_joint_key(context: ResearchMeshContext, influence_index: int):
    position = context.influence_positions[int(influence_index)]
    return (
        float(position[0]),
        float(position[1]),
        float(position[2]),
        context.influence_uuids[int(influence_index)],
    )
