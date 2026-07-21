"""Automatic bind with final blocking followed by optional smoothing."""

from dataclasses import dataclass
import time
from typing import Dict, Optional, Sequence, Tuple

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.bind_smoothing.diffusion import DEFAULT_BLEND
from ad_skin_tools.bind_smoothing.options import BindSmoothingOptions
from ad_skin_tools.bind_smoothing.solver import BindSmoothingResult, solve_bind_smoothing
from ad_skin_tools.core.joint_automatic_bind import InfluenceAutomaticDiagnostic
from ad_skin_tools.core.skin_cluster import (
    create_closest_skin_cluster,
    find_skin_cluster,
)
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import ambiguous_loop_distance_tiebreak
from ad_skin_tools.region import closed_loop_opposite_guard
from ad_skin_tools.region.connectivity import build_vertex_adjacency
from ad_skin_tools.region.distance_ranking import DEFAULT_DISTANCE_CHUNK_SIZE
from ad_skin_tools.region.solver import RegionOwnershipResult, solve_region_ownership


STORED_WEIGHT_TOLERANCE = 1e-10


@dataclass(frozen=True)
class AutomaticSurfaceBindOptions:
    """Production bind options exposed through the UI command boundary."""

    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE
    fail_on_zero_ownership: bool = False
    smoothing_blend: float = DEFAULT_BLEND
    smoothing_iterations: int = 0


@dataclass(frozen=True)
class AutomaticSurfaceBindResult:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    vertex_count: int
    influence_count: int
    topology_component_count: int
    ownership_counts: Dict[str, int]
    owner_vertex_ids: Dict[str, Tuple[int, ...]]
    average_owner_distance: float
    maximum_owner_distance: float
    resolution_pass_count: int
    reassigned_vertex_count: int
    primary_region_count: int
    co_primary_region_count: int
    diagnostics: Tuple[InfluenceAutomaticDiagnostic, ...]
    elapsed_seconds: float
    region_result: RegionOwnershipResult
    blocking_owner_indices: np.ndarray
    smoothing_blend: float
    smoothing_iterations: int
    effective_maximum_influences: int
    smoothing_mixed_vertex_count: int
    guarded_result: object
    blocking_result: object
    smoothing_result: BindSmoothingResult


