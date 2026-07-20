"""Topology completion for exact closest-distance ties.

Distance ranking deliberately leaves a vertex unresolved when several joint
pivots have the exact same minimum squared distance.  This module turns those
candidate sets into a complete initial one-owner map before Region connectivity
and facing are evaluated.

Resolution is deterministic and does not inspect joint names, hierarchy, or UI
selection order.  Exact-tie vertices are partitioned into connected components
that share the same exact-minimum candidate set.  Components are resolved from
already-owned neighbouring vertices first, then by boundary edge geometry.  A
territory-centroid continuation and a position-only canonical fallback exist so
a valid geometric ambiguity does not abort the bind; both are reported.
"""

from dataclasses import dataclass
from typing import Tuple

import numpy as np

from ad_skin_tools.region.distance_ranking import (
    ExactDistanceRankingResult,
    ExactDistanceTables,
)


RESOLVED_NEIGHBOUR_SUPPORT = "resolved_neighbour_support"
RESOLVED_NEIGHBOUR_EDGE_LENGTH = "resolved_neighbour_edge_length"
RESOLVED_TERRITORY_CENTROID = "resolved_territory_centroid"
RESOLVED_SPATIAL_CANONICAL = "resolved_spatial_canonical"


@dataclass(frozen=True)
class ExactTieCandidateDiagnostic:
    influence_index: int
    boundary_edge_count: int
    mean_boundary_squared_edge_length: float
    territory_centroid_squared_distance: float


@dataclass(frozen=True)
class ExactTieComponentDiagnostic:
    vertex_ids: Tuple[int, ...]
    candidate_influence_indices: Tuple[int, ...]
    candidates: Tuple[ExactTieCandidateDiagnostic, ...]
    target_influence_index: int
    classification: str
    resolution_pass: int

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class ExactTieResolutionResult:
    owner_indices: np.ndarray
    candidate_ranks: np.ndarray
    exact_tie_vertex_ids: Tuple[int, ...]
    diagnostics: Tuple[ExactTieComponentDiagnostic, ...]
    resolution_pass_count: int

    @property
    def exact_tie_vertex_count(self) -> int:
        return len(self.exact_tie_vertex_ids)

    @property
    def component_count(self) -> int:
        return len(self.diagnostics)

    @property
    def neighbour_support_component_count(self) -> int:
        return sum(
            diagnostic.classification == RESOLVED_NEIGHBOUR_SUPPORT
            for diagnostic in self.diagnostics
        )

    @property
    def neighbour_edge_length_component_count(self) -> int:
        return sum(
            diagnostic.classification == RESOLVED_NEIGHBOUR_EDGE_LENGTH
            for diagnostic in self.diagnostics
        )

    @property
    def territory_centroid_component_count(self) -> int:
        return sum(
            diagnostic.classification == RESOLVED_TERRITORY_CENTROID
            for diagnostic in self.diagnostics
        )

    @property
    def spatial_canonical_component_count(self) -> int:
        return sum(
            diagnostic.classification == RESOLVED_SPATIAL_CANONICAL
            for diagnostic in self.diagnostics
        )


