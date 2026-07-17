"""Influence weight-lock helpers backed by Maya's skinCluster state.

Bound influence locks are scene data, not UI-only decoration. The helpers use the
skinCluster ``influence`` + ``lockWeights`` edit/query contract and fall back to
Maya's joint ``liw`` attribute where required by a Maya version.
"""

from typing import Iterable, Tuple

import maya.cmds as cmds


def is_influence_locked(skin_cluster: str, joint: str) -> bool:
    """Return Maya's current weight-lock state for one skin influence."""

    resolved_joint = _resolve_joint(joint)
    if not cmds.objExists(skin_cluster):
        raise RuntimeError("skinCluster no longer exists:\n{}".format(skin_cluster))

    try:
        value = cmds.skinCluster(
            skin_cluster,
            query=True,
            influence=resolved_joint,
            lockWeights=True,
        )
        normalized = _normalize_query_value(value)
        if normalized is not None:
            return normalized
    except Exception:
        pass

    lock_attribute = "{}.liw".format(resolved_joint)
    if cmds.objExists(lock_attribute):
        return bool(cmds.getAttr(lock_attribute))
    return False


def set_influence_locked(
    skin_cluster: str,
    joint: str,
    locked: bool,
) -> bool:
    """Set and verify one bound influence's Maya weight-lock state."""

    resolved_joint = _resolve_joint(joint)
    requested = bool(locked)
    if not cmds.objExists(skin_cluster):
        raise RuntimeError("skinCluster no longer exists:\n{}".format(skin_cluster))

    edit_error = None
    try:
        cmds.skinCluster(
            skin_cluster,
            edit=True,
            influence=resolved_joint,
            lockWeights=requested,
        )
    except Exception as exc:
        edit_error = exc

    # Maya exposes the same state through the influence's ``liw`` attribute.
    # Keep this fallback for versions where the command query/edit is incomplete.
    lock_attribute = "{}.liw".format(resolved_joint)
    if cmds.objExists(lock_attribute):
        try:
            cmds.setAttr(lock_attribute, requested)
            edit_error = None
        except Exception:
            if edit_error is not None:
                raise RuntimeError(
                    "Unable to {} influence weights:\n{}\n\n{}".format(
                        "lock" if requested else "unlock",
                        resolved_joint,
                        edit_error,
                    )
                )

    actual = is_influence_locked(skin_cluster, resolved_joint)
    if actual != requested:
        raise RuntimeError(
            "Maya did not store the requested influence lock state.\n\n"
            "Influence: {}\nRequested: {}\nStored: {}".format(
                resolved_joint,
                requested,
                actual,
            )
        )
    return actual


def locked_influences(
    skin_cluster: str,
    influences: Iterable[str],
) -> Tuple[str, ...]:
    """Return supplied influences that are currently locked, preserving order."""

    return tuple(
        joint
        for joint in influences
        if is_influence_locked(skin_cluster, joint)
    )


def _resolve_joint(joint: str) -> str:
    matches = cmds.ls(joint, long=True, type="joint") or []
    if not matches:
        raise RuntimeError("Joint no longer exists:\n{}".format(joint))
    return matches[0]


def _normalize_query_value(value):
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return bool(value[0])
    if value is None:
        return None
    return bool(value)
