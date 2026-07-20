"""Component weight smoothing for AD Skin Tool v8.1."""

from dataclasses import dataclass
from typing import Tuple

import maya.cmds as cmds

from ad_skin_tools.components.selection import collect_weighted_mesh_vertices
from ad_skin_tools.core import mesh
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.component_selection import collect_selected_mesh_vertices
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk


np = ensure_numpy()

SMOOTHING_PASS_MULTIPLIER = 2


@dataclass(frozen=True)
class ComponentSmoothScope:
    mesh_shape: str
    mesh_transform: str
    vertex_ids: Tuple[int, ...]
    strengths: Tuple[float, ...]
    whole_object: bool
    soft_selection_enabled: bool
    soft_selection_used: bool
    source_component_count: int
    ignored_component_count: int

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


@dataclass(frozen=True)
class ComponentSmoothResult:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    smoothing_level: int
    smoothing_passes: int
    whole_object: bool
    selected_vertex_ids: Tuple[int, ...]
    smoothed_vertex_ids: Tuple[int, ...]
    skipped_empty_vertex_ids: Tuple[int, ...]
    skipped_locked_vertex_ids: Tuple[int, ...]
    locked_influences: Tuple[str, ...]
    soft_selection_enabled: bool
    soft_selection_used: bool

    @property
    def selected_vertex_count(self) -> int:
        return len(self.selected_vertex_ids)

    @property
    def smoothed_vertex_count(self) -> int:
        return len(self.smoothed_vertex_ids)

    @property
    def skipped_vertex_count(self) -> int:
        return (
            len(self.skipped_empty_vertex_ids)
            + len(self.skipped_locked_vertex_ids)
        )


def collect_smooth_scope(
    mesh_shape: str,
    mesh_transform: str,
) -> ComponentSmoothScope:
    """Resolve component selection first, then loaded-mesh object selection."""

    component_scope = collect_selected_mesh_vertices(
        mesh_shape,
        mesh_transform,
    )
    if component_scope.vertex_ids:
        weighted = collect_weighted_mesh_vertices(
            mesh_shape,
            mesh_transform,
        )
        return ComponentSmoothScope(
            mesh_shape=weighted.mesh_shape,
            mesh_transform=weighted.mesh_transform,
            vertex_ids=weighted.vertex_ids,
            strengths=weighted.falloff_weights,
            whole_object=False,
            soft_selection_enabled=weighted.soft_selection_enabled,
            soft_selection_used=weighted.soft_selection_used,
            source_component_count=weighted.source_component_count,
            ignored_component_count=weighted.ignored_component_count,
        )

    if _loaded_mesh_object_selected(
        component_scope.mesh_shape,
        component_scope.mesh_transform,
    ):
        vertex_count = mesh.get_vertex_count(component_scope.mesh_shape)
        vertex_ids = tuple(range(vertex_count))
        return ComponentSmoothScope(
            mesh_shape=component_scope.mesh_shape,
            mesh_transform=component_scope.mesh_transform,
            vertex_ids=vertex_ids,
            strengths=tuple(1.0 for _ in vertex_ids),
            whole_object=True,
            soft_selection_enabled=False,
            soft_selection_used=False,
            source_component_count=0,
            ignored_component_count=component_scope.ignored_component_count,
        )

    raise RuntimeError(
        "Select vertices, edges, or faces on the loaded mesh, "
        "or select the loaded mesh object."
    )


