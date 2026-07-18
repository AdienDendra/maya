"""v5 smoke test: add influences by donor-region geodesic split.

The existing hard one-hot ownership is authoritative. Each new joint finds its
nearest surface vertex, uses that vertex's current owner as the donor, and may
claim only inside that donor's connected writable region. Donor and targets are
split by exact shortest-path distance along mesh edges. Locked ownership is
never part of a writable region.
"""

from dataclasses import dataclass
import heapq
from typing import Dict, Sequence, Tuple

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.influence_lock import locked_influences
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter
from ad_skin_tools.core.undo import undo_chunk
from ad_skin_tools.region.connectivity import build_vertex_adjacency
from ad_skin_tools.region.maya_scene import collect_distance_input


np = ensure_numpy()


@dataclass(frozen=True)
class TargetClaim:
    joint: str
    donor_joint: str
    anchor_vertex_ids: Tuple[int, ...]
    candidate_vertex_ids: Tuple[int, ...]
    accepted_vertex_ids: Tuple[int, ...]
    ambiguous_vertex_ids: Tuple[int, ...]


@dataclass(frozen=True)
class ObjectRegionAddResult:
    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    target_joints: Tuple[str, ...]
    locked_influences: Tuple[str, ...]
    protected_vertex_ids: Tuple[int, ...]
    unchanged_vertex_ids: Tuple[int, ...]
    claimed_vertex_ids_by_joint: Dict[str, Tuple[int, ...]]
    diagnostics: Tuple[TargetClaim, ...]

    @property
    def claimed_vertex_count(self) -> int:
        return sum(len(ids) for ids in self.claimed_vertex_ids_by_joint.values())


def add_object_region_influences(
    mesh: str,
    target_joints: Sequence[str],
) -> ObjectRegionAddResult:
    """Add new influences at weight 0, then write accepted claims at weight 1."""

    selection_before = cmds.ls(selection=True, long=True) or []
    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    adapter = SkinClusterAdapter.from_mesh(mesh_shape)
    existing = tuple(adapter.influences())
    targets = _new_targets(target_joints, existing)

    scene_input = collect_distance_input(mesh_transform, existing + targets)
    vertex_positions = np.asarray(scene_input.vertex_positions, dtype=np.float64)
    influence_positions = np.asarray(scene_input.influence_positions, dtype=np.float64)
    vertex_ids = np.arange(vertex_positions.shape[0], dtype=np.int32)

    before = adapter.get_weights(vertex_ids)
    if tuple(before.influences) != existing:
        raise RuntimeError("skinCluster influence order changed during setup.")

    baseline_weights = np.asarray(before.weights, dtype=np.float64).copy()
    baseline_owners = _hard_owner_indices(baseline_weights)
    locked = locked_influences(adapter.skin_cluster, existing)
    protected_mask = _protected_mask(baseline_weights, existing, locked)
    adjacency = build_vertex_adjacency(mesh_shape)

    claimed, diagnostics = _solve_donor_region_claims(
        existing=existing,
        targets=targets,
        vertex_positions=vertex_positions,
        influence_positions=influence_positions,
        baseline_owners=baseline_owners,
        protected_mask=protected_mask,
        adjacency=adjacency,
    )

    claimed_ids = tuple(
        sorted(vertex_id for ids in claimed.values() for vertex_id in ids)
    )
    claimed_mask = np.zeros(vertex_positions.shape[0], dtype=bool)
    if claimed_ids:
        claimed_mask[np.asarray(claimed_ids, dtype=np.int32)] = True
    unchanged_ids = tuple(np.where(~claimed_mask)[0].astype(np.int32).tolist())
    protected_ids = tuple(np.where(protected_mask)[0].astype(np.int32).tolist())

    mutation_recorded = False
    try:
        try:
            with undo_chunk("AD Skin Tool v5 Object Region Add"):
                for joint in targets:
                    cmds.skinCluster(
                        adapter.skin_cluster,
                        edit=True,
                        addInfluence=joint,
                        weight=0.0,
                    )
                    mutation_recorded = True

                adapter = SkinClusterAdapter.from_mesh(mesh_shape)
                _write_claims(adapter, claimed)
                _validate(
                    adapter,
                    vertex_ids,
                    existing,
                    baseline_weights,
                    targets,
                    claimed,
                    unchanged_ids,
                )
        except Exception:
            if mutation_recorded:
                _undo_failed_operation()
            raise
    finally:
        _restore_selection(selection_before)

    return ObjectRegionAddResult(
        skin_cluster=adapter.skin_cluster,
        mesh_shape=mesh_shape,
        mesh_transform=mesh_transform,
        target_joints=targets,
        locked_influences=locked,
        protected_vertex_ids=protected_ids,
        unchanged_vertex_ids=unchanged_ids,
        claimed_vertex_ids_by_joint=claimed,
        diagnostics=diagnostics,
    )


