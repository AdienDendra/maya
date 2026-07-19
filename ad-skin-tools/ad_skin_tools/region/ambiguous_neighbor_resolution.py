"""Resolve post-loop ambiguous Region islands through mesh-boundary neighbours.

This experimental layer starts from v3.10F. Detached regions have already been
advanced by the existing distance/facing policy. Any remaining non-anchor
AMBIGUOUS connected region is inspected through mesh adjacency:

- exactly one neighbouring owner across the region boundary -> assign the whole
  ambiguous island to that neighbouring owner;
- zero or multiple neighbouring owners -> preserve the current owner and report
  the region, but do not block Bind Skin.

No joint naming, hierarchy, body-part rule, angle threshold, or region-size rule
is used.
"""

from dataclasses import dataclass, replace
from typing import Tuple

import numpy as np

from ad_skin_tools.region.closed_loop_facing_resolution import (
    ClosedLoopFacingResolutionResult,
)
from ad_skin_tools.region.connectivity import (
    build_vertex_adjacency,
    partition_influence_ownership,
)
from ad_skin_tools.region.facing import (
    AMBIGUOUS,
    build_facing_mesh_context,
    classify_region_facing,
)
from ad_skin_tools.region.solver import RegionOwnershipResult


@dataclass(frozen=True)
class AmbiguousNeighbourAssignment:
    source_owner_index: int
    target_owner_index: int
    source_region_index: int
    vertex_ids: Tuple[int, ...]
    boundary_edge_count: int

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class PreservedAmbiguousRegion:
    source_owner_index: int
    source_region_index: int
    vertex_ids: Tuple[int, ...]
    neighbouring_owner_indices: Tuple[int, ...]

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class AmbiguousNeighbourResolutionResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    initial_owner_indices: np.ndarray
    final_owner_indices: np.ndarray
    assignments: Tuple[AmbiguousNeighbourAssignment, ...]
    preserved_regions: Tuple[PreservedAmbiguousRegion, ...]
    final_detached_vertex_ids: Tuple[int, ...]
    final_ambiguous_vertex_ids: Tuple[int, ...]

    @property
    def assigned_vertex_ids(self) -> Tuple[int, ...]:
        return tuple(
            sorted(
                vertex_id
                for assignment in self.assignments
                for vertex_id in assignment.vertex_ids
            )
        )

    @property
    def assigned_vertex_count(self) -> int:
        return len(self.assigned_vertex_ids)

    @property
    def assignment_count(self) -> int:
        return len(self.assignments)

    @property
    def preserved_region_count(self) -> int:
        return len(self.preserved_regions)


def resolve_ambiguous_regions_to_boundary_neighbour(
    region_result: RegionOwnershipResult,
    facing_result: ClosedLoopFacingResolutionResult,
) -> AmbiguousNeighbourResolutionResult:
    """Assign each uniquely enclosed ambiguous island to its boundary owner."""

    _validate_inputs(region_result, facing_result)

    owners = np.asarray(
        facing_result.final_owner_indices,
        dtype=np.int32,
    ).copy()
    initial_owners = owners.copy()
    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    facing_context = build_facing_mesh_context(region_result.mesh_shape)
    iteration_result = _distance_result_with_owners(
        region_result.distance_result,
        owners,
    )

    assignments = []
    preserved_regions = []

    for source_index in range(region_result.influence_count):
        connectivity = partition_influence_ownership(
            iteration_result,
            owners,
            source_index,
            adjacency,
        )
        facing = classify_region_facing(
            iteration_result,
            connectivity,
            facing_context,
        )

        for diagnostic in facing.diagnostics:
            if diagnostic.classification != AMBIGUOUS:
                continue

            region_vertices = tuple(
                int(value) for value in diagnostic.vertex_ids
            )
            neighbour_counts = _boundary_neighbour_owner_counts(
                region_vertices=region_vertices,
                source_owner_index=int(source_index),
                owner_indices=owners,
                adjacency=adjacency,
            )
            neighbouring_owners = tuple(sorted(neighbour_counts))

            if len(neighbouring_owners) == 1:
                target_owner = int(neighbouring_owners[0])
                assignments.append(
                    AmbiguousNeighbourAssignment(
                        source_owner_index=int(source_index),
                        target_owner_index=target_owner,
                        source_region_index=int(diagnostic.region_index),
                        vertex_ids=region_vertices,
                        boundary_edge_count=int(neighbour_counts[target_owner]),
                    )
                )
            else:
                preserved_regions.append(
                    PreservedAmbiguousRegion(
                        source_owner_index=int(source_index),
                        source_region_index=int(diagnostic.region_index),
                        vertex_ids=region_vertices,
                        neighbouring_owner_indices=neighbouring_owners,
                    )
                )

    for assignment in assignments:
        owners[
            np.asarray(assignment.vertex_ids, dtype=np.int32)
        ] = int(assignment.target_owner_index)

    final_detached, final_ambiguous = _validate_final_owner_map(
        region_result=region_result,
        owner_indices=owners,
        adjacency=adjacency,
        facing_context=facing_context,
    )

    return AmbiguousNeighbourResolutionResult(
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        initial_owner_indices=initial_owners,
        final_owner_indices=owners,
        assignments=tuple(assignments),
        preserved_regions=tuple(preserved_regions),
        final_detached_vertex_ids=final_detached,
        final_ambiguous_vertex_ids=final_ambiguous,
    )


def _boundary_neighbour_owner_counts(
    region_vertices,
    source_owner_index,
    owner_indices,
    adjacency,
):
    region_set = set(int(value) for value in region_vertices)
    counts = {}

    for vertex_id in region_vertices:
        for neighbour_id in adjacency[int(vertex_id)]:
            neighbour_id = int(neighbour_id)
            if neighbour_id in region_set:
                continue

            neighbour_owner = int(owner_indices[neighbour_id])
            if neighbour_owner == int(source_owner_index):
                continue

            counts[neighbour_owner] = counts.get(neighbour_owner, 0) + 1

    return counts


def _validate_final_owner_map(
    region_result,
    owner_indices,
    adjacency,
    facing_context,
):
    iteration_result = _distance_result_with_owners(
        region_result.distance_result,
        owner_indices,
    )
    detached = set()
    ambiguous = set()

    for source_index in range(region_result.influence_count):
        connectivity = partition_influence_ownership(
            iteration_result,
            owner_indices,
            source_index,
            adjacency,
        )
        facing = classify_region_facing(
            iteration_result,
            connectivity,
            facing_context,
        )
        detached.update(int(value) for value in facing.detached_vertex_ids)
        ambiguous.update(int(value) for value in facing.ambiguous_vertex_ids)

    return (
        tuple(sorted(detached)),
        tuple(sorted(ambiguous)),
    )


def _distance_result_with_owners(result, owners):
    counts = {
        joint: int(np.count_nonzero(owners == influence_index))
        for influence_index, joint in enumerate(result.influences)
    }
    return replace(
        result,
        nearest_influence_indices=np.asarray(owners, dtype=np.int32).copy(),
        unique_assignment_counts=counts,
    )


def _validate_inputs(region_result, facing_result):
    if facing_result.mesh_shape != region_result.mesh_shape:
        raise RuntimeError("Region and facing results use different meshes.")
    if facing_result.mesh_transform != region_result.mesh_transform:
        raise RuntimeError("Region and facing results use different transforms.")
    if facing_result.influences != region_result.influences:
        raise RuntimeError("Region and facing results use different influences.")

    owners = np.asarray(facing_result.final_owner_indices)
    if owners.shape != (region_result.vertex_count,):
        raise ValueError("Facing owner map must contain one owner per vertex.")
