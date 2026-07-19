"""UI-driven v6.1 smoke test for constrained Bind Smoothing.

No skinCluster is created or modified. The script compares raw synchronous
surface diffusion with the final Max Influence projection while retaining the
hard Region owner for every vertex.
"""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.bind_smoothing import max_influences
from ad_skin_tools.bind_smoothing import options
from ad_skin_tools.bind_smoothing import solver as smoothing_solver
from ad_skin_tools.bind_smoothing import validation
from ad_skin_tools.region import connectivity
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


SMOOTH_ITERATIONS = 5
RELAXATION = 0.5
MAXIMUM_INFLUENCES = 5
WEIGHT_EPSILON = 1e-12
SELECT_PROJECTED_VERTICES = True
PRINT_VERTEX_LIMIT = 20
INSPECT_VERTEX_IDS = (50,)


for module in (
    options,
    max_influences,
    validation,
    smoothing_solver,
    connectivity,
    region_solver,
):
    importlib.reload(module)


def _loaded_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise builtins.RuntimeError(
            "Open AD Skin Tool before running the v6.1 smoke test."
        )
    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()
    state = tool_window._STATE
    joints = builtins.list(state.get("joints", []))
    if builtins.len(joints) < 2:
        raise builtins.RuntimeError(
            "Add at least two joints to the AD Skin Tool list."
        )
    return state["mesh_transform"], joints


def _active_histogram(weights, epsilon):
    active = np.count_nonzero(weights > epsilon, axis=1).astype(np.int32)
    values, counts = np.unique(active, return_counts=True)
    return tuple(
        (int(value), int(count))
        for value, count in zip(values.tolist(), counts.tolist())
    )


def _maximum_active(weights, epsilon):
    if not weights.size:
        return 0
    return int(np.max(np.count_nonzero(weights > epsilon, axis=1)))


def _format_row(row, influences, epsilon):
    columns = np.where(row > epsilon)[0].astype(np.int32)
    return " | ".join(
        "{}={:.8f}".format(
            influences[int(column)].split("|")[-1],
            float(row[int(column)]),
        )
        for column in columns.tolist()
    )


def _print_row(vertex_id, region_result, smooth_result):
    vertex_id = int(vertex_id)
    if vertex_id < 0 or vertex_id >= smooth_result.vertex_count:
        return
    owner_index = int(region_result.owner_indices[vertex_id])
    owner_name = region_result.influences[owner_index].split("|")[-1]
    epsilon = smooth_result.options.weight_epsilon
    builtins.print(
        "\nvtx[{}] | Region owner={}".format(vertex_id, owner_name)
    )
    builtins.print(
        "  raw:   {}".format(
            _format_row(
                smooth_result.diffusion_result.weights[vertex_id],
                region_result.influences,
                epsilon,
            )
        )
    )
    builtins.print(
        "  final: {}".format(
            _format_row(
                smooth_result.weights[vertex_id],
                region_result.influences,
                epsilon,
            )
        )
    )


