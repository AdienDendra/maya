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
    Create a skinCluster and assign every mesh vertex to its closest joint.

    Distance calculation is performed by this tool, not by Maya's automatic
    skin binding:

        closest = argmin(
            squared_world_distance(vertex, joint)
        )

    Each vertex receives exactly:
        closest joint = 1.0
        all other joints = 0.0

    Exact-distance ties are resolved deterministically by influence-list order.
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

    if len(joints) < 2:
        raise RuntimeError(
            "Closest Object Bind requires at least two joints.\n\n"
            "Select joints in Maya and click Add Selected Joints."
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

            # Always use the actual influence order stored by skinCluster.
            influence_names = adapter.influences()

            if len(influence_names) < 2:
                raise RuntimeError(
                    "The created skinCluster contains fewer than two influences."
                )

            vertex_positions = get_vertex_positions(
                mesh_shape,
                vertex_ids,
            )

            joint_positions = get_world_positions(
                influence_names,
            )

            if vertex_positions.shape != (vertex_count, 3):
                raise RuntimeError(
                    "Unexpected vertex-position matrix shape: "
                    f"{vertex_positions.shape}"
                )

            if joint_positions.shape != (len(influence_names), 3):
                raise RuntimeError(
                    "Unexpected joint-position matrix shape: "
                    f"{joint_positions.shape}"
                )

            # Shape:
            # delta[v, j, xyz]
            delta = (
                vertex_positions[:, np.newaxis, :]
                - joint_positions[np.newaxis, :, :]
            )

            # Squared Euclidean distance.
            # No square root is required because argmin remains identical.
            distances_squared = np.einsum(
                "vji,vji->vj",
                delta,
                delta,
            )

            closest_columns = np.argmin(
                distances_squared,
                axis=1,
            ).astype(np.int32)

            # Build a strict one-hot skin weight matrix.
            weight_matrix = np.zeros(
                (vertex_count, len(influence_names)),
                dtype=np.float64,
            )

            weight_matrix[
                np.arange(vertex_count),
                closest_columns,
            ] = 1.0

            # Matrix is already normalized, so Maya normalization is disabled.
            adapter.set_weights(
                vertex_ids=vertex_ids,
                weights=weight_matrix,
                normalize=False,
            )

            # Read back from Maya instead of trusting only the source matrix.
            result_data = adapter.get_weights(vertex_ids)

            non_zero_counts = np.count_nonzero(
                result_data.weights > 1e-8,
                axis=1,
            )

            row_sums = result_data.weights.sum(axis=1)

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

            if invalid_influence_rows.size:
                raise RuntimeError(
                    "Custom Closest validation failed.\n\n"
                    f"{invalid_influence_rows.size} vertices do not have "
                    "exactly one influence."
                )

            if invalid_sum_rows.size:
                raise RuntimeError(
                    "Custom Closest validation failed.\n\n"
                    f"{invalid_sum_rows.size} vertex weight rows do not "
                    "sum to 1.0."
                )

            winning_columns = np.argmax(
                result_data.weights,
                axis=1,
            )

            assignment_counts = {
                influence: int(
                    np.count_nonzero(
                        winning_columns == column_index
                    )
                )
                for column_index, influence
                in enumerate(result_data.influences)
            }

            return ClosestObjectBindResult(
                skin_cluster=adapter.skin_cluster,
                mesh_transform=mesh_transform,
                vertex_count=vertex_count,
                influence_count=len(result_data.influences),
                assignment_counts=assignment_counts,
            )

    except Exception:
        # Avoid leaving a partial/broken skinCluster behind.
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
                try:
                    cmds.delete(adapter.skin_cluster)
                except Exception:
                    pass

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