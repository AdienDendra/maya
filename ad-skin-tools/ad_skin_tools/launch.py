"""Launch and reload the AD Skin Weights Tool."""

import importlib


def _install_ui(
    tool_window,
    joint_list,
    global_owner_tag,
    skin_operations,
    smoothing_bind_section,
    component_section,
):
    skin_operations.install(tool_window)
    global_owner_tag.install(tool_window, joint_list)
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

    import ad_skin_tools.components.selection as component_selection_weights
    import ad_skin_tools.components.flood as component_flood
    import ad_skin_tools.components.smooth as component_smooth

    import ad_skin_tools.region.mesh_context as ownership_mesh_context
    import ad_skin_tools.region.exact_distance_ties as ownership_exact_ties
    import ad_skin_tools.region.closest_region_ownership as ownership_closest
    import ad_skin_tools.region.secondary_surface_facing as ownership_facing
    import ad_skin_tools.region.global_owner_assignment as ownership_global
    import ad_skin_tools.region.closed_loop_ownership as ownership_loops
    import ad_skin_tools.region.ownership_pipeline as ownership_pipeline

    import ad_skin_tools.bind_smoothing.diffusion as smoothing_diffusion
    import ad_skin_tools.bind_smoothing.cutoff_projection as smoothing_cutoff
    import ad_skin_tools.bind_smoothing.final_constraints as smoothing_constraints
    import ad_skin_tools.bind_smoothing.options as smoothing_options
    import ad_skin_tools.bind_smoothing.validation as smoothing_validation
    import ad_skin_tools.bind_smoothing.solver as smoothing_solver

    import ad_skin_tools.core.smoothed_automatic_bind as smoothed_automatic_bind
    import ad_skin_tools.core.automatic_surface_commands as automatic_surface_commands
    import ad_skin_tools.core.add_influence as add_influence

    import ad_skin_tools.ui.smoothing_controls as smoothing_controls
    import ad_skin_tools.ui.joint_list as joint_list
    import ad_skin_tools.ui.global_owner_tag as global_owner_tag
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
        ownership_mesh_context,
        ownership_exact_ties,
        ownership_closest,
        ownership_facing,
        ownership_global,
        ownership_loops,
        ownership_pipeline,
        smoothing_diffusion,
        smoothing_cutoff,
        smoothing_constraints,
        smoothing_options,
        smoothing_validation,
        smoothing_solver,
        smoothed_automatic_bind,
        automatic_surface_commands,
        add_influence,
    ]:
        importlib.reload(module)

    importlib.reload(smoothing_controls)
    importlib.reload(joint_list)
    importlib.reload(global_owner_tag)
    importlib.reload(skin_operations)
    importlib.reload(smoothing_bind_section)
    importlib.reload(component_section)
    importlib.reload(tool_window)

    _install_ui(
        tool_window,
        joint_list,
        global_owner_tag,
        skin_operations,
        smoothing_bind_section,
        component_section,
    )


def show(reload=False, auto_refresh=False):
    if reload:
        reload_modules()

    from ad_skin_tools.ui import (
        component_section,
        global_owner_tag,
        joint_list,
        skin_operations,
        smoothing_bind_section,
        tool_window,
    )

    _install_ui(
        tool_window,
        joint_list,
        global_owner_tag,
        skin_operations,
        smoothing_bind_section,
        component_section,
    )
    tool_window.show(auto_refresh=auto_refresh)
