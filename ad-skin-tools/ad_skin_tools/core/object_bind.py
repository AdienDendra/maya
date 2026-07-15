from dataclasses import dataclass
from typing import Dict, List, Optional

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import get_vertex_count
from ad_skin_tools.core.native_bind import (
    NativeBindOptions,
    create_native_bind,
)
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk

np = ensure_numpy()


@dataclass(frozen=True)
class NativeObjectBindResult:
    skin_cluster: str
    mesh_transform: str
    method: str
    vertex_count: int
    influence_count: int
    max_influences_requested: int
    average_influence_count: float
    maximum_influence_count_used: int
    dominant_assignment_counts: Dict[str, int]


def bind_object_native(
    mesh_shape: str,
    mesh_transform: str,
    joints: List[str],
    options: Optional[NativeBindOptions] = None,
) -> NativeObjectBindResult:
    """
    Create and validate a Maya Closest Distance initial object bind.

    Maya calculates the weights. MFnSkinCluster reads the stored result back
    so AD Skin Tool can validate data integrity before reporting completion.
    This validation does not judge deformation or anatomical quality.
    """
    if not mesh_shape or not cmds.objExists(mesh_shape):
        raise RuntimeError(
            "The loaded mesh shape no longer exists. Load the mesh again."
        )

    if not mesh_transform or not cmds.objExists(mesh_transform):
        raise RuntimeError(
            "The loaded mesh transform no longer exists. Load the mesh again."
        )

    bind_options = options or NativeBindOptions()
    original_selection = cmds.ls(
        selection=True,
        long=True,
        flatten=True,
    ) or []

    created_skin_cluster = None

    try:
        with undo_chunk("AD Skin Closest Distance Bind"):
            native_result = create_native_bind(
                mesh_transform=mesh_transform,
                joints=joints,
                options=bind_options,
            )
            created_skin_cluster = native_result.skin_cluster

            adapter = SkinClusterAdapter(
                skin_cluster=native_result.skin_cluster,
                mesh_shape=mesh_shape,
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
            stored_data = adapter.get_weights(vertex_ids)
            stored_weights = stored_data.weights

            _validate_stored_weights(
                weights=stored_weights,
                vertex_count=vertex_count,
                influence_count=len(stored_data.influences),
                max_influences=int(bind_options.max_influences),
                obey_max_influences=bool(
                    bind_options.obey_max_influences
                ),
            )

            influence_counts = np.count_nonzero(
                stored_weights > 1e-8,
                axis=1,
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

            return NativeObjectBindResult(
                skin_cluster=native_result.skin_cluster,
                mesh_transform=mesh_transform,
                method=native_result.method,
                vertex_count=vertex_count,
                influence_count=len(stored_data.influences),
                max_influences_requested=(
                    native_result.max_influences
                ),
                average_influence_count=float(
                    influence_counts.mean()
                ),
                maximum_influence_count_used=int(
                    influence_counts.max()
                ),
                dominant_assignment_counts=assignment_counts,
            )

    except Exception:
        _remove_created_skin_cluster(created_skin_cluster)
        raise

    finally:
        _restore_scene_selection(original_selection)


def _validate_stored_weights(
    weights,
    vertex_count: int,
    influence_count: int,
    max_influences: int,
    obey_max_influences: bool,
) -> None:
    expected_shape = (
        vertex_count,
        influence_count,
    )

    if weights.shape != expected_shape:
        raise RuntimeError(
            "Unexpected Closest Distance weight matrix shape.\n\n"
            f"Expected: {expected_shape}\n"
            f"Received: {weights.shape}"
        )

    if not np.all(np.isfinite(weights)):
        invalid_row_count = int(
            np.count_nonzero(
                ~np.isfinite(weights).all(axis=1)
            )
        )
        raise RuntimeError(
            "Maya Closest Distance produced non-finite weights.\n\n"
            f"Invalid rows: {invalid_row_count}"
        )

    if np.any(weights < -1e-8):
        negative_row_count = int(
            np.count_nonzero(
                (weights < -1e-8).any(axis=1)
            )
        )
        raise RuntimeError(
            "Maya Closest Distance produced negative weights.\n\n"
            f"Invalid rows: {negative_row_count}"
        )

    row_sums = weights.sum(axis=1)
    invalid_sum_rows = np.where(
        ~np.isclose(
            row_sums,
            1.0,
            atol=1e-5,
        )
    )[0]

    if invalid_sum_rows.size:
        raise RuntimeError(
            "Closest Distance data validation failed.\n\n"
            f"{invalid_sum_rows.size} vertex rows do not sum to 1.0."
        )

    influence_counts = np.count_nonzero(
        weights > 1e-8,
        axis=1,
    )
    empty_rows = np.where(
        influence_counts < 1
    )[0]

    if empty_rows.size:
        raise RuntimeError(
            "Closest Distance data validation failed.\n\n"
            f"{empty_rows.size} vertices have no influence."
        )

    if obey_max_influences:
        excessive_rows = np.where(
            influence_counts > max_influences
        )[0]

        if excessive_rows.size:
            raise RuntimeError(
                "Closest Distance data validation failed.\n\n"
                f"{excessive_rows.size} vertices exceed the requested "
                f"maximum of {max_influences} influences."
            )


def _remove_created_skin_cluster(skin_cluster) -> None:
    if not skin_cluster or not cmds.objExists(skin_cluster):
        return

    try:
        cmds.skinCluster(
            skin_cluster,
            edit=True,
            unbind=True,
        )
        return
    except Exception:
        pass

    try:
        cmds.delete(skin_cluster)
    except Exception:
        pass


def _restore_scene_selection(items) -> None:
    try:
        cmds.select(clear=True)

        if items:
            cmds.select(
                items,
                replace=True,
            )
    except Exception:
        # Scene selection restoration must not invalidate a successful bind.
        pass
