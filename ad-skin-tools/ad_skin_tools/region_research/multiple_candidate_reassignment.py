"""Stage 04: resolve multiple topology candidates with whole-region distance.

Stage 03 already handles secondary regions with exactly one boundary candidate.
This stage starts from the Stage 03 proposal owner map and resolves only deferred
regions that touch multiple assigned owners. Every decision is made for the whole
connected secondary region; no vertex inside the region is split independently.
"""

from dataclasses import dataclass
import time
from typing import Dict, Tuple

import numpy as np

from ad_skin_tools.region_research.single_candidate_reassignment import (
    DEFERRED_MULTIPLE_ASSIGNED_CONTACTS,
    DEFERRED_NO_ASSIGNED_CONTACT,
    DeferredSecondaryRegion,
    SingleCandidateReassignmentResult,
)


RESOLUTION_AGGREGATE_DISTANCE = "aggregate_squared_distance"
RESOLUTION_CONTACT_EDGE_COUNT = "contact_edge_count"
RESOLUTION_FEWER_OWNED_VERTICES = "fewer_owned_vertices"
RESOLUTION_STABLE_JOINT_KEY = "stable_joint_key"


@dataclass(frozen=True)
class MultipleCandidateScore:
    influence_index: int
    joint: str
    aggregate_squared_distance: float
    mean_squared_distance: float
    contact_edge_count: int
    frozen_owner_vertex_count: int


@dataclass(frozen=True)
class MultipleCandidateProposal:
    source_influence_index: int
    source_joint: str
    source_region_index: int
    target_influence_index: int
    target_joint: str
    vertex_ids: Tuple[int, ...]
    candidate_scores: Tuple[MultipleCandidateScore, ...]
    resolution_reason: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class MultipleCandidateReassignmentResult:
    stage_03: SingleCandidateReassignmentResult
    proposed_owner_indices: np.ndarray
    proposals: Tuple[MultipleCandidateProposal, ...]
    unresolved_regions: Tuple[DeferredSecondaryRegion, ...]
    changed_vertex_ids: Tuple[int, ...]
    elapsed_seconds: float

    @property
    def proposal_count(self) -> int:
        return len(self.proposals)

    @property
    def unresolved_region_count(self) -> int:
        return len(self.unresolved_regions)

    @property
    def changed_vertex_count(self) -> int:
        return len(self.changed_vertex_ids)

    @property
    def total_secondary_region_count(self) -> int:
        return self.stage_03.stage_02.secondary_region_count

    @property
    def resolved_secondary_region_count(self) -> int:
        return self.stage_03.proposal_count + self.proposal_count

    @property
    def all_secondary_regions_resolved(self) -> bool:
        return self.unresolved_region_count == 0


