from dataclasses import dataclass
from typing import List

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy, get_rich_selection_list

np = ensure_numpy()


class SelectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComponentSelection:
    mesh_shape: str
    mesh_transform: str
    vertex_ids: np.ndarray
    falloff: np.ndarray

    @property
    def count(self) -> int:
        return int(self.vertex_ids.size)


@dataclass(frozen=True)
class MeshObjectSelection:
    mesh_shape: str
    mesh_transform: str


def get_selected_mesh_object() -> MeshObjectSelection:
    """
    Read selected mesh object.

    Used by Load Skin Weight.

    Accepts:
    - selected mesh transform
    - selected mesh shape
    - selected mesh component, but only uses the owning mesh
    """
    selected = cmds.ls(selection=True, long=True) or []

    if not selected:
        raise SelectionError("Select a mesh object first.")

    for item in selected:
        node = item.split(".")[0]
        mesh_shape = _find_mesh_shape(node)

        if mesh_shape:
            return MeshObjectSelection(
                mesh_shape=mesh_shape,
                mesh_transform=_get_parent_transform(mesh_shape),
            )

    raise SelectionError("Selected object is not a polygon mesh.")


def get_component_selection() -> ComponentSelection:
    """
    Read Maya component selection.

    Supports Maya 2023+ through compat.get_rich_selection_list().
    """
    selection_list = get_rich_selection_list(default_to_active=True)

    result = _collect_mesh_vertex_components(
        selection_list,
        read_soft_weights=True,
    )

    if result is None:
        active_selection = om.MGlobal.getActiveSelectionList()
        result = _collect_mesh_vertex_components(
            active_selection,
            read_soft_weights=False,
        )

    if result is None:
        raise SelectionError(
            "Select mesh vertices first. Component vertex selection is required."
        )

    return result


def get_selected_joints() -> List[str]:
    """
    Return currently selected joints.
    """
    selected = cmds.ls(selection=True, type="joint", long=True) or []
    return selected


def _collect_mesh_vertex_components(selection_list, read_soft_weights=True):
    collected = {}

    iterator = om.MItSelectionList(selection_list, om.MFn.kMeshVertComponent)

    while not iterator.isDone():
        dag_path, component = iterator.getComponent()
        mesh_dag = _as_mesh_shape_dag_path(dag_path)

        mesh_shape = mesh_dag.fullPathName()
        mesh_transform = _get_parent_transform(mesh_shape)

        component_fn = om.MFnSingleIndexedComponent(component)
        elements = list(component_fn.getElements())

        if not elements:
            iterator.next()
            continue

        falloff_values = []

        for local_index, _vertex_id in enumerate(elements):
            value = 1.0

            if read_soft_weights:
                try:
                    weight_obj = component_fn.weight(local_index)
                    influence = getattr(weight_obj, "influence", 1.0)
                    value = influence() if callable(influence) else influence
                except Exception:
                    value = 1.0

            falloff_values.append(float(value))

        key = mesh_shape

        if key not in collected:
            collected[key] = {
                "mesh_shape": mesh_shape,
                "mesh_transform": mesh_transform,
                "ids": [],
                "falloff": [],
            }

        collected[key]["ids"].extend(elements)
        collected[key]["falloff"].extend(falloff_values)

        iterator.next()

    if not collected:
        return None

    if len(collected) > 1:
        raise SelectionError(
            "Multiple meshes selected. For now, select vertices from one mesh only."
        )

    data = list(collected.values())[0]

    merged = {}
    for vertex_id, falloff in zip(data["ids"], data["falloff"]):
        vertex_id = int(vertex_id)
        falloff = float(falloff)
        merged[vertex_id] = max(merged.get(vertex_id, 0.0), falloff)

    vertex_ids = np.array(sorted(merged.keys()), dtype=np.int32)
    falloff = np.array(
        [merged[int(vertex_id)] for vertex_id in vertex_ids],
        dtype=np.float64,
    )

    return ComponentSelection(
        mesh_shape=data["mesh_shape"],
        mesh_transform=data["mesh_transform"],
        vertex_ids=vertex_ids,
        falloff=falloff,
    )


def _find_mesh_shape(node_name: str):
    """
    Return the non-intermediate mesh shape for a transform/shape/component owner.
    """
    if not cmds.objExists(node_name):
        return None

    node_type = cmds.nodeType(node_name)

    if node_type == "mesh":
        try:
            if not cmds.getAttr(f"{node_name}.intermediateObject"):
                return cmds.ls(node_name, long=True)[0]
        except Exception:
            return cmds.ls(node_name, long=True)[0]

        return None

    shapes = cmds.listRelatives(
        node_name,
        shapes=True,
        noIntermediate=True,
        fullPath=True,
    ) or []

    for shape in shapes:
        if cmds.nodeType(shape) == "mesh":
            return shape

    return None


def _as_mesh_shape_dag_path(dag_path):
    mesh_dag = om.MDagPath(dag_path)

    if not mesh_dag.node().hasFn(om.MFn.kMesh):
        try:
            mesh_dag.extendToShape()
        except Exception as exc:
            raise SelectionError("Selected component is not on a mesh.") from exc

    return mesh_dag


def _get_parent_transform(mesh_shape: str) -> str:
    parents = cmds.listRelatives(mesh_shape, parent=True, fullPath=True) or []

    if not parents:
        return mesh_shape

    return parents[0]