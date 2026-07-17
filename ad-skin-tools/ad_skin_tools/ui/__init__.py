"""UI package for AD Skin Tools.

Compose v4.1 at package load time, after direct ``tool_window`` reloads, and
immediately before every window build.

Maya shelf commands are not consistent: some reload only ``tool_window`` while
others also reload ``component_flood_section`` before calling ``tool_window.show``.
A reload of either module can restore an older builder function while the window
still reports v4.1. The reload hook repairs ``tool_window`` reloads, and the show
guard below is the final authority that recomposes the latest UI at open time.
"""

import importlib.abc
import importlib.machinery
import sys

from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_tree_maya2023
from ad_skin_tools.ui import tool_window


_TOOL_WINDOW_MODULE = "ad_skin_tools.ui.tool_window"
_RELOAD_FINDER_MARKER = "_ad_skin_v41_tool_window_reload_finder"
_SHOW_GUARD_MARKER = "_ad_skin_v41_show_guard"
_SHOW_GUARD_ORIGINAL = "_ad_skin_v41_original_show"


def _compose_v41(tool_window_module) -> None:
    """Install the current v4.1 builders against the supplied base module."""

    # ``importlib.reload`` reuses the existing module dictionary, so dynamically
    # added flags can survive even though the original base functions were restored.
    # Remove them before installation and let the current composition rebuild state.
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


def _install_show_guard(tool_window_module) -> None:
    """Recompose v4.1 immediately before any entry point builds the window."""

    current_show = tool_window_module.show
    if getattr(current_show, _SHOW_GUARD_MARKER, False):
        original_show = getattr(current_show, _SHOW_GUARD_ORIGINAL)
    else:
        original_show = current_show

    def show_with_current_v41(*args, **kwargs):
        # This deliberately runs at call time. It repairs cases where another shelf
        # script reloaded ``component_flood_section`` after package initialization.
        _compose_v41(tool_window_module)
        return original_show(*args, **kwargs)

    setattr(show_with_current_v41, _SHOW_GUARD_MARKER, True)
    setattr(show_with_current_v41, _SHOW_GUARD_ORIGINAL, original_show)
    tool_window_module.show = show_with_current_v41


class _ToolWindowReloadLoader(importlib.abc.Loader):
    """Delegate normal loading, then recompose and guard the v4.1 UI."""

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
        _install_show_guard(module)


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
_install_show_guard(tool_window)


__all__ = ["tool_window"]
