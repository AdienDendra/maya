"""Stage 04: recompute ownership connectivity after Stage 03 proposals.

This stage is diagnostic only. It receives the complete Stage 03 proposed owner map,
rebuilds connected components for every influence, identifies primary/secondary
regions from the new ownership, and measures how Stage 03 changed fragmentation.

No owner is changed and no Maya skin weight is written here.
"""

from dataclasses import dataclass
import time
from typing import Dict, Tuple

import numpy as np

from ad_skin_tools.region_research.nearest_regions import (
    ConnectedOwnerRegion,
    InfluenceRegionSummary,
)
from ad_skin_tools.region_research.single_candidate_reassignment import (
    SingleCandidateReassignmentResult,
)


@dataclass(frozen=True)
class ProposalConnectivityDiagnostic:
    source_influence_index: int
    source_joint: str
    source_region_index: int
    target_influence_index: int
    target_joint: str
    vertex_ids: Tuple[int, ...]
    resulting_target_region_index: int
    resulting_target_region_vertex_count: int
    target_region_is_primary: bool
    contains_stage_01_target_primary: bool

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class PostReassignmentConnectivityResult:
    stage_03: SingleCandidateReassignmentResult
    owner_indices: np.ndarray
    influence_summaries: Tuple[InfluenceRegionSummary, ...]
    owner_vertex_ids: Dict[str, Tuple[int, ...]]
    all_secondary_vertex_ids: Tuple[int, ...]
    eliminated_stage_01_secondary_vertex_ids: Tuple[int, ...]
    residual_stage_01_secondary_vertex_ids: Tuple[int, ...]
    newly_secondary_vertex_ids: Tuple[int, ...]
    proposal_diagnostics: Tuple[ProposalConnectivityDiagnostic, ...]
    elapsed_seconds: float

    @property
    def context(self):
        return self.stage_03.stage_02.stage_01.context

    @property
    def stage_01(self):
        return self.stage_03.stage_02.stage_01

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
    def secondary_vertex_count(self) -> int:
        return len(self.all_secondary_vertex_ids)

    @property
    def eliminated_stage_01_secondary_vertex_count(self) -> int:
        return len(self.eliminated_stage_01_secondary_vertex_ids)

    @property
    def residual_stage_01_secondary_vertex_count(self) -> int:
        return len(self.residual_stage_01_secondary_vertex_ids)

    @property
    def newly_secondary_vertex_count(self) -> int:
        return len(self.newly_secondary_vertex_ids)

    @property
    def proposal_target_primary_count(self) -> int:
        return sum(
            diagnostic.target_region_is_primary
            for diagnostic in self.proposal_diagnostics
        )

    @property
    def proposal_target_secondary_count(self) -> int:
        return len(self.proposal_diagnostics) - self.proposal_target_primary_count

    @property
    def ambiguous_primary_influence_count(self) -> int:
        return sum(
            bool(summary.regions) and not summary.primary_is_unambiguous
            for summary in self.influence_summaries
        )


def analyze_post_reassignment_connectivity(
    stage_03: SingleCandidateReassignmentResult,
) -> PostReassignmentConnectivityResult:
    """Rebuild connected regions from Stage 03 without changing ownership."""

    started = time.perf_counter()
    stage_01 = stage_03.stage_02.stage_01
    context = stage_01.context

    owners = np.asarray(
        stage_03.proposed_owner_indices,
        dtype=np.int32,
    ).copy()
    if owners.shape != (context.vertex_count,):
        raise RuntimeError(
            "Stage 03 proposed owner map shape does not match the mesh vertex count."
        )
    if np.any(owners < 0):
        bad_ids = np.where(owners < 0)[0].astype(np.int32).tolist()
        raise RuntimeError(
            "Stage 04 received unassigned owners. First vertex IDs: {}".format(
                bad_ids[:20]
            )
        )
    if np.any(owners >= context.influence_count):
        bad_ids = np.where(owners >= context.influence_count)[0].astype(np.int32)
        raise RuntimeError(
            "Stage 04 received out-of-range owner indices. First vertex IDs: {}"
            .format(bad_ids[:20].tolist())
        )

    summaries = _build_post_reassignment_summaries(
        context=context,
        owner_indices=owners,
    )
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

    stage_01_secondary = set(stage_01.all_secondary_vertex_ids)
    post_secondary = set(all_secondary)

    eliminated = tuple(sorted(stage_01_secondary - post_secondary))
    residual = tuple(sorted(stage_01_secondary & post_secondary))
    newly_secondary = tuple(sorted(post_secondary - stage_01_secondary))

    diagnostics = _build_proposal_diagnostics(
        stage_03=stage_03,
        summaries=summaries,
        owner_indices=owners,
    )

    return PostReassignmentConnectivityResult(
        stage_03=stage_03,
        owner_indices=owners,
        influence_summaries=summaries,
        owner_vertex_ids=owner_vertex_ids,
        all_secondary_vertex_ids=all_secondary,
        eliminated_stage_01_secondary_vertex_ids=eliminated,
        residual_stage_01_secondary_vertex_ids=residual,
        newly_secondary_vertex_ids=newly_secondary,
        proposal_diagnostics=diagnostics,
        elapsed_seconds=float(time.perf_counter() - started),
    )


