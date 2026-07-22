"""Apply an explicit Global Owner only to detached Stage 01 secondary regions.

Without a Global Owner tag, the result is exactly the Stage 01 closest-distance owner
map and facing is skipped. With a tag, facing protects co-primary and ambiguous
secondary regions; only detached secondary vertices are reassigned.
"""

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np

from ad_skin_tools.region_research.nearest_regions import (
    NearestRegionResearchResult,
)
from ad_skin_tools.region_research.secondary_facing import (
    SecondaryFacingResult,
    classify_secondary_facing,
)


@dataclass(frozen=True)
class GlobalOwnerOverrideResult:
    stage_01: NearestRegionResearchResult
    global_owner_joint: Optional[str]
    global_owner_influence_index: Optional[int]
    facing: Optional[SecondaryFacingResult]
    owner_indices: np.ndarray
    reassigned_vertex_ids: Tuple[int, ...]

    @property
    def global_owner_enabled(self) -> bool:
        return self.global_owner_joint is not None

    @property
    def reassigned_vertex_count(self) -> int:
        return len(self.reassigned_vertex_ids)

    @property
    def co_primary_region_count(self) -> int:
        return self.facing.co_primary_region_count if self.facing else 0

    @property
    def detached_region_count(self) -> int:
        return self.facing.detached_region_count if self.facing else 0

    @property
    def ambiguous_region_count(self) -> int:
        return self.facing.ambiguous_region_count if self.facing else 0


def apply_global_owner_override(
    stage_01: NearestRegionResearchResult,
    global_owner_joint: Optional[str],
) -> GlobalOwnerOverrideResult:
    """Return the final hard owner map for the v10.3 smoke test."""

    owners = np.asarray(
        stage_01.nearest.owner_indices,
        dtype=np.int32,
    ).copy()

    if not global_owner_joint:
        return GlobalOwnerOverrideResult(
            stage_01=stage_01,
            global_owner_joint=None,
            global_owner_influence_index=None,
            facing=None,
            owner_indices=owners,
            reassigned_vertex_ids=tuple(),
        )

    try:
        target_index = stage_01.context.influences.index(global_owner_joint)
    except ValueError:
        raise RuntimeError(
            "The tagged Global Owner is not present in the Stage 01 influence list:\n{}"
            .format(global_owner_joint)
        )

    facing = classify_secondary_facing(stage_01)
    detached_ids = np.asarray(facing.detached_vertex_ids, dtype=np.int32)
    if detached_ids.size:
        changed_mask = owners[detached_ids] != int(target_index)
        changed_ids = detached_ids[changed_mask]
        owners[detached_ids] = int(target_index)
    else:
        changed_ids = np.asarray([], dtype=np.int32)

    return GlobalOwnerOverrideResult(
        stage_01=stage_01,
        global_owner_joint=global_owner_joint,
        global_owner_influence_index=int(target_index),
        facing=facing,
        owner_indices=owners,
        reassigned_vertex_ids=tuple(
            int(value) for value in changed_ids.tolist()
        ),
    )
