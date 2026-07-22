"""Production Bind Skin using the validated ownership pipeline and smoothing."""

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
from ad_skin_tools.region_research.closest_region_ownership import (
    DEFAULT_DISTANCE_CHUNK_SIZE,
)
from ad_skin_tools.region_research.ownership_pipeline import (
    OwnershipPipelineResult,
    solve_ownership_pipeline,
)


STORED_WEIGHT_TOLERANCE = 1e-10


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
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    vertex_count: int
    influence_count: int
    ownership_counts: Dict[str, int]
    owner_vertex_ids: Dict[str, Tuple[int, ...]]
    average_owner_distance: float
    maximum_owner_distance: float
    elapsed_seconds: float
    ownership_pipeline: OwnershipPipelineResult
    blocking_owner_indices: np.ndarray
    smoothing_blend: float
    smoothing_iterations: int
    effective_maximum_influences: int
    smoothing_mixed_vertex_count: int
    smoothing_result: BindSmoothingResult
    stored_maximum_weight_difference: float

    @property
    def global_owner_joint(self) -> Optional[str]:
        return self.ownership_pipeline.global_owner_assignment.global_owner_joint

    @property
    def global_owner_reassigned_vertex_count(self) -> int:
        return (
            self.ownership_pipeline.global_owner_assignment.reassigned_vertex_count
        )

    @property
    def closed_loop_changed_vertex_count(self) -> int:
        return self.ownership_pipeline.closed_loop_ownership.changed_vertex_count


def bind_object_automatic_surface(
    mesh: str,
    joints: Sequence[str],
    options: Optional[AutomaticSurfaceBindOptions] = None,
) -> AutomaticSurfaceBindResult:
    """Solve final owners, optionally smooth, then write one skinCluster matrix."""

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
    final_owners = np.asarray(
        pipeline.final_owner_indices,
        dtype=np.int32,
    ).copy()

    owner_vertex_ids, ownership_counts = _final_ownership_maps(
        context.influences,
        final_owners,
    )
    zero_ownership = tuple(
        joint
        for joint in context.influences
        if ownership_counts[joint] == 0
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

    adapter = None
    try:
        with undo_chunk("AD Skin Tool Bind Skin"):
            adapter = create_closest_skin_cluster(
                mesh_shape=context.mesh_shape,
                mesh_transform=context.mesh_transform,
                joints=list(context.influences),
                max_influences=smoothing_result.effective_maximum_influences,
            )
            expected = _weights_in_skin_order(
                adapter,
                context.influences,
                context.vertex_count,
                smoothing_result.weights,
            )
            vertex_ids = np.arange(context.vertex_count, dtype=np.int32)
            adapter.set_weights(vertex_ids, expected, normalize=False)
            stored_maximum_difference = _validate_stored_weights(
                adapter,
                expected,
                smoothing_result.effective_maximum_influences,
            )
    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            try:
                cmds.delete(adapter.skin_cluster)
            except Exception:
                pass
        raise
    finally:
        _restore_selection(selection_before)

    owner_distances = _owner_distances(context, final_owners)
    return AutomaticSurfaceBindResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=context.mesh_shape,
        mesh_transform=context.mesh_transform,
        influences=context.influences,
        vertex_count=context.vertex_count,
        influence_count=context.influence_count,
        ownership_counts=ownership_counts,
        owner_vertex_ids=owner_vertex_ids,
        average_owner_distance=float(np.mean(owner_distances)),
        maximum_owner_distance=float(np.max(owner_distances)),
        elapsed_seconds=float(time.perf_counter() - started),
        ownership_pipeline=pipeline,
        blocking_owner_indices=final_owners,
        smoothing_blend=float(smoothing_result.options.blend),
        smoothing_iterations=int(smoothing_result.options.iterations),
        effective_maximum_influences=int(
            smoothing_result.effective_maximum_influences
        ),
        smoothing_mixed_vertex_count=int(
            smoothing_result.diffusion_result.mixed_vertex_count
        ),
        smoothing_result=smoothing_result,
        stored_maximum_weight_difference=float(stored_maximum_difference),
    )


