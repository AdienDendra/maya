"""Install the v8.0 weighted Component Flood callback."""

import builtins

import maya.cmds as cmds

from ad_skin_tools.components import flood
from ad_skin_tools.ui import component_flood_section
from ad_skin_tools.ui import joint_list


_TOOL_WINDOW = None


def install(tool_window_module) -> None:
    global _TOOL_WINDOW
    _TOOL_WINDOW = tool_window_module

    component_flood_section.apply_component_flood = apply_component_flood
    tool_window_module.show_help = show_help
    tool_window_module.WINDOW_LABEL = "AD Skin Weights Tool v8.0"
    tool_window_module._V80_WEIGHTED_COMPONENT_FLOOD_INSTALLED = True


def apply_component_flood() -> None:
    wait_cursor_active = False

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _TOOL_WINDOW._STATE.get("has_skin_cluster"):
            raise RuntimeError(
                "Component Flood requires an existing skinCluster.\n\n"
                "Use Bind Skin first."
            )

        selected_joints = joint_list.selected_joint_paths()
        if len(selected_joints) != 1:
            raise RuntimeError(
                "Select exactly one target joint in the UI influence list."
            )
        target_joint = selected_joints[0]

        component_flood_section._set_flood_busy(
            True,
            "Reading component falloff and redistributing weights...",
        )
        cmds.waitCursor(state=True)
        wait_cursor_active = True
        cmds.refresh(force=True)

        staged_joints = builtins.list(
            _TOOL_WINDOW._STATE.get("joints", [])
        )
        staged_locks = set(
            _TOOL_WINDOW._STATE.get("pending_locked_joints", set())
        )
        result = flood.flood_selected_components_to_joint(
            mesh_shape=_TOOL_WINDOW._STATE["mesh_shape"],
            mesh_transform=_TOOL_WINDOW._STATE["mesh_transform"],
            target_joint=target_joint,
            target_locked_override=joint_list.joint_is_locked(target_joint),
        )

        if not result.target_locked:
            joint_list.sync_after_flood_preserving_pending(
                staged_joints,
                staged_locks,
            )
        joint_list.select_joint_paths([result.target_joint])

        builtins.AD_SKIN_V80_FLOOD_RESULT = result
        builtins.AD_SKIN_V42_FLOOD_RESULT = result
        builtins.AD_SKIN_V41_FLOOD_RESULT = result
        builtins.AD_SKIN_V40_FLOOD_RESULT = result
        flood.print_component_flood_report(result)

        short_name = result.target_joint.split("|")[-1]
        if result.target_locked:
            _TOOL_WINDOW._info(
                "Flood ignored: {} is locked.".format(short_name)
            )
            return

        suffixes = []
        if result.influence_added:
            suffixes.append("Added new influence.")
        if result.protected_vertex_count:
            suffixes.append(
                "{} locked vertex/vertices protected.".format(
                    result.protected_vertex_count
                )
            )
        if result.ignored_component_count:
            suffixes.append(
                "{} other component(s) ignored.".format(
                    result.ignored_component_count
                )
            )
        suffix = " " + " ".join(suffixes) if suffixes else ""

        if result.soft_selection_used:
            message = (
                "Flood complete: {} of {} affected vertices set to {} "
                "from {:.3f} to {:.3f}.{}"
            ).format(
                result.flooded_vertex_count,
                result.vertex_count,
                short_name,
                result.minimum_target_weight,
                result.maximum_target_weight,
                suffix,
            )
        else:
            message = (
                "Flood complete: {} of {} selected vertices set to {} = 1.0.{}"
            ).format(
                result.flooded_vertex_count,
                result.vertex_count,
                short_name,
                suffix,
            )
        _TOOL_WINDOW._info(message)
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)
    finally:
        if wait_cursor_active:
            try:
                cmds.waitCursor(state=False)
            except Exception:
                pass
        component_flood_section._set_flood_busy(False)


def show_help() -> None:
    cmds.confirmDialog(
        title="AD Skin Weights Tool v8.0",
        message=(
            "Binding\n"
            "- Smoothing Iterations remains the v7.5 level from 0 to 10.\n"
            "- Bind Skin and Add Influence keep their existing Region behaviour.\n\n"
            "Component Flood\n"
            "- Select exactly one target joint in the list.\n"
            "- Select vertices, edges, or faces on the loaded mesh.\n"
            "- Soft Selection off: target weight becomes exactly 1.0.\n"
            "- Soft Selection on: Maya's per-vertex falloff becomes the target weight.\n"
            "- Remaining weight is returned proportionally to the previous "
            "non-target influences.\n"
            "- Locked influence ownership is preserved."
        ),
        button=["OK"],
    )
