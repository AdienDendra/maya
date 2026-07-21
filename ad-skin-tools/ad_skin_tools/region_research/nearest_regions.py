"""Stage 01: exact nearest-owner regions with visual diagnostics.

The stage deliberately stops before facing, fallback, loop, or smoothing logic.
Its only questions are:

1. Which joint pivot is the unique exact nearest owner of each vertex?
2. For each owner, how many connected topology regions were produced?
3. Which connected region contains that owner's exact closest owned vertex?

Exact-distance ties remain unassigned and are exposed for visual selection.
"""

from dataclasses import dataclass
import time
from typing import Dict, Sequence, Tuple

import numpy as np

from ad_skin_tools.region_research.context import (
    ResearchMeshContext,
    build_research_mesh_context,
)


DEFAULT_DISTANCE_CHUNK_SIZE = 16384
UNASSIGNED_OWNER = -1


@dataclass(frozen=True)
class ExactNearestResult:
    owner_indices: np.ndarray
    minimum_squared_distances: np.ndarray
    exact_tie_counts: np.ndarray
    exact_tie_vertex_ids: Tuple[int, ...]
    elapsed_seconds: float

    @property
    def exact_tie_vertex_count(self) -> int:
        return len(self.exact_tie_vertex_ids)


@dataclass(frozen=True)
class ConnectedOwnerRegion:
    influence_index: int
    joint: str
    region_index: int
    vertex_ids: Tuple[int, ...]
    minimum_squared_distance: float
    is_primary: bool

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class InfluenceRegionSummary:
    influence_index: int
    joint: str
    raw_vertex_ids: Tuple[int, ...]
    regions: Tuple[ConnectedOwnerRegion, ...]
    primary_region_indices: Tuple[int, ...]
    secondary_region_indices: Tuple[int, ...]

    @property
    def raw_vertex_count(self) -> int:
        return len(self.raw_vertex_ids)

    @property
    def region_count(self) -> int:
        return len(self.regions)

    @property
    def primary_is_unambiguous(self) -> bool:
        return len(self.primary_region_indices) == 1

    @property
    def primary_vertex_ids(self) -> Tuple[int, ...]:
        selected = set(self.primary_region_indices)
        return tuple(
            vertex_id
            for region in self.regions
            if region.region_index in selected
            for vertex_id in region.vertex_ids
        )

    @property
    def secondary_vertex_ids(self) -> Tuple[int, ...]:
        selected = set(self.secondary_region_indices)
        return tuple(
            vertex_id
            for region in self.regions
            if region.region_index in selected
            for vertex_id in region.vertex_ids
        )


@dataclass(frozen=True)
class NearestRegionResearchResult:
    context: ResearchMeshContext
    nearest: ExactNearestResult
    influence_summaries: Tuple[InfluenceRegionSummary, ...]
    owner_vertex_ids: Dict[str, Tuple[int, ...]]
    all_secondary_vertex_ids: Tuple[int, ...]
    connectivity_seconds: float
    elapsed_seconds: float

    @property
    def vertex_count(self) -> int:
        return self.context.vertex_count

    @property
    def influence_count(self) -> int:
        return self.context.influence_count

    @property
    def total_region_count(self) -> int:
        return sum(summary.region_count for summary in self.influence_summaries)

    @property
    def secondary_region_count(self) -> int:
        return sum(
            len(summary.secondary_region_indices)
            for summary in self.influence_summaries
        )

    @property
    def ambiguous_primary_influence_count(self) -> int:
        return sum(
            bool(summary.regions) and not summary.primary_is_unambiguous
            for summary in self.influence_summaries
        )