def print_report(result: ObjectRegionAddResult) -> None:
    print("\n[AD Skin Tool v5.0 - Donor Region Geodesic Smoke Test]")
    print("SkinCluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("New influences:", len(result.target_joints))
    print("Locked existing influences:", len(result.locked_influences))
    print("Protected vertices:", len(result.protected_vertex_ids))
    print("Claimed vertices:", result.claimed_vertex_count)
    print("Unchanged vertices:", len(result.unchanged_vertex_ids))
    print("\nPer target:")
    for item in result.diagnostics:
        donor = item.donor_joint.split("|")[-1] if item.donor_joint else "<none>"
        print(
            "  {}: donor={} | anchors={} | domain={} | accepted={} | ties={}".format(
                item.joint.split("|")[-1],
                donor,
                len(item.anchor_vertex_ids),
                len(item.candidate_vertex_ids),
                len(item.accepted_vertex_ids),
                len(item.ambiguous_vertex_ids),
            )
        )


def _solve_donor_region_claims(
    existing,
    targets,
    vertex_positions,
    influence_positions,
    baseline_owners,
    protected_mask,
    adjacency,
):
    existing_count = len(existing)
    claimed_lists = {joint: [] for joint in targets}
    diagnostic_data = {}
    component_cache = {}
    groups = {}

    for offset, joint in enumerate(targets):
        target_index = existing_count + offset
        target_position = influence_positions[target_index]
        anchors = _nearest_vertices(
            tuple(range(vertex_positions.shape[0])),
            vertex_positions,
            target_position,
        )

        anchor_array = np.asarray(anchors, dtype=np.int32)
        anchor_owners = set(int(value) for value in baseline_owners[anchor_array])
        anchor_is_protected = bool(np.any(protected_mask[anchor_array]))

        if anchor_is_protected or len(anchor_owners) != 1:
            diagnostic_data[joint] = {
                "donor_joint": "",
                "anchors": anchors,
                "domain": tuple(),
                "accepted": tuple(),
                "ambiguous": anchors,
            }
            continue

        donor_index = next(iter(anchor_owners))
        if donor_index not in component_cache:
            donor_vertex_ids = tuple(
                np.where(
                    (baseline_owners == donor_index) & (~protected_mask)
                )[0]
                .astype(np.int32)
                .tolist()
            )
            components = _induced_components(donor_vertex_ids, adjacency)
            component_by_vertex = {
                vertex_id: component_index
                for component_index, component in enumerate(components)
                for vertex_id in component
            }
            component_cache[donor_index] = (components, component_by_vertex)

        components, component_by_vertex = component_cache[donor_index]
        component_indices = {
            component_by_vertex[int(vertex_id)] for vertex_id in anchors
        }
        if len(component_indices) != 1:
            diagnostic_data[joint] = {
                "donor_joint": existing[donor_index],
                "anchors": anchors,
                "domain": tuple(),
                "accepted": tuple(),
                "ambiguous": anchors,
            }
            continue

        component_index = next(iter(component_indices))
        component = components[component_index]
        groups.setdefault((donor_index, component_index), []).append(
            (joint, target_index, anchors)
        )
        diagnostic_data[joint] = {
            "donor_joint": existing[donor_index],
            "anchors": anchors,
            "domain": component,
            "accepted": tuple(),
            "ambiguous": tuple(),
        }

    for (donor_index, component_index), target_records in groups.items():
        component = component_cache[donor_index][0][component_index]
        component_array = np.asarray(component, dtype=np.int32)
        donor_anchors = _nearest_vertices(
            component,
            vertex_positions,
            influence_positions[donor_index],
        )
        donor_distances = _shortest_path_distances(
            component,
            donor_anchors,
            adjacency,
            vertex_positions,
        )[component_array]

        target_distances = []
        for _, _, anchors in target_records:
            target_distances.append(
                _shortest_path_distances(
                    component,
                    anchors,
                    adjacency,
                    vertex_positions,
                )[component_array]
            )

        target_matrix = np.column_stack(target_distances)
        minimum_target = np.min(target_matrix, axis=1)
        winner = np.argmin(target_matrix, axis=1).astype(np.int32)
        unique_winner = (
            np.count_nonzero(
                target_matrix == minimum_target[:, np.newaxis],
                axis=1,
            )
            == 1
        )
        target_wins = unique_winner & (minimum_target < donor_distances)
        tied_rows = ~unique_winner

        for local_index, (joint, _, _) in enumerate(target_records):
            accepted = tuple(
                int(value)
                for value in component_array[
                    target_wins & (winner == local_index)
                ].tolist()
            )
            ambiguous = tuple(
                int(value)
                for value in component_array[
                    tied_rows
                    & (target_matrix[:, local_index] == minimum_target)
                ].tolist()
            )
            claimed_lists[joint].extend(accepted)
            diagnostic_data[joint]["accepted"] = accepted
            diagnostic_data[joint]["ambiguous"] = ambiguous

    claimed = {
        joint: tuple(sorted(int(value) for value in claimed_lists[joint]))
        for joint in targets
    }
    diagnostics = tuple(
        TargetClaim(
            joint=joint,
            donor_joint=diagnostic_data[joint]["donor_joint"],
            anchor_vertex_ids=tuple(diagnostic_data[joint]["anchors"]),
            candidate_vertex_ids=tuple(diagnostic_data[joint]["domain"]),
            accepted_vertex_ids=tuple(diagnostic_data[joint]["accepted"]),
            ambiguous_vertex_ids=tuple(diagnostic_data[joint]["ambiguous"]),
        )
        for joint in targets
    )
    return claimed, diagnostics


def _nearest_vertices(vertex_ids, vertex_positions, point):
    ids = np.asarray(vertex_ids, dtype=np.int32)
    positions = vertex_positions[ids]
    delta = positions - point[np.newaxis, :]
    squared = np.einsum("vi,vi->v", delta, delta)
    minimum = np.min(squared)
    return tuple(int(value) for value in ids[squared == minimum].tolist())


def _induced_components(vertex_ids, adjacency):
    unseen = set(int(value) for value in vertex_ids)
    components = []
    while unseen:
        seed = min(unseen)
        unseen.remove(seed)
        stack = [seed]
        component = []
        while stack:
            vertex_id = stack.pop()
            component.append(vertex_id)
            for neighbour in adjacency[vertex_id]:
                if neighbour in unseen:
                    unseen.remove(neighbour)
                    stack.append(neighbour)
        components.append(tuple(sorted(component)))
    components.sort(key=lambda values: values[0])
    return tuple(components)


def _shortest_path_distances(
    component,
    anchors,
    adjacency,
    vertex_positions,
):
    allowed = np.zeros(vertex_positions.shape[0], dtype=bool)
    allowed[np.asarray(component, dtype=np.int32)] = True
    distances = np.full(vertex_positions.shape[0], np.inf, dtype=np.float64)
    queue = []

    for anchor in anchors:
        distances[int(anchor)] = 0.0
        heapq.heappush(queue, (0.0, int(anchor)))

    while queue:
        distance, vertex_id = heapq.heappop(queue)
        if distance != distances[vertex_id]:
            continue
        for neighbour in adjacency[vertex_id]:
            if not allowed[neighbour]:
                continue
            delta = vertex_positions[vertex_id] - vertex_positions[neighbour]
            edge_length = float(np.sqrt(np.dot(delta, delta)))
            candidate = distance + edge_length
            if candidate < distances[neighbour]:
                distances[neighbour] = candidate
                heapq.heappush(queue, (candidate, int(neighbour)))

    return distances


def _hard_owner_indices(weights):
    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1]) * 16.0
    rows = np.arange(weights.shape[0], dtype=np.int32)
    owners = np.argmax(weights, axis=1).astype(np.int32)
    owner_values = weights[rows, owners]
    non_owner = weights.copy()
    non_owner[rows, owners] = 0.0
    invalid = (
        (np.abs(owner_values - 1.0) > tolerance)
        | (np.abs(np.sum(weights, axis=1) - 1.0) > tolerance)
        | np.any(np.abs(non_owner) > tolerance, axis=1)
    )
    if np.any(invalid):
        raise RuntimeError(
            "v5.0 smoke test requires hard one-hot existing weights.\n\n"
            "First invalid vertex IDs: {}".format(
                np.where(invalid)[0][:20].tolist()
            )
        )
    return owners


