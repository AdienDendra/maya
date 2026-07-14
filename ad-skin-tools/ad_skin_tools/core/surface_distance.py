from dataclasses import dataclass
import heapq
from typing import List, Optional, Sequence, Tuple

from ad_skin_tools.core.compat import ensure_numpy

np = ensure_numpy()


WeightedAdjacency = List[List[Tuple[int, float]]]

@dataclass(frozen=True)
class SurfaceTopKResult:
    """
    Top-K surface distances from distinct labels.

    Each row represents one mesh vertex.

    distances:
        Sorted surface distances. Unused entries contain infinity.

    label_indices:
        Segment or influence label associated with each distance.
        Unused entries contain -1.

    source_indices:
        Original seed index that produced each winning path.
        This lets the segment solver recover information such as
        the seed's position along a bone segment.
    """

    distances: np.ndarray
    label_indices: np.ndarray
    source_indices: np.ndarray
    reached_vertex_count: int
    
@dataclass(frozen=True)
class SurfaceDistanceResult:
    """
    Result of a multi-source shortest-path calculation.

    distances:
        Minimum accumulated edge distance from any seed.

    source_indices:
        Index of the seed that produced the minimum distance.
        Unreachable vertices contain -1.

    reached_vertex_count:
        Number of vertices connected to at least one seed.
    """

    distances: np.ndarray
    source_indices: np.ndarray
    reached_vertex_count: int