def bind_object_automatic_surface(
    mesh: str,
    joints: Sequence[str],
    options: Optional[AutomaticSurfaceBindOptions] = None,
) -> AutomaticSurfaceBindResult:
    """Bind final ownership and optionally smooth the resulting weights."""

    started = time.perf_counter()
    options = options or AutomaticSurfaceBindOptions()
    if int(options.distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")

    smoothing_options = BindSmoothingOptions(
        iterations=int(options.smoothing_iterations),
        blend=float(options.smoothing_blend),
    ).validated()
    selection_before = cmds.ls(selection=True, long=True) or []

    region_result = solve_region_ownership(
        mesh=mesh,
        joints=joints,
        distance_chunk_size=int(options.distance_chunk_size),
    )
    guarded_result = closed_loop_opposite_guard.solve_closed_loop_opposite_guard(
        region_result
    )
    blocking_result = (
        ambiguous_loop_distance_tiebreak.solve_ambiguous_loop_distance_tiebreak(
            region_result,
            guarded_result,
        )
    )
    final_owners = np.asarray(
        blocking_result.corrected_owner_indices,
        dtype=np.int32,
    ).copy()

    owner_vertex_ids, ownership_counts = _final_ownership_maps(
        region_result,
        final_owners,
    )
    zero_ownership = tuple(
        joint
        for joint in region_result.influences
        if ownership_counts[joint] == 0
    )
    if zero_ownership and options.fail_on_zero_ownership:
        raise RuntimeError(
            "Final blocking produced no vertices for these joints:\n{}".format(
                "\n".join(zero_ownership)
            )
        )

    if find_skin_cluster(region_result.mesh_shape, required=False):
        raise RuntimeError(
            "The loaded object already has skin weights. Initial binding is only "
            "available for an unskinned mesh."
        )

    smoothing_result = solve_bind_smoothing(
        owner_indices=final_owners,
        adjacency=build_vertex_adjacency(region_result.mesh_shape),
        vertex_positions=region_result.vertex_positions,
        influence_positions=region_result.influence_positions,
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
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=smoothing_result.effective_maximum_influences,
            )
            expected = _weights_in_skin_order(
                adapter,
                region_result,
                smoothing_result.weights,
            )
            vertex_ids = np.arange(region_result.vertex_count, dtype=np.int32)
            adapter.set_weights(vertex_ids, expected, normalize=False)
            _validate_stored_weights(
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

    owner_distances = _owner_distances(region_result, final_owners)
    diagnostics = _build_diagnostics(
        region_result,
        owner_vertex_ids,
    )

    return AutomaticSurfaceBindResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        vertex_count=region_result.vertex_count,
        influence_count=region_result.influence_count,
        topology_component_count=region_result.topology_component_count,
        ownership_counts=ownership_counts,
        owner_vertex_ids=owner_vertex_ids,
        average_owner_distance=float(np.mean(owner_distances)),
        maximum_owner_distance=float(np.max(owner_distances)),
        resolution_pass_count=region_result.resolution_pass_count,
        reassigned_vertex_count=region_result.reassigned_vertex_count,
        primary_region_count=region_result.primary_region_count,
        co_primary_region_count=region_result.co_primary_region_count,
        diagnostics=diagnostics,
        elapsed_seconds=float(time.perf_counter() - started),
        region_result=region_result,
        blocking_owner_indices=final_owners,
        smoothing_blend=float(smoothing_result.options.blend),
        smoothing_iterations=int(smoothing_result.options.iterations),
        effective_maximum_influences=int(
            smoothing_result.effective_maximum_influences
        ),
        smoothing_mixed_vertex_count=int(
            smoothing_result.diffusion_result.mixed_vertex_count
        ),
        guarded_result=guarded_result,
        blocking_result=blocking_result,
        smoothing_result=smoothing_result,
    )


def print_automatic_surface_report(result: AutomaticSurfaceBindResult) -> None:
    print("\n[AD Skin Tool - Final Blocking + Smoothing Bind]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Vertices:", result.vertex_count)
    print("Influences:", result.influence_count)
    print("Topology components:", result.topology_component_count)
    print(
        "Exact-tie vertices:",
        result.region_result.exact_tie_result.exact_tie_vertex_count,
    )
    print(
        "Exact-tie components:",
        result.region_result.exact_tie_result.component_count,
    )
    print("Final blocking owner rows:", result.blocking_owner_indices.size)
    print("Smoothing Blend:", result.smoothing_blend)
    print("Smoothing Iterations:", result.smoothing_iterations)
    print("Effective Max Influences:", result.effective_maximum_influences)
    print("Smoothing mixed vertices:", result.smoothing_mixed_vertex_count)
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


def _final_ownership_maps(region_result, owner_indices):
    owner_vertex_ids = {}
    ownership_counts = {}
    for influence_index, joint in enumerate(region_result.influences):
        vertex_ids = tuple(
            int(value)
            for value in np.where(owner_indices == int(influence_index))[0].tolist()
        )
        owner_vertex_ids[joint] = vertex_ids
        ownership_counts[joint] = len(vertex_ids)
    return owner_vertex_ids, ownership_counts


def _weights_in_skin_order(adapter, region_result, region_weights):
    skin_influences = tuple(adapter.influences())
    skin_column_by_joint = {
        joint: column for column, joint in enumerate(skin_influences)
    }
    missing = [
        joint
        for joint in region_result.influences
        if joint not in skin_column_by_joint
    ]
    if missing:
        raise RuntimeError(
            "Created skinCluster is missing Region influences:\n{}".format(
                "\n".join(missing)
            )
        )

    ordered = np.zeros(
        (region_result.vertex_count, len(skin_influences)),
        dtype=np.float64,
    )
    for region_column, joint in enumerate(region_result.influences):
        ordered[:, skin_column_by_joint[joint]] = region_weights[:, region_column]
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


def _owner_distances(region_result, owner_indices):
    delta = (
        np.asarray(region_result.vertex_positions, dtype=np.float64)
        - np.asarray(region_result.influence_positions, dtype=np.float64)[owner_indices]
    )
    squared = np.einsum("vi,vi->v", delta, delta)
    return np.sqrt(squared)


def _build_diagnostics(region_result, owner_vertex_ids):
    reassigned = set(region_result.reassigned_vertex_ids)
    result = []
    for item in region_result.diagnostics:
        owned_ids = owner_vertex_ids[item.joint]
        messages = []
        if not owned_ids:
            messages.append("zero final ownership")
        if item.co_primary_region_count:
            messages.append("contains valid co-primary region")
        result.append(
            InfluenceAutomaticDiagnostic(
                joint=item.joint,
                ownership_count=len(owned_ids),
                connected_region_count=item.connected_region_count,
                primary_region_count=item.primary_region_count,
                co_primary_region_count=item.co_primary_region_count,
                reassigned_vertex_count=sum(
                    int(vertex_id in reassigned) for vertex_id in owned_ids
                ),
                messages=tuple(messages),
            )
        )
    return tuple(result)


def _restore_selection(selection_before):
    existing = [node for node in selection_before if cmds.objExists(node)]
    cmds.select(clear=True)
    if existing:
        cmds.select(existing, replace=True)
