"""Maya-style Replace 1.0 flood for selected mesh components.

This operation is deliberately separate from Region Ownership. Region solves an
initial unskinned object; component flood is an explicit artist override on an
existing skinCluster. Only selected vertices are written.
"""

from dataclasses import dataclass
from typing import Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.component_selection import (
    collect_selected_mesh_vertices,
)
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk


np = ensure_numpy()


@dataclass(frozen=True)
class ComponentFloodResult:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    target_joint: str
    vertex_ids: Tuple[int, ...]
    influence_count: int
    influence_added: bool
    source_component_count: int
    ignored_component_count: int

    @property
    def vertex_count(self) -> int:
        return len(self.vertex_ids)


def flood_selected_components_to_joint(
    mesh_shape: str,
    mesh_transform: str,
    target_joint: str,
) -> ComponentFloodResult:
    """Replace selected component weights with one exact target influence.

    Equivalent artist-facing result:

        Edit Influences > Add Influence (when missing)
        Paint Operation: Replace
        Value: 1.0
        Flood

    The Paint Skin Weights context is not invoked. MFnSkinCluster writes only
    the selected vertex rows, so every unselected vertex remains untouched.
    """

    selection_before = cmds.ls(selection=True, long=True) or []
    scope = collect_selected_mesh_vertices(mesh_shape, mesh_transform)
    if not scope.vertex_ids:
        raise RuntimeError(
            "Select vertices, edges, or faces on the loaded mesh.\n\n"
            "Components from other meshes are ignored."
        )

    resolved_joint = _resolve_joint(target_joint)
    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)
    influence_added = False

    try:
        with undo_chunk("AD Skin Tool Component Flood"):
            if resolved_joint not in adapter.influences():
                _add_influence(
                    skin_cluster=adapter.skin_cluster,
                    joint=resolved_joint,
                )
                influence_added = True
                adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)

            vertex_ids = np.asarray(scope.vertex_ids, dtype=np.int32)
            influences = tuple(adapter.influences())
            try:
                target_column = influences.index(resolved_joint)
            except ValueError:
                raise RuntimeError(
                    "Target joint was not found in the skinCluster after Add "
                    "Influence:\n{}".format(resolved_joint)
                )

            weights = np.zeros(
                (len(vertex_ids), len(influences)),
                dtype=np.float64,
            )
            weights[:, int(target_column)] = 1.0
            adapter.set_weights(vertex_ids, weights, normalize=False)
            _validate_component_flood(
                adapter=adapter,
                vertex_ids=vertex_ids,
                target_joint=resolved_joint,
            )
    finally:
        _restore_selection(selection_before)

    return ComponentFloodResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=scope.mesh_shape,
        mesh_transform=scope.mesh_transform,
        target_joint=resolved_joint,
        vertex_ids=scope.vertex_ids,
        influence_count=len(adapter.influences()),
        influence_added=influence_added,
        source_component_count=scope.source_component_count,
        ignored_component_count=scope.ignored_component_count,
    )


def print_component_flood_report(result: ComponentFloodResult) -> None:
    print("\n[AD Skin Tool v4.0 - Component Flood]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Target influence:", result.target_joint)
    print("Selected vertices:", result.vertex_count)
    print("Source components:", result.source_component_count)
    print("Ignored components:", result.ignored_component_count)
    print("Influence added:", result.influence_added)
    print("SkinCluster influences:", result.influence_count)
    print("Result: target=1.0, every other influence=0.0 on selected vertices")


def _resolve_joint(joint: str) -> str:
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Target joint does not exist:\n{}".format(joint))
    return matches[0]


def _add_influence(skin_cluster: str, joint: str) -> None:
    cmds.skinCluster(
        skin_cluster,
        edit=True,
        addInfluence=joint,
        weight=0.0,
    )


def _validate_component_flood(
    adapter: SkinClusterAdapter,
    vertex_ids,
    target_joint: str,
) -> None:
    stored = adapter.get_weights(vertex_ids)
    influences = tuple(stored.influences)
    if target_joint not in influences:
        raise RuntimeError(
            "Stored skinCluster data is missing the target influence:\n{}".format(
                target_joint
            )
        )

    weights = np.asarray(stored.weights, dtype=np.float64)
    target_column = influences.index(target_joint)
    target_values = weights[:, target_column]
    non_target = np.delete(weights, target_column, axis=1)

    epsilon = float(np.finfo(np.float64).eps)
    error_bound = epsilon * max(1, weights.shape[1])
    bad_target_rows = np.where(np.abs(target_values - 1.0) > error_bound)[0]
    if bad_target_rows.size:
        bad_vertex_ids = vertex_ids[bad_target_rows[:20]].tolist()
        raise RuntimeError(
            "Component Flood did not store target weight 1.0. "
            "First vertex IDs: {}".format(bad_vertex_ids)
        )

    if non_target.size:
        bad_other_rows = np.where(
            np.any(np.abs(non_target) > error_bound, axis=1)
        )[0]
        if bad_other_rows.size:
            bad_vertex_ids = vertex_ids[bad_other_rows[:20]].tolist()
            raise RuntimeError(
                "Component Flood left non-target weights on selected vertices. "
                "First vertex IDs: {}".format(bad_vertex_ids)
            )


def _restore_selection(selection_before) -> None:
    cmds.select(clear=True)
    if not selection_before:
        return
    try:
        cmds.select(selection_before, replace=True)
    except Exception:
        # Selection restoration must never hide the result of a successful write.
        pass
