"""UI package for AD Skin Tools v4.1."""

from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_tree_maya2023
from ad_skin_tools.ui import tool_window


joint_tree_maya2023.patch(component_flood_section)
component_flood_section.install(tool_window)
tool_window._build_skin_cluster_section = (
    joint_tree_maya2023._build_skin_cluster_section
)


__all__ = ["tool_window"]
