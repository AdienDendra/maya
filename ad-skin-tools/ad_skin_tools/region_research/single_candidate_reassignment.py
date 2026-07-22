"""Stage 03: conservative single-candidate reassignment proposals.

This stage never edits a skinCluster or the stage-one result. It copies the hard
owner map and changes a whole secondary region only when Stage 02 found exactly
one assigned boundary-contact owner and no unassigned boundary contact.
"""

from dataclasses import dataclass
import time
from typing import Tuple

import numpy as np

from ad_skin_tools.region_research.boundary_contacts import (
    BoundaryContactResearchResult,
)


DEFERRED_NO_ASSIGNED_CONTACT = "no_assigned_boundary_contact"
DEFERRED_MULTIPLE_ASSIGNED_CONTACTS = "multiple_assigned_boundary_contacts"
DEFERRED_UNASSIGNED_BOUNDARY_CONTACT = "unassigned_boundary_contact"


@dataclass(frozen=True)
class SingleCandidateProposal:
    source_influence_index: int
    source_joint: str
    source_region_index: int
    target_influence_index: int
    target_joint: str
    vertex_ids: Tuple[int, ...]
    contact_edge_count: int

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class DeferredSecondaryRegion:
    source_influence_index: int
    source_joint: str
    source_region_index: int
    vertex_ids: Tuple[int, ...]
    candidate_influence_indices: Tuple[int, ...]
    candidate_joints: Tuple[str, ...]
    unassigned_edge_count: int
    reason: str

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class SingleCandidateReassignmentResult:
    stage_02: BoundaryContactResearchResult
    proposed_owner_indices: np.ndarray
    proposals: Tuple[SingleCandidateProposal, ...]
    deferred_regions: Tuple[DeferredSecondaryRegion, ...]
    changed_vertex_ids: Tuple[int, ...]
    elapsed_seconds: float

    @property
    def proposal_count(self) -> int:
        return len(self.proposals)

    @property
    def deferred_region_count(self) -> int:
        return len(self.deferred_regions)

    @property
    def changed_vertex_count(self) -> int:
        return len(self.changed_vertex_ids)


def propose_single_candidate_reassignments(
    stage_02: BoundaryContactResearchResult,
) -> SingleCandidateReassignmentResult:
    """Build one simultaneous proposal pass without changing Maya scene data."""

    started = time.perf_counter()
    stage_01 = stage_02.stage_01
    owners = np.asarray(
        stage_01.nearest.owner_indices,
        dtype=np.int32,
    ).copy()

    proposals = []
    deferred = []

    for region in stage_02.secondary_regions:
        contacts = region.owner_contacts

        if region.unassigned_edge_count:
            deferred.append(
                _deferred_region(
                    region=region,
                    reason=DEFERRED_UNASSIGNED_BOUNDARY_CONTACT,
                )
            )
            continue

        if not contacts:
            deferred.append(
                _deferred_region(
                    region=region,
                    reason=DEFERRED_NO_ASSIGNED_CONTACT,
                )
            )
            continue

        if len(contacts) != 1:
            deferred.append(
                _deferred_region(
                    region=region,
                    reason=DEFERRED_MULTIPLE_ASSIGNED_CONTACTS,
                )
            )
            continue

        contact = contacts[0]
        vertex_ids = np.asarray(region.region_vertex_ids, dtype=np.int32)
        owners[vertex_ids] = int(contact.influence_index)
        proposals.append(
            SingleCandidateProposal(
                source_influence_index=int(region.influence_index),
                source_joint=region.joint,
                source_region_index=int(region.region_index),
                target_influence_index=int(contact.influence_index),
                target_joint=contact.joint,
                vertex_ids=region.region_vertex_ids,
                contact_edge_count=int(contact.edge_count),
            )
        )

    changed_vertex_ids = tuple(
        int(value)
        for value in np.where(
            owners != stage_01.nearest.owner_indices
        )[0].astype(np.int32).tolist()
    )
    return SingleCandidateReassignmentResult(
        stage_02=stage_02,
        proposed_owner_indices=owners,
        proposals=tuple(proposals),
        deferred_regions=tuple(deferred),
        changed_vertex_ids=changed_vertex_ids,
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _deferred_region(region, reason: str) -> DeferredSecondaryRegion:
    return DeferredSecondaryRegion(
        source_influence_index=int(region.influence_index),
        source_joint=region.joint,
        source_region_index=int(region.region_index),
        vertex_ids=region.region_vertex_ids,
        candidate_influence_indices=tuple(
            int(contact.influence_index)
            for contact in region.owner_contacts
        ),
        candidate_joints=tuple(
            contact.joint
            for contact in region.owner_contacts
        ),
        unassigned_edge_count=int(region.unassigned_edge_count),
        reason=str(reason),
    )
