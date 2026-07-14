import re
import sys

import maya.cmds as cmds
import maya.api.OpenMaya as om


def maya_version() -> int:
    """
    Return Maya major version.

    Examples:
        "2023"   -> 2023
        "2023.3" -> 2023
        "2026"   -> 2026
    """
    version_text = cmds.about(version=True)
    match = re.search(r"\d{4}", version_text)

    if not match:
        return 0

    return int(match.group(0))


def maya_api_version() -> int:
    try:
        return int(cmds.about(apiVersion=True))
    except Exception:
        return 0


def python_version_tuple():
    return sys.version_info[:3]


def is_maya_2025_or_newer() -> bool:
    return maya_version() >= 2025


def get_rich_selection_list(default_to_active=True):
    """
    API-safe rich selection reader.

    Maya API 2.0 returns MRichSelection directly in newer signatures.
    Some versions/signatures differ, so we try a few safe options.
    """
    try:
        rich_selection = om.MGlobal.getRichSelection(default_to_active)
        return rich_selection.getSelection()

    except TypeError:
        pass

    try:
        rich_selection = om.MGlobal.getRichSelection()
        return rich_selection.getSelection()

    except Exception:
        return om.MGlobal.getActiveSelectionList()


def import_qt_modules():
    """
    Import Qt modules safely across Maya versions.

    Maya <= 2024 commonly uses PySide2.
    Maya 2025+ moved toward PySide6 / Qt6.
    We use feature detection instead of version-only branching.
    """
    try:
        from PySide6 import QtWidgets, QtGui, QtCore
        return QtWidgets, QtGui, QtCore, "PySide6"
    except ImportError:
        pass

    try:
        from PySide2 import QtWidgets, QtGui, QtCore
        return QtWidgets, QtGui, QtCore, "PySide2"
    except ImportError as exc:
        raise RuntimeError(
            "Could not import PySide6 or PySide2 from Maya Python."
        ) from exc


def ensure_numpy():
    """
    Friendly NumPy checker.

    The tool requires NumPy because skin weights are handled as matrices.
    """
    try:
        import numpy as np
        return np
    except ImportError as exc:
        major = maya_version()
        pyver = ".".join(str(v) for v in python_version_tuple())

        raise RuntimeError(
            "NumPy is not installed in Maya's Python environment.\n\n"
            f"Maya version: {major}\n"
            f"Python version: {pyver}\n\n"
            "Install NumPy using the matching mayapy.exe, for example:\n\n"
            f'"C:/Program Files/Autodesk/Maya{major}/bin/mayapy.exe" '
            '-m pip install "numpy<2"\n\n'
            "For Maya 2023, numpy==1.23.5 is a safe option."
        ) from exc


def environment_report() -> str:
    """
    Useful for debugging user machines.
    """
    qt_name = "Not checked"

    try:
        _, _, _, qt_name = import_qt_modules()
    except Exception:
        qt_name = "Unavailable"

    np_version = "Unavailable"

    try:
        np = ensure_numpy()
        np_version = np.__version__
    except Exception:
        pass

    return (
        f"Maya: {cmds.about(version=True)}\n"
        f"Maya API: {maya_api_version()}\n"
        f"Python: {sys.version}\n"
        f"Qt Binding: {qt_name}\n"
        f"NumPy: {np_version}"
    )