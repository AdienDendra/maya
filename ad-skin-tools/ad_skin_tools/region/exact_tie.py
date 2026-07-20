"""Complete exact closest-distance ties before Region connectivity.

Distance ranking leaves tied vertices unresolved. This module groups connected
vertices that share the same exact-minimum candidate set and assigns each group
from neighbouring owned vertices. Joint names, hierarchy, and UI selection order
are never used.
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
RESOLVED_SPATIAL_CANONICAL = "resolved_spatial_canonical"


@dataclass(frozen=True)
class ExactTieComponentDiagnostic:
    vertex_ids: Tuple[int, ...]
    candidate_influence_indices: Tuple[int, ...]
    boundary_edge_counts: Tuple[int, ...]
    mean_boundary_squared_edge_lengths: Tuple[float, ...]
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

    def classification_count(self, classification: str) -> int:
        return sum(
            diagnostic.classification == classification
            for diagnostic in self.diagnostics
        )

    @property
    def neighbour_support_component_count(self) -> int:
        return self.classification_count(RESOLVED_NEIGHBOUR_SUPPORT)

    @property
    def neighbour_edge_length_component_count(self) -> int:
        return self.classification_count(RESOLVED_NEIGHBOUR_EDGE_LENGTH)

    @property
    def spatial_canonical_component_count(self) -> int:
        return self.classification_count(RESOLVED_SPATIAL_CANONICAL)


def resolve_exact_distance_ties(
    distance_result: ExactDistanceRankingResult,
    distance_tables: ExactDistanceTables,
    adjacency: Tuple[Tuple[int, ...], ...],
) -> ExactTieResolutionResult:
    """Return one valid exact-minimum owner for every mesh vertex."""

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

    candidate_sets = _candidate_sets(distance_result, distance_tables)
    pending = list(
        _connected_candidate_components(
            distance_result.exact_tie_vertex_ids,
            candidate_sets,
            adjacency,
        )
    )

    # The current distance group ends at tie_count - 1. If facing later rejects
    # this owner, the existing detached search advances to the next greater
    # distance instead of revisiting another equal minimum.
    for vertex_id in distance_result.exact_tie_vertex_ids:
        vertex_id = int(vertex_id)
        candidate_ranks[vertex_id] = (
            int(distance_result.exact_tie_counts[vertex_id]) - 1
        )

    diagnostics = []
    resolution_pass = 0

    while pending:
        resolution_pass += 1
        proposals = []
        unresolved = []

        for vertex_ids, candidate_indices in pending:
            counts, mean_lengths = _boundary_evidence(
                vertex_ids=vertex_ids,
                candidate_indices=candidate_indices,
                owners=owners,
                adjacency=adjacency,
                vertex_positions=distance_result.vertex_positions,
            )
            decision = _choose_neighbour_candidate(
                candidate_indices,
                counts,
                mean_lengths,
            )
            if decision is None:
                unresolved.append(
                    (vertex_ids, candidate_indices, counts, mean_lengths)
                )
                continue

            target, classification = decision
            proposals.append(
                (
                    vertex_ids,
                    candidate_indices,
                    counts,
                    mean_lengths,
                    target,
                    classification,
                )
            )

        if proposals:
            # Apply all decisions together so component order cannot influence
            # another component during the same pass.
            for vertex_ids, _, _, _, target, _ in proposals:
                owners[np.asarray(vertex_ids, dtype=np.int32)] = int(target)

            for item in proposals:
                diagnostics.append(
                    _make_diagnostic(*item, resolution_pass=resolution_pass)
                )
            pending = [
                (vertex_ids, candidate_indices)
                for vertex_ids, candidate_indices, _, _ in unresolved
            ]
            continue

        # A completely balanced neighbour boundary contains no geometric reason
        # to prefer one exact-minimum candidate. Use a stable world-position
        # convention rather than joint name or list order, and report every use.
        for vertex_ids, candidate_indices, counts, mean_lengths in unresolved:
            target = _spatial_canonical_candidate(
                candidate_indices,
                distance_result.influence_positions,
            )
            owners[np.asarray(vertex_ids, dtype=np.int32)] = int(target)
            diagnostics.append(
                _make_diagnostic(
                    vertex_ids,
                    candidate_indices,
                    counts,
                    mean_lengths,
                    target,
                    RESOLVED_SPATIAL_CANONICAL,
                    resolution_pass=resolution_pass,
                )
            )
        pending = []

    if np.any(owners < 0):
        bad = np.where(owners < 0)[0][:20]
        raise RuntimeError(
            "Exact-tie completion produced invalid owners. First IDs: {}".format(
                bad.tolist()
            )
        )

    diagnostics.sort(key=lambda diagnostic: diagnostic.vertex_ids[0])
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


def _candidate_sets(distance_result, distance_tables):
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


def _connected_candidate_components(vertex_ids, candidate_sets, adjacency):
    unseen = set(int(value) for value in vertex_ids)
    components = []

    while unseen:
        seed = min(unseen)
        candidates = candidate_sets[seed]
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
                    and candidate_sets[neighbour_id] == candidates
                ):
                    unseen.remove(neighbour_id)
                    stack.append(neighbour_id)

        components.append((tuple(sorted(component)), candidates))

    components.sort(key=lambda item: item[0][0])
    return tuple(components)


def _boundary_evidence(
    vertex_ids,
    candidate_indices,
    owners,
    adjacency,
    vertex_positions,
):
    component = set(int(value) for value in vertex_ids)
    count_by_candidate = {
        int(candidate): 0 for candidate in candidate_indices
    }
    lengths_by_candidate = {
        int(candidate): [] for candidate in candidate_indices
    }

    for vertex_id in component:
        position = vertex_positions[vertex_id]
        for neighbour_id in adjacency[vertex_id]:
            neighbour_id = int(neighbour_id)
            if neighbour_id in component:
                continue
            owner = int(owners[neighbour_id])
            if owner not in count_by_candidate:
                continue

            delta = vertex_positions[neighbour_id] - position
            count_by_candidate[owner] += 1
            lengths_by_candidate[owner].append(float(np.dot(delta, delta)))

    counts = tuple(
        int(count_by_candidate[int(candidate)])
        for candidate in candidate_indices
    )
    mean_lengths = tuple(
        float(np.mean(lengths_by_candidate[int(candidate)]))
        if lengths_by_candidate[int(candidate)]
        else float("inf")
        for candidate in candidate_indices
    )
    return counts, mean_lengths


def _choose_neighbour_candidate(candidate_indices, counts, mean_lengths):
    maximum_count = max(counts)
    if maximum_count <= 0:
        return None

    winners = [
        index for index, count in enumerate(counts)
        if int(count) == int(maximum_count)
    ]
    if len(winners) == 1:
        return (
            int(candidate_indices[winners[0]]),
            RESOLVED_NEIGHBOUR_SUPPORT,
        )

    minimum_length = min(mean_lengths[index] for index in winners)
    length_winners = [
        index for index in winners
        if _numerically_equal(mean_lengths[index], minimum_length)
    ]
    if len(length_winners) == 1:
        return (
            int(candidate_indices[length_winners[0]]),
            RESOLVED_NEIGHBOUR_EDGE_LENGTH,
        )
    return None


def _spatial_canonical_candidate(candidate_indices, influence_positions):
    candidates = tuple(int(value) for value in candidate_indices)
    keys = {
        candidate: tuple(
            float(value) for value in influence_positions[candidate].tolist()
        )
        for candidate in candidates
    }
    if len(set(keys.values())) != len(candidates):
        raise RuntimeError(
            "Exactly coincident candidate joints cannot be distinguished."
        )
    return min(candidates, key=lambda candidate: keys[candidate])


def _make_diagnostic(
    vertex_ids,
    candidate_indices,
    counts,
    mean_lengths,
    target,
    classification,
    resolution_pass,
):
    return ExactTieComponentDiagnostic(
        vertex_ids=tuple(int(value) for value in vertex_ids),
        candidate_influence_indices=tuple(
            int(value) for value in candidate_indices
        ),
        boundary_edge_counts=tuple(int(value) for value in counts),
        mean_boundary_squared_edge_lengths=tuple(
            float(value) for value in mean_lengths
        ),
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
