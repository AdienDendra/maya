"""Production Bind Skin with timing that stops at the completed weight write."""

from dataclasses import dataclass
import time
from typing import Dict, Optional, Sequence, Tuple

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.bind_smoothing.diffusion import DEFAULT_BLEND
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.solver import BindSmoothingResult, solve_bind_smoothing
from ad_skin_tools.core.skin_cluster import (
    create_closest_skin_cluster,
    find_skin_cluster,
)
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region.closest_region_ownership import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
)
from ad_skin_tools.region.ownership_pipeline import (
    OwnershipPipelineResult,
    solve_ownership_pipeline,
)


@dataclass(frozen=True)
class AutomaticSurfaceBindOptions:
    """Production options exposed through the UI command boundary."""

    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE
    fail_on_zero_ownership: bool = False
    smoothing_blend: float = DEFAULT_BLEND
    smoothing_iterations: int = 0
    global_owner_joint: Optional[str] = None


@dataclass(frozen=True)
class AutomaticSurfaceBindResult:
    """Production bind result and timings up to the completed Maya weight write."""

    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    vertex_count: int
    influence_count: int
    ownership_counts: Dict[str, int]
    owner_vertex_ids: Dict[str, Tuple[int, ...]]
    ownership_pipeline: OwnershipPipelineResult
    blocking_owner_indices: np.ndarray
    smoothing_blend: float
    smoothing_iterations: int
    effective_maximum_influences: int
    smoothing_mixed_vertex_count: int
    smoothing_result: Optional[BindSmoothingResult]
    ownership_seconds: float
    weight_calculation_seconds: float
    skin_cluster_creation_seconds: float
    skin_column_remap_seconds: float
    weight_write_seconds: float
    production_elapsed_seconds: float

    @property
    def elapsed_seconds(self) -> float:
        return self.production_elapsed_seconds

    @property
    def global_owner_joint(self) -> Optional[str]:
        return self.ownership_pipeline.global_owner_assignment.global_owner_joint

    @property
    def global_owner_reassigned_vertex_count(self) -> int:
        return self.ownership_pipeline.global_owner_assignment.reassigned_vertex_count

    @property
    def closed_loop_changed_vertex_count(self) -> int:
        return self.ownership_pipeline.closed_loop_ownership.changed_vertex_count


def bind_object_automatic_surface(
    mesh: str,
    joints: Sequence[str],
    options: Optional[AutomaticSurfaceBindOptions] = None,
) -> AutomaticSurfaceBindResult:
    """Solve owners, create final weights, create skinCluster, write once, finish."""

    started = time.perf_counter()
    options = options or AutomaticSurfaceBindOptions()
    if int(options.distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")

    smoothing_options = BindSmoothingOptions(
        iterations=int(options.smoothing_iterations),
        blend=float(options.smoothing_blend),
    ).validated()
    selection_before = cmds.ls(selection=True, long=True) or []

    pipeline = solve_ownership_pipeline(
        mesh=mesh,
        joints=joints,
        global_owner_joint=options.global_owner_joint,
        distance_chunk_size=int(options.distance_chunk_size),
    )
    closest = pipeline.closest_ownership
    context = closest.context
    final_owners = np.asarray(pipeline.final_owner_indices, dtype=np.int32).copy()

    owner_vertex_ids, ownership_counts = _final_ownership_maps(
        context.influences,
        final_owners,
    )
    zero_ownership = tuple(
        joint for joint in context.influences if ownership_counts[joint] == 0
    )
    if zero_ownership and options.fail_on_zero_ownership:
        raise RuntimeError(
            "Final blocking produced no vertices for these joints:\n{}".format(
                "\n".join(zero_ownership)
            )
        )

    if find_skin_cluster(context.mesh_shape, required=False):
        raise RuntimeError(
            "The loaded object already has skin weights. Initial binding is only "
            "available for an unskinned mesh."
        )
    ownership_seconds = time.perf_counter() - started

    weight_started = time.perf_counter()
    if smoothing_options.iterations == 0:
        ownership_weights = _build_hard_weights(
            final_owners,
            context.influence_count,
        )
        smoothing_result = None
        effective_maximum_influences = 1
        smoothing_mixed_vertex_count = 0
    else:
        smoothing_result = solve_bind_smoothing(
            owner_indices=final_owners,
            adjacency=context.adjacency,
            vertex_positions=context.vertex_positions,
            influence_positions=context.influence_positions,
            options=smoothing_options,
        )
        if not np.array_equal(
            smoothing_result.blocking_owner_indices,
            final_owners,
        ):
            raise RuntimeError("Smoothing modified the final blocking owner map.")
        ownership_weights = smoothing_result.weights
        effective_maximum_influences = int(
            smoothing_result.effective_maximum_influences
        )
        smoothing_mixed_vertex_count = int(
            smoothing_result.diffusion_result.mixed_vertex_count
        )
    weight_calculation_seconds = time.perf_counter() - weight_started

    adapter = None
    skin_cluster_creation_seconds = 0.0
    skin_column_remap_seconds = 0.0
    weight_write_seconds = 0.0
    production_elapsed_seconds = 0.0
    try:
        with undo_chunk("AD Skin Tool Bind Skin"):
            create_started = time.perf_counter()
            adapter = create_closest_skin_cluster(
                mesh_shape=context.mesh_shape,
                mesh_transform=context.mesh_transform,
                joints=list(context.influences),
                max_influences=effective_maximum_influences,
            )
            skin_cluster_creation_seconds = time.perf_counter() - create_started

            remap_started = time.perf_counter()
            weights_to_write = _weights_in_skin_order(
                adapter,
                context.influences,
                ownership_weights,
            )
            skin_column_remap_seconds = time.perf_counter() - remap_started

            vertex_ids = np.arange(context.vertex_count, dtype=np.int32)
            write_started = time.perf_counter()
            adapter.set_weights(vertex_ids, weights_to_write, normalize=False)
            weight_write_seconds = time.perf_counter() - write_started

            production_elapsed_seconds = time.perf_counter() - started
    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            try:
                cmds.delete(adapter.skin_cluster)
            except Exception:
                pass
        raise
    finally:
        _restore_selection(selection_before)

    return AutomaticSurfaceBindResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=context.mesh_shape,
        mesh_transform=context.mesh_transform,
        influences=context.influences,
        vertex_count=context.vertex_count,
        influence_count=context.influence_count,
        ownership_counts=ownership_counts,
        owner_vertex_ids=owner_vertex_ids,
        ownership_pipeline=pipeline,
        blocking_owner_indices=final_owners,
        smoothing_blend=float(smoothing_options.blend),
        smoothing_iterations=int(smoothing_options.iterations),
        effective_maximum_influences=effective_maximum_influences,
        smoothing_mixed_vertex_count=smoothing_mixed_vertex_count,
        smoothing_result=smoothing_result,
        ownership_seconds=float(ownership_seconds),
        weight_calculation_seconds=float(weight_calculation_seconds),
        skin_cluster_creation_seconds=float(skin_cluster_creation_seconds),
        skin_column_remap_seconds=float(skin_column_remap_seconds),
        weight_write_seconds=float(weight_write_seconds),
        production_elapsed_seconds=float(production_elapsed_seconds),
    )