def _protected_mask(weights, influences, locked):
    if not locked:
        return np.zeros(weights.shape[0], dtype=bool)
    column_by_joint = {joint: index for index, joint in enumerate(influences)}
    columns = [column_by_joint[joint] for joint in locked]
    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1]) * 16.0
    return np.any(np.abs(weights[:, columns]) > tolerance, axis=1)


def _write_claims(adapter, claimed):
    influences = tuple(adapter.influences())
    column_by_joint = {joint: index for index, joint in enumerate(influences)}
    assignments = sorted(
        (
            (int(vertex_id), int(column_by_joint[joint]))
            for joint, vertex_ids in claimed.items()
            for vertex_id in vertex_ids
        ),
        key=lambda item: item[0],
    )
    if not assignments:
        return

    ids = np.asarray([item[0] for item in assignments], dtype=np.int32)
    columns = np.asarray([item[1] for item in assignments], dtype=np.int32)
    weights = np.zeros((len(ids), len(influences)), dtype=np.float64)
    weights[np.arange(len(ids), dtype=np.int32), columns] = 1.0
    adapter.set_weights(ids, weights, normalize=False)


def _validate(
    adapter,
    vertex_ids,
    baseline_influences,
    baseline_weights,
    targets,
    claimed,
    unchanged_ids,
):
    stored = adapter.get_weights(vertex_ids)
    influences = tuple(stored.influences)
    weights = np.asarray(stored.weights, dtype=np.float64)
    columns = {joint: index for index, joint in enumerate(influences)}

    unchanged = np.asarray(unchanged_ids, dtype=np.int32)
    if unchanged.size:
        old_columns = [columns[joint] for joint in baseline_influences]
        target_columns = [columns[joint] for joint in targets]
        if not np.array_equal(
            weights[unchanged][:, old_columns],
            baseline_weights[unchanged],
        ):
            raise RuntimeError("An unclaimed existing weight row changed.")
        if np.any(weights[unchanged][:, target_columns] != 0.0):
            raise RuntimeError("A new influence affected an unclaimed vertex.")

    tolerance = float(np.finfo(np.float64).eps) * max(1, weights.shape[1])
    for joint, ids in claimed.items():
        if not ids:
            continue
        rows = np.asarray(ids, dtype=np.int32)
        expected = np.zeros((len(rows), len(influences)), dtype=np.float64)
        expected[:, columns[joint]] = 1.0
        if np.any(np.abs(weights[rows] - expected) > tolerance):
            raise RuntimeError(
                "Stored claim is not exact Replace 1.0 for:\n{}".format(joint)
            )