def smooth_skin_weights(
    scope: ComponentSmoothScope,
    smoothing_level: int,
) -> ComponentSmoothResult:
    """Smooth existing skin weights inside the resolved selection scope."""

    level = int(smoothing_level)
    if level < 1 or level > 10:
        raise ValueError("Component Smooth requires Smoothing Iterations from 1 to 10.")

    selection_before = cmds.ls(selection=True, long=True) or []
    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)
    influences = tuple(adapter.influences())
    active_locked = locked_influences(
        adapter.skin_cluster,
        influences,
    )

    all_vertex_ids = np.arange(
        mesh.get_vertex_count(scope.mesh_shape),
        dtype=np.int32,
    )
    selected_vertex_ids = np.asarray(scope.vertex_ids, dtype=np.int32)
    strengths = np.clip(
        np.asarray(scope.strengths, dtype=np.float64),
        0.0,
        1.0,
    )
    if selected_vertex_ids.size != strengths.size:
        raise RuntimeError("Component Smooth selection data is inconsistent.")

    all_data = adapter.get_weights(all_vertex_ids)
    baseline = np.asarray(all_data.weights, dtype=np.float64).copy()
    adjacency = mesh.get_all_vertex_neighbors(scope.mesh_shape)
    locked_columns = tuple(
        influences.index(joint)
        for joint in active_locked
        if joint in influences
    )
    smoothing_passes = level * SMOOTHING_PASS_MULTIPLIER

    (
        final_weights,
        changed_vertex_ids,
        skipped_empty_vertex_ids,
        skipped_locked_vertex_ids,
    ) = _smooth_selected_rows(
        baseline=baseline,
        adjacency=adjacency,
        selected_vertex_ids=selected_vertex_ids,
        strengths=strengths,
        locked_columns=locked_columns,
        passes=smoothing_passes,
    )

    mutation_recorded = False
    try:
        try:
            with undo_chunk("AD Skin Tool Component Smooth"):
                if changed_vertex_ids.size:
                    adapter.set_weights(
                        changed_vertex_ids,
                        final_weights[changed_vertex_ids],
                        normalize=False,
                    )
                    mutation_recorded = True
                    _validate_written_rows(
                        adapter=adapter,
                        vertex_ids=changed_vertex_ids,
                        expected_weights=final_weights[changed_vertex_ids],
                    )
        except Exception:
            if mutation_recorded:
                _undo_failed_smooth()
            raise
    finally:
        _restore_selection(selection_before)

    return ComponentSmoothResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=scope.mesh_shape,
        mesh_transform=scope.mesh_transform,
        smoothing_level=level,
        smoothing_passes=smoothing_passes,
        whole_object=scope.whole_object,
        selected_vertex_ids=tuple(
            int(value) for value in selected_vertex_ids.tolist()
        ),
        smoothed_vertex_ids=tuple(
            int(value) for value in changed_vertex_ids.tolist()
        ),
        skipped_empty_vertex_ids=tuple(
            int(value) for value in skipped_empty_vertex_ids.tolist()
        ),
        skipped_locked_vertex_ids=tuple(
            int(value) for value in skipped_locked_vertex_ids.tolist()
        ),
        locked_influences=active_locked,
        soft_selection_enabled=scope.soft_selection_enabled,
        soft_selection_used=scope.soft_selection_used,
    )


def print_component_smooth_report(result: ComponentSmoothResult) -> None:
    print("\n[AD Skin Tool v8.1 - Component Smooth]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Whole object:", result.whole_object)
    print("Soft Selection enabled:", result.soft_selection_enabled)
    print("Soft Selection weights used:", result.soft_selection_used)
    print("Smoothing level:", result.smoothing_level)
    print("Internal smoothing passes:", result.smoothing_passes)
    print("Selected vertices:", result.selected_vertex_count)
    print("Changed vertices:", result.smoothed_vertex_count)
    print("Skipped empty vertices:", len(result.skipped_empty_vertex_ids))
    print("Skipped fully locked vertices:", len(result.skipped_locked_vertex_ids))
    print("Locked influences:", len(result.locked_influences))


