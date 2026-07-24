"""Viewport 2.0 draw plug-in for Skin Weight Visual."""

import maya.api.OpenMaya as om
import maya.api.OpenMayaRender as omr

from ad_skin_tools.ui import skin_weight_visual_draw


def maya_useNewAPI():
    pass


class _SkinWeightVisualNode(om.MPxLocatorNode):
    @staticmethod
    def creator():
        return _SkinWeightVisualNode()

    @staticmethod
    def initialize():
        pass


class _SkinWeightVisualData(om.MUserData):
    def __init__(self):
        super(_SkinWeightVisualData, self).__init__(False)
        self.positions = om.MPointArray()
        self.colors = om.MColorArray()
        self.indices = om.MUintArray()
        self.valid = False


class _SkinWeightVisualDrawOverride(omr.MPxDrawOverride):
    def __init__(self, obj):
        super(_SkinWeightVisualDrawOverride, self).__init__(
            obj,
            None,
            True,
        )

    @staticmethod
    def creator(obj):
        return _SkinWeightVisualDrawOverride(obj)

    def supportedDrawAPIs(self):
        return omr.MRenderer.kAllDevices

    def hasUIDrawables(self):
        return True

    def isBounded(self, _obj_path, _camera_path):
        return False

    def prepareForDraw(
        self,
        obj_path,
        _camera_path,
        _frame_context,
        old_data,
    ):
        data = (
            old_data
            if isinstance(old_data, _SkinWeightVisualData)
            else _SkinWeightVisualData()
        )
        try:
            skin_weight_visual_draw.populate_draw_data(
                obj_path.node(),
                data,
            )
        except Exception:
            data.valid = False
        return data

    def addUIDrawables(
        self,
        _obj_path,
        draw_manager,
        _frame_context,
        data,
    ):
        if not isinstance(data, _SkinWeightVisualData) or not data.valid:
            return

        draw_manager.beginDrawable()
        try:
            draw_manager.setDepthPriority(
                omr.MRenderItem.sDormantFilledDepthPriority
            )
            draw_manager.mesh(
                omr.MUIDrawManager.kTriangles,
                data.positions,
                None,
                data.colors,
                data.indices,
            )
        finally:
            draw_manager.endDrawable()


def initializePlugin(plugin_object):
    plugin = om.MFnPlugin(
        plugin_object,
        "AD Skin Tool",
        "1.0.0",
        "Any",
    )
    plugin.registerNode(
        skin_weight_visual_draw.NODE_TYPE_NAME,
        om.MTypeId(skin_weight_visual_draw.NODE_TYPE_ID_VALUE),
        _SkinWeightVisualNode.creator,
        _SkinWeightVisualNode.initialize,
        om.MPxNode.kLocatorNode,
        skin_weight_visual_draw.DRAW_CLASSIFICATION,
    )
    omr.MDrawRegistry.registerDrawOverrideCreator(
        skin_weight_visual_draw.DRAW_CLASSIFICATION,
        skin_weight_visual_draw.DRAW_REGISTRANT_ID,
        _SkinWeightVisualDrawOverride.creator,
    )


def uninitializePlugin(plugin_object):
    omr.MDrawRegistry.deregisterDrawOverrideCreator(
        skin_weight_visual_draw.DRAW_CLASSIFICATION,
        skin_weight_visual_draw.DRAW_REGISTRANT_ID,
    )
    plugin = om.MFnPlugin(plugin_object)
    plugin.deregisterNode(
        om.MTypeId(skin_weight_visual_draw.NODE_TYPE_ID_VALUE)
    )
