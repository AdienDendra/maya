"""Assign detached secondary regions to one explicit Global Owner joint."""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from ad_skin_tools.region_research.closest_region_ownership import (
    ClosestRegionOwnershipResult,
)
from ad_skin_tools.region_research.secondary_surface_facing import (
    SecondarySurfaceFacingResult,
    classify_secondary_surface_facing,
)


@dataclass(frozen=True)
class GlobalOwnerAssignmentResult:
    closest_ownership: ClosestRegionOwnershipResult
    global_owner_joint: Optional[str]
    global_owner_influence_index: Optional[int]
    facing: Optional[SecondarySurfaceFacingResult]
    owner_indices: np.ndarray
    reassigned_vertex_ids: Tuple[int, ...]

    @property
    def global_owner_enabled(self) -> bool:
        return self.global_owner_joint is not None

    @property
    def reassigned_vertex_count(self) -> int:
        return len(self.reassigned_vertex_ids)


def assign_detached_to_global_owner(
    closest_ownership: ClosestRegionOwnershipResult,
    global_owner_joint: Optional[str],
) -> GlobalOwnerAssignmentResult:
    """Keep Stage 1 unchanged without a tag; otherwise move detached secondary only."""

    owners = np.asarray(
        closest_ownership.closest.owner_indices,
        dtype=np.int32,
    ).copy()

    if not global_owner_joint:
        return GlobalOwnerAssignmentResult(
            closest_ownership=closest_ownership,
            global_owner_joint=None,
            global_owner_influence_index=None,
            facing=None,
            owner_indices=owners,
            reassigned_vertex_ids=tuple(),
        )

    try:
        target_index = closest_ownership.context.influences.index(global_owner_joint)
    except ValueError:
        raise RuntimeError(
            "The tagged Global Owner is not present in the influence list:\n{}"
            .format(global_owner_joint)
        )

    facing = classify_secondary_surface_facing(closest_ownership)
    detached_ids = np.asarray(facing.detached_vertex_ids, dtype=np.int32)
    if detached_ids.size:
        changed_mask = owners[detached_ids] != int(target_index)
        changed_ids = detached_ids[changed_mask]
        owners[detached_ids] = int(target_index)
    else:
        changed_ids = np.asarray([], dtype=np.int32)

    return GlobalOwnerAssignmentResult(
        closest_ownership=closest_ownership,
        global_owner_joint=global_owner_joint,
        global_owner_influence_index=int(target_index),
        facing=facing,
        owner_indices=owners,
        reassigned_vertex_ids=tuple(
            int(value) for value in changed_ids.tolist()
        ),
    )
