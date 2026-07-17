"""Report which AD Skin Tool package Maya is actually importing."""

import os
import sys

import ad_skin_tools
from ad_skin_tools import launch
from ad_skin_tools.ui import (
    component_flood_section,
    joint_tree_maya2023,
    tool_window,
)


package_dir = os.path.dirname(os.path.abspath(ad_skin_tools.__file__))
joint_tree_path = os.path.join(package_dir, "ui", "joint_tree_maya2023.py")
ui_init_path = os.path.join(package_dir, "ui", "__init__.py")
deploy_marker_path = os.path.join(package_dir, ".ad_skin_deploy_info")

expected_files = {
    "component selection": os.path.join(package_dir, "core", "component_selection.py"),
    "influence locks": os.path.join(package_dir, "core", "influence_lock.py"),
    "component flood": os.path.join(package_dir, "core", "component_flood.py"),
    "flood UI": os.path.join(package_dir, "ui", "component_flood_section.py"),
    "cross-version joint tree": joint_tree_path,
    "UI package initializer": ui_init_path,
}


def _read_text(path):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return stream.read()
    except Exception:
        return ""


joint_tree_source = _read_text(joint_tree_path)
ui_init_source = _read_text(ui_init_path)
deploy_marker = _read_text(deploy_marker_path).strip()

latest_source = (
    '"Select Joints In The List"' in joint_tree_source
    and 'label="Select Joints In The Scene"' in joint_tree_source
    and '[("Load Mesh",' in joint_tree_source
    and "class _ToolWindowReloadFinder" in ui_init_source
)

print("\n[AD Skin Tool v4.1 - Install Diagnostic]")
print("Python:", sys.version)
print("Package:", ad_skin_tools.__file__)
print("Launch:", launch.__file__)
print("Tool window:", tool_window.__file__)
print("Flood UI:", component_flood_section.__file__)
print("Joint tree:", joint_tree_maya2023.__file__)
print("Window label:", tool_window.WINDOW_LABEL)
print("Latest UI source:", latest_source)
print("Deploy marker exists:", os.path.exists(deploy_marker_path))
if deploy_marker:
    print("Deploy marker:\n{}".format(deploy_marker))

print(
    "Skin-context builder module:",
    getattr(tool_window._build_skin_cluster_section, "__module__", "<unknown>"),
)
print(
    "Joint-list builder module:",
    getattr(tool_window._build_joints_section, "__module__", "<unknown>"),
)
print(
    "Initial-bind builder module:",
    getattr(tool_window._build_initial_bind_section, "__module__", "<unknown>"),
)

for label, path in expected_files.items():
    print("{} exists: {} -> {}".format(label, os.path.exists(path), path))

matching_paths = []
for path in sys.path:
    candidate = os.path.join(path, "ad_skin_tools") if path else "ad_skin_tools"
    if os.path.isdir(candidate):
        matching_paths.append(os.path.abspath(candidate))

print("Importable ad_skin_tools copies:")
for path in matching_paths:
    marker = "<-- ACTIVE" if os.path.normcase(path) == os.path.normcase(package_dir) else ""
    print("  {} {}".format(path, marker))

installed = (
    latest_source
    and tool_window.WINDOW_LABEL == "AD Skin Weights Tool v4.1"
    and getattr(tool_window._build_skin_cluster_section, "__module__", "")
    == "ad_skin_tools.ui.joint_tree_maya2023"
    and getattr(tool_window._build_joints_section, "__module__", "")
    == "ad_skin_tools.ui.joint_tree_maya2023"
    and getattr(tool_window._build_initial_bind_section, "__module__", "")
    == "ad_skin_tools.ui.component_flood_section"
    and all(os.path.exists(path) for path in expected_files.values())
)

print("v4.1 latest UI installed:", installed)
if not installed:
    raise RuntimeError(
        "Maya is not using the latest composed AD Skin Tool v4.1 UI. "
        "Check the active package, deploy marker, source revision, and duplicate "
        "copies printed above."
    )

print("Diagnostic passed. The shelf may call tool_window.show() or launch.show().")
