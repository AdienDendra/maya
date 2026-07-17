"""Exact joint-pivot distance ranking for Region Ownership.

Every mesh vertex is compared with every supplied joint pivot using squared
Euclidean distance. Exact ties remain unresolved and are never broken by joint
name, selection order, hierarchy, or an ownership quota.
"""

from dataclasses import dataclass
from typing import Dict, Tuple
import math
import time

import numpy as np

from ad_skin_tools.region.maya_scene import MayaDistanceInput


DEFAULT_DISTANCE_CHUNK_SIZE = 16384


@dataclass(frozen=True)
class DistanceCandidate:
    influence_index: int
    influence: str
    squared_distance: float
    distance: float
    is_exact_minimum: bool


@dataclass(frozen=True)
class ExactDistanceRankingResult:
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    vertex_positions: np.ndarray
    influence_positions: np.ndarray
    nearest_influence_indices: np.ndarray
    minimum_squared_distances: np.ndarray
    exact_tie_counts: np.ndarray
    exact_tie_vertex_ids: Tuple[int, ...]
    unique_assignment_counts: Dict[str, int]
    coincident_influence_groups: Tuple[Tuple[str, ...], ...]
    elapsed_seconds: float

    @property
    def vertex_count(self) -> int:
        return int(self.vertex_positions.shape[0])

    @property
    def influence_count(self) -> int:
        return int(self.influence_positions.shape[0])


@dataclass(frozen=True)
class ExactDistanceTables:
    influence_indices: np.ndarray
    squared_distances: np.ndarray


