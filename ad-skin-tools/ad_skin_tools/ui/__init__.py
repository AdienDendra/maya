"""UI package for AD Skin Tools.

Compose v4.1 at package load time and after direct ``tool_window`` reloads.

Some legacy Maya shelf commands import ``ad_skin_tools.ui.tool_window``, call
``importlib.reload(tool_window)``, and then invoke ``tool_window.show()``. Reloading
that base module restores its old v2.7 builders unless the v4.1 composition is
installed again afterwards. A small import hook below wraps only that one module's
loader and reapplies the current cross-version UI after every reload.
"""

import importlib.abc
import importlib.machinery
import sys

from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_tree_maya2023
from ad_skin_tools.ui import tool_window


_TOOL_WINDOW_MODULE = "ad_skin_tools.ui.tool_window"
_RELOAD_FINDER_MARKER = "_ad_skin_v41_tool_window_reload_finder"


def _compose_v41(tool_window_module) -> None:
    """Install the current v4.1 builders against a freshly loaded base module."""

    # ``importlib.reload`` reuses the existing module dictionary, so dynamically
    # added flags can survive even though the original base functions were restored.
    # Remove them before installation and let the actual composition rebuild state.
    for flag_name in (
        "_V4_COMPONENT_FLOOD_INSTALLED",
        "_V41_INFLUENCE_LOCKS_INSTALLED",
    ):
        try:
            delattr(tool_window_module, flag_name)
        except AttributeError:
            pass

    joint_tree_maya2023.patch(component_flood_section)
    component_flood_section.install(tool_window_module)


class _ToolWindowReloadLoader(importlib.abc.Loader):
    """Delegate normal loading, then recompose the v4.1 UI."""

    def __init__(self, wrapped_loader):
        self._wrapped_loader = wrapped_loader

    def create_module(self, spec):
        create_module = getattr(self._wrapped_loader, "create_module", None)
        if create_module is None:
            return None
        return create_module(spec)

    def exec_module(self, module):
        self._wrapped_loader.exec_module(module)
        _compose_v41(module)


class _ToolWindowReloadFinder(importlib.abc.MetaPathFinder):
    """Wrap reloads of only ``ad_skin_tools.ui.tool_window``."""

    def __init__(self):
        setattr(self, _RELOAD_FINDER_MARKER, True)

    def find_spec(self, fullname, path=None, target=None):
        if fullname != _TOOL_WINDOW_MODULE:
            return None

        # Call PathFinder directly so this finder does not recurse through
        # ``sys.meta_path`` while resolving the original source loader.
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            return spec

        if not isinstance(spec.loader, _ToolWindowReloadLoader):
            spec.loader = _ToolWindowReloadLoader(spec.loader)
        return spec


def _install_reload_finder() -> None:
    """Keep exactly one v4.1 reload hook in Maya's interpreter."""

    sys.meta_path[:] = [
        finder
        for finder in sys.meta_path
        if not getattr(finder, _RELOAD_FINDER_MARKER, False)
    ]
    sys.meta_path.insert(0, _ToolWindowReloadFinder())


_install_reload_finder()
_compose_v41(tool_window)


__all__ = ["tool_window"]
