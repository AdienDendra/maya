"""Visual smoke comparison for current blocking and surface geodesic Voronoi.

This module is experimental and does not modify production Region ownership.
Run it from Maya after loading one unskinned mesh and adding at least two joints
in AD Skin Tool.

Current production blocking:
    import ad_skin_tools.region.surface_geodesic_smoke as smoke
    smoke.run_current()

Experimental surface geodesic blocking:
    cmds.undo()
    smoke.run_geodesic()

Undo once after either run to remove the smoke-test skinCluster.
"""

import builtins
import heapq
import importlib
import math

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.core import smoothed_automatic_bind
from ad_skin_tools.core.skin_cluster import create_closest_skin_cluster
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region import ambiguous_loop_distance_tiebreak
from ad_skin_tools.region import closed_loop_opposite_guard
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.region.connectivity import build_vertex_adjacency
from ad_skin_tools.ui import skin_operations


for module in (
    region_solver,
    closed_loop_opposite_guard,
    ambiguous_loop_distance_tiebreak,
):
    importlib.reload(module)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError("Open AD Skin Tool before running this smoke test.")

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()
    state = tool_window._STATE
    joints = list(state.get("joints", []))
    if len(joints) < 2:
        raise RuntimeError("Add at least two joints to the AD Skin Tool list.")
    return state["mesh_transform"], joints


def _solve_current_blocking(mesh, joints):
    region_result = region_solver.solve_region_ownership(mesh=mesh, joints=joints)
    guarded_result = closed_loop_opposite_guard.solve_closed_loop_opposite_guard(
        region_result
    )
    blocking_result = (
        ambiguous_loop_distance_tiebreak.solve_ambiguous_loop_distance_tiebreak(
            region_result,
            guarded_result,
        )
    )
    owners = np.asarray(
        blocking_result.corrected_owner_indices,
        dtype=np.int32,
    ).copy()
    return region_result, guarded_result, blocking_result, owners


def _one_hot(owner_indices, influence_count):
    owners = np.asarray(owner_indices, dtype=np.int32)
    weights = np.zeros((owners.size, int(influence_count)), dtype=np.float64)
    weights[np.arange(owners.size, dtype=np.int32), owners] = 1.0
    return weights


def _create_visual_skin(region_result, owner_indices, undo_label):
    adapter = None
    try:
        with undo_chunk(undo_label):
            adapter = create_closest_skin_cluster(
                mesh_shape=region_result.mesh_shape,
                mesh_transform=region_result.mesh_transform,
                joints=list(region_result.influences),
                max_influences=1,
            )
            source_weights = _one_hot(
                owner_indices,
                region_result.influence_count,
            )
            expected = smoothed_automatic_bind._weights_in_skin_order(
                adapter,
                region_result,
                source_weights,
            )
            vertex_ids = np.arange(region_result.vertex_count, dtype=np.int32)
            adapter.set_weights(vertex_ids, expected, normalize=False)
            smoothed_automatic_bind._validate_stored_weights(
                adapter,
                expected,
                maximum_influences=1,
            )
    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            try:
                cmds.delete(adapter.skin_cluster)
            except Exception:
                pass
        raise
    return adapter


def _ownership_counts(owner_indices, influence_count):
    return np.bincount(
        np.asarray(owner_indices, dtype=np.int32),
        minlength=int(influence_count),
    )


def run_current():
    """Create a one-hot skinCluster from the current production blocking map."""

    mesh, joints = _loaded_unskinned_context()
    region_result, guarded_result, blocking_result, owners = (
        _solve_current_blocking(mesh, joints)
    )
    adapter = _create_visual_skin(
        region_result,
        owners,
        "AD Skin Tool Current Blocking Smoke",
    )

    builtins.AD_SKIN_CURRENT_SMOKE_REGION_RESULT = region_result
    builtins.AD_SKIN_CURRENT_SMOKE_GUARDED_RESULT = guarded_result
    builtins.AD_SKIN_CURRENT_SMOKE_BLOCKING_RESULT = blocking_result
    builtins.AD_SKIN_CURRENT_SMOKE_OWNERS = owners
    builtins.AD_SKIN_CURRENT_SMOKE_SKIN_CLUSTER = adapter.skin_cluster

    counts = _ownership_counts(owners, region_result.influence_count)
    validation = blocking_result.final_validation
    cmds.select(region_result.mesh_transform, replace=True)

    print("\n[AD Skin Tool - Current Blocking Smoke]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Region resolution passes:", region_result.resolution_pass_count)
    print(
        "Region neighbour fallback vertices:",
        region_result.neighbour_fallback_vertex_count,
    )
    print("Final detached vertices:", validation.detached_vertex_count)
    print("Final ambiguous vertices:", validation.ambiguous_vertex_count)
    print("SkinCluster:", adapter.skin_cluster)
    print("\nOwnership counts:")
    for influence_index, joint in enumerate(region_result.influences):
        print("  {}: {}".format(joint.split("|")[-1], int(counts[influence_index])))
    print("\nUndo once before running run_geodesic().")


