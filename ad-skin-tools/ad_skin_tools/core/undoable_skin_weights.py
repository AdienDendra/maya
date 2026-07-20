"""Undoable skin-weight matrix writes for Maya."""

import os
import tempfile

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter


np = ensure_numpy()
COMMAND_NAME = "adSkinSetWeightMatrix"


def maya_useNewAPI():
    pass


def apply_undoable_weights(
    skin_cluster,
    mesh_shape,
    vertex_ids,
    before_weights,
    after_weights,
):
    """Write one skin-weight matrix through Maya's undo queue."""

    vertex_ids = np.asarray(vertex_ids, dtype=np.int32)
    before_weights = np.asarray(before_weights, dtype=np.float64)
    after_weights = np.asarray(after_weights, dtype=np.float64)

    if before_weights.shape != after_weights.shape:
        raise RuntimeError("Undoable weight matrices do not have matching shapes.")
    if before_weights.ndim != 2:
        raise RuntimeError("Undoable weight matrices must be two-dimensional.")
    if before_weights.shape[0] != vertex_ids.size:
        raise RuntimeError("Undoable weight row count does not match vertex count.")

    _ensure_plugin_loaded()

    descriptor, payload_path = tempfile.mkstemp(
        prefix="ad_skin_weights_",
        suffix=".npz",
    )
    os.close(descriptor)

    try:
        np.savez_compressed(
            payload_path,
            skin_cluster=np.asarray(str(skin_cluster)),
            mesh_shape=np.asarray(str(mesh_shape)),
            vertex_ids=vertex_ids,
            before_weights=before_weights,
            after_weights=after_weights,
        )
        getattr(cmds, COMMAND_NAME)(payload_path)
    finally:
        try:
            if os.path.exists(payload_path):
                os.remove(payload_path)
        except Exception:
            pass


def _ensure_plugin_loaded():
    plugin_path = os.path.abspath(__file__)
    plugin_name = os.path.splitext(os.path.basename(plugin_path))[0]

    try:
        loaded = bool(
            cmds.pluginInfo(
                plugin_name,
                query=True,
                loaded=True,
            )
        )
    except Exception:
        loaded = False

    if not loaded:
        cmds.loadPlugin(plugin_path, quiet=True)


class _SetWeightMatrixCommand(om.MPxCommand):
    def __init__(self):
        super(_SetWeightMatrixCommand, self).__init__()
        self._skin_cluster = None
        self._mesh_shape = None
        self._vertex_ids = None
        self._before_weights = None
        self._after_weights = None

    @staticmethod
    def creator():
        return _SetWeightMatrixCommand()

    def doIt(self, args):
        try:
            payload_path = args.asString(0)
        except (IndexError, RuntimeError, TypeError) as exc:
            raise RuntimeError(
                "{} requires one weight payload path.\n\n{}".format(
                    COMMAND_NAME,
                    exc,
                )
            )

        with np.load(payload_path, allow_pickle=False) as payload:
            self._skin_cluster = str(payload["skin_cluster"].item())
            self._mesh_shape = str(payload["mesh_shape"].item())
            self._vertex_ids = np.asarray(
                payload["vertex_ids"],
                dtype=np.int32,
            ).copy()
            self._before_weights = np.asarray(
                payload["before_weights"],
                dtype=np.float64,
            ).copy()
            self._after_weights = np.asarray(
                payload["after_weights"],
                dtype=np.float64,
            ).copy()

        self.redoIt()

    def redoIt(self):
        self._write(self._after_weights)

    def undoIt(self):
        self._write(self._before_weights)

    def isUndoable(self):
        return True

    def _write(self, weights):
        adapter = SkinClusterAdapter(
            skin_cluster=self._skin_cluster,
            mesh_shape=self._mesh_shape,
        )
        adapter.set_weights(
            self._vertex_ids,
            weights,
            normalize=False,
        )


def initializePlugin(plugin_object):
    plugin = om.MFnPlugin(
        plugin_object,
        "AD Skin Tool",
        "1.0.0",
        "Any",
    )
    plugin.registerCommand(
        COMMAND_NAME,
        _SetWeightMatrixCommand.creator,
    )


def uninitializePlugin(plugin_object):
    plugin = om.MFnPlugin(plugin_object)
    plugin.deregisterCommand(COMMAND_NAME)