def _print_report(region_result, smooth_result):
    raw = smooth_result.diffusion_result
    projection = smooth_result.projection_result
    final = smooth_result.validation_result
    epsilon = smooth_result.options.weight_epsilon

    builtins.print(
        "\n[AD Skin Tool v6.1 - Bind Smoothing Constraints Smoke]"
    )
    builtins.print("Mesh:", region_result.mesh_transform)
    builtins.print("Vertices:", smooth_result.vertex_count)
    builtins.print("Influences:", smooth_result.influence_count)
    builtins.print("Iterations:", smooth_result.options.iterations)
    builtins.print("Relaxation:", smooth_result.options.relaxation)
    builtins.print(
        "Requested Max Influences:",
        smooth_result.options.maximum_influences,
    )
    builtins.print(
        "Effective Max Influences:",
        smooth_result.effective_maximum_influences,
    )

    builtins.print("\nRaw diffusion:")
    builtins.print("  mixed vertices:", raw.mixed_vertex_count)
    builtins.print(
        "  maximum active influences:",
        _maximum_active(raw.weights, epsilon),
    )
    builtins.print(
        "  dominant owner changed:",
        raw.dominant_owner_changed_vertex_count,
    )
    builtins.print(
        "  active histogram:",
        _active_histogram(raw.weights, epsilon),
    )

    builtins.print("\nFinal Max Influence projection:")
    builtins.print(
        "  projected vertices:",
        projection.pruned_vertex_count,
    )
    builtins.print(
        "  discarded entries:",
        projection.discarded_entry_count,
    )
    builtins.print(
        "  discarded weight sum:",
        projection.discarded_weight_sum,
    )
    builtins.print(
        "  maximum discarded weight:",
        projection.maximum_discarded_weight,
    )
    builtins.print(
        "  owner reinsertions:",
        projection.owner_reinserted_vertex_count,
    )
    builtins.print(
        "  cutoff-tie vertices:",
        projection.cutoff_tie_vertex_count,
    )
    builtins.print(
        "  final dominant owner changed:",
        final.dominant_owner_changed_vertex_count,
    )
    builtins.print(
        "  maximum row-sum error:",
        final.maximum_row_sum_error,
    )
    builtins.print(
        "  final active histogram:",
        final.active_influence_histogram,
    )

    if projection.cutoff_tie_vertex_ids:
        builtins.print(
            "\nNOTICE: equal weights crossed the Max Influence cutoff."
        )
        builtins.print(
            "First IDs:",
            builtins.list(
                projection.cutoff_tie_vertex_ids[:PRINT_VERTEX_LIMIT]
            ),
        )
        builtins.print(
            "Column order was used only to keep this smoke test repeatable; "
            "production tie-breaking is not approved yet."
        )

    if final.dominant_owner_changed_vertex_ids:
        builtins.print(
            "\nNOTICE: Region owners remain present but are not dominant on "
            "{} vertices.".format(final.dominant_owner_changed_vertex_count)
        )
        builtins.print(
            "First IDs:",
            builtins.list(
                final.dominant_owner_changed_vertex_ids[:PRINT_VERTEX_LIMIT]
            ),
        )

    ids = []
    for vertex_id in INSPECT_VERTEX_IDS:
        if int(vertex_id) not in ids:
            ids.append(int(vertex_id))
    for vertex_id in projection.pruned_vertex_ids:
        if int(vertex_id) not in ids:
            ids.append(int(vertex_id))
        if builtins.len(ids) >= PRINT_VERTEX_LIMIT:
            break
    builtins.print("\nRaw versus final rows:")
    for vertex_id in ids:
        _print_row(vertex_id, region_result, smooth_result)


def _select_projected(mesh_transform, smooth_result):
    if not SELECT_PROJECTED_VERTICES:
        return
    vertex_ids = smooth_result.projection_result.pruned_vertex_ids
    if not vertex_ids:
        return
    cmds.select(
        [
            "{}.vtx[{}]".format(mesh_transform, int(vertex_id))
            for vertex_id in vertex_ids
        ],
        replace=True,
    )


mesh, joints = _loaded_context()
region_result = region_solver.solve_region_ownership(mesh=mesh, joints=joints)
adjacency = connectivity.build_vertex_adjacency(region_result.mesh_shape)
smooth_result = smoothing_solver.solve_bind_smoothing(
    owner_indices=region_result.owner_indices,
    adjacency=adjacency,
    influence_count=region_result.influence_count,
    options=options.BindSmoothingOptions(
        iterations=SMOOTH_ITERATIONS,
        relaxation=RELAXATION,
        maximum_influences=MAXIMUM_INFLUENCES,
        weight_epsilon=WEIGHT_EPSILON,
    ),
)

builtins.AD_SKIN_V61_REGION_RESULT = region_result
builtins.AD_SKIN_V61_BIND_SMOOTHING_RESULT = smooth_result
_print_report(region_result, smooth_result)
_select_projected(region_result.mesh_transform, smooth_result)

builtins.print("\nNo skinCluster was created or modified.")
builtins.print(
    "Saved result as builtins.AD_SKIN_V61_BIND_SMOOTHING_RESULT"
)
