"""UI package for AD Skin Tools.

Compose v4.1 at package load time so both ``launch.show`` and legacy shelf
commands that import ``ad_skin_tools.ui.tool_window`` receive the same UI.
"""

from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_tree_maya2023
from ad_skin_tools.ui import tool_window


joint_tree_maya2023.patch(component_flood_section)
component_flood_section.install(tool_window)


__all__ = ["tool_window"]
