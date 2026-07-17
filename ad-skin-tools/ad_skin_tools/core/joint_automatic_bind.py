"""AD Skin Tool v2.8 universal hard-ownership bind.

Production input is one polygon mesh and one complete joint list. Ownership is
computed from the supplied Maya hierarchy graph and actual geometry only. No
body-part rule, joint-name rule, shell assignment, calibrated percentile,
tuned multiplier, confidence threshold, or minimum vertex quota is used.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple
import time

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.mesh import (
    get_vertex_count,
    get_vertex_positions,
    get_weighted_vertex_neighbors,
    get_world_positions,
)
from ad_skin_tools.core.skin_cluster import (
    create_closest_skin_cluster,
    find_skin_cluster,
)
from ad_skin_tools.core.undo import undo_chunk

np = ensure_numpy()


@dataclass(frozen=True)
class AutomaticSurfaceBindOptions:
    """Options that do not tune ownership behaviour."""

    # Memory/performance only; changing it must not change the result.
    distance_chunk_size: int = 20000
    # Abort instead of silently accepting structurally unused influences.
    fail_on_zero_ownership: bool = False


@dataclass(frozen=True)
class ComponentAnchor:
    """Nearest surface sample for one joint on one topology component."""

    component_index: int
    component_vertex_count: int
    owner_index: int
    owner_joint: str
    seed_vertex_id: int
    initial_cost: float


@dataclass(frozen=True)
class InfluenceAutomaticDiagnostic:
    """Compatibility diagnostic for one influence in the v2.8 solve."""

    joint: str
    radial_candidate_count: int
    competitive_seed_count: int
    component_anchor_count: int
    ownership_count: int
    radial_valid: bool
    messages: Tuple[str, ...]


@dataclass(frozen=True)
class AutomaticSurfaceBindResult:
    """Exactly one influence owns every vertex with weight 1.0."""

    skin_cluster: str
    mesh_shape: str
    mesh_transform: str
    influences: Tuple[str, ...]
    vertex_count: int
    influence_count: int
    topology_component_count: int
    radial_candidate_count: int
    competitive_seed_count: int
    component_anchor_count: int
    average_surface_cost: float
    maximum_surface_cost: float
    ownership_counts: Dict[str, int]
    owner_vertex_ids: Dict[str, Tuple[int, ...]]
    component_anchors: Tuple[ComponentAnchor, ...]
    diagnostics: Tuple[InfluenceAutomaticDiagnostic, ...]
    elapsed_seconds: float


def bind_object_automatic_surface(
    mesh: str,
    joints: Sequence[str],
    options: Optional[AutomaticSurfaceBindOptions] = None,
) -> AutomaticSurfaceBindResult:
    """Create deterministic hard ownership from geometry and joints only.

    The supplied hierarchy is converted to an undirected skeleton graph. Every
    hierarchy edge is evaluated as its exact line segment and every joint as an
    exact point primitive. Each mesh vertex belongs to the nearest skeleton
    primitive. If an edge is nearest, its nearer endpoint joint owns the vertex.

    Thus an interior chain joint D in C-D-E owns the halves of C-D and D-E for
    which D is the nearer endpoint. No tuned back-limit or artificial quota is
    required.
    """

    started = time.perf_counter()
    options = options or AutomaticSurfaceBindOptions()
    _validate_options(options)

    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    if find_skin_cluster(mesh_shape, required=False):
        raise RuntimeError(
            "The mesh already has a skinCluster.\n\n"
            "Run v2.8 on an unskinned duplicate."
        )

    influences = _normalize_joint_paths(joints)
    if len(influences) < 2:
        raise RuntimeError("Automatic surface bind requires at least two joints.")

    vertex_count = get_vertex_count(mesh_shape)
    if vertex_count <= 0:
        raise RuntimeError("The mesh contains no vertices.")

    vertex_ids = np.arange(vertex_count, dtype=np.int32)
    positions = get_vertex_positions(mesh_shape, vertex_ids)
    joint_positions = get_world_positions(list(influences))
    adjacency = get_weighted_vertex_neighbors(mesh_shape)
    if len(adjacency) != vertex_count:
        raise RuntimeError("Topology adjacency does not match vertex count.")

    components = _connected_components(adjacency)
    joint_edges = _build_joint_edges(influences)
    owner_indices, skeletal_costs = _solve_skeletal_owners(
        positions=positions,
        joint_positions=joint_positions,
        joint_edges=joint_edges,
        distance_chunk_size=int(options.distance_chunk_size),
    )

    uncovered = np.where(owner_indices < 0)[0]
    if uncovered.size:
        raise RuntimeError(
            "Automatic solve left vertices uncovered.\n\n"
            "Count: {}\nFirst IDs: {}".format(
                int(uncovered.size), uncovered[:20].tolist()
            )
        )
    if not np.all(np.isfinite(skeletal_costs)):
        raise RuntimeError("Automatic solve produced non-finite costs.")

    component_anchors = _build_component_anchors(
        components=components,
        positions=positions,
        joint_positions=joint_positions,
        influences=influences,
        distance_chunk_size=int(options.distance_chunk_size),
    )
    owner_vertex_ids = _build_owner_vertex_map(owner_indices, influences)
    ownership_counts = {
        joint: len(owner_vertex_ids[joint]) for joint in influences
    }
    diagnostics = _build_influence_diagnostics(
        influences=influences,
        joint_edges=joint_edges,
        ownership_counts=ownership_counts,
        component_count=len(components),
    )

    zero_owners = [d.joint for d in diagnostics if d.ownership_count == 0]
    if zero_owners and options.fail_on_zero_ownership:
        raise RuntimeError(
            "One or more influences own no vertices. The solver did not invent "
            "an artificial quota for them:\n{}".format("\n".join(zero_owners))
        )

    original_selection = cmds.ls(
        selection=True, long=True, flatten=True
    ) or []
    adapter = None
    try:
        with undo_chunk("AD Skin v2.8 Universal Surface Bind"):
            adapter = create_closest_skin_cluster(
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                joints=list(influences),
                max_influences=1,
            )
            stored_influences = tuple(adapter.influences())
            source_to_stored = _build_influence_column_map(
                source_influences=influences,
                stored_influences=stored_influences,
            )
            expected_stored_owners = source_to_stored[owner_indices]
            weights = np.zeros(
                (vertex_count, len(stored_influences)), dtype=np.float64
            )
            weights[vertex_ids, expected_stored_owners] = 1.0
            adapter.set_weights(
                vertex_ids=vertex_ids, weights=weights, normalize=False
            )
            stored_data = adapter.get_weights(vertex_ids)
            _validate_stored_hard_weights(
                weights=stored_data.weights,
                vertex_count=vertex_count,
                influence_count=len(stored_influences),
                expected_owner_indices=expected_stored_owners,
            )

            return AutomaticSurfaceBindResult(
                skin_cluster=adapter.skin_cluster,
                mesh_shape=mesh_shape,
                mesh_transform=mesh_transform,
                influences=influences,
                vertex_count=vertex_count,
                influence_count=len(influences),
                topology_component_count=len(components),
                radial_candidate_count=0,
                competitive_seed_count=0,
                component_anchor_count=len(component_anchors),
                average_surface_cost=float(np.mean(skeletal_costs)),
                maximum_surface_cost=float(np.max(skeletal_costs)),
                ownership_counts=ownership_counts,
                owner_vertex_ids=owner_vertex_ids,
                component_anchors=component_anchors,
                diagnostics=diagnostics,
                elapsed_seconds=time.perf_counter() - started,
            )
    except Exception:
        if adapter is not None and cmds.objExists(adapter.skin_cluster):
            _remove_skin_cluster(adapter.skin_cluster)
        raise
    finally:
        _restore_selection(original_selection)


def print_automatic_surface_report(result: AutomaticSurfaceBindResult) -> None:
    print("\n[AD Skin Tool v2.8 Universal Surface Bind]")
    print("Skin cluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Vertices:", result.vertex_count)
    print("Influences:", result.influence_count)
    print("Topology components:", result.topology_component_count)
    print("Radial heuristic candidates:", result.radial_candidate_count)
    print("Global heuristic competition seeds:", result.competitive_seed_count)
    print("Diagnostic component anchors:", result.component_anchor_count)
    print("Average skeleton distance:", round(result.average_surface_cost, 6))
    print("Maximum skeleton distance:", round(result.maximum_surface_cost, 6))
    print("Elapsed seconds:", round(result.elapsed_seconds, 3))
    print("\nPer-joint result:")
    for item in result.diagnostics:
        print(
            "  {}: anchors={}, owned={}".format(
                item.joint, item.component_anchor_count, item.ownership_count
            )
        )
        for message in item.messages:
            print("    note:", message)


def select_automatic_owned_vertices(
    result: AutomaticSurfaceBindResult, joint: str
) -> None:
    joint_path = _resolve_result_joint(result, joint)
    components = _vertex_components(
        mesh_shape=result.mesh_shape,
        vertex_ids=result.owner_vertex_ids[joint_path],
    )
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)


def _build_joint_edges(
    influences: Tuple[str, ...],
) -> Tuple[Tuple[int, int], ...]:
    """Connect each supplied joint to its nearest supplied ancestor."""

    index_by_joint = {joint: i for i, joint in enumerate(influences)}
    edges = set()
    for child_index, child in enumerate(influences):
        current = child
        visited = set()
        while True:
            parents = cmds.listRelatives(
                current, parent=True, fullPath=True, type="joint"
            ) or []
            if not parents:
                break
            parent = parents[0]
            if parent in visited:
                raise RuntimeError(
                    "Cycle detected while reading joint hierarchy:\n{}".format(
                        child
                    )
                )
            visited.add(parent)
            parent_index = index_by_joint.get(parent)
            if parent_index is not None:
                if parent_index != child_index:
                    edges.add(tuple(sorted((parent_index, child_index))))
                break
            current = parent
    return tuple(sorted(edges))


def _solve_skeletal_owners(
    positions: np.ndarray,
    joint_positions: np.ndarray,
    joint_edges: Tuple[Tuple[int, int], ...],
    distance_chunk_size: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """Find the nearest point on the supplied skeleton graph per vertex."""

    vertex_count = int(positions.shape[0])
    joint_count = int(joint_positions.shape[0])
    owners = np.full(vertex_count, -1, dtype=np.int32)
    squared_costs = np.full(vertex_count, np.inf, dtype=np.float64)

    for start in range(0, vertex_count, distance_chunk_size):
        stop = min(start + distance_chunk_size, vertex_count)
        chunk = positions[start:stop]
        count = int(chunk.shape[0])
        best_primitive = np.full(count, np.inf, dtype=np.float64)
        best_owner_distance = np.full(count, np.inf, dtype=np.float64)
        best_owner = np.full(count, -1, dtype=np.int32)
        best_rank = np.full(count, np.iinfo(np.int32).max, dtype=np.int32)
        rank = 0

        # Joint points remain valid primitives even if hierarchy is absent or
        # an edge has coincident endpoints.
        for joint_index in range(joint_count):
            delta = chunk - joint_positions[joint_index]
            squared = np.einsum("vi,vi->v", delta, delta)
            _update_best_candidates(
                squared,
                squared,
                np.full(count, joint_index, dtype=np.int32),
                rank,
                best_primitive,
                best_owner_distance,
                best_owner,
                best_rank,
            )
            rank += 1

        for joint_a, joint_b in joint_edges:
            point_a = joint_positions[joint_a]
            point_b = joint_positions[joint_b]
            segment = point_b - point_a
            segment_squared = float(np.dot(segment, segment))
            if segment_squared == 0.0:
                continue

            from_a = chunk - point_a
            parameter = np.einsum("vi,i->v", from_a, segment) / segment_squared
            parameter = np.clip(parameter, 0.0, 1.0)
            closest = point_a + parameter[:, np.newaxis] * segment
            to_segment = chunk - closest
            primitive_squared = np.einsum(
                "vi,vi->v", to_segment, to_segment
            )

            to_a = chunk - point_a
            to_b = chunk - point_b
            squared_to_a = np.einsum("vi,vi->v", to_a, to_a)
            squared_to_b = np.einsum("vi,vi->v", to_b, to_b)
            a_is_nearer = squared_to_a < squared_to_b
            equal_distance = squared_to_a == squared_to_b
            if joint_a < joint_b:
                a_is_nearer = a_is_nearer | equal_distance
            candidate_owner = np.where(
                a_is_nearer, joint_a, joint_b
            ).astype(np.int32)
            candidate_owner_distance = np.where(
                a_is_nearer, squared_to_a, squared_to_b
            )
            _update_best_candidates(
                primitive_squared,
                candidate_owner_distance,
                candidate_owner,
                rank,
                best_primitive,
                best_owner_distance,
                best_owner,
                best_rank,
            )
            rank += 1

        if np.any(best_owner < 0):
            raise RuntimeError("Could not resolve a skeletal owner for a vertex.")
        owners[start:stop] = best_owner
        squared_costs[start:stop] = best_primitive

    return owners, np.sqrt(squared_costs)


def _update_best_candidates(
    candidate_primitive: np.ndarray,
    candidate_owner_distance: np.ndarray,
    candidate_owner: np.ndarray,
    candidate_rank: int,
    best_primitive: np.ndarray,
    best_owner_distance: np.ndarray,
    best_owner: np.ndarray,
    best_rank: np.ndarray,
) -> None:
    """Apply an exact deterministic lexicographic comparison in-place."""

    better_primitive = candidate_primitive < best_primitive
    equal_primitive = candidate_primitive == best_primitive
    better_owner_distance = candidate_owner_distance < best_owner_distance
    equal_owner_distance = candidate_owner_distance == best_owner_distance
    better_owner = candidate_owner < best_owner
    equal_owner = candidate_owner == best_owner
    better_rank = candidate_rank < best_rank
    update = better_primitive | (
        equal_primitive
        & (
            better_owner_distance
            | (
                equal_owner_distance
                & (
                    (best_owner < 0)
                    | better_owner
                    | (equal_owner & better_rank)
                )
            )
        )
    )
    if np.any(update):
        best_primitive[update] = candidate_primitive[update]
        best_owner_distance[update] = candidate_owner_distance[update]
        best_owner[update] = candidate_owner[update]
        best_rank[update] = int(candidate_rank)


def _build_component_anchors(
    components: Tuple[Tuple[int, ...], ...],
    positions: np.ndarray,
    joint_positions: np.ndarray,
    influences: Tuple[str, ...],
    distance_chunk_size: int,
) -> Tuple[ComponentAnchor, ...]:
    """Build diagnostic nearest-surface samples; they do not affect owners."""

    anchors: List[ComponentAnchor] = []
    for component_index, component in enumerate(components):
        component_ids = np.asarray(component, dtype=np.int32)
        nearest_ids, nearest_distances = _nearest_component_vertices_by_joint(
            component_ids,
            positions,
            joint_positions,
            distance_chunk_size,
        )
        for owner_index, influence in enumerate(influences):
            anchors.append(
                ComponentAnchor(
                    component_index=component_index,
                    component_vertex_count=int(component_ids.size),
                    owner_index=owner_index,
                    owner_joint=influence,
                    seed_vertex_id=int(nearest_ids[owner_index]),
                    initial_cost=float(nearest_distances[owner_index]),
                )
            )
    return tuple(anchors)


def _nearest_component_vertices_by_joint(
    component_ids: np.ndarray,
    positions: np.ndarray,
    joint_positions: np.ndarray,
    distance_chunk_size: int,
) -> Tuple[np.ndarray, np.ndarray]:
    joint_count = int(joint_positions.shape[0])
    best_squared = np.full(joint_count, np.inf, dtype=np.float64)
    best_ids = np.full(joint_count, -1, dtype=np.int32)

    for start in range(0, int(component_ids.size), distance_chunk_size):
        chunk_ids = component_ids[start:start + distance_chunk_size]
        delta = (
            positions[chunk_ids, np.newaxis, :]
            - joint_positions[np.newaxis, :, :]
        )
        squared = np.einsum("vji,vji->vj", delta, delta)
        local_rows = np.argmin(squared, axis=0)
        columns = np.arange(joint_count, dtype=np.int32)
        local_squared = squared[local_rows, columns]
        local_ids = chunk_ids[local_rows]
        for joint_index in range(joint_count):
            candidate_distance = float(local_squared[joint_index])
            candidate_id = int(local_ids[joint_index])
            current_distance = float(best_squared[joint_index])
            current_id = int(best_ids[joint_index])
            if candidate_distance < current_distance or (
                candidate_distance == current_distance
                and (current_id < 0 or candidate_id < current_id)
            ):
                best_squared[joint_index] = candidate_distance
                best_ids[joint_index] = candidate_id

    if np.any(best_ids < 0):
        raise RuntimeError("Could not create automatic component anchors.")
    return best_ids, np.sqrt(best_squared)


def _connected_components(adjacency) -> Tuple[Tuple[int, ...], ...]:
    remaining = set(range(len(adjacency)))
    components = []
    while remaining:
        start = min(remaining)
        remaining.remove(start)
        component = {start}
        stack = [start]
        while stack:
            vertex_id = stack.pop()
            for raw_neighbor, _ in adjacency[vertex_id]:
                neighbor = int(raw_neighbor)
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    component.add(neighbor)
                    stack.append(neighbor)
        components.append(tuple(sorted(component)))
    components.sort(key=lambda item: (-len(item), item[0]))
    return tuple(components)


def _build_influence_diagnostics(
    influences: Tuple[str, ...],
    joint_edges: Tuple[Tuple[int, int], ...],
    ownership_counts: Dict[str, int],
    component_count: int,
) -> Tuple[InfluenceAutomaticDiagnostic, ...]:
    degrees = [0 for _ in influences]
    for joint_a, joint_b in joint_edges:
        degrees[joint_a] += 1
        degrees[joint_b] += 1

    result = []
    for index, joint in enumerate(influences):
        messages = [
            "ownership derived from the nearest supplied hierarchy graph"
        ]
        if degrees[index] == 0:
            messages.append(
                "no supplied hierarchy edge reaches this joint; evaluated "
                "as an exact point primitive"
            )
        if ownership_counts[joint] == 0:
            messages.append(
                "no mesh vertex is closest to this joint's skeletal region; "
                "no artificial minimum ownership was added"
            )
        result.append(
            InfluenceAutomaticDiagnostic(
                joint=joint,
                radial_candidate_count=0,
                competitive_seed_count=0,
                component_anchor_count=component_count,
                ownership_count=ownership_counts[joint],
                radial_valid=False,
                messages=tuple(messages),
            )
        )
    return tuple(result)


def _resolve_mesh(mesh: str) -> Tuple[str, str]:
    if not mesh or not cmds.objExists(mesh):
        raise RuntimeError("Mesh does not exist.")
    matches = cmds.ls(mesh, long=True) or []
    if not matches:
        raise RuntimeError("Could not resolve mesh path:\n" + mesh)
    node = matches[0]
    node_type = cmds.nodeType(node)
    if node_type == "mesh":
        parents = cmds.listRelatives(
            node, parent=True, fullPath=True
        ) or []
        if not parents:
            raise RuntimeError("Mesh shape has no transform parent:\n" + node)
        return node, parents[0]
    if node_type == "transform":
        shapes = cmds.listRelatives(
            node,
            shapes=True,
            noIntermediate=True,
            fullPath=True,
            type="mesh",
        ) or []
        if len(shapes) != 1:
            raise RuntimeError(
                "Geometry transform must contain exactly one non-intermediate "
                "mesh shape.\n\nTransform: {}\nMesh shapes: {}".format(
                    node, len(shapes)
                )
            )
        return shapes[0], node
    raise RuntimeError("Node is not a polygon mesh or mesh transform:\n" + node)


def _normalize_joint_paths(joints: Sequence[str]) -> Tuple[str, ...]:
    result = []
    seen = set()
    for joint in joints:
        matches = cmds.ls(joint, long=True, type="joint") or []
        if not matches:
            raise RuntimeError("Joint does not exist:\n" + joint)
        path = matches[0]
        if path not in seen:
            seen.add(path)
            result.append(path)
    return tuple(result)


def _build_owner_vertex_map(
    owner_indices: np.ndarray,
    influences: Tuple[str, ...],
) -> Dict[str, Tuple[int, ...]]:
    return {
        joint: tuple(
            np.where(owner_indices == index)[0].astype(np.int32).tolist()
        )
        for index, joint in enumerate(influences)
    }


def _build_influence_column_map(
    source_influences: Tuple[str, ...],
    stored_influences: Tuple[str, ...],
) -> np.ndarray:
    stored_index = {joint: i for i, joint in enumerate(stored_influences)}
    source_set = set(source_influences)
    missing = [j for j in source_influences if j not in stored_index]
    unexpected = [j for j in stored_influences if j not in source_set]
    if missing or unexpected:
        raise RuntimeError(
            "skinCluster membership does not match v2.8 input.\n\n"
            "Missing:\n{}\n\nUnexpected:\n{}".format(
                "\n".join(missing) if missing else "None",
                "\n".join(unexpected) if unexpected else "None",
            )
        )
    return np.asarray(
        [stored_index[j] for j in source_influences], dtype=np.int32
    )


def _validate_stored_hard_weights(
    weights: np.ndarray,
    vertex_count: int,
    influence_count: int,
    expected_owner_indices: np.ndarray,
) -> None:
    expected_shape = (vertex_count, influence_count)
    if weights.shape != expected_shape:
        raise RuntimeError(
            "Stored weight shape is invalid.\n\nExpected: {}\nReceived: {}"
            .format(expected_shape, weights.shape)
        )
    if not np.all(np.isfinite(weights)):
        raise RuntimeError("Stored weights contain non-finite values.")

    dtype = (
        weights.dtype
        if np.issubdtype(weights.dtype, np.floating)
        else np.dtype(np.float64)
    )
    # Standard floating-point summation error bound derived from dtype and the
    # actual number of terms, not a calibrated tolerance.
    error_bound = np.finfo(dtype).eps * max(int(influence_count), 1)
    if np.any(np.abs(weights.sum(axis=1) - 1.0) > error_bound):
        raise RuntimeError("Stored weight rows do not all sum to 1.0.")
    if np.any(np.count_nonzero(weights > error_bound, axis=1) != 1):
        raise RuntimeError("Hard ownership validation failed.")

    stored_owners = np.argmax(weights, axis=1).astype(np.int32)
    mismatch = np.where(stored_owners != expected_owner_indices)[0]
    if mismatch.size:
        raise RuntimeError(
            "Maya stored ownership differs from v2.8 result.\n\n"
            "Mismatched vertices: {}\nFirst IDs: {}".format(
                int(mismatch.size), mismatch[:20].tolist()
            )
        )


def _resolve_result_joint(
    result: AutomaticSurfaceBindResult, joint: str
) -> str:
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Joint does not exist: " + joint)
    path = matches[0]
    if path not in result.owner_vertex_ids:
        raise RuntimeError("Joint was not included in this v2.8 result:\n" + path)
    return path


def _vertex_components(mesh_shape: str, vertex_ids) -> List[str]:
    parents = cmds.listRelatives(
        mesh_shape, parent=True, fullPath=True
    ) or []
    mesh_name = parents[0] if parents else mesh_shape
    return [
        "{}.vtx[{}]".format(mesh_name, int(vertex_id))
        for vertex_id in vertex_ids
    ]


def _remove_skin_cluster(skin_cluster: str) -> None:
    try:
        cmds.skinCluster(skin_cluster, edit=True, unbind=True)
        return
    except Exception:
        pass
    try:
        cmds.delete(skin_cluster)
    except Exception:
        pass


def _restore_selection(items) -> None:
    try:
        cmds.select(clear=True)
        if items:
            cmds.select(items, replace=True)
    except Exception:
        pass


def _validate_options(options: AutomaticSurfaceBindOptions) -> None:
    if int(options.distance_chunk_size) < 1:
        raise ValueError("distance_chunk_size must be at least 1.")
