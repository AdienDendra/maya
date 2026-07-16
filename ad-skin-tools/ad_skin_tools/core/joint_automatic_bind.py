"""
AD Skin Tool v2.7 automatic hard-ownership bind.

Public input:
    - one polygon mesh transform or shape;
    - one complete joint list.

The solver contains no fallback joint, shell-joint list, body-part name,
left/right rule, hierarchy role, or manual shell ownership.
"""

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Sequence, Tuple
import heapq
import time

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.joint_seed_competition import (
    CompetitiveSeedResult,
    JointCompetitionOptions,
    resolve_joint_seed_competition,
)
from ad_skin_tools.core.joint_surface_solver import (
    JointSeedOptions,
    JointSeedResult,
    solve_joint_surface_seeds,
)
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
    """
    v2.7 solver options.

    Radial first-hit solving is optional enrichment. A joint that cannot
    produce radial seeds remains active through automatic component anchors.
    """
    use_radial_seed_enrichment: bool = True
    radial_seed_options: JointSeedOptions = JointSeedOptions(
        fail_on_invalid_joint=False,
    )
    competition_options: JointCompetitionOptions = JointCompetitionOptions(
        fail_on_invalid_joint=False,
    )
    distance_chunk_size: int = 20000
    fail_on_zero_ownership: bool = False


@dataclass(frozen=True)
class ComponentAnchor:
    """Nearest joint-to-surface source for one joint on one component."""
    component_index: int
    component_vertex_count: int
    owner_index: int
    owner_joint: str
    seed_vertex_id: int
    initial_cost: float


