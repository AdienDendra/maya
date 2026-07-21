"""v9.4 smoke profiler for Component Smooth optimization.

This module does not replace the production Component Smooth implementation.
It measures the current solver, runs a vectorized candidate with the same inputs,
and verifies that both produce the same selected-row weights within tolerance.

Run from Maya after loading a skinned mesh and selecting components:

    import ad_skin_tools.components.smooth_optimizer_smoke as smoke
    smoke.run()

The smoke test does not write weights to the skinCluster.
"""

from dataclasses import dataclass
import builtins
import time

import numpy as np

from ad_skin_tools.components import smooth
from ad_skin_tools.core import mesh
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.ui import skin_operations
from ad_skin_tools.ui import smoothing_controls


MATCH_TOLERANCE = 1e-10


@dataclass(frozen=True)
class ComponentSmoothOptimizerSmokeResult:
    mesh_shape: str
    mesh_transform: str
    mesh_vertex_count: int
    selected_vertex_count: int
    writable_vertex_count: int
    influence_count: int
    blend: float
    iterations: int
    collect_scope_seconds: float
    query_locks_seconds: float
    read_weights_seconds: float
    build_adjacency_seconds: float
    current_solver_seconds: float
    optimized_solver_seconds: float
    maximum_selected_difference: float
    changed_vertex_ids_match: bool
    skipped_empty_ids_match: bool
    skipped_locked_ids_match: bool

    @property
    def solver_speedup(self) -> float:
        if self.optimized_solver_seconds <= 0.0:
            return float("inf")
        return self.current_solver_seconds / self.optimized_solver_seconds


