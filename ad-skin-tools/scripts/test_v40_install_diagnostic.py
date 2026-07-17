"""Report which AD Skin Tool package Maya is actually importing."""

import os
import sys

import ad_skin_tools
from ad_skin_tools import launch
from ad_skin_tools.ui import component_flood_section, tool_window


package_dir = os.path.dirname(os.path.abspath(ad_skin_tools.__file__))
expected_files = {
    "component selection": os.path.join(package_dir, "core", "component_selection.py"),
    "component flood": os.path.join(package_dir, "core", "component_flood.py"),
    "flood UI": os.path.join(package_dir, "ui", "component_flood_section.py"),
}

print("\n[AD Skin Tool v4.0 - Install Diagnostic]")
print("Python:", sys.version)
print("Package:", ad_skin_tools.__file__)
print("Launch:", launch.__file__)
print("Tool window:", tool_window.__file__)
print("Flood UI:", component_flood_section.__file__)
print("Window label:", tool_window.WINDOW_LABEL)
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
    tool_window.WINDOW_LABEL == "AD Skin Weights Tool v4.0"
    and getattr(tool_window._build_initial_bind_section, "__module__", "")
    == "ad_skin_tools.ui.component_flood_section"
    and all(os.path.exists(path) for path in expected_files.values())
)

print("v4 UI installed:", installed)
if not installed:
    raise RuntimeError(
        "Maya is not using the composed AD Skin Tool v4.0 UI. "
        "Check the active package path and duplicate copies printed above."
    )

print("Diagnostic passed. Run: from ad_skin_tools import launch; launch.show(reload=True)")
