"""Launch and reload the AD Skin Weights Tool."""

import importlib


def _install_ui(
    tool_window,
    joint_list,
    global_owner_tag,
    skin_operations,
    smoothing_bind_section,
    component_section,
    mesh_context_display,
):
    """Install module overrides before Maya builds the window."""

    skin_operations.install(tool_window)
    mesh_context_display.install(tool_window, skin_operations)
    global_owner_tag.install(tool_window, joint_list)
    smoothing_bind_section.install(tool_window, skin_operations)
    component_section.install(
        tool_window,
        skin_operations,
        smoothing_bind_section,
    )


def _install_post_show_ui(
    tool_window,
    joint_list,
    joint_search,
    joint_drag_selection,
    skin_weight_mode,
    skin_weight_mode_integration,
    skin_weight_color_integration,
    skin_operations,
    mesh_context_display,
):
    """Install Qt integrations that require built Maya controls."""

    joint_search.install(tool_window)
    joint_drag_selection.install(
        tool_window.CTRL_JOINT_LIST,
        selection_pruner=joint_search.prune_hidden_selection,
    )
    skin_weight_color_integration.prepare(skin_weight_mode)
    skin_weight_mode_integration.prepare(
        skin_weight_mode,
        skin_operations,
    )
    skin_weight_mode.install(tool_window, joint_list)
    skin_weight_mode_integration.install(
        tool_window,
        joint_list,
    )
    mesh_context_display.align_visual_controls(
        skin_weight_mode,
        skin_weight_mode_integration,
    )


def _reload_modules(modules):
    for module in modules:
        importlib.reload(module)


def reload_modules():
    """Reload implementation modules without rebuilding the window twice."""

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

    import ad_skin_tools.ui.qt_helpers as qt_helpers
    import ad_skin_tools.ui.skin_weight_ramps as skin_weight_ramps
    import ad_skin_tools.ui.skin_weight_color_session as skin_weight_color_session
    import ad_skin_tools.ui.skin_weight_color_integration as skin_weight_color_integration
    import ad_skin_tools.ui.smoothing_controls as smoothing_controls
    import ad_skin_tools.ui.joint_list as joint_list
    import ad_skin_tools.ui.joint_drag_selection as joint_drag_selection
    import ad_skin_tools.ui.joint_search as joint_search
    import ad_skin_tools.ui.global_owner_tag as global_owner_tag
    import ad_skin_tools.ui.skin_operations as skin_operations
    import ad_skin_tools.ui.mesh_context_display as mesh_context_display
    import ad_skin_tools.ui.smoothing_bind_section as smoothing_bind_section
    import ad_skin_tools.ui.component_section as component_section
    import ad_skin_tools.ui.skin_weight_mode as skin_weight_mode
    import ad_skin_tools.ui.skin_weight_mode_integration as skin_weight_mode_integration
    import ad_skin_tools.ui.tool_window as tool_window

    try:
        skin_weight_mode_integration.shutdown()
    except Exception:
        pass
    try:
        skin_weight_mode.shutdown()
    except Exception:
        pass
    try:
        skin_weight_color_integration.shutdown()
    except Exception:
        pass
    try:
        joint_drag_selection.uninstall()
    except Exception:
        pass

    _reload_modules(
        (
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
            qt_helpers,
            skin_weight_ramps,
            skin_weight_color_session,
            skin_weight_color_integration,
            smoothing_controls,
            joint_list,
            joint_drag_selection,
            joint_search,
            global_owner_tag,
            skin_operations,
            mesh_context_display,
            smoothing_bind_section,
            component_section,
            skin_weight_mode,
            skin_weight_mode_integration,
            tool_window,
        )
    )


def show(reload=False, auto_refresh=False):
    if reload:
        reload_modules()

    from ad_skin_tools.ui import (
        component_section,
        global_owner_tag,
        joint_drag_selection,
        joint_list,
        joint_search,
        mesh_context_display,
        skin_operations,
        skin_weight_color_integration,
        skin_weight_mode,
        skin_weight_mode_integration,
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
        mesh_context_display,
    )
    tool_window.show(auto_refresh=auto_refresh)
    _install_post_show_ui(
        tool_window,
        joint_list,
        joint_search,
        joint_drag_selection,
        skin_weight_mode,
        skin_weight_mode_integration,
        skin_weight_color_integration,
        skin_operations,
        mesh_context_display,
    )
