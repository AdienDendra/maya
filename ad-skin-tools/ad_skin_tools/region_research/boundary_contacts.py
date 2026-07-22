"""Boundary-contact diagnostics for disconnected nearest-owner regions.

This stage does not reassign ownership. It only measures which other owners touch
secondary regions through direct mesh edges, preserving the stage-one result.
"""

from dataclasses import dataclass
import time
from typing import Dict, Tuple

import numpy as np

from ad_skin_tools.region_research.nearest_regions import (
    NearestRegionResearchResult,
)


@dataclass(frozen=True)
class BoundaryOwnerContact:
    influence_index: int
    joint: str
    edge_count: int
    source_boundary_vertex_ids: Tuple[int, ...]
    neighbour_vertex_ids: Tuple[int, ...]

    @property
    def source_boundary_vertex_count(self) -> int:
        return len(self.source_boundary_vertex_ids)

    @property
    def neighbour_vertex_count(self) -> int:
        return len(self.neighbour_vertex_ids)


@dataclass(frozen=True)
class SecondaryRegionBoundary:
    influence_index: int
    joint: str
    region_index: int
    region_vertex_ids: Tuple[int, ...]
    boundary_vertex_ids: Tuple[int, ...]
    owner_contacts: Tuple[BoundaryOwnerContact, ...]
    dominant_contact_influence_indices: Tuple[int, ...]
    unassigned_edge_count: int
    unassigned_source_vertex_ids: Tuple[int, ...]
    unassigned_neighbour_vertex_ids: Tuple[int, ...]

    @property
    def region_vertex_count(self) -> int:
        return len(self.region_vertex_ids)

    @property
    def boundary_vertex_count(self) -> int:
        return len(self.boundary_vertex_ids)

    @property
    def contact_owner_count(self) -> int:
        return len(self.owner_contacts)

    @property
    def has_unique_dominant_contact(self) -> bool:
        return len(self.dominant_contact_influence_indices) == 1

    @property
    def has_no_external_contact(self) -> bool:
        return not self.owner_contacts and self.unassigned_edge_count == 0


@dataclass(frozen=True)
class BoundaryContactResearchResult:
    stage_01: NearestRegionResearchResult
    secondary_regions: Tuple[SecondaryRegionBoundary, ...]
    elapsed_seconds: float

    @property
    def secondary_region_count(self) -> int:
        return len(self.secondary_regions)

    @property
    def no_external_contact_region_count(self) -> int:
        return sum(
            region.has_no_external_contact
            for region in self.secondary_regions
        )

    @property
    def multiple_contact_owner_region_count(self) -> int:
        return sum(
            region.contact_owner_count > 1
            for region in self.secondary_regions
        )

    @property
    def unique_dominant_contact_region_count(self) -> int:
        return sum(
            region.has_unique_dominant_contact
            for region in self.secondary_regions
        )


def analyze_secondary_region_boundaries(
    stage_01: NearestRegionResearchResult,
) -> BoundaryContactResearchResult:
    """Measure direct topology contacts for every secondary owner region."""

    started = time.perf_counter()
    owners = np.asarray(stage_01.nearest.owner_indices, dtype=np.int32)
    adjacency = stage_01.context.adjacency
    influences = stage_01.context.influences
    secondary_results = []

    for influence_summary in stage_01.influence_summaries:
        source_influence_index = int(influence_summary.influence_index)

        for region_index in influence_summary.secondary_region_indices:
            region = influence_summary.regions[int(region_index)]
            secondary_results.append(
                _analyze_one_region(
                    owners=owners,
                    adjacency=adjacency,
                    influences=influences,
                    source_influence_index=source_influence_index,
                    source_joint=influence_summary.joint,
                    region_index=int(region.region_index),
                    region_vertex_ids=region.vertex_ids,
                )
            )

    secondary_results.sort(
        key=lambda value: (
            value.influence_index,
            value.region_index,
        )
    )
    return BoundaryContactResearchResult(
        stage_01=stage_01,
        secondary_regions=tuple(secondary_results),
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _analyze_one_region(
    owners: np.ndarray,
    adjacency: Tuple[Tuple[int, ...], ...],
    influences: Tuple[str, ...],
    source_influence_index: int,
    source_joint: str,
    region_index: int,
    region_vertex_ids: Tuple[int, ...],
) -> SecondaryRegionBoundary:
    region_set = set(int(value) for value in region_vertex_ids)
    boundary_vertices = set()
    edge_counts: Dict[int, int] = {}
    source_vertices_by_owner: Dict[int, set] = {}
    neighbour_vertices_by_owner: Dict[int, set] = {}
    unassigned_edge_count = 0
    unassigned_source_vertices = set()
    unassigned_neighbour_vertices = set()

    for vertex_id in region_vertex_ids:
        source_vertex_id = int(vertex_id)
        for neighbour_id in adjacency[source_vertex_id]:
            neighbour_id = int(neighbour_id)
            if neighbour_id in region_set:
                continue

            boundary_vertices.add(source_vertex_id)
            neighbour_owner = int(owners[neighbour_id])

            if neighbour_owner < 0:
                unassigned_edge_count += 1
                unassigned_source_vertices.add(source_vertex_id)
                unassigned_neighbour_vertices.add(neighbour_id)
                continue

            if neighbour_owner == source_influence_index:
                raise RuntimeError(
                    "Secondary region connectivity is inconsistent: owner {} "
                    "continues across boundary edge {}-{}.".format(
                        source_joint,
                        source_vertex_id,
                        neighbour_id,
                    )
                )

            edge_counts[neighbour_owner] = edge_counts.get(neighbour_owner, 0) + 1
            source_vertices_by_owner.setdefault(neighbour_owner, set()).add(
                source_vertex_id
            )
            neighbour_vertices_by_owner.setdefault(neighbour_owner, set()).add(
                neighbour_id
            )

    contacts = tuple(
        BoundaryOwnerContact(
            influence_index=int(owner_index),
            joint=influences[int(owner_index)],
            edge_count=int(edge_counts[owner_index]),
            source_boundary_vertex_ids=tuple(
                sorted(source_vertices_by_owner[owner_index])
            ),
            neighbour_vertex_ids=tuple(
                sorted(neighbour_vertices_by_owner[owner_index])
            ),
        )
        for owner_index in sorted(
            edge_counts,
            key=lambda value: (-edge_counts[value], value),
        )
    )

    if contacts:
        maximum_edge_count = max(contact.edge_count for contact in contacts)
        dominant_indices = tuple(
            contact.influence_index
            for contact in contacts
            if contact.edge_count == maximum_edge_count
        )
    else:
        dominant_indices = tuple()

    return SecondaryRegionBoundary(
        influence_index=int(source_influence_index),
        joint=source_joint,
        region_index=int(region_index),
        region_vertex_ids=tuple(int(value) for value in region_vertex_ids),
        boundary_vertex_ids=tuple(sorted(boundary_vertices)),
        owner_contacts=contacts,
        dominant_contact_influence_indices=dominant_indices,
        unassigned_edge_count=int(unassigned_edge_count),
        unassigned_source_vertex_ids=tuple(sorted(unassigned_source_vertices)),
        unassigned_neighbour_vertex_ids=tuple(sorted(unassigned_neighbour_vertices)),
    )