def propose_multiple_candidate_reassignments(
    stage_03: SingleCandidateReassignmentResult,
) -> MultipleCandidateReassignmentResult:
    """Resolve Stage 03 multiple-candidate deferrals without editing Maya data."""

    started = time.perf_counter()
    stage_02 = stage_03.stage_02
    stage_01 = stage_02.stage_01
    context = stage_01.context

    owners = np.asarray(
        stage_03.proposed_owner_indices,
        dtype=np.int32,
    ).copy()
    if np.any(owners < 0):
        raise RuntimeError(
            "Stage 04 requires a complete owner map before region proposals."
        )

    original_owners = np.asarray(
        stage_01.nearest.owner_indices,
        dtype=np.int32,
    )
    frozen_owner_counts = np.bincount(
        original_owners,
        minlength=context.influence_count,
    ).astype(np.int64)

    boundaries_by_region = {
        (int(region.influence_index), int(region.region_index)): region
        for region in stage_02.secondary_regions
    }

    proposals = []
    unresolved = []

    for deferred in stage_03.deferred_regions:
        if deferred.reason == DEFERRED_NO_ASSIGNED_CONTACT:
            unresolved.append(deferred)
            continue

        if deferred.reason != DEFERRED_MULTIPLE_ASSIGNED_CONTACTS:
            raise RuntimeError(
                "Stage 04 received an unsupported Stage 03 deferral reason: {}".format(
                    deferred.reason
                )
            )

        key = (
            int(deferred.source_influence_index),
            int(deferred.source_region_index),
        )
        region = boundaries_by_region.get(key)
        if region is None:
            raise RuntimeError(
                "Unable to find Stage 02 boundary data for {} region {}.".format(
                    deferred.source_joint,
                    deferred.source_region_index,
                )
            )
        if len(region.owner_contacts) < 2:
            raise RuntimeError(
                "Multiple-candidate deferral does not contain multiple contacts."
            )
        if region.unassigned_edge_count:
            raise RuntimeError(
                "Stage 04 found an unassigned boundary after exact-tie resolution."
            )

        candidate_scores = _score_candidates(
            context=context,
            region_vertex_ids=region.region_vertex_ids,
            owner_contacts=region.owner_contacts,
            frozen_owner_counts=frozen_owner_counts,
        )
        target_index, reason = _select_candidate(
            context=context,
            candidate_scores=candidate_scores,
        )
        target_joint = context.influences[int(target_index)]

        vertex_ids = np.asarray(region.region_vertex_ids, dtype=np.int32)
        owners[vertex_ids] = int(target_index)
        proposals.append(
            MultipleCandidateProposal(
                source_influence_index=int(region.influence_index),
                source_joint=region.joint,
                source_region_index=int(region.region_index),
                target_influence_index=int(target_index),
                target_joint=target_joint,
                vertex_ids=region.region_vertex_ids,
                candidate_scores=candidate_scores,
                resolution_reason=reason,
            )
        )

    changed_vertex_ids = tuple(
        int(value)
        for value in np.where(
            owners != original_owners
        )[0].astype(np.int32).tolist()
    )

    return MultipleCandidateReassignmentResult(
        stage_03=stage_03,
        proposed_owner_indices=owners,
        proposals=tuple(proposals),
        unresolved_regions=tuple(unresolved),
        changed_vertex_ids=changed_vertex_ids,
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _score_candidates(
    context,
    region_vertex_ids,
    owner_contacts,
    frozen_owner_counts,
) -> Tuple[MultipleCandidateScore, ...]:
    vertex_ids = np.asarray(region_vertex_ids, dtype=np.int32)
    candidate_indices = np.asarray(
        [int(contact.influence_index) for contact in owner_contacts],
        dtype=np.int32,
    )

    region_positions = context.vertex_positions[vertex_ids]
    candidate_positions = context.influence_positions[candidate_indices]
    delta = (
        region_positions[:, np.newaxis, :]
        - candidate_positions[np.newaxis, :, :]
    )
    squared = np.einsum("vci,vci->vc", delta, delta)
    aggregate = np.sum(squared, axis=0, dtype=np.float64)
    mean = aggregate / float(vertex_ids.size)

    contacts_by_index: Dict[int, object] = {
        int(contact.influence_index): contact
        for contact in owner_contacts
    }
    scores = [
        MultipleCandidateScore(
            influence_index=int(owner_index),
            joint=context.influences[int(owner_index)],
            aggregate_squared_distance=float(aggregate[column]),
            mean_squared_distance=float(mean[column]),
            contact_edge_count=int(
                contacts_by_index[int(owner_index)].edge_count
            ),
            frozen_owner_vertex_count=int(
                frozen_owner_counts[int(owner_index)]
            ),
        )
        for column, owner_index in enumerate(candidate_indices.tolist())
    ]
    scores.sort(
        key=lambda score: (
            score.aggregate_squared_distance,
            _stable_joint_key(context, score.influence_index),
        )
    )
    return tuple(scores)


def _select_candidate(
    context,
    candidate_scores: Tuple[MultipleCandidateScore, ...],
) -> Tuple[int, str]:
    if len(candidate_scores) < 2:
        raise RuntimeError("Stage 04 candidate selection requires at least two owners.")

    minimum_distance = min(
        score.aggregate_squared_distance
        for score in candidate_scores
    )
    winners = tuple(
        score
        for score in candidate_scores
        if score.aggregate_squared_distance == minimum_distance
    )
    if len(winners) == 1:
        return int(winners[0].influence_index), RESOLUTION_AGGREGATE_DISTANCE

    maximum_contact = max(score.contact_edge_count for score in winners)
    winners = tuple(
        score
        for score in winners
        if score.contact_edge_count == maximum_contact
    )
    if len(winners) == 1:
        return int(winners[0].influence_index), RESOLUTION_CONTACT_EDGE_COUNT

    minimum_owner_count = min(
        score.frozen_owner_vertex_count
        for score in winners
    )
    winners = tuple(
        score
        for score in winners
        if score.frozen_owner_vertex_count == minimum_owner_count
    )
    if len(winners) == 1:
        return int(winners[0].influence_index), RESOLUTION_FEWER_OWNED_VERTICES

    selected = min(
        winners,
        key=lambda score: _stable_joint_key(
            context,
            score.influence_index,
        ),
    )
    return int(selected.influence_index), RESOLUTION_STABLE_JOINT_KEY


def _stable_joint_key(context, influence_index: int):
    position = context.influence_positions[int(influence_index)]
    return (
        float(position[0]),
        float(position[1]),
        float(position[2]),
        context.influence_uuids[int(influence_index)],
    )
