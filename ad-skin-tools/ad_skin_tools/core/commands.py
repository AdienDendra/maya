import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
np = ensure_numpy()

from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.core.selection import get_component_selection
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.influence import (
    resolve_influence_indices,
    resolve_influence_names,
)
from ad_skin_tools.core.mesh import (
    get_vertex_positions,
    get_world_positions,
    get_vertex_count,
    get_all_vertex_neighbors,
)
from ad_skin_tools.core.weights import (
    normalize_rows,
    blend_by_falloff,
    build_even_target,
    build_closest_target,
)


def flood_even(selected_influences: list[str], strength: float = 1.0) -> None:
    """
    Set selected vertices so selected influences share weight evenly.

    Soft selection controls how strongly each vertex is affected.
    """
    with undo_chunk("AD Skin Flood Even"):
        component_selection = get_component_selection()
        adapter = SkinClusterAdapter.from_mesh(component_selection.mesh_shape)
        skin_data = adapter.get_weights(component_selection.vertex_ids)

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


def flood_closest(selected_influences: list[str], strength: float = 1.0) -> None:
    """
    For each selected vertex:
    - find closest selected influence in world space
    - give that influence 1.0 target weight
    - blend by soft selection falloff
    """
    with undo_chunk("AD Skin Flood Closest"):
        component_selection = get_component_selection()
        adapter = SkinClusterAdapter.from_mesh(component_selection.mesh_shape)
        skin_data = adapter.get_weights(component_selection.vertex_ids)

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

        influence_positions = get_world_positions(influence_names)

        distances = np.linalg.norm(
            vertex_positions[:, np.newaxis, :] - influence_positions[np.newaxis, :, :],
            axis=2,
        )

        closest_local_indices = np.argmin(distances, axis=1)
        closest_global_indices = influence_indices[closest_local_indices]

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
    """
    Normalize selected vertices only.
    """
    with undo_chunk("AD Skin Normalize Selected"):
        component_selection = get_component_selection()
        adapter = SkinClusterAdapter.from_mesh(component_selection.mesh_shape)
        skin_data = adapter.get_weights(component_selection.vertex_ids)

        final_weights = normalize_rows(skin_data.weights)

        adapter.set_weights(
            vertex_ids=component_selection.vertex_ids,
            weights=final_weights,
            normalize=True,
        )


def smooth_selected(iterations: int = 1, strength: float = 1.0) -> None:
    """
    Smooth selected vertices using connected vertex weights.

    This reads the full mesh weight matrix because selected vertices need
    neighbor information from outside the current selection.
    """
    with undo_chunk("AD Skin Smooth Selected"):
        component_selection = get_component_selection()
        adapter = SkinClusterAdapter.from_mesh(component_selection.mesh_shape)

        vertex_count = get_vertex_count(component_selection.mesh_shape)
        all_vertex_ids = np.arange(vertex_count, dtype=np.int32)

        full_skin_data = adapter.get_weights(all_vertex_ids)
        result = full_skin_data.weights.copy()

        neighbors_by_vertex = get_all_vertex_neighbors(component_selection.mesh_shape)

        selected_ids = component_selection.vertex_ids.astype(np.int32)

        for _ in range(int(iterations)):
            previous = result.copy()

            for vertex_id in selected_ids:
                neighbors = neighbors_by_vertex[int(vertex_id)]

                if not neighbors:
                    continue

                result[int(vertex_id)] = previous[neighbors].mean(axis=0)

        selected_target = result[selected_ids]
        selected_old = full_skin_data.weights[selected_ids]

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