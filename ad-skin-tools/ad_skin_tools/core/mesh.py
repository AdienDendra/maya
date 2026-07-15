from typing import List

import maya.api.OpenMaya as om
import maya.cmds as cmds
from ad_skin_tools.core.compat import ensure_numpy

np = ensure_numpy()


def get_dag_path(node_name: str) -> om.MDagPath:
    selection = om.MSelectionList()
    selection.add(node_name)
    dag_path = selection.getDagPath(0)

    if not dag_path.node().hasFn(om.MFn.kMesh):
        dag_path.extendToShape()

    return dag_path


def get_vertex_count(mesh_shape: str) -> int:
    dag_path = get_dag_path(mesh_shape)
    mesh_fn = om.MFnMesh(dag_path)
    return int(mesh_fn.numVertices)


def get_vertex_positions(
    mesh_shape: str,
    vertex_ids: np.ndarray,
) -> np.ndarray:
    """Return world-space positions for given vertex ids."""
    dag_path = get_dag_path(mesh_shape)
    mesh_fn = om.MFnMesh(dag_path)
    points = mesh_fn.getPoints(om.MSpace.kWorld)

    positions = np.zeros(
        (len(vertex_ids), 3),
        dtype=np.float64,
    )

    for row, vertex_id in enumerate(vertex_ids):
        point = points[int(vertex_id)]
        positions[row] = [point.x, point.y, point.z]

    return positions


def get_vertex_normals(
    mesh_shape: str,
    vertex_ids: np.ndarray,
    angle_weighted: bool = True,
) -> np.ndarray:
    """
    Return averaged world-space normals for the requested vertices.

    MFnMesh performs the face-normal averaging once in C++; Python only
    copies the requested rows into a NumPy matrix. This avoids per-vertex
    cmds.polyNormalPerVertex calls.
    """
    dag_path = get_dag_path(mesh_shape)
    mesh_fn = om.MFnMesh(dag_path)
    normals_array = mesh_fn.getVertexNormals(
        bool(angle_weighted),
        om.MSpace.kWorld,
    )

    normals = np.zeros(
        (len(vertex_ids), 3),
        dtype=np.float64,
    )

    for row, vertex_id in enumerate(vertex_ids):
        normal = normals_array[int(vertex_id)]
        normals[row] = [normal.x, normal.y, normal.z]

    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-12
    normals[valid] /= lengths[valid, np.newaxis]

    return normals


def get_all_vertex_neighbors(mesh_shape: str) -> List[List[int]]:
    """Return connected vertices for every vertex in the mesh."""
    dag_path = get_dag_path(mesh_shape)
    vertex_count = get_vertex_count(mesh_shape)

    neighbors = [[] for _ in range(vertex_count)]
    iterator = om.MItMeshVertex(dag_path)

    while not iterator.isDone():
        vertex_id = int(iterator.index())
        connected = list(iterator.getConnectedVertices())
        neighbors[vertex_id] = [int(v) for v in connected]
        iterator.next()

    return neighbors


def get_weighted_vertex_neighbors(
    mesh_shape: str,
) -> List[List[tuple[int, float]]]:
    """
    Build a weighted graph from the mesh topology.

    Each vertex stores:
        [
            (connected_vertex_id, world_space_edge_length),
            ...
        ]

    The edge length becomes the traversal cost for surface/geodesic
    distance calculations.

    Unlike world-space volume distance, propagation can only travel
    through actual connected mesh edges.
    """
    dag_path = get_dag_path(mesh_shape)
    mesh_fn = om.MFnMesh(dag_path)

    points = mesh_fn.getPoints(
        om.MSpace.kWorld
    )
    vertex_count = int(
        mesh_fn.numVertices
    )

    adjacency = [
        []
        for _ in range(vertex_count)
    ]

    iterator = om.MItMeshVertex(
        dag_path
    )
    minimum_edge_length = 1e-12

    while not iterator.isDone():
        vertex_id = int(
            iterator.index()
        )
        source_point = points[vertex_id]
        connected_vertices = iterator.getConnectedVertices()
        weighted_neighbors = []

        for neighbor_id in connected_vertices:
            neighbor_id = int(neighbor_id)
            target_point = points[neighbor_id]
            delta_x = target_point.x - source_point.x
            delta_y = target_point.y - source_point.y
            delta_z = target_point.z - source_point.z
            edge_length = (
                delta_x * delta_x
                + delta_y * delta_y
                + delta_z * delta_z
            ) ** 0.5
            edge_length = max(
                float(edge_length),
                minimum_edge_length,
            )
            weighted_neighbors.append(
                (
                    neighbor_id,
                    edge_length,
                )
            )

        adjacency[vertex_id] = weighted_neighbors
        iterator.next()

    return adjacency


def get_world_positions(nodes: list[str]) -> np.ndarray:
    positions = []

    for node in nodes:
        if not cmds.objExists(node):
            raise RuntimeError(
                f"Influence does not exist: {node}"
            )

        position = cmds.xform(
            node,
            query=True,
            worldSpace=True,
            translation=True,
        )
        positions.append(position)

    return np.array(positions, dtype=np.float64)