def _new_targets(joints, existing):
    result = []
    seen = set(existing)
    for joint in joints:
        matches = cmds.ls(joint, long=True, type="joint") or []
        if not matches:
            raise RuntimeError("Target joint does not exist:\n{}".format(joint))
        path = matches[0]
        if path not in seen:
            seen.add(path)
            result.append(path)
    if not result:
        raise RuntimeError("Select at least one joint not already bound to the mesh.")
    return tuple(result)


def _resolve_mesh(mesh):
    matches = cmds.ls(mesh, long=True) or []
    if not matches:
        raise RuntimeError("Mesh does not exist:\n{}".format(mesh))
    node = matches[0]
    if cmds.nodeType(node) == "mesh":
        parent = cmds.listRelatives(node, parent=True, fullPath=True) or []
        if not parent:
            raise RuntimeError("Mesh shape has no transform parent.")
        return node, parent[0]
    shapes = cmds.listRelatives(
        node,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
        type="mesh",
    ) or []
    if cmds.nodeType(node) != "transform" or len(shapes) != 1:
        raise RuntimeError("Supply one polygon mesh transform or mesh shape.")
    return shapes[0], node


def _undo_failed_operation():
    try:
        cmds.undo()
    except Exception:
        cmds.warning(
            "v5 Object Region Add failed after changing the skinCluster. "
            "Automatic rollback also failed; use Maya Undo."
        )


def _restore_selection(selection_before):
    cmds.select(clear=True)
    if selection_before:
        try:
            cmds.select(selection_before, replace=True)
        except Exception:
            pass
