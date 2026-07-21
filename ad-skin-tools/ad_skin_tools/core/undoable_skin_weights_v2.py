"""Versioned Maya command for in-memory undoable skin-weight writes."""

import maya.api.OpenMaya as om

from ad_skin_tools.core import undoable_skin_weights


COMMAND_NAME = undoable_skin_weights.COMMAND_NAME


def maya_useNewAPI():
    pass


class _SetWeightMatrixCommandV2(om.MPxCommand):
    def __init__(self):
        super(_SetWeightMatrixCommandV2, self).__init__()
        self._skin_cluster = None
        self._mesh_shape = None
        self._vertex_ids = None
        self._before_weights = None
        self._after_weights = None

    @staticmethod
    def creator():
        return _SetWeightMatrixCommandV2()

    def doIt(self, args):
        try:
            token = args.asString(0)
        except (IndexError, RuntimeError, TypeError) as exc:
            raise RuntimeError(
                "{} requires one in-memory payload token.\n\n{}".format(
                    COMMAND_NAME,
                    exc,
                )
            )

        (
            self._skin_cluster,
            self._mesh_shape,
            self._vertex_ids,
            self._before_weights,
            self._after_weights,
        ) = undoable_skin_weights._take_payload(token)

        self.redoIt()

    def redoIt(self):
        self._write(self._after_weights)

    def undoIt(self):
        self._write(self._before_weights)

    def isUndoable(self):
        return True

    def _write(self, weights):
        # Resolve the adapter at execution time so launch.reload_modules() can
        # refresh skin_cluster.py without leaving this loaded plug-in stale.
        from ad_skin_tools.core import skin_cluster

        adapter = skin_cluster.SkinClusterAdapter(
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
        "2.0.0",
        "Any",
    )
    plugin.registerCommand(
        COMMAND_NAME,
        _SetWeightMatrixCommandV2.creator,
    )


def uninitializePlugin(plugin_object):
    plugin = om.MFnPlugin(plugin_object)
    plugin.deregisterCommand(COMMAND_NAME)