def compute_top_k_surface_distances(
    adjacency: WeightedAdjacency,
    seed_vertex_ids: Sequence[int],
    seed_label_indices: Sequence[int],
    seed_costs: Optional[Sequence[float]] = None,
    max_labels: int = 5,
    max_distance: Optional[float] = None,
) -> SurfaceTopKResult:
    """
    Calculate the nearest distinct labels through mesh topology.

    Multiple seeds may share the same label. For example, several surface
    seeds sampled along one bone segment all use one segment label.

    Each mesh vertex retains only its nearest max_labels distinct labels.

    Queue state:
        (
            accumulated surface distance,
            label index,
            source seed index,
            current vertex ID,
        )
    """
    vertex_count = len(adjacency)

    if vertex_count == 0:
        raise ValueError(
            "Surface graph is empty."
        )

    seed_vertex_ids = [
        int(vertex_id)
        for vertex_id in seed_vertex_ids
    ]

    seed_label_indices = [
        int(label_index)
        for label_index in seed_label_indices
    ]

    if not seed_vertex_ids:
        raise ValueError(
            "At least one surface seed is required."
        )

    if len(seed_vertex_ids) != len(seed_label_indices):
        raise ValueError(
            "seed_vertex_ids and seed_label_indices must have "
            "the same length."
        )

    max_labels = int(max_labels)

    if max_labels < 1:
        raise ValueError(
            "max_labels must be at least 1."
        )

    if seed_costs is None:
        seed_costs = [
            0.0
            for _ in seed_vertex_ids
        ]
    else:
        seed_costs = [
            float(cost)
            for cost in seed_costs
        ]

    if len(seed_costs) != len(seed_vertex_ids):
        raise ValueError(
            "seed_costs count must match seed_vertex_ids count."
        )

    if max_distance is not None:
        max_distance = float(max_distance)

        if max_distance < 0.0:
            raise ValueError(
                "max_distance cannot be negative."
            )

    for vertex_id in seed_vertex_ids:
        if vertex_id < 0 or vertex_id >= vertex_count:
            raise IndexError(
                f"Seed vertex is outside the mesh range: {vertex_id}"
            )

    for label_index in seed_label_indices:
        if label_index < 0:
            raise ValueError(
                "Seed label indices cannot be negative."
            )

    for cost in seed_costs:
        if cost < 0.0:
            raise ValueError(
                "Seed costs cannot be negative."
            )

    # Per vertex:
    #
    # {
    #     label_index: (best_distance, source_seed_index),
    # }
    #
    # Each dictionary is limited to max_labels entries.
    best_by_vertex = [
        {}
        for _ in range(vertex_count)
    ]

    queue = []
    tolerance = 1e-12

    def store_candidate(
        vertex_id,
        label_index,
        source_index,
        candidate_distance,
    ):
        """
        Store a candidate only when it belongs in this vertex's top-K
        distinct labels.

        Returns True when the state was inserted or improved.
        """
        bucket = best_by_vertex[vertex_id]

        existing = bucket.get(
            label_index
        )

        if existing is not None:
            existing_distance, existing_source = existing

            is_better = (
                candidate_distance
                < existing_distance - tolerance
            )

            is_equal_but_stable = (
                abs(
                    candidate_distance
                    - existing_distance
                ) <= tolerance
                and source_index < existing_source
            )

            if not is_better and not is_equal_but_stable:
                return False

            bucket[label_index] = (
                candidate_distance,
                source_index,
            )

            return True

        if len(bucket) < max_labels:
            bucket[label_index] = (
                candidate_distance,
                source_index,
            )

            return True

        # Find the least useful label currently stored.
        #
        # Larger distance is worse. For exact ties, the larger label
        # index loses, keeping results deterministic.
        worst_label, worst_value = max(
            bucket.items(),
            key=lambda item: (
                item[1][0],
                item[0],
            ),
        )

        worst_distance = worst_value[0]

        candidate_is_better = (
            candidate_distance
            < worst_distance - tolerance
        )

        candidate_wins_tie = (
            abs(
                candidate_distance
                - worst_distance
            ) <= tolerance
            and label_index < worst_label
        )

        if not candidate_is_better and not candidate_wins_tie:
            return False

        del bucket[worst_label]

        bucket[label_index] = (
            candidate_distance,
            source_index,
        )

        return True

    # Initialize all surface seeds.
    for source_index, (
        vertex_id,
        label_index,
        initial_cost,
    ) in enumerate(
        zip(
            seed_vertex_ids,
            seed_label_indices,
            seed_costs,
        )
    ):
        if not store_candidate(
            vertex_id=vertex_id,
            label_index=label_index,
            source_index=source_index,
            candidate_distance=initial_cost,
        ):
            continue

        heapq.heappush(
            queue,
            (
                initial_cost,
                label_index,
                source_index,
                vertex_id,
            ),
        )

    while queue:
        (
            current_distance,
            label_index,
            source_index,
            vertex_id,
        ) = heapq.heappop(queue)

        bucket = best_by_vertex[vertex_id]

        stored = bucket.get(
            label_index
        )

        # This state may have been improved or removed after it was
        # inserted into the queue.
        if stored is None:
            continue

        stored_distance, stored_source = stored

        if (
            stored_source != source_index
            or current_distance
            > stored_distance + tolerance
        ):
            continue

        if (
            max_distance is not None
            and current_distance > max_distance
        ):
            continue

        for neighbor_id, edge_length in adjacency[vertex_id]:
            neighbor_id = int(
                neighbor_id
            )

            edge_length = float(
                edge_length
            )

            if edge_length <= 0.0:
                raise ValueError(
                    "Surface graph contains a non-positive edge length: "
                    f"{vertex_id} -> {neighbor_id}: {edge_length}"
                )

            candidate_distance = (
                current_distance
                + edge_length
            )

            if (
                max_distance is not None
                and candidate_distance > max_distance
            ):
                continue

            if not store_candidate(
                vertex_id=neighbor_id,
                label_index=label_index,
                source_index=source_index,
                candidate_distance=candidate_distance,
            ):
                continue

            heapq.heappush(
                queue,
                (
                    candidate_distance,
                    label_index,
                    source_index,
                    neighbor_id,
                ),
            )

    distances = np.full(
        (vertex_count, max_labels),
        np.inf,
        dtype=np.float64,
    )

    label_indices = np.full(
        (vertex_count, max_labels),
        -1,
        dtype=np.int32,
    )

    source_indices = np.full(
        (vertex_count, max_labels),
        -1,
        dtype=np.int32,
    )

    reached_vertex_count = 0

    for vertex_id, bucket in enumerate(
        best_by_vertex
    ):
        if not bucket:
            continue

        reached_vertex_count += 1

        sorted_entries = sorted(
            (
                (
                    distance,
                    label_index,
                    source_index,
                )
                for label_index, (
                    distance,
                    source_index,
                ) in bucket.items()
            ),
            key=lambda item: (
                item[0],
                item[1],
                item[2],
            ),
        )

        for column, (
            distance,
            label_index,
            source_index,
        ) in enumerate(sorted_entries):
            distances[
                vertex_id,
                column,
            ] = distance

            label_indices[
                vertex_id,
                column,
            ] = label_index

            source_indices[
                vertex_id,
                column,
            ] = source_index

    return SurfaceTopKResult(
        distances=distances,
        label_indices=label_indices,
        source_indices=source_indices,
        reached_vertex_count=reached_vertex_count,
    )

