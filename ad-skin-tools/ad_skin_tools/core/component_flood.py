"""Maya-style Replace 1.0 flood for selected mesh components.

Region Ownership handles the initial full-object bind. Component Flood is an
explicit artist override on an existing skinCluster. v4.1 respects Maya influence
locks: a locked target becomes a no-op, and selected vertices carrying weight from
any other locked influence are preserved.
"""

from dataclasses import dataclass
from typing import Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.component_selection import (
    collect_selected_mesh_vertices,
)
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk


np = ensure_numpy()


@dataclass(frozen=True)
class ComponentFloodResult:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    target_joint: str
    selected_vertex_ids: Tuple[int, ...]
    flooded_vertex_ids: Tuple[int, ...]
    protected_vertex_ids: Tuple[int, ...]
    locked_influences: Tuple[str, ...]
    influence_count: int
    influence_added: bool
    target_locked: bool
    source_component_count: int
    ignored_component_count: int

    @property
    def vertex_ids(self) -> Tuple[int, ...]:
        """Compatibility alias for the full selected component scope."""

        return self.selected_vertex_ids

    @property
    def vertex_count(self) -> int:
        return len(self.selected_vertex_ids)

    @property
    def flooded_vertex_count(self) -> int:
        return len(self.flooded_vertex_ids)

    @property
    def protected_vertex_count(self) -> int:
        return len(self.protected_vertex_ids)