def _build_post_reassignment_summaries(
    context,
    owner_indices: np.ndarray,
) -> Tuple[InfluenceRegionSummary, ...]:
    """Build owner components using distance to each current owner joint."""

    owners = np.asarray(owner_indices, dtype=np.int32)
    vertex_ids = np.arange(context.vertex_count, dtype=np.int32)
    owner_counts = np.bincount(
        owners,
        minlength=context.influence_count,
    ).astype(np.int32)
    owner_order = vertex_ids[np.argsort(owners, kind="stable")]
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

        joint_position = context.influence_positions[int(influence_index)]
        minima = tuple(
            _minimum_squared_distance_to_joint(
                context.vertex_positions,
                component,
                joint_position,
            )
            for component in components
        )
        exact_primary_minimum = min(minima)
        primary_indices = tuple(
            region_index
            for region_index, minimum in enumerate(minima)
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


def _minimum_squared_distance_to_joint(
    vertex_positions: np.ndarray,
    component: Tuple[int, ...],
    joint_position: np.ndarray,
) -> float:
    ids = np.asarray(component, dtype=np.int32)
    delta = vertex_positions[ids] - joint_position[np.newaxis, :]
    squared = np.einsum("vi,vi->v", delta, delta)
    minimum = float(np.min(squared))
    if not np.isfinite(minimum):
        raise RuntimeError("Post-reassignment primary distance is not finite.")
    return minimum


def _build_proposal_diagnostics(
    stage_03,
    summaries,
    owner_indices,
) -> Tuple[ProposalConnectivityDiagnostic, ...]:
    stage_01 = stage_03.stage_02.stage_01
    region_index_by_vertex = np.full(
        stage_01.vertex_count,
        -1,
        dtype=np.int32,
    )

    for summary in summaries:
        for region in summary.regions:
            ids = np.asarray(region.vertex_ids, dtype=np.int32)
            region_index_by_vertex[ids] = int(region.region_index)

    diagnostics = []
    for proposal in stage_03.proposals:
        ids = np.asarray(proposal.vertex_ids, dtype=np.int32)
        if ids.size == 0:
            raise RuntimeError("Stage 03 proposal unexpectedly contains no vertices.")

        target_index = int(proposal.target_influence_index)
        if np.any(owner_indices[ids] != target_index):
            bad_ids = ids[owner_indices[ids] != target_index]
            raise RuntimeError(
                "Stage 03 proposal vertices do not all retain the proposed target. "
                "First vertex IDs: {}".format(bad_ids[:20].tolist())
            )

        resulting_indices = np.unique(region_index_by_vertex[ids])
        if resulting_indices.size != 1 or int(resulting_indices[0]) < 0:
            raise RuntimeError(
                "One connected Stage 03 proposal maps to multiple post-reassignment "
                "target regions: {}".format(resulting_indices.tolist())
            )

        resulting_index = int(resulting_indices[0])
        target_summary = summaries[target_index]
        target_region = target_summary.regions[resulting_index]

        stage_01_target_summary = stage_01.influence_summaries[target_index]
        stage_01_primary_ids = set(stage_01_target_summary.primary_vertex_ids)
        contains_stage_01_primary = any(
            vertex_id in stage_01_primary_ids
            for vertex_id in target_region.vertex_ids
        )

        diagnostics.append(
            ProposalConnectivityDiagnostic(
                source_influence_index=int(proposal.source_influence_index),
                source_joint=proposal.source_joint,
                source_region_index=int(proposal.source_region_index),
                target_influence_index=target_index,
                target_joint=proposal.target_joint,
                vertex_ids=proposal.vertex_ids,
                resulting_target_region_index=resulting_index,
                resulting_target_region_vertex_count=target_region.vertex_count,
                target_region_is_primary=bool(target_region.is_primary),
                contains_stage_01_target_primary=bool(
                    contains_stage_01_primary
                ),
            )
        )

    return tuple(diagnostics)


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
