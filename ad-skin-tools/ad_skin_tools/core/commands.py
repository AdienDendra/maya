import maya.cmds as cmds
from dataclasses import dataclass
from typing import Dict

from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.core.selection import get_component_selection
from ad_skin_tools.core.compat import ensure_numpy
np = ensure_numpy()

from ad_skin_tools.core.skin_cluster import (
    SkinClusterAdapter,
    SkinClusterError,
    create_closest_skin_cluster,
    find_skin_cluster,
)

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

@dataclass(frozen=True)
class ClosestObjectBindResult:
    skin_cluster: str
    mesh_transform: str
    vertex_count: int
    influence_count: int
    assignment_counts: Dict[str, int]

def bind_object_closest(
    mesh_shape: str,
    mesh_transform: str,
    joints: list[str],
) -> ClosestObjectBindResult:
    """
    QC-2 initial Closest bind.

    This command is only valid when the loaded mesh has no skinCluster.

    All joints passed to this function are used. Selection in the Maya
    viewport does not determine which joints are included.
    """
    if not mesh_shape or not cmds.objExists(mesh_shape):
        raise RuntimeError(
            "The loaded mesh shape no longer exists. "
            "Load the mesh again."
        )

    if not mesh_transform or not cmds.objExists(mesh_transform):
        raise RuntimeError(
            "The loaded mesh transform no longer exists. "
            "Load the mesh again."
        )

    existing_skin = find_skin_cluster(
        mesh_shape,
        required=False,
    )

    if existing_skin:
        raise RuntimeError(
            "This object already has skin weights.\n\n"
            "Object-wide Closest is blocked to prevent accidental "
            "redistribution of existing skin weights.\n\n"
            "An already-skinned mesh must be edited through vertex selection."
        )

    if not joints:
        raise RuntimeError(
            "No joints are available in the window list.\n\n"
            "Select joints in Maya and click Add Selected Joints first."
        )

    original_selection = cmds.ls(
        selection=True,
        long=True,
        flatten=True,
    ) or []

    adapter = None

    try:
        with undo_chunk("AD Skin Initial Closest Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=joints,
            )

            vertex_count = get_vertex_count(mesh_shape)

            if vertex_count <= 0:
                raise RuntimeError(
                    "The loaded mesh has no vertices."
                )

            vertex_ids = np.arange(
                vertex_count,
                dtype=np.int32,
            )

            skin_data = adapter.get_weights(vertex_ids)

            # QC validation:
            # every vertex must have exactly one non-zero influence and
            # every weight row must sum to 1.0.
            non_zero_counts = np.count_nonzero(
                skin_data.weights > 1e-8,
                axis=1,
            )

            row_sums = skin_data.weights.sum(axis=1)

            invalid_influence_rows = np.where(
                non_zero_counts != 1
            )[0]

            invalid_sum_rows = np.where(
                ~np.isclose(
                    row_sums,
                    1.0,
                    atol=1e-6,
                )
            )[0]

            if (
                invalid_influence_rows.size > 0
                or invalid_sum_rows.size > 0
            ):
                raise RuntimeError(
                    "Closest bind validation failed.\n\n"
                    f"Vertices without exactly one influence: "
                    f"{invalid_influence_rows.size}\n"
                    f"Vertices whose weights do not sum to 1.0: "
                    f"{invalid_sum_rows.size}"
                )

            winning_columns = np.argmax(
                skin_data.weights,
                axis=1,
            )

            assignment_counts = {}

            for column_index, influence in enumerate(
                skin_data.influences
            ):
                assignment_counts[influence] = int(
                    np.count_nonzero(
                        winning_columns == column_index
                    )
                )

            return ClosestObjectBindResult(
                skin_cluster=adapter.skin_cluster,
                mesh_transform=mesh_transform,
                vertex_count=vertex_count,
                influence_count=len(skin_data.influences),
                assignment_counts=assignment_counts,
            )

    except Exception:
        # Prevent a partially-created skinCluster from remaining after
        # validation or API failure.
        if (
            adapter is not None
            and cmds.objExists(adapter.skin_cluster)
        ):
            try:
                cmds.skinCluster(
                    adapter.skin_cluster,
                    edit=True,
                    unbind=True,
                )
            except Exception:
                cmds.delete(adapter.skin_cluster)

        raise

    finally:
        _restore_scene_selection(original_selection)

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

def _restore_scene_selection(items):
    """
    Restore the artist's Maya selection after the bind operation.
    """
    try:
        cmds.select(clear=True)

        if items:
            cmds.select(
                items,
                replace=True,
            )

    except Exception:
        # A selected scene item may have been deleted or renamed.
        # Selection restoration must not invalidate a successful bind.
        pass