import importlib


def reload_modules():
    import ad_skin_tools.core.compat as compat
    import ad_skin_tools.core.undo as undo
    import ad_skin_tools.core.selection as selection
    import ad_skin_tools.core.mesh as mesh
    import ad_skin_tools.core.skin_cluster as skin_cluster
    import ad_skin_tools.core.influence as influence
    import ad_skin_tools.core.weights as weights
    import ad_skin_tools.core.commands as commands
    import ad_skin_tools.ui.tool_window as tool_window
    import ad_skin_tools.core.segment_solver as segment_solver
    import ad_skin_tools.core.commands as commands

    for module in [
        compat,
        undo,
        selection,
        mesh,
        skin_cluster,
        influence,
        weights,
        segment_solver,
        commands,
        tool_window,
    ]:
        importlib.reload(module)


def show(reload=False, auto_refresh=False):
    if reload:
        reload_modules()

    from ad_skin_tools.ui import tool_window
    tool_window.show(auto_refresh=auto_refresh)