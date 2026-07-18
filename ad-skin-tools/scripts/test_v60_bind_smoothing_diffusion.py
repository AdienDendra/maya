"""UI-driven v6.0 smoke test for raw Bind Smoothing diffusion.

This diagnostic does not create or edit a skinCluster. It calculates the
existing production Region ownership, diffuses the one-hot rows in memory, and
selects the vertices that became mixed.

Workflow:
    1. Open AD Skin Tool.
    2. Load one unskinned polygon mesh.
    3. Add every intended bind joint to the UI list.
    4. Deploy, then execute this file in Maya's Script Editor.

Edit SMOOTH_ITERATIONS below to compare 0, 1, 2, ... up to 10.
"""

import builtins
import importlib

import maya.cmds as cmds
import numpy as np

from ad_skin_tools.bind_smoothing import diffusion
from ad_skin_tools.region import connectivity
from ad_skin_tools.region import solver as region_solver
from ad_skin_tools.ui import skin_operations


SMOOTH_ITERATIONS = 1
RELAXATION = 0.5
SELECT_MIXED_VERTICES = True
PRINT_VERTEX_LIMIT = 20


importlib.reload(diffusion)
importlib.reload(connectivity)
importlib.reload(region_solver)


def _loaded_unskinned_context():
    tool_window = skin_operations._TOOL_WINDOW
    if tool_window is None:
        raise builtins.RuntimeError(
            "AD Skin Tool UI is not installed. Open the tool before running "
            "the v6.0 Bind Smoothing smoke test."
        )

    tool_window._require_not_busy()
    tool_window._require_unskinned_mesh()

    state = tool_window._STATE
    joints = builtins.list(
        state.get(
            "joints",
            [],
        )
    )
    if builtins.len(joints) < 2:
        raise builtins.RuntimeError(
            "The v6.0 Bind Smoothing smoke test requires at least two "
            "joints in the UI list."
        )

    return (
        state["mesh_transform"],
        joints,
    )


def _print_report(
    region_result,
    smooth_result,
):
    builtins.print(
        "\n[AD Skin Tool v6.0 - Bind Smoothing Diffusion Smoke]"
    )
    builtins.print(
        "Mesh:",
        region_result.mesh_transform,
    )
    builtins.print(
        "Vertices:",
        smooth_result.vertex_count,
    )
    builtins.print(
        "Influences:",
        smooth_result.influence_count,
    )
    builtins.print(
        "Iterations:",
        smooth_result.iterations,
    )
    builtins.print(
        "Relaxation:",
        smooth_result.relaxation,
    )
    builtins.print(
        "Changed from hard one-hot:",
        smooth_result.changed_vertex_count,
    )
    builtins.print(
        "Mixed vertices:",
        smooth_result.mixed_vertex_count,
    )
    builtins.print(
        "Dominant owner changed:",
        smooth_result.dominant_owner_changed_vertex_count,
    )
    builtins.print(
        "Maximum row-sum error:",
        smooth_result.maximum_row_sum_error,
    )

    builtins.print(
        "\nPer-iteration expansion:"
    )
    if not smooth_result.iteration_changed_counts:
        builtins.print(
            "  iteration 0: exact Region one-hot weights"
        )
    else:
        for iteration_index, (
            changed_count,
            mixed_count,
        ) in enumerate(
            zip(
                smooth_result.iteration_changed_counts,
                smooth_result.iteration_mixed_counts,
            ),
            start=1,
        ):
            builtins.print(
                "  iteration {}: changed={} | mixed={}".format(
                    iteration_index,
                    changed_count,
                    mixed_count,
                )
            )

    builtins.print(
        "\nActive influences per vertex:"
    )
    for active_count, vertex_count in (
        smooth_result.active_influence_histogram
    ):
        builtins.print(
            "  {} active: {} vertices".format(
                active_count,
                vertex_count,
            )
        )

    if smooth_result.dominant_owner_changed_vertex_ids:
        builtins.print(
            "\nWARNING: raw unconstrained diffusion changed the dominant "
            "Region owner on {} vertices.".format(
                smooth_result.dominant_owner_changed_vertex_count
            )
        )
        builtins.print(
            "First IDs:",
            builtins.list(
                smooth_result.dominant_owner_changed_vertex_ids[
                    :PRINT_VERTEX_LIMIT
                ]
            ),
        )
        builtins.print(
            "This is diagnostic evidence for the owner-preservation "
            "constraint planned in the next smoke stage."
        )

    _print_mixed_rows(
        region_result=region_result,
        smooth_result=smooth_result,
    )


def _print_mixed_rows(
    region_result,
    smooth_result,
):
    mixed_ids = smooth_result.mixed_vertex_ids[
        :PRINT_VERTEX_LIMIT
    ]
    if not mixed_ids:
        builtins.print(
            "\nNo mixed rows to print."
        )
        return

    tolerance = (
        float(np.finfo(np.float64).eps)
        * max(
            1,
            smooth_result.influence_count,
        )
        * 32.0
    )

    builtins.print(
        "\nFirst {} mixed rows:".format(
            builtins.len(mixed_ids)
        )
    )
    for vertex_id in mixed_ids:
        row = smooth_result.weights[
            int(vertex_id)
        ]
        owner_index = int(
            smooth_result.owner_indices[
                int(vertex_id)
            ]
        )
        owner_joint = region_result.influences[
            owner_index
        ]
        active_columns = np.where(
            row > tolerance
        )[0].astype(np.int32)

        values = [
            "{}={:.6f}".format(
                region_result.influences[
                    int(column)
                ].split("|")[-1],
                float(
                    row[
                        int(column)
                    ]
                ),
            )
            for column in active_columns.tolist()
        ]
        builtins.print(
            "  vtx[{}] | Region owner={} | {}".format(
                int(vertex_id),
                owner_joint.split("|")[-1],
                " | ".join(values),
            )
        )


def _select_mixed_vertices(
    mesh_transform,
    smooth_result,
):
    if (
        not SELECT_MIXED_VERTICES
        or not smooth_result.mixed_vertex_ids
    ):
        return

    components = [
        "{}.vtx[{}]".format(
            mesh_transform,
            int(vertex_id),
        )
        for vertex_id
        in smooth_result.mixed_vertex_ids
    ]
    cmds.select(
        components,
        replace=True,
    )


mesh, joints = _loaded_unskinned_context()
region_result = region_solver.solve_region_ownership(
    mesh=mesh,
    joints=joints,
)
adjacency = connectivity.build_vertex_adjacency(
    region_result.mesh_shape
)
smooth_result = diffusion.diffuse_hard_ownership(
    owner_indices=region_result.owner_indices,
    adjacency=adjacency,
    influence_count=region_result.influence_count,
    iterations=SMOOTH_ITERATIONS,
    relaxation=RELAXATION,
)

builtins.AD_SKIN_V60_REGION_RESULT = region_result
builtins.AD_SKIN_V60_BIND_DIFFUSION_RESULT = smooth_result

_print_report(
    region_result=region_result,
    smooth_result=smooth_result,
)
_select_mixed_vertices(
    mesh_transform=region_result.mesh_transform,
    smooth_result=smooth_result,
)

builtins.print(
    "\nNo skinCluster was created or modified."
)
builtins.print(
    "Saved Region result as builtins.AD_SKIN_V60_REGION_RESULT"
)
builtins.print(
    "Saved diffusion result as "
    "builtins.AD_SKIN_V60_BIND_DIFFUSION_RESULT"
)
