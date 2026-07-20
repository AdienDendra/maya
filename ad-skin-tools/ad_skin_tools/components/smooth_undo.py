"""Undoable write layer for AD Skin Tool v8.1 Component Smooth."""

import maya.cmds as cmds

from ad_skin_tools.components import smooth as component_smooth
from ad_skin_tools.components.undoable_weights import apply_undoable_weights
from ad_skin_tools.core import mesh
from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter


np = ensure_numpy()


def install():
    """Replace only the v8.1 Smooth write function."""

    component_smooth.smooth_skin_weights = smooth_skin_weights


def smooth_skin_weights(scope, smoothing_level):
    """Calculate existing-weight smoothing and write it as one undoable command."""

    level = int(smoothing_level)
    if level < 1 or level > 10:
        raise ValueError(
            "Component Smooth requires Smoothing Iterations from 1 to 10."
        )

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
    smoothing_passes = (
        level * component_smooth.SMOOTHING_PASS_MULTIPLIER
    )

    (
        final_weights,
        changed_vertex_ids,
        skipped_empty_vertex_ids,
        skipped_locked_vertex_ids,
    ) = component_smooth._smooth_selected_rows(
        baseline=baseline,
        adjacency=adjacency,
        selected_vertex_ids=selected_vertex_ids,
        strengths=strengths,
        locked_columns=locked_columns,
        passes=smoothing_passes,
    )

    command_applied = False
    try:
        if changed_vertex_ids.size:
            before_weights = baseline[changed_vertex_ids].copy()
            after_weights = final_weights[changed_vertex_ids].copy()
            apply_undoable_weights(
                skin_cluster=adapter.skin_cluster,
                mesh_shape=scope.mesh_shape,
                vertex_ids=changed_vertex_ids,
                before_weights=before_weights,
                after_weights=after_weights,
            )
            command_applied = True
            _validate_written_rows(
                adapter=adapter,
                vertex_ids=changed_vertex_ids,
                expected_weights=after_weights,
            )
    except Exception:
        if command_applied:
            _undo_failed_smooth()
        raise

    return component_smooth.ComponentSmoothResult(
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


def _validate_written_rows(adapter, vertex_ids, expected_weights):
    if not vertex_ids.size:
        return

    actual = np.asarray(
        adapter.get_weights(vertex_ids).weights,
        dtype=np.float64,
    )
    expected = np.asarray(expected_weights, dtype=np.float64)
    tolerance = 1e-8

    if not np.all(np.isfinite(actual)):
        raise RuntimeError("Component Smooth stored non-finite weights.")

    differences = np.abs(actual - expected)
    if not np.allclose(actual, expected, rtol=0.0, atol=tolerance):
        changed_rows = np.where(
            np.any(differences > tolerance, axis=1)
        )[0][:20]
        raise RuntimeError(
            "Component Smooth did not store the calculated weights. "
            "Maximum difference: {:.12g}. First vertex IDs: {}".format(
                float(np.max(differences)),
                vertex_ids[changed_rows].tolist(),
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


def _undo_failed_smooth():
    try:
        cmds.undo()
    except Exception:
        cmds.warning(
            "Component Smooth failed after modifying the skinCluster. "
            "Use Maya Undo before continuing."
        )


install()
