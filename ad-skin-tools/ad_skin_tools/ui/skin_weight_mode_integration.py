"""Stable Maya-control lookup for Skin Weight Mode Qt connections."""

from maya import OpenMayaUI as omui

from ad_skin_tools.ui import qt_helpers


def install(skin_weight_mode_module, skin_operations_module) -> None:
    """Replace legacy text scanning with one deterministic Load Mesh lookup."""

    control_name = skin_operations_module.CTRL_LOAD_MESH_BUTTON

    def find_load_mesh_button(QtWidgets, binding_name):
        return qt_helpers.wrap_instance(
            omui.MQtUtil.findControl(control_name),
            QtWidgets.QPushButton,
            binding_name,
        )

    skin_weight_mode_module._find_load_mesh_button = find_load_mesh_button
