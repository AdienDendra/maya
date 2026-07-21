"""Undoable skin-weight matrix writes for Maya."""

import builtins
import os
import uuid

import maya.cmds as cmds

from ad_skin_tools.core.compat import ensure_numpy


np = ensure_numpy()
COMMAND_NAME = "adSkinSetWeightMatrixV2"
_PLUGIN_FILENAME = "undoable_skin_weights_v2.py"
_PAYLOAD_REGISTRY_NAME = "_AD_SKIN_WEIGHT_PAYLOADS"


def apply_undoable_weights(
    skin_cluster,
    mesh_shape,
    vertex_ids,
    before_weights,
    after_weights,
):
    """Write one skin-weight matrix through Maya's undo queue.

    The payload is transferred through a short-lived in-memory registry. The
    versioned Maya command lives in a separate plug-in module so an older loaded
    command cannot mistake the registry token for the legacy temporary file path.
    """

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

    token = uuid.uuid4().hex
    registry = _payload_registry()
    registry[token] = (
        str(skin_cluster),
        str(mesh_shape),
        np.array(vertex_ids, dtype=np.int32, copy=True, order="C"),
        np.array(before_weights, dtype=np.float64, copy=True, order="C"),
        np.array(after_weights, dtype=np.float64, copy=True, order="C"),
    )

    try:
        getattr(cmds, COMMAND_NAME)(token)
    finally:
        # Normally doIt() consumes the payload. This also clears it if command
        # dispatch fails before the plug-in can take ownership.
        registry.pop(token, None)


def _payload_registry():
    registry = getattr(builtins, _PAYLOAD_REGISTRY_NAME, None)
    if registry is None:
        registry = {}
        setattr(builtins, _PAYLOAD_REGISTRY_NAME, registry)
    return registry


def _take_payload(token):
    payload = _payload_registry().pop(str(token), None)
    if payload is None:
        raise RuntimeError(
            "{} could not resolve its in-memory weight payload.".format(
                COMMAND_NAME
            )
        )
    return payload


def _plugin_path():
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        _PLUGIN_FILENAME,
    )


def _ensure_plugin_loaded():
    plugin_path = _plugin_path()
    plugin_name = os.path.splitext(os.path.basename(plugin_path))[0]

    if not os.path.exists(plugin_path):
        raise RuntimeError(
            "Undoable skin-weight plug-in is missing:\n{}".format(plugin_path)
        )

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

    try:
        getattr(cmds, COMMAND_NAME)
    except AttributeError:
        raise RuntimeError(
            "Maya loaded the undo plug-in but did not register {}.".format(
                COMMAND_NAME
            )
        )