def _surface_geodesic_owners(
    vertex_positions,
    influence_positions,
    adjacency,
    current_owners,
):
    positions = np.asarray(vertex_positions, dtype=np.float64)
    influences = np.asarray(influence_positions, dtype=np.float64)
    baseline = np.asarray(current_owners, dtype=np.int32)

    best_costs = np.full(positions.shape[0], np.inf, dtype=np.float64)
    owners = np.full(positions.shape[0], -1, dtype=np.int32)
    queue = []
    seed_vertex_ids = []
    exact_tie_vertex_ids = set()

    def owner_key(vertex_id, owner_index):
        delta = positions[int(vertex_id)] - influences[int(owner_index)]
        squared = float(np.dot(delta, delta))
        pivot = influences[int(owner_index)]
        return (
            0 if int(owner_index) == int(baseline[int(vertex_id)]) else 1,
            squared,
            float(pivot[0]),
            float(pivot[1]),
            float(pivot[2]),
            int(owner_index),
        )

    def offer(vertex_id, owner_index, cost):
        vertex_id = int(vertex_id)
        owner_index = int(owner_index)
        current_cost = float(best_costs[vertex_id])
        if float(cost) < current_cost:
            best_costs[vertex_id] = float(cost)
            owners[vertex_id] = owner_index
            heapq.heappush(queue, (float(cost), owner_index, vertex_id))
            return
        if float(cost) != current_cost or int(owners[vertex_id]) == owner_index:
            return

        exact_tie_vertex_ids.add(vertex_id)
        current_owner = int(owners[vertex_id])
        if owner_key(vertex_id, owner_index) < owner_key(vertex_id, current_owner):
            owners[vertex_id] = owner_index
            heapq.heappush(queue, (float(cost), owner_index, vertex_id))

    for influence_index in range(influences.shape[0]):
        delta = positions - influences[influence_index][np.newaxis, :]
        squared = np.einsum("vi,vi->v", delta, delta)
        exact_minimum = float(np.min(squared))
        seeds = np.where(squared == exact_minimum)[0].astype(np.int32)
        seed_vertex_ids.append(tuple(int(value) for value in seeds.tolist()))
        for vertex_id in seeds.tolist():
            offer(vertex_id, influence_index, 0.0)

    while queue:
        cost, owner_index, vertex_id = heapq.heappop(queue)
        vertex_id = int(vertex_id)
        owner_index = int(owner_index)
        if float(cost) != float(best_costs[vertex_id]):
            continue
        if owner_index != int(owners[vertex_id]):
            continue

        source = positions[vertex_id]
        for neighbour_id in adjacency[vertex_id]:
            neighbour_id = int(neighbour_id)
            target = positions[neighbour_id]
            dx = float(target[0] - source[0])
            dy = float(target[1] - source[1])
            dz = float(target[2] - source[2])
            edge_length = math.sqrt(dx * dx + dy * dy + dz * dz)
            offer(neighbour_id, owner_index, float(cost) + edge_length)

    unassigned = np.where(owners < 0)[0].astype(np.int32)
    if unassigned.size:
        owners[unassigned] = baseline[unassigned]

    return (
        owners,
        best_costs,
        tuple(seed_vertex_ids),
        tuple(sorted(exact_tie_vertex_ids)),
        tuple(int(value) for value in unassigned.tolist()),
    )


def _boundary_vertex_ids(owner_indices, adjacency):
    owners = np.asarray(owner_indices, dtype=np.int32)
    return tuple(
        int(vertex_id)
        for vertex_id, neighbours in enumerate(adjacency)
        if any(
            int(owners[int(neighbour_id)]) != int(owners[vertex_id])
            for neighbour_id in neighbours
        )
    )


