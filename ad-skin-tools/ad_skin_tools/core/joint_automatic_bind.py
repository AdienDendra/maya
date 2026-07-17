"""UI-facing hard bind backed by the universal Region Ownership solver."""

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core.skin_cluster import (
    SkinClusterAdapter,
    create_closest_skin_cluster,
    find_skin_cluster,
)
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region.distance_ranking import DEFAULT_DISTANCE_CHUNK_SIZE
from ad_skin_tools.region.solver import RegionOwnershipResult, solve_region_ownership


@dataclass(frozen=True)
class AutomaticSurfaceBindOptions:
    """Production options that cannot alter the mathematical ownership rule."""

    distance_chunk_size: int = DEFAULT_DISTANCE_CHUNK_SIZE
    fail_on_zero_ownership: bool = False


@dataclass(frozen=True)
class InfluenceAutomaticDiagnostic:
    joint: str
    ownership_count: int
    connected_region_count: int
    primary_region_count: int
    co_primary_region_count: int
    reassigned_vertex_count: int
    messages: Tuple[str, ...]


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


def bind_object_automatic_surface(
    mesh: str,
    joints: Sequence[str],
    options: Optional[AutomaticSurfaceBindOptions] = None,
) -> AutomaticSurfaceBindResult:
    """Resolve Region Ownership and write exact one-hot skin weights."""

    options = options or AutomaticSurfaceBindOptions()
    if int(options.distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")

    selection_before = cmds.ls(selection=True, long=True) or []
    region_result = solve_region_ownership(
        mesh=mesh,
        joints=joints,
        distance_chunk_size=int(options.distance_chunk_size),
    )

    zero_ownership = tuple(
        joint
        for joint in region_result.influences
        if region_result.ownership_counts[joint] == 0
    )
    if zero_ownership and options.fail_on_zero_ownership:
        raise RuntimeError(
            "Region Ownership produced no vertices for these joints:\n{}".format(
                "\n".join(zero_ownership)
            )
        )

    if find_skin_cluster(region_result.mesh_shape, required=False):
        raise RuntimeError(
            "The loaded object already has skin weights. Region binding is only "
            "available for an unskinned mesh."
        )

    adapter = None
    try:
        with undo_chunk("AD Skin Tool Region Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=1,
            )
            _write_hard_weights(adapter, region_result)
            _validate_stored_hard_weights(adapter, region_result)
    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            try:
                cmds.delete(adapter.skin_cluster)
            except Exception:
                pass
        raise
    finally:
        _restore_selection(selection_before)

    diagnostics = _build_diagnostics(region_result)
    final_distances = np.sqrt(region_result.final_squared_distances)

    return AutomaticSurfaceBindResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=region_result.mesh_shape,
        mesh_transform=region_result.mesh_transform,
        influences=region_result.influences,
        vertex_count=region_result.vertex_count,
        influence_count=region_result.influence_count,
        topology_component_count=region_result.topology_component_count,
        ownership_counts=region_result.ownership_counts,
        owner_vertex_ids=region_result.owner_vertex_ids,
        average_owner_distance=float(np.mean(final_distances)),
        maximum_owner_distance=float(np.max(final_distances)),
        resolution_pass_count=region_result.resolution_pass_count,
        reassigned_vertex_count=region_result.reassigned_vertex_count,
        primary_region_count=region_result.primary_region_count,
        co_primary_region_count=region_result.co_primary_region_count,
        diagnostics=diagnostics,
        elapsed_seconds=region_result.elapsed_seconds,
        region_result=region_result,
    )


def print_automatic_surface_report(result: AutomaticSurfaceBindResult) -> None:
    print("\n[AD Skin Tool - Region Ownership Bind]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Vertices:", result.vertex_count)
    print("Influences:", result.influence_count)
    print("Topology components:", result.topology_component_count)
    print("Resolution passes:", result.resolution_pass_count)
    print("Vertices reassigned from raw nearest owner:", result.reassigned_vertex_count)
    print("Primary regions:", result.primary_region_count)
    print("Co-primary regions:", result.co_primary_region_count)
    print("Average final owner distance:", result.average_owner_distance)
    print("Maximum final owner distance:", result.maximum_owner_distance)
    print("Elapsed seconds:", round(result.elapsed_seconds, 6))
    print("\nPer-influence ownership:")

    for diagnostic in result.diagnostics:
        short_name = diagnostic.joint.split("|")[-1]
        print(
            "  {}: vertices={} | regions={} | primary={} | co-primary={} | "
            "reassigned-in={}{}".format(
                short_name,
                diagnostic.ownership_count,
                diagnostic.connected_region_count,
                diagnostic.primary_region_count,
                diagnostic.co_primary_region_count,
                diagnostic.reassigned_vertex_count,
                _message_suffix(diagnostic.messages),
            )
        )


