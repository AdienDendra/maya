import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.constrained_bind import (
    ConstrainedClosestOptions,
    ConstrainedClosestResult,
    bind_object_constrained_closest as _bind_object_constrained_closest,
)
from ad_skin_tools.core.influence import (
    resolve_influence_indices,
    resolve_influence_names,
)
from ad_skin_tools.core.mesh import (
    get_all_vertex_neighbors,
    get_vertex_count,
    get_vertex_positions,
    get_world_positions,
)
from ad_skin_tools.core.native_bind import NativeBindOptions
from ad_skin_tools.core.object_bind import (
    NativeObjectBindResult,
    bind_object_native,
)
from ad_skin_tools.core.selection import get_component_selection
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.core.weights import (
    blend_by_falloff,
    build_closest_target,
    build_even_target,
    normalize_rows,
)

np = ensure_numpy()


def bind_object_constrained_closest(
    mesh_shape: str,
    mesh_transform: str,
    joints: list[str],
    root_back_fraction: float = 0.05,
    terminal_back_fraction: float = 0.35,
    normal_penalty_strength: float = 2.0,
) -> ConstrainedClosestResult:
    """
    Run the v2.5 constrained hard-ownership experiment.

    Every vertex receives exactly one owner. The solver uses parent-owned
    joint segments, a root half-space constraint, and a one-sided vertex
    normal penalty. No smoothing or soft weighting is applied yet.
    """
    options = ConstrainedClosestOptions(
        root_back_fraction=float(root_back_fraction),
        terminal_back_fraction=float(terminal_back_fraction),
        normal_penalty_strength=float(normal_penalty_strength),
        endpoint_inset=0.001,
        chunk_size=4096,
    )

    return _bind_object_constrained_closest(
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        joints=joints,
        options=options,
    )


def bind_object_closest_distance(
    mesh_shape: str,
    mesh_transform: str,
    joints: list[str],
    max_influences: int = 5,
    dropoff_rate: float = 4.0,
) -> NativeObjectBindResult:
    """
    Create an initial bind using Maya Closest Distance only.

    Retained as a baseline comparison while the constrained hard-ownership
    solver is evaluated. Maya owns this weight calculation through
    skinCluster(bindMethod=0).
    """
    options = NativeBindOptions(
        max_influences=int(max_influences),
        obey_max_influences=True,
        normalize_weights=1,
        skin_method=0,
        dropoff_rate=float(dropoff_rate),
    )

    return bind_object_native(
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        joints=joints,
        options=options,
    )


def flood_even(
    selected_influences: list[str],
    strength: float = 1.0,
) -> None:
    """
    Set selected vertices so selected influences share weight evenly.

    Soft selection controls how strongly each vertex is affected.
    """
    with undo_chunk("AD Skin Flood Even"):
        component_selection = get_component_selection()
        adapter = SkinClusterAdapter.from_mesh(
            component_selection.mesh_shape
        )
        skin_data = adapter.get_weights(
            component_selection.vertex_ids
        )

        influence_indices = resolve_influence_indices(
            skin_data.influences,
            selected_influences,
        )

        target = build_even_target(
            old_weights=skin_data.weights,
            influence_indices=influence_indices,
        )

        final_weights = blend_by_falloff(
            old_weights=skin_data.weights,
            target_weights=target,
            falloff=component_selection.falloff,
            strength=strength,
        )

        final_weights = normalize_rows(final_weights)

        adapter.set_weights(
            vertex_ids=component_selection.vertex_ids,
            weights=final_weights,
            normalize=True,
        )


def flood_closest(
    selected_influences: list[str],
    strength: float = 1.0,
) -> None:
    """
    Give each selected vertex to its closest selected influence pivot.

    This selected-vertex editing operation remains separate from the initial
    constrained bind and will be refined independently.
    """
    with undo_chunk("AD Skin Flood Closest"):
        component_selection = get_component_selection()
        adapter = SkinClusterAdapter.from_mesh(
            component_selection.mesh_shape
        )
        skin_data = adapter.get_weights(
            component_selection.vertex_ids
        )

        influence_indices = resolve_influence_indices(
            skin_data.influences,
            selected_influences,
        )

        influence_names = resolve_influence_names(
            skin_data.influences,
            selected_influences,
        )

        vertex_positions = get_vertex_positions(
            component_selection.mesh_shape,
            component_selection.vertex_ids,
        )
        influence_positions = get_world_positions(
            influence_names
        )

        distances = np.linalg.norm(
            vertex_positions[:, np.newaxis, :]
            - influence_positions[np.newaxis, :, :],
            axis=2,
        )

        closest_local_indices = np.argmin(
            distances,
            axis=1,
        )
        closest_global_indices = influence_indices[
            closest_local_indices
        ]

        target = build_closest_target(
            old_weights=skin_data.weights,
            closest_influence_indices=closest_global_indices,
        )

        final_weights = blend_by_falloff(
            old_weights=skin_data.weights,
            target_weights=target,
            falloff=component_selection.falloff,
            strength=strength,
        )

        final_weights = normalize_rows(final_weights)

        adapter.set_weights(
            vertex_ids=component_selection.vertex_ids,
            weights=final_weights,
            normalize=True,
        )


def normalize_selected() -> None:
    """Normalize selected vertices only."""
    with undo_chunk("AD Skin Normalize Selected"):
        component_selection = get_component_selection()
        adapter = SkinClusterAdapter.from_mesh(
            component_selection.mesh_shape
        )
        skin_data = adapter.get_weights(
            component_selection.vertex_ids
        )

        final_weights = normalize_rows(
            skin_data.weights
        )

        adapter.set_weights(
            vertex_ids=component_selection.vertex_ids,
            weights=final_weights,
            normalize=True,
        )


def smooth_selected(
    iterations: int = 1,
    strength: float = 1.0,
) -> None:
    """
    Smooth selected vertices using connected vertex weights.

    The full mesh weight matrix is read because selected vertices may need
    neighbor data from outside the current component selection.
    """
    with undo_chunk("AD Skin Smooth Selected"):
        component_selection = get_component_selection()
        adapter = SkinClusterAdapter.from_mesh(
            component_selection.mesh_shape
        )

        vertex_count = get_vertex_count(
            component_selection.mesh_shape
        )
        all_vertex_ids = np.arange(
            vertex_count,
            dtype=np.int32,
        )

        full_skin_data = adapter.get_weights(
            all_vertex_ids
        )
        result = full_skin_data.weights.copy()

        neighbors_by_vertex = get_all_vertex_neighbors(
            component_selection.mesh_shape
        )
        selected_ids = component_selection.vertex_ids.astype(
            np.int32
        )

        for _ in range(int(iterations)):
            previous = result.copy()

            for vertex_id in selected_ids:
                neighbors = neighbors_by_vertex[
                    int(vertex_id)
                ]

                if not neighbors:
                    continue

                result[int(vertex_id)] = previous[
                    neighbors
                ].mean(axis=0)

        selected_target = result[selected_ids]
        selected_old = full_skin_data.weights[
            selected_ids
        ]

        final_weights = blend_by_falloff(
            old_weights=selected_old,
            target_weights=selected_target,
            falloff=component_selection.falloff,
            strength=strength,
        )

        final_weights = normalize_rows(final_weights)

        adapter.set_weights(
            vertex_ids=selected_ids,
            weights=final_weights,
            normalize=True,
        )


def show_done_message(message: str) -> None:
    cmds.inViewMessage(
        assistMessage=message,
        position="topCenter",
        fade=True,
    )