def run_geodesic():
    """Create a one-hot skinCluster from experimental surface propagation."""

    mesh, joints = _loaded_unskinned_context()
    region_result, guarded_result, blocking_result, current_owners = (
        _solve_current_blocking(mesh, joints)
    )
    adjacency = build_vertex_adjacency(region_result.mesh_shape)
    (
        geodesic_owners,
        geodesic_costs,
        seed_vertex_ids,
        exact_tie_vertex_ids,
        unassigned_vertex_ids,
    ) = _surface_geodesic_owners(
        region_result.vertex_positions,
        region_result.influence_positions,
        adjacency,
        current_owners,
    )

    changed_ids = np.where(geodesic_owners != current_owners)[0].astype(np.int32)
    current_boundary = _boundary_vertex_ids(current_owners, adjacency)
    geodesic_boundary = _boundary_vertex_ids(geodesic_owners, adjacency)
    boundary_difference = tuple(
        sorted(set(current_boundary).symmetric_difference(geodesic_boundary))
    )

    adapter = _create_visual_skin(
        region_result,
        geodesic_owners,
        "AD Skin Tool Surface Geodesic Smoke",
    )

    builtins.AD_SKIN_GEODESIC_SMOKE_REGION_RESULT = region_result
    builtins.AD_SKIN_GEODESIC_SMOKE_GUARDED_RESULT = guarded_result
    builtins.AD_SKIN_GEODESIC_SMOKE_BLOCKING_RESULT = blocking_result
    builtins.AD_SKIN_GEODESIC_SMOKE_CURRENT_OWNERS = current_owners
    builtins.AD_SKIN_GEODESIC_SMOKE_OWNERS = geodesic_owners
    builtins.AD_SKIN_GEODESIC_SMOKE_COSTS = geodesic_costs
    builtins.AD_SKIN_GEODESIC_SMOKE_SEEDS = seed_vertex_ids
    builtins.AD_SKIN_GEODESIC_SMOKE_CHANGED_VERTEX_IDS = tuple(
        int(value) for value in changed_ids.tolist()
    )
    builtins.AD_SKIN_GEODESIC_SMOKE_BOUNDARY_DIFFERENCE_VERTEX_IDS = (
        boundary_difference
    )
    builtins.AD_SKIN_GEODESIC_SMOKE_MESH_TRANSFORM = region_result.mesh_transform
    builtins.AD_SKIN_GEODESIC_SMOKE_SKIN_CLUSTER = adapter.skin_cluster

    current_counts = _ownership_counts(
        current_owners,
        region_result.influence_count,
    )
    geodesic_counts = _ownership_counts(
        geodesic_owners,
        region_result.influence_count,
    )
    changed_percent = (
        100.0 * float(changed_ids.size) / float(region_result.vertex_count)
        if region_result.vertex_count
        else 0.0
    )

    cmds.select(region_result.mesh_transform, replace=True)

    print("\n[AD Skin Tool - Surface Geodesic Voronoi Smoke]")
    print("Mesh:", region_result.mesh_transform)
    print("Vertices:", region_result.vertex_count)
    print("Influences:", region_result.influence_count)
    print("Changed owner vertices:", int(changed_ids.size))
    print("Changed owner percent:", round(changed_percent, 4))
    print("Current boundary vertices:", len(current_boundary))
    print("Geodesic boundary vertices:", len(geodesic_boundary))
    print("Boundary symmetric difference:", len(boundary_difference))
    print("Exact geodesic cost ties:", len(exact_tie_vertex_ids))
    print("Disconnected vertices using current owner:", len(unassigned_vertex_ids))
    print("SkinCluster:", adapter.skin_cluster)
    print("\nSeed counts and ownership shift:")
    for influence_index, joint in enumerate(region_result.influences):
        delta = int(geodesic_counts[influence_index] - current_counts[influence_index])
        print(
            "  {}: seeds={} | current={} | geodesic={} | delta={:+d}".format(
                joint.split("|")[-1],
                len(seed_vertex_ids[influence_index]),
                int(current_counts[influence_index]),
                int(geodesic_counts[influence_index]),
                delta,
            )
        )
    print("\nCall select_changed_vertices() to inspect every shifted owner row.")
    print("Undo once to remove this visual-test skinCluster.")


def select_changed_vertices():
    """Select vertices whose geodesic owner differs from current blocking."""

    mesh_transform = getattr(
        builtins,
        "AD_SKIN_GEODESIC_SMOKE_MESH_TRANSFORM",
        None,
    )
    changed = getattr(
        builtins,
        "AD_SKIN_GEODESIC_SMOKE_CHANGED_VERTEX_IDS",
        tuple(),
    )
    if not mesh_transform:
        raise RuntimeError("Run run_geodesic() first.")

    components = [
        "{}.vtx[{}]".format(mesh_transform, int(vertex_id))
        for vertex_id in changed
    ]
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)
