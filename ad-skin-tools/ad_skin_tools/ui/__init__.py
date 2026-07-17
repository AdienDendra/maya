"""UI package for AD Skin Tools.

Import and compose the v4.0 component-flood section at package load time so all
supported entry points behave consistently, including legacy shelf commands that
import ``ad_skin_tools.ui.tool_window`` directly.
"""

from ad_skin_tools.ui import tool_window
from ad_skin_tools.ui import component_flood_section


component_flood_section.install(tool_window)


__all__ = ["tool_window"]
