"""Report which AD Skin Tool v4.2 UI modules Maya is using."""

import os
import sys

import ad_skin_tools
from ad_skin_tools import launch
from ad_skin_tools.ui import component_flood_section, joint_list, tool_window


package_dir = os.path.dirname(os.path.abspath(ad_skin_tools.__file__))
expected_files = {
    "component selection": os.path.join(package_dir, "core", "component_selection.py"),
    "influence locks": os.path.join(package_dir, "core", "influence_lock.py"),
    "component flood": os.path.join(package_dir, "core", "component_flood.py"),
    "component UI": os.path.join(package_dir, "ui", "component_flood_section.py"),
    "joint-list UI": os.path.join(package_dir, "ui", "joint_list.py"),
}
retired_joint_tree = os.path.join(package_dir, "ui", "joint_tree_maya2023.py")

print("\n[AD Skin Tool v4.2 - Install Diagnostic]")
print("Python:", sys.version)
print("Package:", ad_skin_tools.__file__)
print("Launch:", launch.__file__)
print("Tool window:", tool_window.__file__)
print("Component UI:", component_flood_section.__file__)
print("Joint-list UI:", joint_list.__file__)
print("Window label:", tool_window.WINDOW_LABEL)
print(
    "Skin-context builder module:",
    getattr(tool_window._build_skin_cluster_section, "__module__", "<unknown>"),
)
print(
    "Joint-list builder module:",
    getattr(tool_window._build_joints_section, "__module__", "<unknown>"),
)
print(
    "Joint-list renderer module:",
    getattr(tool_window._set_joint_list, "__module__", "<unknown>"),
)
print(
    "Initial-bind builder module:",
    getattr(tool_window._build_initial_bind_section, "__module__", "<unknown>"),
)

for label, path in expected_files.items():
    print("{} exists: {} -> {}".format(label, os.path.exists(path), path))
print("Retired duplicate joint tree exists:", os.path.exists(retired_joint_tree))

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
    tool_window.WINDOW_LABEL == "AD Skin Weights Tool v4.2"
    and getattr(tool_window._build_skin_cluster_section, "__module__", "")
    == "ad_skin_tools.ui.component_flood_section"
    and getattr(tool_window._build_initial_bind_section, "__module__", "")
    == "ad_skin_tools.ui.component_flood_section"
    and getattr(tool_window._build_joints_section, "__module__", "")
    == "ad_skin_tools.ui.joint_list"
    and getattr(tool_window._set_joint_list, "__module__", "")
    == "ad_skin_tools.ui.joint_list"
    and all(os.path.exists(path) for path in expected_files.values())
    and not os.path.exists(retired_joint_tree)
)

print("v4.2 UI installed:", installed)
if not installed:
    raise RuntimeError(
        "Maya is not using the consolidated AD Skin Tool v4.2 UI. "
        "Check the active package path and duplicate copies printed above."
    )

print("Diagnostic passed. Run: from ad_skin_tools import launch; launch.show(reload=True)")
