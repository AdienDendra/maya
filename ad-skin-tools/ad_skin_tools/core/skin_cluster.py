from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from ad_skin_tools.core import mesh
from ad_skin_tools.core.compat import ensure_numpy

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
    """Focused API wrapper around one Maya ``skinCluster`` and output mesh."""

    def __init__(self, skin_cluster: str, mesh_shape: str):
        self.skin_cluster = str(skin_cluster)
        self.mesh_shape = str(mesh_shape)
        self.mesh_dag_path = mesh.get_dag_path(self.mesh_shape)
        self.skin_object = _get_depend_node(self.skin_cluster)
        self.skin_fn = oma.MFnSkinCluster(self.skin_object)

    @classmethod
    def from_mesh(cls, mesh_shape: str) -> "SkinClusterAdapter":
        return cls(
            skin_cluster=find_skin_cluster(mesh_shape, required=True),
            mesh_shape=mesh_shape,
        )

    def influences(self) -> List[str]:
        """Return bound influences as unambiguous full DAG paths."""

        return [path.fullPathName() for path in self._influence_objects()]

    def get_weights(self, vertex_ids: np.ndarray) -> SkinData:
        """Read the complete influence matrix for the requested vertices."""

        vertex_ids = _as_vertex_ids(vertex_ids)
        flat_weights, influence_count = self.skin_fn.getWeights(
            self.mesh_dag_path,
            _make_vertex_component(vertex_ids),
        )
        weights = np.asarray(flat_weights, dtype=np.float64).reshape(
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

    def influence_weights(self, influence: str) -> np.ndarray:
        """Read one full-mesh physical influence column without a full matrix."""

        influence_index = self._physical_influence_index(influence)
        vertex_count = self.vertex_count()
        result = self.skin_fn.getWeights(
            self.mesh_dag_path,
            _make_vertex_component(range(vertex_count)),
            influence_index,
        )
        if isinstance(result, tuple):
            result = result[0]

        weights = np.asarray(result, dtype=np.float64).reshape(-1)
        if len(weights) != vertex_count:
            raise SkinClusterError(
                "Influence weight count does not match the loaded mesh: "
                "{} != {}".format(len(weights), vertex_count)
            )
        return weights

    def affected_vertex_ids(self, influences: Iterable[str]) -> np.ndarray:
        """Return the union of non-zero vertices for the requested influences."""

        influence_objects = {
            path.fullPathName(): path
            for path in self._influence_objects()
        }
        mesh_path = self.mesh_dag_path.fullPathName()
        affected = set()

        for influence in influences:
            influence_path = influence_objects.get(str(influence))
            if influence_path is None:
                continue
            selection, _weights = self.skin_fn.getPointsAffectedByInfluence(
                influence_path
            )
            affected.update(
                _selection_vertex_ids(selection, expected_mesh_path=mesh_path)
            )

        return np.asarray(sorted(affected), dtype=np.int32)

    def set_weights(
        self,
        vertex_ids: np.ndarray,
        weights: np.ndarray,
        normalize: bool = True,
    ) -> None:
        """Write one dense vertex-by-influence matrix."""

        vertex_ids = _as_vertex_ids(vertex_ids)
        weights = np.asarray(weights, dtype=np.float64)
        if weights.ndim != 2:
            raise SkinClusterError("Weights must be a 2D matrix.")
        if weights.shape[0] != len(vertex_ids):
            raise SkinClusterError(
                "Weight row count does not match vertex count: "
                "{} != {}".format(weights.shape[0], len(vertex_ids))
            )

        influence_indices = om.MIntArray(list(range(weights.shape[1])))
        flat_weights = om.MDoubleArray(weights.ravel().tolist())
        self.skin_fn.setWeights(
            self.mesh_dag_path,
            _make_vertex_component(vertex_ids),
            influence_indices,
            flat_weights,
            bool(normalize),
        )

    def vertex_count(self) -> int:
        return int(om.MFnMesh(self.mesh_dag_path).numVertices)

    def _influence_objects(self):
        return self.skin_fn.influenceObjects()

    def _physical_influence_index(self, influence: str) -> int:
        target = str(influence)
        for index, path in enumerate(self._influence_objects()):
            if path.fullPathName() == target:
                return int(index)
        raise SkinClusterError(
            "Influence is not bound to the loaded skinCluster: {}".format(
                influence
            )
        )


def find_skin_cluster(mesh_shape: str, required: bool = True) -> Optional[str]:
    """Return the first skinCluster in mesh history."""

    history = cmds.listHistory(mesh_shape, pruneDagObjects=True) or []
    for node in history:
        if cmds.nodeType(node) == "skinCluster":
            return node

    if required:
        raise SkinClusterError("No skinCluster found on mesh: {}".format(mesh_shape))
    return None


def has_skin_cluster(mesh_shape: str) -> bool:
    return find_skin_cluster(mesh_shape, required=False) is not None


def create_closest_skin_cluster(
    mesh_shape: str,
    mesh_transform: str,
    joints: Sequence[str],
    max_influences: int = 5,
) -> SkinClusterAdapter:
    """Create an empty-enough skinCluster container for the custom solver."""

    if find_skin_cluster(mesh_shape, required=False):
        raise SkinClusterError(
            "The loaded object already has skin weights. "
            "Object-wide Closest binding is not allowed."
        )
    if not cmds.objExists(mesh_transform):
        raise SkinClusterError(
            "Loaded mesh no longer exists: {}".format(mesh_transform)
        )

    max_influences = int(max_influences)
    if max_influences < 1:
        raise SkinClusterError("Maximum influences must be at least 1.")

    normalized_joints = _normalize_joint_paths(joints)
    if len(normalized_joints) < 2:
        raise SkinClusterError(
            "Segment Weighted Bind requires at least two joints."
        )

    created = cmds.skinCluster(
        *(normalized_joints + [mesh_transform]),
        name=_next_available_skin_cluster_name(mesh_transform),
        toSelectedBones=True,
        bindMethod=0,
        skinMethod=0,
        maximumInfluences=max_influences,
        obeyMaxInfluences=False,
        normalizeWeights=1,
    )
    skin_cluster = _first_result(created)
    if not skin_cluster or not cmds.objExists(skin_cluster):
        raise SkinClusterError("Maya did not return a valid skinCluster.")

    return SkinClusterAdapter(
        skin_cluster=skin_cluster,
        mesh_shape=mesh_shape,
    )


def _normalize_joint_paths(joints: Sequence[str]) -> List[str]:
    normalized = []
    seen = set()
    for joint in joints:
        matches = cmds.ls(joint, long=True, type="joint") or []
        if not matches:
            raise SkinClusterError("Joint no longer exists: {}".format(joint))
        path = matches[0]
        if path not in seen:
            seen.add(path)
            normalized.append(path)
    return normalized


def _selection_vertex_ids(selection, expected_mesh_path: str):
    for index in range(selection.length()):
        try:
            dag_path, component = selection.getComponent(index)
        except (RuntimeError, TypeError):
            continue
        if component.isNull():
            continue
        try:
            if dag_path.node().hasFn(om.MFn.kTransform):
                dag_path.extendToShape()
        except RuntimeError:
            continue
        if dag_path.fullPathName() != expected_mesh_path:
            continue
        if not component.hasFn(om.MFn.kMeshVertComponent):
            continue
        component_fn = om.MFnSingleIndexedComponent(component)
        for value in component_fn.getElements():
            yield int(value)


def _first_result(result):
    if isinstance(result, (list, tuple)):
        return result[0] if result else None
    return result


def _next_available_skin_cluster_name(mesh_transform: str) -> str:
    mesh_name = str(mesh_transform).rsplit("|", 1)[-1]
    base_name = "{}_adSc".format(mesh_name)
    if not cmds.objExists(base_name):
        return base_name

    index = 1
    while cmds.objExists("{}{}".format(base_name, index)):
        index += 1
    return "{}{}".format(base_name, index)


def _get_depend_node(node_name: str) -> om.MObject:
    selection = om.MSelectionList()
    selection.add(node_name)
    return selection.getDependNode(0)


def _as_vertex_ids(vertex_ids) -> np.ndarray:
    return np.asarray(vertex_ids, dtype=np.int32).reshape(-1)


def _make_vertex_component(vertex_ids) -> om.MObject:
    component_fn = om.MFnSingleIndexedComponent()
    component = component_fn.create(om.MFn.kMeshVertComponent)
    component_fn.addElements([int(vertex_id) for vertex_id in vertex_ids])
    return component
