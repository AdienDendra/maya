"""Launch and reload the AD Skin Weights Tool."""

import importlib


def _install_ui(
    tool_window,
    skin_operations,
    smoothing_bind_section,
    component_section,
):
    skin_operations.install(tool_window)
    smoothing_bind_section.install(tool_window, skin_operations)
    component_section.install(
        tool_window,
        skin_operations,
        smoothing_bind_section,
    )


def reload_modules():
    import ad_skin_tools.core.compat as compat
    import ad_skin_tools.core.undo as undo
    import ad_skin_tools.core.selection as selection
    import ad_skin_tools.core.mesh as mesh
    import ad_skin_tools.core.skin_cluster as skin_cluster
    import ad_skin_tools.core.component_selection as component_selection
    import ad_skin_tools.core.influence_lock as influence_lock
    import ad_skin_tools.core.undoable_skin_weights as undoable_skin_weights
    import ad_skin_tools.core.joint_automatic_bind as joint_automatic_bind
    import ad_skin_tools.core.smoothed_automatic_bind as smoothed_automatic_bind
    import ad_skin_tools.core.automatic_surface_commands as automatic_surface_commands
    import ad_skin_tools.core.add_influence as add_influence

    import ad_skin_tools.components.selection as component_selection_weights
    import ad_skin_tools.components.flood as component_flood
    import ad_skin_tools.components.smooth as component_smooth

    import ad_skin_tools.region.maya_scene as region_maya_scene
    import ad_skin_tools.region.distance_ranking as region_distance_ranking
    import ad_skin_tools.region.exact_tie as region_exact_tie
    import ad_skin_tools.region.connectivity as region_connectivity
    import ad_skin_tools.region.facing as region_facing
    import ad_skin_tools.region.closed_loop_consensus as region_closed_loop_consensus
    import ad_skin_tools.region.closed_loop_opposite_guard as region_opposite_guard
    import ad_skin_tools.region.ambiguous_loop_distance_tiebreak as region_tiebreak
    import ad_skin_tools.region.solver as region_solver

    import ad_skin_tools.bind_smoothing.diffusion as smoothing_diffusion
    import ad_skin_tools.bind_smoothing.cutoff_projection as smoothing_cutoff
    import ad_skin_tools.bind_smoothing.final_constraints as smoothing_constraints
    import ad_skin_tools.bind_smoothing.options as smoothing_options
    import ad_skin_tools.bind_smoothing.validation as smoothing_validation
    import ad_skin_tools.bind_smoothing.solver as smoothing_solver

    import ad_skin_tools.ui.smoothing_controls as smoothing_controls
    import ad_skin_tools.ui.joint_list as joint_list
    import ad_skin_tools.ui.skin_operations as skin_operations
    import ad_skin_tools.ui.smoothing_bind_section as smoothing_bind_section
    import ad_skin_tools.ui.component_section as component_section
    import ad_skin_tools.ui.tool_window as tool_window

    for module in [
        compat,
        undo,
        selection,
        mesh,
        skin_cluster,
        component_selection,
        influence_lock,
        undoable_skin_weights,
        component_selection_weights,
        component_flood,
        component_smooth,
        region_maya_scene,
        region_distance_ranking,
        region_exact_tie,
        region_connectivity,
        region_facing,
        region_closed_loop_consensus,
        region_opposite_guard,
        region_tiebreak,
        region_solver,
        smoothing_diffusion,
        smoothing_cutoff,
        smoothing_constraints,
        smoothing_options,
        smoothing_validation,
        smoothing_solver,
        joint_automatic_bind,
        smoothed_automatic_bind,
        automatic_surface_commands,
        add_influence,
    ]:
        importlib.reload(module)

    importlib.reload(smoothing_controls)
    importlib.reload(joint_list)
    importlib.reload(skin_operations)
    importlib.reload(smoothing_bind_section)
    importlib.reload(component_section)
    importlib.reload(tool_window)

    _install_ui(
        tool_window,
        skin_operations,
        smoothing_bind_section,
        component_section,
    )


def show(reload=False, auto_refresh=False):
    if reload:
        reload_modules()

    from ad_skin_tools.ui import (
        component_section,
        skin_operations,
        smoothing_bind_section,
        tool_window,
    )

    _install_ui(
        tool_window,
        skin_operations,
        smoothing_bind_section,
        component_section,
    )
    tool_window.show(auto_refresh=auto_refresh)