def solve_nearest_regions(
    mesh: str,
    joints: Sequence[str],
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> NearestRegionResearchResult:
    """Run only exact nearest distance and connected-owner region discovery."""

    started = time.perf_counter()
    if int(distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")

    context = build_research_mesh_context(mesh=mesh, joints=joints)
    nearest = solve_exact_nearest(
        context,
        distance_chunk_size=int(distance_chunk_size),
    )

    connectivity_started = time.perf_counter()
    summaries = _build_influence_region_summaries(context, nearest)
    connectivity_seconds = time.perf_counter() - connectivity_started

    owner_vertex_ids = {
        summary.joint: summary.raw_vertex_ids
        for summary in summaries
    }
    all_secondary = tuple(
        sorted(
            vertex_id
            for summary in summaries
            for vertex_id in summary.secondary_vertex_ids
        )
    )

    return NearestRegionResearchResult(
        context=context,
        nearest=nearest,
        influence_summaries=summaries,
        owner_vertex_ids=owner_vertex_ids,
        all_secondary_vertex_ids=all_secondary,
        connectivity_seconds=float(connectivity_seconds),
        elapsed_seconds=float(time.perf_counter() - started),
    )


def solve_exact_nearest(
    context: ResearchMeshContext,
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> ExactNearestResult:
    """Find unique exact nearest owners without building a sorted distance table."""

    started = time.perf_counter()
    vertex_count = context.vertex_count
    owner_indices = np.full(vertex_count, UNASSIGNED_OWNER, dtype=np.int32)
    minimum_squared = np.full(vertex_count, np.inf, dtype=np.float64)
    tie_counts = np.zeros(vertex_count, dtype=np.int32)

    for start in range(0, vertex_count, int(distance_chunk_size)):
        stop = min(start + int(distance_chunk_size), vertex_count)
        squared = _squared_distance_block(
            context.vertex_positions[start:stop],
            context.influence_positions,
        )
        chunk_minimum = np.min(squared, axis=1)
        exact_mask = squared == chunk_minimum[:, np.newaxis]
        chunk_tie_counts = np.count_nonzero(exact_mask, axis=1).astype(np.int32)
        unique_mask = chunk_tie_counts == 1
        chunk_argmin = np.argmin(squared, axis=1).astype(np.int32)

        minimum_squared[start:stop] = chunk_minimum
        tie_counts[start:stop] = chunk_tie_counts
        owner_indices[start:stop][unique_mask] = chunk_argmin[unique_mask]

    if np.any(~np.isfinite(minimum_squared)):
        raise RuntimeError("Exact nearest distance produced non-finite values.")
    if np.any(tie_counts < 1):
        raise RuntimeError("One or more vertices have no distance candidate.")

    exact_tie_vertex_ids = tuple(
        int(value)
        for value in np.where(tie_counts > 1)[0].astype(np.int32).tolist()
    )
    return ExactNearestResult(
        owner_indices=owner_indices,
        minimum_squared_distances=minimum_squared,
        exact_tie_counts=tie_counts,
        exact_tie_vertex_ids=exact_tie_vertex_ids,
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _build_influence_region_summaries(
    context: ResearchMeshContext,
    nearest: ExactNearestResult,
) -> Tuple[InfluenceRegionSummary, ...]:
    owners = np.asarray(nearest.owner_indices, dtype=np.int32)
    influence_count = context.influence_count

    valid_vertex_ids = np.where(owners >= 0)[0].astype(np.int32)
    valid_owners = owners[valid_vertex_ids]
    owner_counts = np.bincount(valid_owners, minlength=influence_count).astype(np.int32)
    owner_order = valid_vertex_ids[
        np.argsort(valid_owners, kind="stable")
    ]
    owner_offsets = np.concatenate(
        (
            np.asarray([0], dtype=np.int64),
            np.cumsum(owner_counts, dtype=np.int64),
        )
    )

    summaries = []
    for influence_index, joint in enumerate(context.influences):
        start = int(owner_offsets[influence_index])
        stop = int(owner_offsets[influence_index + 1])
        raw_ids = tuple(int(value) for value in owner_order[start:stop].tolist())
        components = _connected_components(raw_ids, context.adjacency)

        if not components:
            summaries.append(
                InfluenceRegionSummary(
                    influence_index=int(influence_index),
                    joint=joint,
                    raw_vertex_ids=tuple(),
                    regions=tuple(),
                    primary_region_indices=tuple(),
                    secondary_region_indices=tuple(),
                )
            )
            continue

        minima = tuple(
            float(
                np.min(
                    nearest.minimum_squared_distances[
                        np.asarray(component, dtype=np.int32)
                    ]
                )
            )
            for component in components
        )
        exact_primary_minimum = min(minima)
        primary_indices = tuple(
            index
            for index, minimum in enumerate(minima)
            if float(minimum) == float(exact_primary_minimum)
        )
        primary_set = set(primary_indices)

        regions = tuple(
            ConnectedOwnerRegion(
                influence_index=int(influence_index),
                joint=joint,
                region_index=int(region_index),
                vertex_ids=component,
                minimum_squared_distance=float(minima[region_index]),
                is_primary=region_index in primary_set,
            )
            for region_index, component in enumerate(components)
        )
        secondary_indices = tuple(
            region_index
            for region_index in range(len(regions))
            if region_index not in primary_set
        )
        summaries.append(
            InfluenceRegionSummary(
                influence_index=int(influence_index),
                joint=joint,
                raw_vertex_ids=raw_ids,
                regions=regions,
                primary_region_indices=primary_indices,
                secondary_region_indices=secondary_indices,
            )
        )

    return tuple(summaries)


def _connected_components(
    raw_vertex_ids: Tuple[int, ...],
    adjacency: Tuple[Tuple[int, ...], ...],
) -> Tuple[Tuple[int, ...], ...]:
    unseen = set(int(value) for value in raw_vertex_ids)
    components = []

    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        stack = [seed]
        component = []

        while stack:
            vertex_id = stack.pop()
            component.append(vertex_id)
            for neighbour_id in adjacency[vertex_id]:
                if neighbour_id in unseen:
                    unseen.remove(neighbour_id)
                    stack.append(neighbour_id)

        components.append(tuple(sorted(component)))

    components.sort(key=lambda values: values[0])
    return tuple(components)


def _squared_distance_block(vertex_positions, influence_positions):
    delta = (
        vertex_positions[:, np.newaxis, :]
        - influence_positions[np.newaxis, :, :]
    )
    return np.einsum("vji,vji->vj", delta, delta)
