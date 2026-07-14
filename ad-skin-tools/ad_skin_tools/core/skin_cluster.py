from dataclasses import dataclass
from typing import List, Optional

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core import mesh

np = ensure_numpy()


class SkinClusterError(RuntimeError):
    pass


@dataclass(frozen=True)
class SkinData:
    skin_cluster: str
    mesh_shape: str
    vertex_ids: np.ndarray
    influences: List[str]
    weights: np.ndarray


class SkinClusterAdapter:
    """
    Small API wrapper around Maya skinCluster.

    Responsibility:
    - find skinCluster
    - read influence list
    - read weight matrix
    - write weight matrix
    """

    def __init__(self, skin_cluster: str, mesh_shape: str):
        self.skin_cluster = skin_cluster
        self.mesh_shape = mesh_shape
        self.mesh_dag_path = mesh.get_dag_path(mesh_shape)
        self.skin_object = _get_depend_node(skin_cluster)
        self.skin_fn = oma.MFnSkinCluster(self.skin_object)

    @classmethod
    def from_mesh(cls, mesh_shape: str) -> "SkinClusterAdapter":
        skin_cluster = find_skin_cluster(mesh_shape, required=True)
        return cls(skin_cluster=skin_cluster, mesh_shape=mesh_shape)

    def influences(self) -> List[str]:
        """
        Return full DAG paths.

        Full paths are required so duplicate joint names remain unambiguous.
        The UI is responsible for displaying shorter readable labels.
        """
        paths = self.skin_fn.influenceObjects()
        return [path.fullPathName() for path in paths]

    def get_weights(self, vertex_ids: np.ndarray) -> SkinData:
        vertex_ids = np.asarray(vertex_ids, dtype=np.int32)
        component = _make_vertex_component(vertex_ids)

        flat_weights, influence_count = self.skin_fn.getWeights(
            self.mesh_dag_path,
            component,
        )

        weights = np.array(flat_weights, dtype=np.float64).reshape(
            len(vertex_ids),
            int(influence_count),
        )

        return SkinData(
            skin_cluster=self.skin_cluster,
            mesh_shape=self.mesh_shape,
            vertex_ids=vertex_ids,
            influences=self.influences(),
            weights=weights,
        )

    def set_weights(
        self,
        vertex_ids: np.ndarray,
        weights: np.ndarray,
        normalize: bool = True,
    ) -> None:
        vertex_ids = np.asarray(vertex_ids, dtype=np.int32)
        weights = np.asarray(weights, dtype=np.float64)

        if weights.ndim != 2:
            raise SkinClusterError("Weights must be a 2D matrix.")

        if weights.shape[0] != len(vertex_ids):
            raise SkinClusterError(
                f"Weight row count does not match vertex count: "
                f"{weights.shape[0]} != {len(vertex_ids)}"
            )

        component = _make_vertex_component(vertex_ids)

        influence_count = weights.shape[1]
        influence_indices = om.MIntArray(list(range(influence_count)))
        flat_weights = om.MDoubleArray(weights.ravel().tolist())

        self.skin_fn.setWeights(
            self.mesh_dag_path,
            component,
            influence_indices,
            flat_weights,
            normalize,
        )


def find_skin_cluster(mesh_shape: str, required: bool = True) -> Optional[str]:
    """
    Find skinCluster from mesh history.

    required=True:
        Raise SkinClusterError if not found.

    required=False:
        Return None if not found.
    """
    history = cmds.listHistory(mesh_shape, pruneDagObjects=True) or []
    skin_clusters = [
        node for node in history
        if cmds.nodeType(node) == "skinCluster"
    ]

    if not skin_clusters:
        if required:
            raise SkinClusterError(f"No skinCluster found on mesh: {mesh_shape}")
        return None

    return skin_clusters[0]


def has_skin_cluster(mesh_shape: str) -> bool:
    return find_skin_cluster(mesh_shape, required=False) is not None

def create_closest_skin_cluster(
    mesh_shape: str,
    mesh_transform: str,
    joints: List[str],
) -> SkinClusterAdapter:
    """
    Create a skinCluster container for the custom Closest solver.

    Important:
    Maya may generate temporary initial weights while creating the skinCluster,
    but commands.bind_object_closest() immediately replaces every vertex row
    with weights calculated by our own world-space distance solver.
    """
    existing_skin = find_skin_cluster(
        mesh_shape,
        required=False,
    )

    if existing_skin:
        raise SkinClusterError(
            "The loaded object already has skin weights. "
            "Object-wide Closest binding is not allowed."
        )

    if not cmds.objExists(mesh_transform):
        raise SkinClusterError(
            f"Loaded mesh no longer exists: {mesh_transform}"
        )

    normalized_joints = []
    seen = set()

    for joint in joints:
        matches = cmds.ls(
            joint,
            long=True,
            type="joint",
        ) or []

        if not matches:
            raise SkinClusterError(
                f"Joint no longer exists: {joint}"
            )

        joint_path = matches[0]

        if joint_path in seen:
            continue

        seen.add(joint_path)
        normalized_joints.append(joint_path)

    if len(normalized_joints) < 2:
        raise SkinClusterError(
            "Closest Object Bind requires at least two joints."
        )

    created = cmds.skinCluster(
        *(normalized_joints + [mesh_transform]),
        toSelectedBones=True,
        bindMethod=0,
        skinMethod=0,
        maximumInfluences=1,
        obeyMaxInfluences=False,
        normalizeWeights=1,
    )

    skin_cluster = (
        created[0]
        if isinstance(created, (list, tuple))
        else created
    )

    if not skin_cluster or not cmds.objExists(skin_cluster):
        raise SkinClusterError(
            "Maya did not return a valid skinCluster."
        )

    return SkinClusterAdapter(
        skin_cluster=skin_cluster,
        mesh_shape=mesh_shape,
    )
        
def _get_depend_node(node_name: str) -> om.MObject:
    selection = om.MSelectionList()
    selection.add(node_name)
    return selection.getDependNode(0)


def _make_vertex_component(vertex_ids: np.ndarray) -> om.MObject:
    component_fn = om.MFnSingleIndexedComponent()
    component = component_fn.create(om.MFn.kMeshVertComponent)
    component_fn.addElements([int(v) for v in vertex_ids])
    return component