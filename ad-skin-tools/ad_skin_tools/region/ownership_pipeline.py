"""One-pass hard ownership pipeline used before the skinCluster write."""

from dataclasses import dataclass
import time
from typing import Optional, Sequence

import numpy as np

from ad_skin_tools.region.closest_region_ownership import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
    ClosestRegionOwnershipResult,
    solve_closest_region_ownership,
)
from ad_skin_tools.region.closed_loop_ownership import (
    ClosedLoopOwnershipResult,
    resolve_closed_loop_ownership,
)
from ad_skin_tools.region.global_owner_assignment import (
    GlobalOwnerAssignmentResult,
    assign_detached_to_global_owner,
)


@dataclass(frozen=True)
class OwnershipPipelineResult:
    closest_ownership: ClosestRegionOwnershipResult
    global_owner_assignment: GlobalOwnerAssignmentResult
    closed_loop_ownership: ClosedLoopOwnershipResult
    final_owner_indices: np.ndarray
    elapsed_seconds: float

    @property
    def vertex_count(self) -> int:
        return self.closest_ownership.vertex_count

    @property
    def influence_count(self) -> int:
        return self.closest_ownership.influence_count


def solve_ownership_pipeline(
    mesh: str,
    joints: Sequence[str],
    global_owner_joint: Optional[str],
    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE,
) -> OwnershipPipelineResult:
    """Solve final hard owners without creating or modifying a skinCluster."""

    started = time.perf_counter()
    closest = solve_closest_region_ownership(
        mesh=mesh,
        joints=joints,
        distance_chunk_size=int(distance_chunk_size),
    )
    global_assignment = assign_detached_to_global_owner(
        closest_ownership=closest,
        global_owner_joint=global_owner_joint,
    )
    closed_loops = resolve_closed_loop_ownership(
        context=closest.context,
        owner_indices=global_assignment.owner_indices,
    )
    final_owners = np.asarray(
        closed_loops.corrected_owner_indices,
        dtype=np.int32,
    ).copy()

    if final_owners.shape != (closest.vertex_count,):
        raise RuntimeError("Final owner map shape does not match the mesh.")
    if np.any(final_owners < 0) or np.any(final_owners >= closest.influence_count):
        raise RuntimeError("Final owner map contains invalid influence indices.")

    return OwnershipPipelineResult(
        closest_ownership=closest,
        global_owner_assignment=global_assignment,
        closed_loop_ownership=closed_loops,
        final_owner_indices=final_owners,
        elapsed_seconds=float(time.perf_counter() - started),
    )