def _smooth_selected_rows(
    baseline,
    adjacency,
    selected_vertex_ids,
    strengths,
    locked_columns,
    passes,
):
    current = np.asarray(baseline, dtype=np.float64).copy()
    original = current.copy()
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

    selected_row_sums = np.sum(
        original[selected_vertex_ids],
        axis=1,
        dtype=np.float64,
    )
    empty_mask = selected_row_sums <= tolerance

    if unlocked_columns.size:
        selected_unlocked_sums = np.sum(
            original[selected_vertex_ids][:, unlocked_columns],
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
    writable_strengths = strengths[writable_mask]

    for _ in range(int(passes)):
        source = current
        next_weights = source.copy()

        for vertex_id, strength in zip(
            writable_vertex_ids.tolist(),
            writable_strengths.tolist(),
        ):
            neighbours = adjacency[int(vertex_id)]
            if not neighbours or strength <= 0.0:
                continue

            neighbour_ids = np.asarray(neighbours, dtype=np.int32)
            neighbour_unlocked = source[neighbour_ids][:, unlocked_columns]
            neighbour_sums = np.sum(
                neighbour_unlocked,
                axis=1,
                dtype=np.float64,
            )
            valid_neighbours = neighbour_sums > tolerance
            if not np.any(valid_neighbours):
                continue

            neighbour_average = np.mean(
                neighbour_unlocked[valid_neighbours],
                axis=0,
            )
            average_sum = float(
                np.sum(neighbour_average, dtype=np.float64)
            )
            if average_sum <= tolerance:
                continue
            neighbour_distribution = neighbour_average / average_sum

            target_unlocked = source[int(vertex_id), unlocked_columns]
            target_sum = float(
                np.sum(target_unlocked, dtype=np.float64)
            )
            if target_sum <= tolerance:
                continue
            target_distribution = target_unlocked / target_sum

            blended_distribution = (
                target_distribution
                + float(strength)
                * (neighbour_distribution - target_distribution)
            )
            blended_distribution = np.maximum(
                blended_distribution,
                0.0,
            )
            blended_sum = float(
                np.sum(blended_distribution, dtype=np.float64)
            )
            if blended_sum <= tolerance:
                continue
            blended_distribution /= blended_sum

            locked_mass = float(
                np.sum(
                    original[int(vertex_id), locked_mask],
                    dtype=np.float64,
                )
            )
            available_mass = max(0.0, 1.0 - locked_mass)

            next_weights[int(vertex_id), unlocked_columns] = (
                blended_distribution * available_mass
            )
            if locked_columns:
                next_weights[int(vertex_id), locked_mask] = (
                    original[int(vertex_id), locked_mask]
                )

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
    )


def _loaded_mesh_object_selected(
    mesh_shape: str,
    mesh_transform: str,
) -> bool:
    selection = {
        str(item)
        for item in (cmds.ls(selection=True, long=True) or [])
    }
    return mesh_shape in selection or mesh_transform in selection


def _validate_written_rows(
    adapter: SkinClusterAdapter,
    vertex_ids,
    expected_weights,
) -> None:
    if not vertex_ids.size:
        return

    stored = adapter.get_weights(vertex_ids)
    actual = np.asarray(stored.weights, dtype=np.float64)
    expected = np.asarray(expected_weights, dtype=np.float64)
    tolerance = max(
        1e-10,
        float(np.finfo(np.float64).eps)
        * max(1, expected.shape[1])
        * 256.0,
    )

    if not np.all(np.isfinite(actual)):
        raise RuntimeError("Component Smooth stored non-finite weights.")
    if not np.allclose(actual, expected, rtol=0.0, atol=tolerance):
        changed_rows = np.where(
            np.any(np.abs(actual - expected) > tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Component Smooth did not store the calculated weights. "
            "First vertex IDs: {}".format(
                vertex_ids[changed_rows].tolist()
            )
        )

    row_sums = np.sum(actual, axis=1, dtype=np.float64)
    bad_rows = np.where(np.abs(row_sums - 1.0) > tolerance)[0]
    if bad_rows.size:
        raise RuntimeError(
            "Component Smooth stored weights that do not total 1.0. "
            "First vertex IDs: {}".format(
                vertex_ids[bad_rows[:20]].tolist()
            )
        )


def _undo_failed_smooth() -> None:
    try:
        cmds.undo()
    except Exception:
        cmds.warning(
            "Component Smooth failed after modifying the skinCluster. "
            "Use Maya Undo before continuing."
        )


def _restore_selection(selection_before) -> None:
    cmds.select(clear=True)
    if not selection_before:
        return
    try:
        cmds.select(selection_before, replace=True)
    except Exception:
        pass