def resolve_exact_distance_ties(
    distance_result: ExactDistanceRankingResult,
    distance_tables: ExactDistanceTables,
    adjacency: Tuple[Tuple[int, ...], ...],
) -> ExactTieResolutionResult:
    """Return a complete initial owner map from exact distance candidates."""

    _validate_inputs(distance_result, distance_tables, adjacency)

    owners = np.asarray(
        distance_result.nearest_influence_indices,
        dtype=np.int32,
    ).copy()
    candidate_ranks = np.zeros(distance_result.vertex_count, dtype=np.int32)

    if not distance_result.exact_tie_vertex_ids:
        return ExactTieResolutionResult(
            owner_indices=owners,
            candidate_ranks=candidate_ranks,
            exact_tie_vertex_ids=tuple(),
            diagnostics=tuple(),
            resolution_pass_count=0,
        )

    candidate_sets = _exact_candidate_sets(distance_result, distance_tables)
    components = _candidate_components(
        distance_result.exact_tie_vertex_ids,
        candidate_sets,
        adjacency,
    )

    # A resolved exact minimum occupies the whole first equal-distance group.
    # Keeping the last rank of that group lets the existing detached-region
    # search advance directly to the next strictly greater distance.
    for vertex_id in distance_result.exact_tie_vertex_ids:
        candidate_ranks[int(vertex_id)] = (
            int(distance_result.exact_tie_counts[int(vertex_id)]) - 1
        )

    pending = list(components)
    diagnostics = []
    resolution_pass = 0

    while pending:
        resolution_pass += 1
        proposals = []
        still_pending = []

        for vertex_ids, candidate_indices in pending:
            evidence = _candidate_evidence(
                vertex_ids=vertex_ids,
                candidate_indices=candidate_indices,
                owners=owners,
                adjacency=adjacency,
                vertex_positions=distance_result.vertex_positions,
            )
            decision = _choose_from_boundary(evidence)
            if decision is None:
                still_pending.append((vertex_ids, candidate_indices))
                continue

            target, classification = decision
            proposals.append(
                (
                    vertex_ids,
                    candidate_indices,
                    evidence,
                    int(target),
                    classification,
                )
            )

        if proposals:
            # Synchronous application keeps component order from affecting the
            # neighbouring evidence read during this pass.
            for vertex_ids, _, _, target, _ in proposals:
                owners[np.asarray(vertex_ids, dtype=np.int32)] = int(target)

            for (
                vertex_ids,
                candidate_indices,
                evidence,
                target,
                classification,
            ) in proposals:
                diagnostics.append(
                    _diagnostic(
                        vertex_ids=vertex_ids,
                        candidate_indices=candidate_indices,
                        evidence=evidence,
                        target=target,
                        classification=classification,
                        resolution_pass=resolution_pass,
                    )
                )

            pending = still_pending
            continue

        # No component gained new direct boundary evidence. Resolve the
        # remaining components from the geometric continuation of each already
        # owned candidate territory. This remains independent from names and
        # influence-list order.
        territory_centroids = _territory_centroids(
            owners,
            distance_result.vertex_positions,
            distance_result.influence_count,
        )

        for vertex_ids, candidate_indices in still_pending:
            evidence = _candidate_evidence(
                vertex_ids=vertex_ids,
                candidate_indices=candidate_indices,
                owners=owners,
                adjacency=adjacency,
                vertex_positions=distance_result.vertex_positions,
                territory_centroids=territory_centroids,
            )
            decision = _choose_from_territory(evidence)
            if decision is None:
                target = _spatial_canonical_candidate(
                    candidate_indices,
                    distance_result.influence_positions,
                )
                classification = RESOLVED_SPATIAL_CANONICAL
            else:
                target = int(decision)
                classification = RESOLVED_TERRITORY_CENTROID

            owners[np.asarray(vertex_ids, dtype=np.int32)] = int(target)
            diagnostics.append(
                _diagnostic(
                    vertex_ids=vertex_ids,
                    candidate_indices=candidate_indices,
                    evidence=evidence,
                    target=target,
                    classification=classification,
                    resolution_pass=resolution_pass,
                )
            )

        pending = []

    if np.any(owners < 0):
        bad = np.where(owners < 0)[0][:20]
        raise RuntimeError(
            "Exact-tie resolution left vertices without an owner. First IDs: {}"
            .format(bad.tolist())
        )

    diagnostics.sort(key=lambda item: item.vertex_ids[0])
    return ExactTieResolutionResult(
        owner_indices=owners,
        candidate_ranks=candidate_ranks,
        exact_tie_vertex_ids=tuple(
            int(value) for value in distance_result.exact_tie_vertex_ids
        ),
        diagnostics=tuple(diagnostics),
        resolution_pass_count=int(resolution_pass),
    )


def _validate_inputs(distance_result, distance_tables, adjacency):
    expected = (
        distance_result.vertex_count,
        distance_result.influence_count,
    )
    if distance_tables.influence_indices.shape != expected:
        raise ValueError("Distance influence table shape does not match the result.")
    if distance_tables.squared_distances.shape != expected:
        raise ValueError("Distance value table shape does not match the result.")
    if len(adjacency) != distance_result.vertex_count:
        raise ValueError("Mesh adjacency does not match the distance result.")


def _exact_candidate_sets(distance_result, distance_tables):
    result = {}
    for vertex_id in distance_result.exact_tie_vertex_ids:
        vertex_id = int(vertex_id)
        count = int(distance_result.exact_tie_counts[vertex_id])
        candidates = tuple(
            sorted(
                int(value)
                for value in distance_tables.influence_indices[
                    vertex_id,
                    :count,
                ].tolist()
            )
        )
        if len(candidates) < 2:
            raise RuntimeError(
                "Exact-tie vertex {} has fewer than two candidates.".format(
                    vertex_id
                )
            )
        result[vertex_id] = candidates
    return result


def _candidate_components(vertex_ids, candidate_sets, adjacency):
    unseen = set(int(value) for value in vertex_ids)
    components = []

    while unseen:
        seed = min(unseen)
        candidate_indices = candidate_sets[seed]
        unseen.remove(seed)
        stack = [seed]
        component = []

        while stack:
            vertex_id = stack.pop()
            component.append(vertex_id)
            for neighbour_id in adjacency[vertex_id]:
                neighbour_id = int(neighbour_id)
                if (
                    neighbour_id in unseen
                    and candidate_sets[neighbour_id] == candidate_indices
                ):
                    unseen.remove(neighbour_id)
                    stack.append(neighbour_id)

        components.append(
            (
                tuple(sorted(component)),
                tuple(candidate_indices),
            )
        )

    components.sort(key=lambda item: item[0][0])
    return tuple(components)