@dataclass(frozen=True)
class InfluenceAutomaticDiagnostic:
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
    """
    Create v2.7 hard ownership from geometry and joints only.

    Cost model:
        joint-to-source world distance
        + source-to-vertex polygon geodesic distance

    Sources:
        1. one automatic nearest-surface anchor per joint per topology
           component;
        2. optional visibility-resolved radial seeds.

    Because every joint competes on every topology component with its actual
    joint-to-surface cost, a disconnected shell is not assigned wholesale to
    one hardcoded influence. A long shell can be split naturally between
    several joints.
    """
    started = time.perf_counter()
    options = options or AutomaticSurfaceBindOptions()
    _validate_options(options)

    mesh_shape, mesh_transform = _resolve_mesh(mesh)
    if find_skin_cluster(mesh_shape, required=False):
        raise RuntimeError(
            "The mesh already has a skinCluster.\n\n"
            "Run v2.7 on an unskinned duplicate."
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
    radial_result, competitive_result = _build_radial_enrichment(
        mesh_shape=mesh_shape,
        influences=influences,
        options=options,
    )

    component_anchors = _build_component_anchors(
        components=components,
        positions=positions,
        joint_positions=joint_positions,
        influences=influences,
        distance_chunk_size=int(options.distance_chunk_size),
    )

    seed_candidates = _build_seed_candidates(
        component_anchors=component_anchors,
        radial_result=radial_result,
        competitive_result=competitive_result,
        influences=influences,
        positions=positions,
        joint_positions=joint_positions,
        vertex_count=vertex_count,
    )

    owner_indices, surface_costs = _propagate_weighted_owners(
        adjacency=adjacency,
        seed_candidates=seed_candidates,
        vertex_count=vertex_count,
    )

    uncovered = np.where(owner_indices < 0)[0]
    if uncovered.size:
        raise RuntimeError(
            "Automatic solve left vertices uncovered.\n\n"
            "Count: {}\nFirst IDs: {}".format(
                int(uncovered.size),
                uncovered[:20].tolist(),
            )
        )
    if not np.all(np.isfinite(surface_costs)):
        raise RuntimeError("Automatic solve produced non-finite costs.")

    owner_vertex_ids = _build_owner_vertex_map(owner_indices, influences)
    ownership_counts = {
        joint: len(owner_vertex_ids[joint])
        for joint in influences
    }
    radial_counts = _radial_candidate_counts(radial_result, influences)
    competitive_counts = _competitive_seed_counts(
        competitive_result,
        influences,
    )
    diagnostics = _build_influence_diagnostics(
        influences=influences,
        radial_result=radial_result,
        radial_counts=radial_counts,
        competitive_counts=competitive_counts,
        ownership_counts=ownership_counts,
        component_count=len(components),
    )

    zero_owners = [
        item.joint
        for item in diagnostics
        if item.ownership_count == 0
    ]
    if zero_owners and options.fail_on_zero_ownership:
        raise RuntimeError(
            "One or more influences own no vertices:\n{}".format(
                "\n".join(zero_owners)
            )
        )

    original_selection = cmds.ls(
        selection=True,
        long=True,
        flatten=True,
    ) or []
    adapter = None

    try:
        with undo_chunk("AD Skin v2.7 Automatic Surface Bind"):
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
                (vertex_count, len(stored_influences)),
                dtype=np.float64,
            )
            weights[vertex_ids, expected_stored_owners] = 1.0

            adapter.set_weights(
                vertex_ids=vertex_ids,
                weights=weights,
                normalize=False,
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
                radial_candidate_count=sum(radial_counts.values()),
                competitive_seed_count=sum(competitive_counts.values()),
                component_anchor_count=len(component_anchors),
                average_surface_cost=float(np.mean(surface_costs)),
                maximum_surface_cost=float(np.max(surface_costs)),
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
    print("\n[AD Skin Tool v2.7 Automatic Surface Bind]")
    print("Skin cluster:", result.skin_cluster)
    print("Mesh:", result.mesh_transform)
    print("Vertices:", result.vertex_count)
    print("Influences:", result.influence_count)
    print("Topology components:", result.topology_component_count)
    print("Radial candidates:", result.radial_candidate_count)
    print("Competitive seeds:", result.competitive_seed_count)
    print("Automatic component anchors:", result.component_anchor_count)
    print("Average surface cost:", round(result.average_surface_cost, 6))
    print("Maximum surface cost:", round(result.maximum_surface_cost, 6))
    print("Elapsed seconds:", round(result.elapsed_seconds, 3))
    print("\nPer-joint result:")

    for item in result.diagnostics:
        print(
            "  {}: radial={}, competitive={}, anchors={}, owned={}, "
            "radial_valid={}".format(
                item.joint,
                item.radial_candidate_count,
                item.competitive_seed_count,
                item.component_anchor_count,
                item.ownership_count,
                item.radial_valid,
            )
        )
        for message in item.messages:
            print("    note:", message)


def select_automatic_owned_vertices(
    result: AutomaticSurfaceBindResult,
    joint: str,
) -> None:
    joint_path = _resolve_result_joint(result, joint)
    components = _vertex_components(
        mesh_shape=result.mesh_shape,
        vertex_ids=result.owner_vertex_ids[joint_path],
    )
    cmds.select(clear=True)
    if components:
        cmds.select(components, replace=True)


def _build_radial_enrichment(
    mesh_shape: str,
    influences: Tuple[str, ...],
    options: AutomaticSurfaceBindOptions,
) -> Tuple[Optional[JointSeedResult], Optional[CompetitiveSeedResult]]:
    if not options.use_radial_seed_enrichment:
        return None, None

    radial_options = replace(
        options.radial_seed_options,
        fail_on_invalid_joint=False,
    )
    try:
        radial_result = solve_joint_surface_seeds(
            mesh_shape=mesh_shape,
            joints=influences,
            options=radial_options,
        )
    except Exception as exc:
        cmds.warning(
            "AD Skin v2.7 radial enrichment was skipped: {}".format(exc)
        )
        return None, None

    usable_influences = tuple(
        joint
        for joint in influences
        if radial_result.seed_vertex_ids.get(joint, ())
    )
    if len(usable_influences) < 2:
        return radial_result, None

    usable_set = set(usable_influences)
    filtered_result = JointSeedResult(
        mesh_shape=radial_result.mesh_shape,
        influences=usable_influences,
        seed_vertex_ids={
            joint: tuple(radial_result.seed_vertex_ids[joint])
            for joint in usable_influences
        },
        diagnostics=tuple(
            item
            for item in radial_result.diagnostics
            if item.joint in usable_set
        ),
        elapsed_seconds=radial_result.elapsed_seconds,
    )
    competition_options = replace(
        options.competition_options,
        fail_on_invalid_joint=False,
    )

    try:
        competition = resolve_joint_seed_competition(
            seed_result=filtered_result,
            options=competition_options,
        )
    except Exception as exc:
        cmds.warning(
            "AD Skin v2.7 radial competition was skipped: {}".format(exc)
        )
        return radial_result, None

    return radial_result, competition


def _build_component_anchors(
    components: Tuple[Tuple[int, ...], ...],
    positions: np.ndarray,
    joint_positions: np.ndarray,
    influences: Tuple[str, ...],
    distance_chunk_size: int,
) -> Tuple[ComponentAnchor, ...]:
    """
    Build one distance-weighted source per joint per topology component.

    This is the key v2.7 replacement for both fallback_joint and
    additional_shell_candidate_joints.
    """
    anchors: List[ComponentAnchor] = []

    for component_index, component in enumerate(components):
        component_ids = np.asarray(component, dtype=np.int32)
        nearest_ids, nearest_distances = _nearest_component_vertices_by_joint(
            component_ids=component_ids,
            positions=positions,
            joint_positions=joint_positions,
            distance_chunk_size=distance_chunk_size,
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
    best_vertex_ids = np.full(joint_count, -1, dtype=np.int32)
    tolerance = 1e-12

    for start in range(0, int(component_ids.size), distance_chunk_size):
        chunk_ids = component_ids[start:start + distance_chunk_size]
        chunk_positions = positions[chunk_ids]
        delta = (
            chunk_positions[:, np.newaxis, :]
            - joint_positions[np.newaxis, :, :]
        )
        squared = np.einsum("vji,vji->vj", delta, delta)
        local_rows = np.argmin(squared, axis=0)
        joint_columns = np.arange(joint_count, dtype=np.int32)
        local_squared = squared[local_rows, joint_columns]
        local_vertex_ids = chunk_ids[local_rows]

        for joint_index in range(joint_count):
            candidate_distance = float(local_squared[joint_index])
            candidate_vertex = int(local_vertex_ids[joint_index])
            current_distance = float(best_squared[joint_index])
            current_vertex = int(best_vertex_ids[joint_index])

            better = candidate_distance < current_distance - tolerance
            tied = abs(candidate_distance - current_distance) <= tolerance
            if better or (
                tied
                and (current_vertex < 0 or candidate_vertex < current_vertex)
            ):
                best_squared[joint_index] = candidate_distance
                best_vertex_ids[joint_index] = candidate_vertex

    if np.any(best_vertex_ids < 0):
        raise RuntimeError("Could not create automatic component anchors.")

    return best_vertex_ids, np.sqrt(best_squared)


def _build_seed_candidates(
    component_anchors: Tuple[ComponentAnchor, ...],
    radial_result: Optional[JointSeedResult],
    competitive_result: Optional[CompetitiveSeedResult],
    influences: Tuple[str, ...],
    positions: np.ndarray,
    joint_positions: np.ndarray,
    vertex_count: int,
) -> Tuple[Tuple[int, int, float, int], ...]:
    """
    Seed tuple:
        (vertex_id, owner_index, initial_cost, source_rank)

    source_rank is only a deterministic tie breaker:
        0 = radial source;
        1 = automatic component anchor.
    """
    index_by_joint = {
        joint: index
        for index, joint in enumerate(influences)
    }
    best_by_owner_vertex: Dict[
        Tuple[int, int],
        Tuple[float, int],
    ] = {}

    for anchor in component_anchors:
        key = (anchor.owner_index, anchor.seed_vertex_id)
        value = (anchor.initial_cost, 1)
        current = best_by_owner_vertex.get(key)
        if current is None or value < current:
            best_by_owner_vertex[key] = value

    radial_sources = {}
    if competitive_result is not None:
        radial_sources = competitive_result.resolved_seed_vertex_ids
    elif radial_result is not None:
        radial_sources = radial_result.seed_vertex_ids

    for joint, source_ids in radial_sources.items():
        owner_index = index_by_joint.get(joint)
        if owner_index is None:
            raise RuntimeError(
                "Radial result contains an influence outside v2.7 input:\n"
                + joint
            )
        joint_position = joint_positions[owner_index]

        for raw_vertex_id in source_ids:
            vertex_id = int(raw_vertex_id)
            if vertex_id < 0 or vertex_id >= vertex_count:
                raise RuntimeError(
                    "Radial seed contains invalid vertex ID: {}".format(
                        vertex_id
                    )
                )
            initial_cost = float(
                np.linalg.norm(positions[vertex_id] - joint_position)
            )
            key = (owner_index, vertex_id)
            value = (initial_cost, 0)
            current = best_by_owner_vertex.get(key)
            if current is None or value < current:
                best_by_owner_vertex[key] = value

    result = [
        (vertex_id, owner_index, initial_cost, source_rank)
        for (owner_index, vertex_id), (
            initial_cost,
            source_rank,
        ) in best_by_owner_vertex.items()
    ]
    result.sort(key=lambda item: (item[2], item[1], item[0], item[3]))

    if not result:
        raise RuntimeError("Automatic surface solve contains no sources.")
    return tuple(result)


def _propagate_weighted_owners(
    adjacency,
    seed_candidates: Tuple[Tuple[int, int, float, int], ...],
    vertex_count: int,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Weighted multi-source Dijkstra.

    Unlike v2.6 synthetic shell seeds, sources do not all begin at zero.
    Starting with real joint-to-surface distance prevents a distant joint from
    claiming a shell simply because it was assigned an artificial source.
    """
    costs = np.full(vertex_count, np.inf, dtype=np.float64)
    owner_indices = np.full(vertex_count, -1, dtype=np.int32)
    source_vertex_ids = np.full(vertex_count, -1, dtype=np.int32)
    source_ranks = np.full(vertex_count, 2**30, dtype=np.int32)
    heap = []
    tolerance = 1e-12

    for vertex_id, owner, initial_cost, source_rank in seed_candidates:
        if not np.isfinite(initial_cost) or initial_cost < 0.0:
            raise RuntimeError("Source contains an invalid initial cost.")

        old_cost = float(costs[vertex_id])
        old_key = (
            int(owner_indices[vertex_id]),
            int(source_ranks[vertex_id]),
            int(source_vertex_ids[vertex_id]),
        )
        candidate_key = (int(owner), int(source_rank), int(vertex_id))
        update = initial_cost < old_cost - tolerance
        if not update and abs(initial_cost - old_cost) <= tolerance:
            update = old_key[0] < 0 or candidate_key < old_key
        if not update:
            continue

        costs[vertex_id] = initial_cost
        owner_indices[vertex_id] = owner
        source_vertex_ids[vertex_id] = vertex_id
        source_ranks[vertex_id] = source_rank
        heapq.heappush(
            heap,
            (
                initial_cost,
                owner,
                source_rank,
                vertex_id,
                vertex_id,
            ),
        )

    while heap:
        (
            current_cost,
            owner,
            source_rank,
            source_vertex,
            vertex_id,
        ) = heapq.heappop(heap)

        stored_cost = float(costs[vertex_id])
        stored_key = (
            int(owner_indices[vertex_id]),
            int(source_ranks[vertex_id]),
            int(source_vertex_ids[vertex_id]),
        )
        candidate_key = (
            int(owner),
            int(source_rank),
            int(source_vertex),
        )
        if current_cost > stored_cost + tolerance:
            continue
        if (
            abs(current_cost - stored_cost) <= tolerance
            and candidate_key != stored_key
        ):
            continue

        for raw_neighbor, raw_edge_length in adjacency[vertex_id]:
            neighbor = int(raw_neighbor)
            edge_length = float(raw_edge_length)
            if not np.isfinite(edge_length) or edge_length <= 0.0:
                raise RuntimeError("Mesh topology contains invalid edge cost.")

            new_cost = current_cost + edge_length
            old_cost = float(costs[neighbor])
            old_key = (
                int(owner_indices[neighbor]),
                int(source_ranks[neighbor]),
                int(source_vertex_ids[neighbor]),
            )
            update = new_cost < old_cost - tolerance
            if not update and abs(new_cost - old_cost) <= tolerance:
                update = old_key[0] < 0 or candidate_key < old_key
            if not update:
                continue

            costs[neighbor] = new_cost
            owner_indices[neighbor] = owner
            source_vertex_ids[neighbor] = source_vertex
            source_ranks[neighbor] = source_rank
            heapq.heappush(
                heap,
                (
                    new_cost,
                    owner,
                    source_rank,
                    source_vertex,
                    neighbor,
                ),
            )

    return owner_indices, costs


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
                if neighbor not in remaining:
                    continue
                remaining.remove(neighbor)
                component.add(neighbor)
                stack.append(neighbor)

        components.append(tuple(sorted(component)))

    components.sort(key=lambda item: (-len(item), item[0]))
    return tuple(components)


def _radial_candidate_counts(
    radial_result: Optional[JointSeedResult],
    influences: Tuple[str, ...],
) -> Dict[str, int]:
    if radial_result is None:
        return {joint: 0 for joint in influences}
    return {
        joint: len(radial_result.seed_vertex_ids.get(joint, ()))
        for joint in influences
    }


def _competitive_seed_counts(
    result: Optional[CompetitiveSeedResult],
    influences: Tuple[str, ...],
) -> Dict[str, int]:
    if result is None:
        return {joint: 0 for joint in influences}
    return {
        joint: len(result.resolved_seed_vertex_ids.get(joint, ()))
        for joint in influences
    }


def _build_influence_diagnostics(
    influences: Tuple[str, ...],
    radial_result: Optional[JointSeedResult],
    radial_counts: Dict[str, int],
    competitive_counts: Dict[str, int],
    ownership_counts: Dict[str, int],
    component_count: int,
) -> Tuple[InfluenceAutomaticDiagnostic, ...]:
    radial_by_joint = {}
    if radial_result is not None:
        radial_by_joint = {
            item.joint: item
            for item in radial_result.diagnostics
        }

    result = []
    for joint in influences:
        radial = radial_by_joint.get(joint)
        messages = []

        if radial is None:
            messages.append("radial enrichment disabled or unavailable")
        elif not radial.valid:
            messages.extend(radial.messages)
            messages.append(
                "joint remained active through automatic component anchors"
            )

        if ownership_counts[joint] == 0:
            messages.append(
                "joint was geometrically outcompeted on every vertex"
            )

        result.append(
            InfluenceAutomaticDiagnostic(
                joint=joint,
                radial_candidate_count=radial_counts[joint],
                competitive_seed_count=competitive_counts[joint],
                component_anchor_count=component_count,
                ownership_count=ownership_counts[joint],
                radial_valid=bool(radial and radial.valid),
                messages=tuple(messages),
            )
        )
    return tuple(result)


def _resolve_mesh(mesh: str) -> Tuple[str, str]:
    """Resolve either one mesh shape or one transform containing one mesh."""
    if not mesh or not cmds.objExists(mesh):
        raise RuntimeError("Mesh does not exist.")

    matches = cmds.ls(mesh, long=True) or []
    if not matches:
        raise RuntimeError("Could not resolve mesh path:\n" + mesh)

    node = matches[0]
    node_type = cmds.nodeType(node)

    if node_type == "mesh":
        parents = cmds.listRelatives(
            node,
            parent=True,
            fullPath=True,
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
                "Geometry transform must contain exactly one "
                "non-intermediate mesh shape.\n\n"
                "Transform: {}\nMesh shapes: {}".format(
                    node,
                    len(shapes),
                )
            )
        return shapes[0], node

    raise RuntimeError(
        "Node is not a polygon mesh or mesh transform:\n" + node
    )


def _normalize_joint_paths(joints: Sequence[str]) -> Tuple[str, ...]:
    result = []
    seen = set()

    for joint in joints:
        matches = cmds.ls(joint, long=True, type="joint") or []
        if not matches:
            raise RuntimeError("Joint does not exist:\n" + joint)

        joint_path = matches[0]
        if joint_path in seen:
            continue
        seen.add(joint_path)
        result.append(joint_path)

    return tuple(result)


def _build_owner_vertex_map(
    owner_indices: np.ndarray,
    influences: Tuple[str, ...],
) -> Dict[str, Tuple[int, ...]]:
    return {
        joint: tuple(
            np.where(owner_indices == owner_index)[0]
            .astype(np.int32)
            .tolist()
        )
        for owner_index, joint in enumerate(influences)
    }


def _build_influence_column_map(
    source_influences: Tuple[str, ...],
    stored_influences: Tuple[str, ...],
) -> np.ndarray:
    stored_index = {
        joint: index
        for index, joint in enumerate(stored_influences)
    }
    source_set = set(source_influences)
    missing = [
        joint
        for joint in source_influences
        if joint not in stored_index
    ]
    unexpected = [
        joint
        for joint in stored_influences
        if joint not in source_set
    ]
    if missing or unexpected:
        raise RuntimeError(
            "skinCluster membership does not match v2.7 input.\n\n"
            "Missing:\n{}\n\nUnexpected:\n{}".format(
                "\n".join(missing) if missing else "None",
                "\n".join(unexpected) if unexpected else "None",
            )
        )

    return np.asarray(
        [stored_index[joint] for joint in source_influences],
        dtype=np.int32,
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
    if not np.allclose(weights.sum(axis=1), 1.0, atol=1e-6):
        raise RuntimeError("Stored weight rows do not all sum to 1.0.")
    if np.any(np.count_nonzero(weights > 1e-8, axis=1) != 1):
        raise RuntimeError("Hard ownership validation failed.")

    stored_owners = np.argmax(weights, axis=1).astype(np.int32)
    mismatch = np.where(stored_owners != expected_owner_indices)[0]
    if mismatch.size:
        raise RuntimeError(
            "Maya stored ownership differs from v2.7 result.\n\n"
            "Mismatched vertices: {}\nFirst IDs: {}".format(
                int(mismatch.size),
                mismatch[:20].tolist(),
            )
        )


def _resolve_result_joint(
    result: AutomaticSurfaceBindResult,
    joint: str,
) -> str:
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Joint does not exist: " + joint)

    path = matches[0]
    if path not in result.owner_vertex_ids:
        raise RuntimeError(
            "Joint was not included in this v2.7 result:\n" + path
        )
    return path


def _vertex_components(mesh_shape: str, vertex_ids) -> List[str]:
    parents = cmds.listRelatives(
        mesh_shape,
        parent=True,
        fullPath=True,
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