def print_automatic_surface_report(result: AutomaticSurfaceBindResult) -> None:
    print("\n[AD Skin Tool - Bind Skin]")
    print("Mesh:", result.mesh_transform)
    print("Global Owner:", result.global_owner_joint or "<none>")
    print("Vertices:", result.vertex_count)
    print("Influences:", result.influence_count)
    print("Smoothing Blend:", result.smoothing_blend)
    print("Smoothing Iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Smoothing mixed vertices:", result.smoothing_mixed_vertex_count)
    print("Elapsed: {:.6f} s".format(result.production_elapsed_seconds))


def _final_ownership_maps(influences, owner_indices):
    """Group final owner rows once instead of scanning once per influence."""

    owners = np.asarray(owner_indices, dtype=np.int32)
    influence_count = len(influences)
    vertex_ids = np.arange(owners.size, dtype=np.int32)
    counts = np.bincount(owners, minlength=influence_count).astype(np.int64)
    order = vertex_ids[np.argsort(owners, kind="stable")]
    offsets = np.concatenate(
        (
            np.asarray([0], dtype=np.int64),
            np.cumsum(counts, dtype=np.int64),
        )
    )

    owner_vertex_ids = {}
    ownership_counts = {}
    for influence_index, joint in enumerate(influences):
        start = int(offsets[influence_index])
        stop = int(offsets[influence_index + 1])
        grouped = tuple(int(value) for value in order[start:stop].tolist())
        owner_vertex_ids[joint] = grouped
        ownership_counts[joint] = int(counts[influence_index])
    return owner_vertex_ids, ownership_counts


def _build_hard_weights(owner_indices, influence_count):
    """Convert final integer owners directly to one-hot skin weights."""

    owners = np.asarray(owner_indices, dtype=np.int32)
    weights = np.zeros(
        (owners.size, int(influence_count)),
        dtype=np.float64,
    )
    if owners.size:
        weights[
            np.arange(owners.size, dtype=np.int32),
            owners,
        ] = 1.0
    return weights


def _weights_in_skin_order(
    adapter,
    ownership_influences,
    ownership_weights,
):
    """Return weights in the exact influence-column order used by Maya."""

    ownership_influences = tuple(ownership_influences)
    skin_influences = tuple(adapter.influences())
    if skin_influences == ownership_influences:
        return ownership_weights

    ownership_column_by_joint = {
        joint: column
        for column, joint in enumerate(ownership_influences)
    }
    missing = [
        joint
        for joint in skin_influences
        if joint not in ownership_column_by_joint
    ]
    if missing or len(skin_influences) != len(ownership_influences):
        raise RuntimeError(
            "Created skinCluster influence order cannot be mapped to ownership "
            "columns. Missing or unexpected influences:\n{}".format(
                "\n".join(missing) if missing else "<count mismatch>"
            )
        )

    permutation = np.asarray(
        [ownership_column_by_joint[joint] for joint in skin_influences],
        dtype=np.int32,
    )
    return ownership_weights[:, permutation]


def _restore_selection(selection_before):
    existing = [node for node in selection_before if cmds.objExists(node)]
    cmds.select(clear=True)
    if existing:
        cmds.select(existing, replace=True)
