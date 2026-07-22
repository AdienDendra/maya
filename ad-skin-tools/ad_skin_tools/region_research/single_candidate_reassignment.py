"""Stage 03: conservative single-contact reassignment proposals.

A secondary region is only a disconnected component of one owner. It is not proof
that the ownership is wrong. This stage copies the complete Stage 01 owner map and
changes a whole secondary region only when exactly one external owner touches its
boundary. Regions touching multiple owners, or no external owner, keep their
original source ownership.
"""

from dataclasses import dataclass
import time
from typing import Tuple

import numpy as np

from ad_skin_tools.region_research.boundary_contacts import (
    BoundaryContactResearchResult,
)


PRESERVED_NO_EXTERNAL_CONTACT = "preserve_no_external_topology_contact"
PRESERVED_MULTIPLE_CONTACT_OWNERS = "preserve_multiple_boundary_contact_owners"

# Backward-compatible names for existing research scripts. These regions are no
# longer considered unresolved; their source ownership is intentionally preserved.
DEFERRED_NO_ASSIGNED_CONTACT = PRESERVED_NO_EXTERNAL_CONTACT
DEFERRED_MULTIPLE_ASSIGNED_CONTACTS = PRESERVED_MULTIPLE_CONTACT_OWNERS


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
class PreservedSecondaryRegion:
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


# Keep the old type name available while the research API is being iterated.
DeferredSecondaryRegion = PreservedSecondaryRegion


@dataclass(frozen=True)
class SingleCandidateReassignmentResult:
    stage_02: BoundaryContactResearchResult
    proposed_owner_indices: np.ndarray
    proposals: Tuple[SingleCandidateProposal, ...]
    deferred_regions: Tuple[PreservedSecondaryRegion, ...]
    changed_vertex_ids: Tuple[int, ...]
    elapsed_seconds: float

    @property
    def proposal_count(self) -> int:
        return len(self.proposals)

    @property
    def preserved_regions(self) -> Tuple[PreservedSecondaryRegion, ...]:
        return self.deferred_regions

    @property
    def preserved_region_count(self) -> int:
        return len(self.deferred_regions)

    @property
    def deferred_region_count(self) -> int:
        """Backward-compatible alias for preserved_region_count."""

        return self.preserved_region_count

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

    if np.any(owners < 0):
        raise RuntimeError(
            "Stage 03 requires a complete owner map before reassignment proposals."
        )

    proposals = []
    preserved = []

    for region in stage_02.secondary_regions:
        contacts = region.owner_contacts

        if region.unassigned_edge_count:
            raise RuntimeError(
                "Stage 02 exposed an unassigned boundary after exact-tie resolution. "
                "Stage 01 ownership invariants are broken."
            )

        if not contacts:
            preserved.append(
                _preserved_region(
                    region=region,
                    reason=PRESERVED_NO_EXTERNAL_CONTACT,
                )
            )
            continue

        if len(contacts) != 1:
            preserved.append(
                _preserved_region(
                    region=region,
                    reason=PRESERVED_MULTIPLE_CONTACT_OWNERS,
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
        deferred_regions=tuple(preserved),
        changed_vertex_ids=changed_vertex_ids,
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _preserved_region(region, reason: str) -> PreservedSecondaryRegion:
    return PreservedSecondaryRegion(
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