def select_automatic_owned_vertices(
    result: AutomaticSurfaceBindResult,
    joint: str,
) -> None:
    resolved_joint = _resolve_result_joint(result, joint)
    vertex_ids = result.owner_vertex_ids.get(resolved_joint, tuple())
    components = [
        "{}.vtx[{}]".format(result.mesh_transform, int(vertex_id))
        for vertex_id in vertex_ids
    ]
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)


def _write_hard_weights(adapter, region_result):
    vertex_ids = np.arange(region_result.vertex_count, dtype=np.int32)
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

    region_to_skin_column = np.asarray(
        [skin_column_by_joint[joint] for joint in region_result.influences],
        dtype=np.int32,
    )
    owner_columns = region_to_skin_column[region_result.owner_indices]
    weights = np.zeros(
        (region_result.vertex_count, len(skin_influences)),
        dtype=np.float64,
    )
    weights[vertex_ids, owner_columns] = 1.0
    adapter.set_weights(vertex_ids, weights, normalize=False)


def _validate_stored_hard_weights(adapter, region_result):
    vertex_ids = np.arange(region_result.vertex_count, dtype=np.int32)
    stored = adapter.get_weights(vertex_ids)
    skin_column_by_joint = {
        joint: column for column, joint in enumerate(stored.influences)
    }
    expected_columns = np.asarray(
        [
            skin_column_by_joint[region_result.influences[int(owner_index)]]
            for owner_index in region_result.owner_indices.tolist()
        ],
        dtype=np.int32,
    )

    weights = np.asarray(stored.weights, dtype=np.float64)
    row_sums = np.sum(weights, axis=1, dtype=np.float64)
    epsilon = float(np.finfo(np.float64).eps)
    sum_error_bound = epsilon * max(1, weights.shape[1])

    if np.any(np.abs(row_sums - 1.0) > sum_error_bound):
        bad = np.where(np.abs(row_sums - 1.0) > sum_error_bound)[0][:20]
        raise RuntimeError(
            "Stored skin weights are not normalized one-hot rows. "
            "First vertex IDs: {}".format(bad.tolist())
        )

    actual_columns = np.argmax(weights, axis=1).astype(np.int32)
    if not np.array_equal(actual_columns, expected_columns):
        bad = np.where(actual_columns != expected_columns)[0][:20]
        raise RuntimeError(
            "Stored skin ownership differs from the Region result. "
            "First vertex IDs: {}".format(bad.tolist())
        )

    expected_values = weights[vertex_ids, expected_columns]
    if np.any(np.abs(expected_values - 1.0) > sum_error_bound):
        bad = np.where(
            np.abs(expected_values - 1.0) > sum_error_bound
        )[0][:20]
        raise RuntimeError(
            "Stored owner weights are not exactly one within numerical error. "
            "First vertex IDs: {}".format(bad.tolist())
        )


def _build_diagnostics(region_result):
    reassigned = set(region_result.reassigned_vertex_ids)
    result = []

    for item in region_result.diagnostics:
        owned_ids = region_result.owner_vertex_ids[item.joint]
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


def _resolve_result_joint(result, joint):
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Joint does not exist:\n{}".format(joint))
    path = matches[0]
    if path not in result.influences:
        raise RuntimeError("Joint is not part of this Region bind:\n{}".format(path))
    return path


def _restore_selection(selection_before):
    existing = [node for node in selection_before if cmds.objExists(node)]
    cmds.select(clear=True)
    if existing:
        cmds.select(existing, replace=True)


def _message_suffix(messages):
    if not messages:
        return ""
    return " | " + "; ".join(messages)