def run() -> ComponentSmoothOptimizerSmokeResult:
    """Profile current and vectorized Component Smooth calculations."""

    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise RuntimeError("Open AD Skin Tool before running the v9.4 smoke test.")

    tool_window._require_not_busy()
    tool_window._require_loaded_mesh()
    if not tool_window._STATE.get("has_skin_cluster"):
        raise RuntimeError("Component Smooth optimizer smoke requires a skinCluster.")

    values = smoothing_controls.query_values()
    if int(values.iterations) < 1:
        raise RuntimeError("Set Iterations to 1 or higher before running the smoke test.")

    started = time.perf_counter()
    scope = smooth.collect_smooth_scope(
        mesh_shape=tool_window._STATE["mesh_shape"],
        mesh_transform=tool_window._STATE["mesh_transform"],
    )
    collect_scope_seconds = time.perf_counter() - started

    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)
    influences = tuple(adapter.influences())

    started = time.perf_counter()
    active_locked = locked_influences(adapter.skin_cluster, influences)
    locked_columns = tuple(
        influences.index(joint)
        for joint in active_locked
        if joint in influences
    )
    query_locks_seconds = time.perf_counter() - started

    vertex_count = mesh.get_vertex_count(scope.mesh_shape)
    all_vertex_ids = np.arange(vertex_count, dtype=np.int32)
    selected_vertex_ids = np.asarray(scope.vertex_ids, dtype=np.int32)
    selection_falloffs = np.clip(
        np.asarray(scope.selection_falloffs, dtype=np.float64),
        0.0,
        1.0,
    )

    started = time.perf_counter()
    baseline = np.asarray(
        adapter.get_weights(all_vertex_ids).weights,
        dtype=np.float64,
    ).copy()
    read_weights_seconds = time.perf_counter() - started

    started = time.perf_counter()
    adjacency = mesh.get_all_vertex_neighbors(scope.mesh_shape)
    build_adjacency_seconds = time.perf_counter() - started

    started = time.perf_counter()
    (
        current_weights,
        current_changed_ids,
        current_empty_ids,
        current_locked_ids,
    ) = smooth._smooth_selected_rows(
        baseline=baseline,
        adjacency=adjacency,
        selected_vertex_ids=selected_vertex_ids,
        selection_falloffs=selection_falloffs,
        locked_columns=locked_columns,
        blend=float(values.blend),
        iterations=int(values.iterations),
    )
    current_solver_seconds = time.perf_counter() - started

    started = time.perf_counter()
    (
        optimized_weights,
        optimized_changed_ids,
        optimized_empty_ids,
        optimized_locked_ids,
        writable_vertex_count,
    ) = _smooth_selected_rows_vectorized(
        baseline=baseline,
        adjacency=adjacency,
        selected_vertex_ids=selected_vertex_ids,
        selection_falloffs=selection_falloffs,
        locked_columns=locked_columns,
        blend=float(values.blend),
        iterations=int(values.iterations),
    )
    optimized_solver_seconds = time.perf_counter() - started

    selected_difference = np.abs(
        optimized_weights[selected_vertex_ids]
        - current_weights[selected_vertex_ids]
    )
    maximum_selected_difference = (
        float(np.max(selected_difference))
        if selected_difference.size
        else 0.0
    )

    changed_match = np.array_equal(
        optimized_changed_ids,
        current_changed_ids,
    )
    empty_match = np.array_equal(optimized_empty_ids, current_empty_ids)
    locked_match = np.array_equal(optimized_locked_ids, current_locked_ids)

    if maximum_selected_difference > MATCH_TOLERANCE:
        bad_local_rows = np.where(
            np.any(selected_difference > MATCH_TOLERANCE, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Vectorized Component Smooth differs from the current solver. "
            "Maximum difference: {:.12g}. First vertex IDs: {}".format(
                maximum_selected_difference,
                selected_vertex_ids[bad_local_rows].tolist(),
            )
        )
    if not changed_match or not empty_match or not locked_match:
        raise RuntimeError(
            "Vectorized Component Smooth classifications differ from the current "
            "solver. changed_match={}, empty_match={}, locked_match={}.".format(
                changed_match,
                empty_match,
                locked_match,
            )
        )

    result = ComponentSmoothOptimizerSmokeResult(
        mesh_shape=scope.mesh_shape,
        mesh_transform=scope.mesh_transform,
        mesh_vertex_count=int(vertex_count),
        selected_vertex_count=int(selected_vertex_ids.size),
        writable_vertex_count=int(writable_vertex_count),
        influence_count=len(influences),
        blend=float(values.blend),
        iterations=int(values.iterations),
        collect_scope_seconds=float(collect_scope_seconds),
        query_locks_seconds=float(query_locks_seconds),
        read_weights_seconds=float(read_weights_seconds),
        build_adjacency_seconds=float(build_adjacency_seconds),
        current_solver_seconds=float(current_solver_seconds),
        optimized_solver_seconds=float(optimized_solver_seconds),
        maximum_selected_difference=float(maximum_selected_difference),
        changed_vertex_ids_match=bool(changed_match),
        skipped_empty_ids_match=bool(empty_match),
        skipped_locked_ids_match=bool(locked_match),
    )

    builtins.AD_SKIN_COMPONENT_SMOOTH_OPTIMIZER_SMOKE_RESULT = result
    builtins.AD_SKIN_COMPONENT_SMOOTH_OPTIMIZED_WEIGHTS = optimized_weights
    builtins.AD_SKIN_COMPONENT_SMOOTH_CURRENT_WEIGHTS = current_weights
    builtins.AD_SKIN_COMPONENT_SMOOTH_SELECTED_VERTEX_IDS = selected_vertex_ids.copy()

    print_report(result)
    return result


def print_report(result: ComponentSmoothOptimizerSmokeResult) -> None:
    print("\n[AD Skin Tool v9.4 - Component Smooth Optimizer Smoke]")
    print("Mesh:", result.mesh_transform)
    print("Mesh vertices:", result.mesh_vertex_count)
    print("Selected vertices:", result.selected_vertex_count)
    print("Writable vertices:", result.writable_vertex_count)
    print("Influences:", result.influence_count)
    print("Blend:", result.blend)
    print("Iterations:", result.iterations)
    print("Collect scope seconds:", round(result.collect_scope_seconds, 6))
    print("Query locks seconds:", round(result.query_locks_seconds, 6))
    print("Read all weights seconds:", round(result.read_weights_seconds, 6))
    print("Build adjacency seconds:", round(result.build_adjacency_seconds, 6))
    print("Current solver seconds:", round(result.current_solver_seconds, 6))
    print("Optimized solver seconds:", round(result.optimized_solver_seconds, 6))
    print("Solver speedup:", round(result.solver_speedup, 3), "x")
    print(
        "Maximum selected-row difference:",
        "{:.12g}".format(result.maximum_selected_difference),
    )
    print("Changed IDs match:", result.changed_vertex_ids_match)
    print("Skipped empty IDs match:", result.skipped_empty_ids_match)
    print("Skipped locked IDs match:", result.skipped_locked_ids_match)
    print("\nNo skin weights were written by this smoke test.")


def _smooth_selected_rows_vectorized(
    baseline,
    adjacency,
    selected_vertex_ids,
    selection_falloffs,
    locked_columns,
    blend,
    iterations,
):
    current = np.asarray(baseline, dtype=np.float64).copy()
    original = current.copy()
    selected_vertex_ids = np.asarray(selected_vertex_ids, dtype=np.int32)
    selection_falloffs = np.asarray(selection_falloffs, dtype=np.float64)

    influence_count = int(current.shape[1])
    tolerance = (
        float(np.finfo(np.float64).eps)
        * max(1, influence_count)
        * 64.0
    )

    locked_mask = np.zeros(influence_count, dtype=bool)
    if locked_columns:
        locked_mask[list(locked_columns)] = True
    unlocked_columns = np.where(~locked_mask)[0]

    selected_original = original[selected_vertex_ids]
    selected_row_sums = np.sum(
        selected_original,
        axis=1,
        dtype=np.float64,
    )
    empty_mask = selected_row_sums <= tolerance

    if unlocked_columns.size:
        selected_unlocked_sums = np.sum(
            selected_original[:, unlocked_columns],
            axis=1,
            dtype=np.float64,
        )
    else:
        selected_unlocked_sums = np.zeros(
            selected_vertex_ids.size,
            dtype=np.float64,
        )

    locked_mask_rows = (~empty_mask) & (selected_unlocked_sums <= tolerance)
    writable_mask = ~(empty_mask | locked_mask_rows)
    writable_vertex_ids = selected_vertex_ids[writable_mask]
    writable_falloffs = selection_falloffs[writable_mask]

    if not writable_vertex_ids.size or not unlocked_columns.size:
        return (
            current,
            np.empty(0, dtype=np.int32),
            selected_vertex_ids[empty_mask],
            selected_vertex_ids[locked_mask_rows],
            int(writable_vertex_ids.size),
        )

    edge_source_rows = []
    edge_neighbour_ids = []
    for local_row, vertex_id in enumerate(writable_vertex_ids.tolist()):
        neighbours = adjacency[int(vertex_id)]
        if not neighbours:
            continue
        edge_source_rows.extend([int(local_row)] * len(neighbours))
        edge_neighbour_ids.extend(int(value) for value in neighbours)

    edge_source_rows = np.asarray(edge_source_rows, dtype=np.int32)
    edge_neighbour_ids = np.asarray(edge_neighbour_ids, dtype=np.int32)

    effective_blend = float(blend) * writable_falloffs[:, np.newaxis]
    if np.any(locked_mask):
        original_locked_mass = np.sum(
            original[writable_vertex_ids][:, locked_mask],
            axis=1,
            dtype=np.float64,
        )
    else:
        original_locked_mass = np.zeros(
            writable_vertex_ids.size,
            dtype=np.float64,
        )
    available_mass = np.maximum(0.0, 1.0 - original_locked_mass)
    locked_indices = np.where(locked_mask)[0]

    for _ in range(int(iterations)):
        source = current
        next_weights = source.copy()

        neighbour_accum = np.zeros(
            (writable_vertex_ids.size, unlocked_columns.size),
            dtype=np.float64,
        )
        if edge_source_rows.size:
            edge_values = source[edge_neighbour_ids][:, unlocked_columns]
            edge_masses = np.sum(edge_values, axis=1, dtype=np.float64)
            valid_edges = edge_masses > tolerance
            if np.any(valid_edges):
                np.add.at(
                    neighbour_accum,
                    edge_source_rows[valid_edges],
                    edge_values[valid_edges],
                )

        neighbour_totals = np.sum(
            neighbour_accum,
            axis=1,
            dtype=np.float64,
        )
        valid_neighbour_rows = neighbour_totals > tolerance

        target_unlocked = source[writable_vertex_ids][:, unlocked_columns]
        target_totals = np.sum(
            target_unlocked,
            axis=1,
            dtype=np.float64,
        )
        valid_target_rows = target_totals > tolerance
        active_rows = (
            valid_neighbour_rows
            & valid_target_rows
            & (writable_falloffs > 0.0)
            & (float(blend) > 0.0)
        )
        if not np.any(active_rows):
            current = next_weights
            continue

        neighbour_distribution = np.zeros_like(neighbour_accum)
        neighbour_distribution[active_rows] = (
            neighbour_accum[active_rows]
            / neighbour_totals[active_rows, np.newaxis]
        )

        target_distribution = np.zeros_like(target_unlocked)
        target_distribution[active_rows] = (
            target_unlocked[active_rows]
            / target_totals[active_rows, np.newaxis]
        )

        blended_distribution = target_distribution[active_rows] + (
            effective_blend[active_rows]
            * (
                neighbour_distribution[active_rows]
                - target_distribution[active_rows]
            )
        )
        blended_distribution = np.maximum(blended_distribution, 0.0)
        blended_totals = np.sum(
            blended_distribution,
            axis=1,
            dtype=np.float64,
        )
        valid_blended = blended_totals > tolerance

        active_local_rows = np.where(active_rows)[0][valid_blended]
        if active_local_rows.size:
            normalized = (
                blended_distribution[valid_blended]
                / blended_totals[valid_blended, np.newaxis]
            )
            vertex_rows = writable_vertex_ids[active_local_rows]
            next_weights[
                vertex_rows[:, np.newaxis],
                unlocked_columns[np.newaxis, :],
            ] = (
                normalized
                * available_mass[active_local_rows, np.newaxis]
            )
            if locked_indices.size:
                next_weights[
                    vertex_rows[:, np.newaxis],
                    locked_indices[np.newaxis, :],
                ] = original[
                    vertex_rows[:, np.newaxis],
                    locked_indices[np.newaxis, :],
                ]

        current = next_weights

    changed_local_mask = np.any(
        np.abs(
            current[selected_vertex_ids]
            - original[selected_vertex_ids]
        ) > tolerance,
        axis=1,
    )
    changed_vertex_ids = selected_vertex_ids[changed_local_mask]

    return (
        current,
        changed_vertex_ids,
        selected_vertex_ids[empty_mask],
        selected_vertex_ids[locked_mask_rows],
        int(writable_vertex_ids.size),
    )
