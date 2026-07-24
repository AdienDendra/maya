"""User event used to invalidate Skin Weight Visual after weight writes."""

import maya.api.OpenMaya as om


EVENT_NAME = "adSkinWeightsChanged"


def ensure_registered() -> None:
    """Register the event once for the current Maya process."""

    if not om.MUserEventMessage.isUserEvent(EVENT_NAME):
        om.MUserEventMessage.registerUserEvent(EVENT_NAME)


def add_callback(callback):
    """Register one callback and return its Maya callback id."""

    ensure_registered()
    return om.MUserEventMessage.addUserEventCallback(EVENT_NAME, callback)


def post(mesh_shape=None) -> None:
    """Post an already-registered invalidation event after a weight write."""

    if not om.MUserEventMessage.isUserEvent(EVENT_NAME):
        return
    client_data = str(mesh_shape) if mesh_shape else None
    om.MUserEventMessage.postUserEvent(EVENT_NAME, client_data)
