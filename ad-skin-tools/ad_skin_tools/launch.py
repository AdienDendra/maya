import importlib


def _install_component_flood(tool_window, component_flood_section):
    """Install the composed v4 UI even after Maya module reloads."""

    installed_builder = getattr(
        component_flood_section,
        "_build_bind_sections",
        None,
    )
    current_builder = getattr(
        tool_window,
        "_build_initial_bind_section",
        None,
    )

    if current_builder is not installed_builder:
        for flag_name in (
            "_V4_COMPONENT_FLOOD_INSTALLED",
            "_V41_INFLUENCE_LOCKS_INSTALLED",
        ):
            try:
                delattr(tool_window, flag_name)
            except AttributeError:
                pass

    component_flood_section.install(tool_window)


def reload_modules():
    import ad_skin_tools.core.compat as compat
    import ad_skin_tools.core.undo as undo
    import ad_skin_tools.core.selection as selection
    import ad_skin_tools.core.mesh as mesh
    import ad_skin_tools.core.skin_cluster as skin_cluster
    import ad_skin_tools.core.influence as influence
    import ad_skin_tools.core.weights as weights
    import ad_skin_tools.core.surface_distance as surface_distance
    import ad_skin_tools.core.segment_solver as segment_solver
    import ad_skin_tools.core.ownership_solver as ownership_solver
    import ad_skin_tools.core.joint_surface_solver as joint_surface_solver
    import ad_skin_tools.core.joint_seed_competition as joint_seed_competition
    import ad_skin_tools.core.component_selection as component_selection
    import ad_skin_tools.core.influence_lock as influence_lock
    import ad_skin_tools.core.component_flood as component_flood

    import ad_skin_tools.region.maya_scene as region_maya_scene
    import ad_skin_tools.region.distance_ranking as region_distance_ranking
    import ad_skin_tools.region.connectivity as region_connectivity
    import ad_skin_tools.region.facing as region_facing
    import ad_skin_tools.region.solver as region_solver

    import ad_skin_tools.core.joint_automatic_bind as joint_automatic_bind
    import ad_skin_tools.core.automatic_surface_commands as automatic_surface_commands
    import ad_skin_tools.core.commands as commands
    import ad_skin_tools.ui.tool_window as tool_window
    import ad_skin_tools.ui.component_flood_section as component_flood_section

    for module in [
        compat,
        undo,
        selection,
        mesh,
        skin_cluster,
        influence,
        weights,
        ownership_solver,
        surface_distance,
        segment_solver,
        joint_surface_solver,
        joint_seed_competition,
        component_selection,
        influence_lock,
        component_flood,
        region_maya_scene,
        region_distance_ranking,
        region_connectivity,
        region_facing,
        region_solver,
        joint_automatic_bind,
        automatic_surface_commands,
        commands,
    ]:
        importlib.reload(module)

    # Reload composition first, restore the base UI second, then install v4.1
    # against the fresh tool-window functions.
    importlib.reload(component_flood_section)
    importlib.reload(tool_window)
    _install_component_flood(tool_window, component_flood_section)


def show(reload=False, auto_refresh=False):
    if reload:
        reload_modules()

    from ad_skin_tools.ui import component_flood_section, tool_window

    _install_component_flood(tool_window, component_flood_section)
    tool_window.show(
        auto_refresh=auto_refresh
    )