def solve_exact_distance_ranking(
    scene_input: MayaDistanceInput,
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> ExactDistanceRankingResult:
    started = time.perf_counter()
    vertex_positions = np.asarray(scene_input.vertex_positions, dtype=np.float64)
    influence_positions = np.asarray(
        scene_input.influence_positions,
        dtype=np.float64,
    )
    _validate_inputs(
        vertex_positions,
        influence_positions,
        scene_input.influences,
        distance_chunk_size,
    )

    vertex_count = int(vertex_positions.shape[0])
    nearest_indices = np.full(vertex_count, -1, dtype=np.int32)
    minimum_squared = np.full(vertex_count, np.inf, dtype=np.float64)
    tie_counts = np.zeros(vertex_count, dtype=np.int32)

    for start in range(0, vertex_count, int(distance_chunk_size)):
        stop = min(start + int(distance_chunk_size), vertex_count)
        squared = _squared_distance_block(
            vertex_positions[start:stop],
            influence_positions,
        )
        chunk_minimum = np.min(squared, axis=1)
        exact_minimum_mask = squared == chunk_minimum[:, np.newaxis]
        chunk_tie_counts = np.count_nonzero(
            exact_minimum_mask,
            axis=1,
        ).astype(np.int32)
        chunk_argmin = np.argmin(squared, axis=1).astype(np.int32)
        unique_mask = chunk_tie_counts == 1

        minimum_squared[start:stop] = chunk_minimum
        tie_counts[start:stop] = chunk_tie_counts
        nearest_indices[start:stop][unique_mask] = chunk_argmin[unique_mask]

    if np.any(~np.isfinite(minimum_squared)):
        raise RuntimeError("Exact distance ranking produced non-finite values.")
    if np.any(tie_counts < 1):
        raise RuntimeError("One or more vertices have no distance candidate.")

    exact_tie_vertex_ids = tuple(
        np.where(tie_counts > 1)[0].astype(np.int32).tolist()
    )
    unique_assignment_counts = {
        joint: int(np.count_nonzero(nearest_indices == joint_index))
        for joint_index, joint in enumerate(scene_input.influences)
    }

    return ExactDistanceRankingResult(
        mesh_shape=scene_input.mesh_shape,
        mesh_transform=scene_input.mesh_transform,
        influences=scene_input.influences,
        vertex_positions=vertex_positions,
        influence_positions=influence_positions,
        nearest_influence_indices=nearest_indices,
        minimum_squared_distances=minimum_squared,
        exact_tie_counts=tie_counts,
        exact_tie_vertex_ids=exact_tie_vertex_ids,
        unique_assignment_counts=unique_assignment_counts,
        coincident_influence_groups=_coincident_influence_groups(
            scene_input.influences,
            influence_positions,
        ),
        elapsed_seconds=time.perf_counter() - started,
    )


def build_exact_distance_tables(
    result: ExactDistanceRankingResult,
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> ExactDistanceTables:
    if int(distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")

    vertex_count = result.vertex_count
    influence_count = result.influence_count
    ordered_indices = np.empty(
        (vertex_count, influence_count),
        dtype=np.int32,
    )
    ordered_squared = np.empty(
        (vertex_count, influence_count),
        dtype=np.float64,
    )

    for start in range(0, vertex_count, int(distance_chunk_size)):
        stop = min(start + int(distance_chunk_size), vertex_count)
        squared = _squared_distance_block(
            result.vertex_positions[start:stop],
            result.influence_positions,
        )
        order = np.argsort(squared, axis=1, kind="mergesort").astype(np.int32)
        ordered_indices[start:stop] = order
        ordered_squared[start:stop] = np.take_along_axis(squared, order, axis=1)

    return ExactDistanceTables(
        influence_indices=ordered_indices,
        squared_distances=ordered_squared,
    )


def rank_vertex(
    result: ExactDistanceRankingResult,
    vertex_id: int,
) -> Tuple[DistanceCandidate, ...]:
    vertex_id = int(vertex_id)
    if vertex_id < 0 or vertex_id >= result.vertex_count:
        raise IndexError(
            "Vertex ID {} is outside [0, {}).".format(
                vertex_id,
                result.vertex_count,
            )
        )

    point = result.vertex_positions[vertex_id]
    delta = result.influence_positions - point[np.newaxis, :]
    squared = np.einsum("ji,ji->j", delta, delta)
    minimum = float(np.min(squared))
    order = np.argsort(squared, kind="mergesort")

    return tuple(
        DistanceCandidate(
            influence_index=int(index),
            influence=result.influences[int(index)],
            squared_distance=float(squared[int(index)]),
            distance=math.sqrt(float(squared[int(index)])),
            is_exact_minimum=bool(float(squared[int(index)]) == minimum),
        )
        for index in order.tolist()
    )


def format_vertex_ranking(
    result: ExactDistanceRankingResult,
    vertex_id: int,
) -> str:
    lines = ["Vertex {} exact joint-distance ranking:".format(int(vertex_id))]
    for rank, candidate in enumerate(rank_vertex(result, vertex_id), start=1):
        marker = " [EXACT MINIMUM]" if candidate.is_exact_minimum else ""
        lines.append(
            "  {:>3}. {} | distance={} | squared={}{}".format(
                rank,
                candidate.influence,
                repr(candidate.distance),
                repr(candidate.squared_distance),
                marker,
            )
        )
    return "\n".join(lines)


def _squared_distance_block(vertex_positions, influence_positions):
    delta = (
        vertex_positions[:, np.newaxis, :]
        - influence_positions[np.newaxis, :, :]
    )
    return np.einsum("vji,vji->vj", delta, delta)


def _coincident_influence_groups(influences, influence_positions):
    groups = {}
    for index, influence in enumerate(influences):
        key = tuple(float(value) for value in influence_positions[index].tolist())
        groups.setdefault(key, []).append(influence)

    coincident = [
        tuple(sorted(group))
        for group in groups.values()
        if len(group) > 1
    ]
    coincident.sort()
    return tuple(coincident)


def _validate_inputs(
    vertex_positions,
    influence_positions,
    influences,
    distance_chunk_size,
):
    if vertex_positions.ndim != 2 or vertex_positions.shape[1] != 3:
        raise ValueError("vertex_positions must have shape (vertex_count, 3).")
    if influence_positions.ndim != 2 or influence_positions.shape[1] != 3:
        raise ValueError("influence_positions must have shape (joint_count, 3).")
    if vertex_positions.shape[0] == 0:
        raise ValueError("vertex_positions cannot be empty.")
    if influence_positions.shape[0] == 0:
        raise ValueError("influence_positions cannot be empty.")
    if influence_positions.shape[0] != len(influences):
        raise ValueError("influence_positions row count must match influence names.")
    if not np.all(np.isfinite(vertex_positions)):
        raise ValueError("vertex_positions contain non-finite values.")
    if not np.all(np.isfinite(influence_positions)):
        raise ValueError("influence_positions contain non-finite values.")
    if int(distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")
