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


def get_vertex_positions(mesh_shape: str, vertex_ids: np.ndarray) -> np.ndarray:
    """
    Return world-space positions for given vertex ids.
    """
    dag_path = get_dag_path(mesh_shape)
    mesh_fn = om.MFnMesh(dag_path)
    points = mesh_fn.getPoints(om.MSpace.kWorld)

    positions = np.zeros((len(vertex_ids), 3), dtype=np.float64)

    for row, vertex_id in enumerate(vertex_ids):
        point = points[int(vertex_id)]
        positions[row] = [point.x, point.y, point.z]

    return positions


def get_all_vertex_neighbors(mesh_shape: str) -> List[List[int]]:
    """
    Return connected vertices for every vertex in the mesh.
    """
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


def get_world_positions(nodes: list[str]) -> np.ndarray:
    positions = []

    for node in nodes:
        if not cmds.objExists(node):
            raise RuntimeError(f"Influence does not exist: {node}")

        pos = cmds.xform(node, query=True, worldSpace=True, translation=True)
        positions.append(pos)

    return np.array(positions, dtype=np.float64)