def print_automatic_surface_report(result: AutomaticSurfaceBindResult) -> None:
    pipeline = result.ownership_pipeline
    closest = pipeline.closest_ownership
    nearest = closest.closest
    global_assignment = pipeline.global_owner_assignment
    loops = pipeline.closed_loop_ownership

    print("\n[AD Skin Tool - Ownership Pipeline + Smoothing Bind]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Vertices:", result.vertex_count)
    print("Influences:", result.influence_count)
    print("Exact-tie vertices:", nearest.exact_tie_vertex_count)
    print("Connected owner regions:", closest.total_region_count)
    print("Secondary regions:", closest.secondary_region_count)
    print(
        "Global Owner:",
        global_assignment.global_owner_joint.split("|")[-1]
        if global_assignment.global_owner_enabled
        else "<none>",
    )
    print(
        "Global Owner reassigned vertices:",
        global_assignment.reassigned_vertex_count,
    )
    print("Ownership boundary edges:", loops.boundary_edge_count)
    print("Relevant closed loops:", loops.discovered_loop_count)
    print("Maya polySelect calls:", loops.maya_polyselect_call_count)
    print("Applied closed loops:", loops.applied_loop_count)
    print("Closed-loop changed vertices:", loops.changed_vertex_count)
    print("Primary opposite axis:", loops.axis_context.primary_axis)
    print("Final blocking owner rows:", result.blocking_owner_indices.size)
    print("Smoothing Blend:", result.smoothing_blend)
    print("Smoothing Iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Smoothing mixed vertices:", result.smoothing_mixed_vertex_count)
    print(
        "Stored maximum weight difference:",
        result.stored_maximum_weight_difference,
    )
    print(
        "Owner below maximum after:",
        len(result.smoothing_result.owner_maximum_result.owner_below_maximum_after),
    )
    print(
        "Final active influence histogram:",
        result.smoothing_result.validation_result.active_influence_histogram,
    )
    print(
        "Final maximum row-sum error:",
        result.smoothing_result.validation_result.maximum_row_sum_error,
    )
    print("Average final owner distance:", result.average_owner_distance)
    print("Maximum final owner distance:", result.maximum_owner_distance)
    print("Elapsed seconds:", round(result.elapsed_seconds, 6))


def _final_ownership_maps(influences, owner_indices):
    owner_vertex_ids = {}
    ownership_counts = {}
    for influence_index, joint in enumerate(influences):
        vertex_ids = tuple(
            int(value)
            for value in np.where(owner_indices == int(influence_index))[0].tolist()
        )
        owner_vertex_ids[joint] = vertex_ids
        ownership_counts[joint] = len(vertex_ids)
    return owner_vertex_ids, ownership_counts


def _weights_in_skin_order(
    adapter,
    ownership_influences,
    vertex_count,
    ownership_weights,
):
    skin_influences = tuple(adapter.influences())
    skin_column_by_joint = {
        joint: column for column, joint in enumerate(skin_influences)
    }
    missing = [
        joint
        for joint in ownership_influences
        if joint not in skin_column_by_joint
    ]
    if missing:
        raise RuntimeError(
            "Created skinCluster is missing ownership influences:\n{}".format(
                "\n".join(missing)
            )
        )

    ordered = np.zeros(
        (int(vertex_count), len(skin_influences)),
        dtype=np.float64,
    )
    for ownership_column, joint in enumerate(ownership_influences):
        ordered[:, skin_column_by_joint[joint]] = ownership_weights[
            :,
            ownership_column,
        ]
    return ordered


def _validate_stored_weights(adapter, expected, maximum_influences):
    vertex_ids = np.arange(expected.shape[0], dtype=np.int32)
    actual = np.asarray(adapter.get_weights(vertex_ids).weights, dtype=np.float64)
    if actual.shape != expected.shape:
        raise RuntimeError(
            "Stored weight matrix shape differs from expected: {} != {}.".format(
                actual.shape,
                expected.shape,
            )
        )

    maximum_difference = float(np.max(np.abs(actual - expected)))
    if maximum_difference > STORED_WEIGHT_TOLERANCE:
        bad = np.where(
            np.any(
                np.abs(actual - expected) > STORED_WEIGHT_TOLERANCE,
                axis=1,
            )
        )[0][:20]
        raise RuntimeError(
            "Maya stored weights differ from the calculated matrix. Maximum "
            "difference: {}. First vertex IDs: {}".format(
                maximum_difference,
                bad.tolist(),
            )
        )

    row_sums = np.sum(actual, axis=1, dtype=np.float64)
    if np.any(np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE):
        bad = np.where(
            np.abs(row_sums - 1.0) > STORED_WEIGHT_TOLERANCE
        )[0][:20]
        raise RuntimeError(
            "Stored weights are not normalized. First vertex IDs: {}".format(
                bad.tolist()
            )
        )

    active_counts = np.count_nonzero(actual > 1e-12, axis=1)
    if np.any(active_counts > int(maximum_influences)):
        bad = np.where(active_counts > int(maximum_influences))[0][:20]
        raise RuntimeError(
            "Stored weights exceed Max Influences. First vertex IDs: {}".format(
                bad.tolist()
            )
        )

    return maximum_difference


def _owner_distances(context, owner_indices):
    delta = (
        np.asarray(context.vertex_positions, dtype=np.float64)
        - np.asarray(context.influence_positions, dtype=np.float64)[owner_indices]
    )
    squared = np.einsum("vi,vi->v", delta, delta)
    return np.sqrt(squared)


def _restore_selection(selection_before):
    existing = [node for node in selection_before if cmds.objExists(node)]
    cmds.select(clear=True)
    if existing:
        cmds.select(existing, replace=True)