def _candidate_evidence(
    vertex_ids,
    candidate_indices,
    owners,
    adjacency,
    vertex_positions,
    territory_centroids=None,
):
    component_set = set(int(value) for value in vertex_ids)
    counts = {int(candidate): 0 for candidate in candidate_indices}
    edge_squared = {int(candidate): [] for candidate in candidate_indices}

    for vertex_id in component_set:
        point = vertex_positions[int(vertex_id)]
        for neighbour_id in adjacency[int(vertex_id)]:
            neighbour_id = int(neighbour_id)
            if neighbour_id in component_set:
                continue
            owner = int(owners[neighbour_id])
            if owner not in counts:
                continue

            delta = vertex_positions[neighbour_id] - point
            counts[owner] += 1
            edge_squared[owner].append(float(np.dot(delta, delta)))

    component_positions = vertex_positions[
        np.asarray(sorted(component_set), dtype=np.int32)
    ]
    component_centroid = np.mean(component_positions, axis=0, dtype=np.float64)

    evidence = []
    for candidate in candidate_indices:
        candidate = int(candidate)
        values = edge_squared[candidate]
        mean_edge = (
            float(np.mean(np.asarray(values, dtype=np.float64)))
            if values
            else float("inf")
        )
        centroid_distance = float("inf")
        if territory_centroids is not None:
            centroid = territory_centroids[candidate]
            if centroid is not None:
                delta = component_centroid - centroid
                centroid_distance = float(np.dot(delta, delta))

        evidence.append(
            ExactTieCandidateDiagnostic(
                influence_index=candidate,
                boundary_edge_count=int(counts[candidate]),
                mean_boundary_squared_edge_length=mean_edge,
                territory_centroid_squared_distance=centroid_distance,
            )
        )

    return tuple(evidence)


def _choose_from_boundary(evidence):
    maximum_count = max(item.boundary_edge_count for item in evidence)
    if maximum_count <= 0:
        return None

    count_winners = [
        item for item in evidence if item.boundary_edge_count == maximum_count
    ]
    if len(count_winners) == 1:
        return (
            int(count_winners[0].influence_index),
            RESOLVED_NEIGHBOUR_SUPPORT,
        )

    minimum_edge = min(
        item.mean_boundary_squared_edge_length for item in count_winners
    )
    edge_winners = [
        item
        for item in count_winners
        if _numerically_equal(
            item.mean_boundary_squared_edge_length,
            minimum_edge,
        )
    ]
    if len(edge_winners) == 1:
        return (
            int(edge_winners[0].influence_index),
            RESOLVED_NEIGHBOUR_EDGE_LENGTH,
        )
    return None


def _territory_centroids(owners, vertex_positions, influence_count):
    centroids = []
    for influence_index in range(int(influence_count)):
        vertex_ids = np.where(owners == influence_index)[0].astype(np.int32)
        if not vertex_ids.size:
            centroids.append(None)
            continue
        centroids.append(
            np.mean(
                vertex_positions[vertex_ids],
                axis=0,
                dtype=np.float64,
            )
        )
    return tuple(centroids)


def _choose_from_territory(evidence):
    finite = [
        item
        for item in evidence
        if np.isfinite(item.territory_centroid_squared_distance)
    ]
    if not finite:
        return None

    minimum = min(item.territory_centroid_squared_distance for item in finite)
    winners = [
        item
        for item in finite
        if _numerically_equal(
            item.territory_centroid_squared_distance,
            minimum,
        )
    ]
    if len(winners) != 1:
        return None
    return int(winners[0].influence_index)


def _spatial_canonical_candidate(candidate_indices, influence_positions):
    candidates = [int(value) for value in candidate_indices]
    position_keys = {
        candidate: tuple(
            float(value) for value in influence_positions[candidate].tolist()
        )
        for candidate in candidates
    }
    if len(set(position_keys.values())) != len(candidates):
        raise RuntimeError(
            "Exactly coincident candidate joints cannot be distinguished by "
            "distance, topology, or spatial position."
        )
    return min(candidates, key=lambda candidate: position_keys[candidate])


def _diagnostic(
    vertex_ids,
    candidate_indices,
    evidence,
    target,
    classification,
    resolution_pass,
):
    return ExactTieComponentDiagnostic(
        vertex_ids=tuple(int(value) for value in vertex_ids),
        candidate_influence_indices=tuple(
            int(value) for value in candidate_indices
        ),
        candidates=tuple(evidence),
        target_influence_index=int(target),
        classification=str(classification),
        resolution_pass=int(resolution_pass),
    )


def _numerically_equal(first, second):
    first = float(first)
    second = float(second)
    if first == second:
        return True
    scale = max(1.0, abs(first), abs(second))
    tolerance = float(np.finfo(np.float64).eps * scale * 64.0)
    return abs(first - second) <= tolerance