def flood_selected_components_to_joint(
    mesh_shape: str,
    mesh_transform: str,
    target_joint: str,
    target_locked_override: bool = False,
) -> ComponentFloodResult:
    """Replace writable selected rows with one exact target influence.

    Equivalent artist-facing intent:

        Edit Influences > Add Influence (when missing)
        Paint Operation: Replace
        Value: 1.0
        Flood

    Lock semantics mirror Maya's artist workflow:

    - a locked target influence ignores the whole flood without raising;
    - vertices carrying any weight from another locked influence are protected;
    - only writable selected rows are sent to MFnSkinCluster;
    - every unselected row remains untouched.
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
    influences_before = tuple(adapter.influences())
    target_is_bound = resolved_joint in influences_before
    bound_locked = locked_influences(
        adapter.skin_cluster,
        influences_before,
    )
    target_locked = bool(target_locked_override) or (
        target_is_bound and resolved_joint in bound_locked
    )

    if target_locked:
        return ComponentFloodResult(
            skin_cluster=adapter.skin_cluster,
            mesh_shape=scope.mesh_shape,
            mesh_transform=scope.mesh_transform,
            target_joint=resolved_joint,
            selected_vertex_ids=scope.vertex_ids,
            flooded_vertex_ids=tuple(),
            protected_vertex_ids=scope.vertex_ids,
            locked_influences=bound_locked,
            influence_count=len(influences_before),
            influence_added=False,
            target_locked=True,
            source_component_count=scope.source_component_count,
            ignored_component_count=scope.ignored_component_count,
        )

    influence_added = False
    mutation_recorded = False
    selected_vertex_ids = np.asarray(scope.vertex_ids, dtype=np.int32)
    flooded_vertex_ids = np.empty(0, dtype=np.int32)
    protected_vertex_ids = np.empty(0, dtype=np.int32)
    active_locked_influences = tuple()

    try:
        try:
            with undo_chunk("AD Skin Tool Component Flood"):
                if not target_is_bound:
                    _add_influence(
                        skin_cluster=adapter.skin_cluster,
                        joint=resolved_joint,
                    )
                    influence_added = True
                    mutation_recorded = True
                    adapter = SkinClusterAdapter.from_mesh(scope.mesh_shape)

                influences = tuple(adapter.influences())
                try:
                    target_column = influences.index(resolved_joint)
                except ValueError:
                    raise RuntimeError(
                        "Target joint was not found in the skinCluster after Add "
                        "Influence:\n{}".format(resolved_joint)
                    )

                active_locked_influences = tuple(
                    joint
                    for joint in locked_influences(
                        adapter.skin_cluster,
                        influences,
                    )
                    if joint != resolved_joint
                )
                before = adapter.get_weights(selected_vertex_ids)
                before_weights = np.asarray(
                    before.weights,
                    dtype=np.float64,
                ).copy()
                protected_mask = _protected_vertex_mask(
                    weights=before_weights,
                    influences=tuple(before.influences),
                    locked=active_locked_influences,
                )
                protected_vertex_ids = selected_vertex_ids[protected_mask]
                flooded_vertex_ids = selected_vertex_ids[~protected_mask]

                if flooded_vertex_ids.size:
                    weights = np.zeros(
                        (len(flooded_vertex_ids), len(influences)),
                        dtype=np.float64,
                    )
                    weights[:, int(target_column)] = 1.0
                    adapter.set_weights(
                        flooded_vertex_ids,
                        weights,
                        normalize=False,
                    )
                    mutation_recorded = True

                _validate_component_flood(
                    adapter=adapter,
                    selected_vertex_ids=selected_vertex_ids,
                    flooded_vertex_ids=flooded_vertex_ids,
                    protected_vertex_ids=protected_vertex_ids,
                    protected_weights_before=before_weights[protected_mask],
                    target_joint=resolved_joint,
                )
        except Exception:
            if mutation_recorded:
                _undo_failed_flood()
            raise
    finally:
        _restore_selection(selection_before)

    return ComponentFloodResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=scope.mesh_shape,
        mesh_transform=scope.mesh_transform,
        target_joint=resolved_joint,
        selected_vertex_ids=scope.vertex_ids,
        flooded_vertex_ids=tuple(
            int(value) for value in flooded_vertex_ids.tolist()
        ),
        protected_vertex_ids=tuple(
            int(value) for value in protected_vertex_ids.tolist()
        ),
        locked_influences=active_locked_influences,
        influence_count=len(adapter.influences()),
        influence_added=influence_added,
        target_locked=False,
        source_component_count=scope.source_component_count,
        ignored_component_count=scope.ignored_component_count,
    )


def print_component_flood_report(result: ComponentFloodResult) -> None:
    print("\n[AD Skin Tool v4.1 - Component Flood]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Target influence:", result.target_joint)
    print("Selected vertices:", result.vertex_count)
    print("Flooded vertices:", result.flooded_vertex_count)
    print("Protected vertices:", result.protected_vertex_count)
    print("Locked influences:", len(result.locked_influences))
    print("Target locked:", result.target_locked)
    print("Source components:", result.source_component_count)
    print("Ignored components:", result.ignored_component_count)
    print("Influence added:", result.influence_added)
    print("SkinCluster influences:", result.influence_count)
    if result.target_locked:
        print("Result: ignored because the target influence is locked")
    else:
        print(
            "Result: writable selected vertices target=1.0; "
            "locked ownership preserved"
        )


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


def _protected_vertex_mask(
    weights,
    influences: Tuple[str, ...],
    locked: Tuple[str, ...],
):
    if not locked:
        return np.zeros(weights.shape[0], dtype=bool)

    column_by_joint = {
        joint: column
        for column, joint in enumerate(influences)
    }
    locked_columns = [
        column_by_joint[joint]
        for joint in locked
        if joint in column_by_joint
    ]
    if not locked_columns:
        return np.zeros(weights.shape[0], dtype=bool)

    locked_weights = np.abs(weights[:, locked_columns])
    numerical_zero = (
        float(np.finfo(np.float64).eps)
        * max(1, weights.shape[1])
        * 16.0
    )
    return np.any(locked_weights > numerical_zero, axis=1)


def _validate_component_flood(
    adapter: SkinClusterAdapter,
    selected_vertex_ids,
    flooded_vertex_ids,
    protected_vertex_ids,
    protected_weights_before,
    target_joint: str,
) -> None:
    if flooded_vertex_ids.size:
        stored = adapter.get_weights(flooded_vertex_ids)
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
        bad_target_rows = np.where(
            np.abs(target_values - 1.0) > error_bound
        )[0]
        if bad_target_rows.size:
            bad_vertex_ids = flooded_vertex_ids[
                bad_target_rows[:20]
            ].tolist()
            raise RuntimeError(
                "Component Flood did not store target weight 1.0. "
                "First vertex IDs: {}".format(bad_vertex_ids)
            )

        if non_target.size:
            bad_other_rows = np.where(
                np.any(np.abs(non_target) > error_bound, axis=1)
            )[0]
            if bad_other_rows.size:
                bad_vertex_ids = flooded_vertex_ids[
                    bad_other_rows[:20]
                ].tolist()
                raise RuntimeError(
                    "Component Flood left non-target weights on writable "
                    "vertices. First vertex IDs: {}".format(bad_vertex_ids)
                )

    if protected_vertex_ids.size:
        protected_after = adapter.get_weights(protected_vertex_ids)
        after_weights = np.asarray(
            protected_after.weights,
            dtype=np.float64,
        )
        if not np.array_equal(after_weights, protected_weights_before):
            changed_rows = np.where(
                np.any(
                    after_weights != protected_weights_before,
                    axis=1,
                )
            )[0][:20]
            bad_vertex_ids = protected_vertex_ids[changed_rows].tolist()
            raise RuntimeError(
                "Component Flood changed weights protected by a locked "
                "influence. First vertex IDs: {}".format(bad_vertex_ids)
            )

    # The selected ID list is intentionally accepted for future whole-scope
    # diagnostics and to make the contract explicit.
    if selected_vertex_ids.ndim != 1:
        raise RuntimeError("Selected vertex IDs must be a one-dimensional array.")


def _undo_failed_flood() -> None:
    try:
        cmds.undo()
    except Exception:
        cmds.warning(
            "Component Flood failed after modifying the skinCluster, and the "
            "automatic rollback also failed. Use Maya Undo before continuing."
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