def compute_surface_distances(
    adjacency: WeightedAdjacency,
    seed_vertex_ids: Sequence[int],
    seed_costs: Optional[Sequence[float]] = None,
    max_distance: Optional[float] = None,
) -> SurfaceDistanceResult:
    """
    Calculate shortest distances across the mesh surface.

    Distance may only travel through connected topology edges:

        seed vertex
        -> connected edge
        -> connected edge
        -> destination vertex

    This is fundamentally different from volume distance, where two
    spatially-close vertices can influence each other despite being
    separated by a gap.

    Parameters
    ----------
    adjacency:
        Weighted mesh graph produced by
        mesh.get_weighted_vertex_neighbors().

    seed_vertex_ids:
        One or more source vertex IDs.

    seed_costs:
        Optional initial cost for every seed. This allows a bone sample
        inside the mesh to include its distance from the bone to its
        nearest surface vertex.

    max_distance:
        Optional propagation limit. None means propagate through the
        complete connected component.

    Returns
    -------
    SurfaceDistanceResult
        Distance and winning-source arrays with one row per vertex.
    """
    vertex_count = len(adjacency)

    if vertex_count == 0:
        raise ValueError(
            "Surface graph is empty."
        )

    seed_vertex_ids = [
        int(vertex_id)
        for vertex_id in seed_vertex_ids
    ]

    if not seed_vertex_ids:
        raise ValueError(
            "At least one seed vertex is required."
        )

    if seed_costs is None:
        seed_costs = [
            0.0
            for _ in seed_vertex_ids
        ]
    else:
        seed_costs = [
            float(cost)
            for cost in seed_costs
        ]

    if len(seed_costs) != len(seed_vertex_ids):
        raise ValueError(
            "seed_costs count must match seed_vertex_ids count."
        )

    if max_distance is not None:
        max_distance = float(max_distance)

        if max_distance < 0.0:
            raise ValueError(
                "max_distance cannot be negative."
            )

    for vertex_id in seed_vertex_ids:
        if vertex_id < 0 or vertex_id >= vertex_count:
            raise IndexError(
                f"Seed vertex is outside the mesh range: {vertex_id}"
            )

    distances = np.full(
        vertex_count,
        np.inf,
        dtype=np.float64,
    )

    source_indices = np.full(
        vertex_count,
        -1,
        dtype=np.int32,
    )

    # Queue entries:
    #
    # (
    #     accumulated distance,
    #     source index,
    #     current vertex ID,
    # )
    queue = []

    tolerance = 1e-12

    for source_index, (vertex_id, initial_cost) in enumerate(
        zip(seed_vertex_ids, seed_costs)
    ):
        if initial_cost < 0.0:
            raise ValueError(
                "Seed costs cannot be negative."
            )

        current_distance = distances[vertex_id]
        current_source = source_indices[vertex_id]

        is_better = (
            initial_cost < current_distance - tolerance
        )

        is_equal_but_stable = (
            abs(initial_cost - current_distance) <= tolerance
            and (
                current_source < 0
                or source_index < current_source
            )
        )

        if not is_better and not is_equal_but_stable:
            continue

        distances[vertex_id] = initial_cost
        source_indices[vertex_id] = source_index

        heapq.heappush(
            queue,
            (
                initial_cost,
                source_index,
                vertex_id,
            ),
        )

    while queue:
        (
            current_distance,
            source_index,
            vertex_id,
        ) = heapq.heappop(queue)

        # Ignore an outdated queue state after a better path has already
        # replaced it.
        if (
            source_indices[vertex_id] != source_index
            or current_distance
            > distances[vertex_id] + tolerance
        ):
            continue

        if (
            max_distance is not None
            and current_distance > max_distance
        ):
            continue

        for neighbor_id, edge_length in adjacency[vertex_id]:
            neighbor_id = int(neighbor_id)
            edge_length = float(edge_length)

            if edge_length <= 0.0:
                raise ValueError(
                    "Surface graph contains a non-positive edge length: "
                    f"{vertex_id} -> {neighbor_id}: {edge_length}"
                )

            candidate_distance = (
                current_distance + edge_length
            )

            if (
                max_distance is not None
                and candidate_distance > max_distance
            ):
                continue

            existing_distance = distances[neighbor_id]
            existing_source = source_indices[neighbor_id]

            is_better = (
                candidate_distance
                < existing_distance - tolerance
            )

            is_equal_but_stable = (
                abs(
                    candidate_distance
                    - existing_distance
                ) <= tolerance
                and (
                    existing_source < 0
                    or source_index < existing_source
                )
            )

            if not is_better and not is_equal_but_stable:
                continue

            distances[neighbor_id] = candidate_distance
            source_indices[neighbor_id] = source_index

            heapq.heappush(
                queue,
                (
                    candidate_distance,
                    source_index,
                    neighbor_id,
                ),
            )

    reached_mask = np.isfinite(
        distances
    )

    return SurfaceDistanceResult(
        distances=distances,
        source_indices=source_indices,
        reached_vertex_count=int(
            np.count_nonzero(reached_mask)
        ),
    )