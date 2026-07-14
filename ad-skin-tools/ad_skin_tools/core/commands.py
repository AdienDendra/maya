import maya.cmds as cmds
from dataclasses import dataclass
from typing import Dict

from ad_skin_tools.core.segment_solver import solve_segment_weights
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
    get_weighted_vertex_neighbors,
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
    segment_count: int
    average_influence_count: float
    max_influence_count: int
    fallback_vertex_count: int

def bind_object_closest(
    mesh_shape: str,
    mesh_transform: str,
    joints: list[str],
    max_influences: int = 5,
) -> ClosestObjectBindResult:
    """
    QC-2.2 Segment Weighted Bind.

    Maya creates only the skinCluster container. All weight distribution is
    calculated by the custom segment solver.

    Solver behaviour:
    - distance is calculated against bone segments;
    - position along a segment controls endpoint blending;
    - radial distance controls local influence;
    - weights are pruned to max_influences;
    - every vertex row is normalized to 1.0.
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
            "Existing skin weights can only be edited through "
            "vertex selection."
        )

    if len(joints) < 2:
        raise RuntimeError(
            "Segment Weighted Bind requires at least two joints."
        )

    max_influences = int(max_influences)

    if max_influences < 1:
        raise RuntimeError(
            "Maximum influences must be at least 1."
        )

    original_selection = cmds.ls(
        selection=True,
        long=True,
        flatten=True,
    ) or []

    adapter = None

    try:
        with undo_chunk("AD Skin Segment Weighted Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=joints,
                max_influences=max_influences,
            )

            vertex_count = get_vertex_count(
                mesh_shape
            )

            if vertex_count <= 0:
                raise RuntimeError(
                    "The loaded mesh has no vertices."
                )

            vertex_ids = np.arange(
                vertex_count,
                dtype=np.int32,
            )

            influence_names = adapter.influences()

            if len(influence_names) < 2:
                raise RuntimeError(
                    "The created skinCluster contains fewer than two influences."
                )

            vertex_positions = get_vertex_positions(
                mesh_shape,
                vertex_ids,
            )
            
            # Build the weighted topology graph for the mesh currently being bound.
            # This must happen inside the operation because mesh_shape belongs to
            # the current loaded object and does not exist during module import.
            surface_adjacency = get_weighted_vertex_neighbors(
                mesh_shape
            )            
            
            solver_result = solve_segment_weights(
                vertex_positions=vertex_positions,
                joints=influence_names,
                max_influences=max_influences,
                radius_scale=1.25,
                prune_threshold=0.0001,
                chunk_size=4096,
                adjacency=surface_adjacency,
                distance_mode="surface",
            )

            expected_shape = (
                vertex_count,
                len(influence_names),
            )

            if solver_result.weights.shape != expected_shape:
                raise RuntimeError(
                    "Unexpected segment-solver weight matrix shape.\n\n"
                    f"Expected: {expected_shape}\n"
                    f"Received: {solver_result.weights.shape}"
                )

            adapter.set_weights(
                vertex_ids=vertex_ids,
                weights=solver_result.weights,
                normalize=False,
            )

            # Validate the values actually stored by Maya.
            stored_data = adapter.get_weights(
                vertex_ids
            )

            stored_weights = stored_data.weights

            if stored_weights.shape != expected_shape:
                raise RuntimeError(
                    "Unexpected stored skin weight matrix shape.\n\n"
                    f"Expected: {expected_shape}\n"
                    f"Received: {stored_weights.shape}"
                )

            if not np.all(
                np.isfinite(stored_weights)
            ):
                raise RuntimeError(
                    "Segment solver created non-finite weight values."
                )

            if np.any(stored_weights < -1e-8):
                raise RuntimeError(
                    "Segment solver created negative skin weights."
                )

            row_sums = stored_weights.sum(
                axis=1,
            )

            invalid_sum_rows = np.where(
                ~np.isclose(
                    row_sums,
                    1.0,
                    atol=1e-6,
                )
            )[0]

            if invalid_sum_rows.size:
                raise RuntimeError(
                    "Segment bind validation failed.\n\n"
                    f"{invalid_sum_rows.size} vertex rows do not sum to 1.0."
                )

            influence_counts = np.count_nonzero(
                stored_weights > 1e-8,
                axis=1,
            )

            empty_rows = np.where(
                influence_counts < 1
            )[0]

            if empty_rows.size:
                raise RuntimeError(
                    "Segment bind validation failed.\n\n"
                    f"{empty_rows.size} vertices have no weighted influence."
                )

            excessive_rows = np.where(
                influence_counts > max_influences
            )[0]

            if excessive_rows.size:
                raise RuntimeError(
                    "Segment bind validation failed.\n\n"
                    f"{excessive_rows.size} vertices exceed "
                    f"the maximum of {max_influences} influences."
                )

            dominant_columns = np.argmax(
                stored_weights,
                axis=1,
            )

            assignment_counts = {
                influence: int(
                    np.count_nonzero(
                        dominant_columns == column_index
                    )
                )
                for column_index, influence
                in enumerate(stored_data.influences)
            }

            return ClosestObjectBindResult(
                skin_cluster=adapter.skin_cluster,
                mesh_transform=mesh_transform,
                vertex_count=vertex_count,
                influence_count=len(stored_data.influences),
                assignment_counts=assignment_counts,
                segment_count=solver_result.segment_count,
                average_influence_count=solver_result.average_influence_count,
                max_influence_count=solver_result.max_influence_count,
                fallback_vertex_count=solver_result.fallback_vertex_count,
            )

    except Exception:
        # Never leave an incomplete or invalid skinCluster in the scene.
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
                    cmds.delete(
                        adapter.skin_cluster
                    )
                except Exception:
                    pass

        raise

    finally:
        _restore_scene_selection(
            original_selection
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