"""Artist-facing refinements for the canonical joint-list module.

The existing ``joint_list`` module remains the public API used by the rest of
AD Skin Tool. ``install()`` replaces only its UI-facing callbacks so Bind,
Region, Flood, Add Influence, and Smooth continue to consume the same
``_STATE['joints']`` data and the same influence-lock implementation.
"""

import builtins

import maya.api.OpenMaya as om
import maya.cmds as cmds
from maya import OpenMayaUI as omui

from ad_skin_tools.core.compat import import_qt_modules
from ad_skin_tools.core.skin_cluster import SkinClusterAdapter


CTRL_JOINT_SORT = "adSkin_jointSort"
CTRL_JOINT_SEARCH = "adSkin_jointSearch"

_JOINT_LIST = None
_TOOL_WINDOW = None
_BASE_CONFIGURE = None
_DRAG_SELECTION_FILTER = None
_DRAG_SELECTION_VIEW = None


def install(joint_list_module) -> None:
    """Install refinements onto the already-imported joint-list module."""

    global _JOINT_LIST, _BASE_CONFIGURE

    _JOINT_LIST = joint_list_module

    base_configure = getattr(
        joint_list_module,
        "_ad_skin_refine_base_configure",
        None,
    )
    if base_configure is None:
        base_configure = joint_list_module.configure
        joint_list_module._ad_skin_refine_base_configure = base_configure
    _BASE_CONFIGURE = base_configure

    joint_list_module.configure = configure
    joint_list_module.build_section = build_section
    joint_list_module.set_joint_list = set_joint_list
    joint_list_module.remove_selected_joints = remove_selected_joints
    joint_list_module.remove_inverse_selected_joints = (
        remove_inverse_selected_joints
    )
    joint_list_module.remove_all_joints = remove_all_joints
    joint_list_module.select_vertices = select_vertices


def configure(tool_window_module) -> None:
    """Configure the canonical module, then add presentation state."""

    global _TOOL_WINDOW

    if _BASE_CONFIGURE is None:
        raise RuntimeError("Joint-list refinements are not installed.")

    _BASE_CONFIGURE(tool_window_module)
    _TOOL_WINDOW = tool_window_module

    state = tool_window_module._STATE
    state.setdefault("joint_display_order", [])
    state.setdefault("joint_filter_text", "")
    state.setdefault("joint_sort_descending", False)


def build_section() -> None:
    """Build sort controls, search, joint rows, and existing list buttons."""

    _require_configured()

    cmds.frameLayout(
        label="Joints / Influences",
        collapsable=True,
        collapse=False,
        marginWidth=6,
        marginHeight=6,
    )
    cmds.columnLayout(adjustableColumn=True, rowSpacing=5)

    cmds.radioButtonGrp(
        CTRL_JOINT_SORT,
        label="Sort:",
        numberOfRadioButtons=2,
        labelArray2=["A to Z", "Z to A"],
        select=2 if _state()["joint_sort_descending"] else 1,
        columnWidth3=(38, 82, 82),
        adjustableColumn=3,
        changeCommand=_on_sort_changed,
    )
    cmds.textField(
        CTRL_JOINT_SEARCH,
        searchField=True,
        placeholderText="Search...",
        text=_state()["joint_filter_text"],
        textChangedCommand=_on_search_changed,
    )

    cmds.treeView(
        _TOOL_WINDOW.CTRL_JOINT_LIST,
        allowMultiSelection=True,
        allowDragAndDrop=False,
        allowReparenting=False,
        enableKeys=True,
        height=220,
        numberOfButtons=1,
        attachButtonRight=False,
        preventOverride=True,
        pressCommand=(1, _JOINT_LIST._on_lock_button_pressed),
        contextMenuCommand=_prepare_context_menu,
        selectCommand=_JOINT_LIST._allow_tree_selection_change,
    )
    cmds.popupMenu(
        _JOINT_LIST.CTRL_JOINT_CONTEXT_MENU,
        parent=_TOOL_WINDOW.CTRL_JOINT_LIST,
        button=3,
        postMenuCommand=_populate_joint_context_menu,
    )

    _TOOL_WINDOW._button_row(
        [
            (
                "Add Joints To The List",
                lambda *_: _JOINT_LIST.add_selected_joints(),
            ),
            (
                "Select Joints In The List",
                lambda *_: _JOINT_LIST.show_selected_joints_in_list(),
            ),
        ],
        height=30,
    )

    cmds.setParent("..")
    cmds.setParent("..")

    _install_drag_selection_filter()


def set_joint_list(joints) -> None:
    """Render sorted rows while preserving selection and scroll position."""

    _require_configured()

    normalized_joints = _TOOL_WINDOW._unique_joint_paths(joints)
    previous_selected_paths = set(_JOINT_LIST.selected_joint_paths())

    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    previous_scroll = _tree_scroll_position(control)
    state = _state()

    state["joints"] = normalized_joints
    state["joint_display_to_path"] = {}
    state["joint_path_to_display"] = {}
    state["joint_item_to_path"] = {}
    state["joint_path_to_item"] = {}

    bound_paths = set()
    if state.get("has_skin_cluster"):
        try:
            adapter = SkinClusterAdapter.from_mesh(state["mesh_shape"])
            bound_paths = set(adapter.influences())
            state["skin_cluster"] = adapter.skin_cluster
        except Exception:
            bound_paths = set()
    state["bound_joint_paths"] = bound_paths

    pending_locks = set(state.get("pending_locked_joints", set()))
    pending_locks.intersection_update(normalized_joints)
    pending_locks.difference_update(bound_paths)
    state["pending_locked_joints"] = pending_locks

    display_labels = {
        joint: _TOOL_WINDOW._make_unique_joint_label(
            joint,
            normalized_joints,
        )
        for joint in normalized_joints
    }
    display_joints = sorted(
        normalized_joints,
        key=lambda joint: (
            display_labels[joint].casefold(),
            joint.casefold(),
        ),
        reverse=bool(state.get("joint_sort_descending", False)),
    )
    state["joint_display_order"] = builtins.list(display_joints)

    if not cmds.treeView(control, exists=True):
        return

    cmds.treeView(control, edit=True, removeAll=True)

    for index, joint in enumerate(display_joints):
        item_id = "joint_{:04d}".format(index)
        display_label = display_labels[joint]

        state["joint_display_to_path"][display_label] = joint
        state["joint_path_to_display"][joint] = display_label
        state["joint_item_to_path"][item_id] = joint
        state["joint_path_to_item"][joint] = item_id

        cmds.treeView(control, edit=True, addItem=(item_id, ""))
        cmds.treeView(
            control,
            edit=True,
            displayLabel=(item_id, display_label),
        )
        cmds.treeView(
            control,
            edit=True,
            buttonStyle=(item_id, 1, "pushButton"),
        )
        cmds.treeView(
            control,
            edit=True,
            buttonVisible=(item_id, 1, True),
        )

        if joint in bound_paths:
            cmds.treeView(
                control,
                edit=True,
                textColor=(item_id,) + _JOINT_LIST._BOUND_TEXT_COLOR,
            )

        _JOINT_LIST._render_lock_button(item_id, joint)

    _apply_joint_filter()
    _JOINT_LIST.select_joint_paths(previous_selected_paths)
    _restore_tree_scroll_position(control, previous_scroll)


def remove_selected_joints() -> None:
    """Remove selected pending rows while preserving bound influences."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        selected = set(_JOINT_LIST.selected_joint_paths())
        if not selected:
            cmds.warning("No joints selected in the list.")
            return
        _remove_pending_joints(selected, action_label="Removed")
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def remove_inverse_selected_joints() -> None:
    """Keep selected rows and remove every unselected pending row."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        selected = set(_JOINT_LIST.selected_joint_paths())
        if not selected:
            cmds.warning(
                "Select the joints to keep before using "
                "Remove Inverse Selected."
            )
            return

        inverse = set(_state().get("joints", [])) - selected
        _remove_pending_joints(
            inverse,
            action_label="Removed inverse-selected",
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def remove_all_joints() -> None:
    """Remove all pending rows while preserving bound influences."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        _remove_pending_joints(
            set(_state().get("joints", [])),
            action_label="Removed",
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def select_vertices() -> None:
    """Select loaded-mesh vertices weighted by selected bound influences."""

    try:
        _TOOL_WINDOW._require_not_busy()
        _TOOL_WINDOW._require_loaded_mesh()
        if not _state().get("has_skin_cluster"):
            raise RuntimeError(
                "Select Vertices requires an existing skinCluster."
            )

        selected = _JOINT_LIST.selected_joint_paths()
        if not selected:
            cmds.warning("No joints selected in the list.")
            return

        adapter = SkinClusterAdapter.from_mesh(_state()["mesh_shape"])
        influence_paths = {
            path.fullPathName(): path
            for path in adapter.skin_fn.influenceObjects()
        }
        bound_targets = [
            joint for joint in selected if joint in influence_paths
        ]
        if not bound_targets:
            raise RuntimeError(
                "Selected rows are not influences on the loaded skinCluster."
            )

        loaded_shape = adapter.mesh_dag_path.fullPathName()
        vertex_ids = set()

        for joint in bound_targets:
            affected, _weights = adapter.skin_fn.getPointsAffectedByInfluence(
                influence_paths[joint]
            )
            iterator = om.MItSelectionList(
                affected,
                om.MFn.kMeshVertComponent,
            )
            while not iterator.isDone():
                dag_path, component = iterator.getComponent()
                mesh_dag = om.MDagPath(dag_path)
                if not mesh_dag.node().hasFn(om.MFn.kMesh):
                    mesh_dag.extendToShape()
                if mesh_dag.fullPathName() == loaded_shape:
                    component_fn = om.MFnSingleIndexedComponent(component)
                    vertex_ids.update(
                        int(value)
                        for value in component_fn.getElements()
                    )
                iterator.next()

        if not vertex_ids:
            cmds.warning(
                "Selected influence(s) do not have non-zero weights on the "
                "loaded mesh."
            )
            return

        component_fn = om.MFnSingleIndexedComponent()
        component = component_fn.create(om.MFn.kMeshVertComponent)
        component_fn.addElements(sorted(vertex_ids))

        selection = om.MSelectionList()
        selection.add((adapter.mesh_dag_path, component))
        cmds.select(selection.getSelectionStrings(), replace=True)

        _TOOL_WINDOW._info(
            "Selected {} influenced vertices from {} joint(s).".format(
                len(vertex_ids),
                len(bound_targets),
            )
        )
    except Exception as exc:
        _TOOL_WINDOW._show_error(exc)


def _remove_pending_joints(candidates, action_label: str) -> None:
    bound = set(_state().get("bound_joint_paths", set()))
    candidates = set(candidates)
    removable = candidates - bound
    skipped_bound = candidates & bound

    if not removable:
        cmds.warning(
            "No pending joints can be removed. "
            "Bound influences are preserved."
        )
        return

    pending_locks = set(_state().get("pending_locked_joints", set()))
    pending_locks.difference_update(removable)
    _state()["pending_locked_joints"] = pending_locks

    remaining = [
        joint
        for joint in _state().get("joints", [])
        if joint not in removable
    ]
    set_joint_list(remaining)
    _TOOL_WINDOW._update_joint_count_label()

    suffix = ""
    if skipped_bound:
        suffix = " {} bound influence(s) preserved.".format(
            len(skipped_bound)
        )
    _TOOL_WINDOW._info(
        "{} {} pending joint(s).{}".format(
            action_label,
            len(removable),
            suffix,
        )
    )


def _prepare_context_menu(clicked_item) -> bool:
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if clicked_item and _JOINT_LIST._tree_item_exists(control, clicked_item):
        selected_ids = set(_JOINT_LIST._selected_item_ids())
        if clicked_item not in selected_ids:
            cmds.treeView(control, edit=True, clearSelection=True)
            _JOINT_LIST._set_tree_item_selected(
                control,
                clicked_item,
                True,
            )
    return True


def _populate_joint_context_menu(menu, *_):
    cmds.popupMenu(menu, edit=True, deleteAllItems=True)
    cmds.menuItem(
        label="Lock Selected",
        parent=menu,
        command=lambda *_: _JOINT_LIST.lock_selected_joints(True),
    )
    cmds.menuItem(
        label="Unlock Selected",
        parent=menu,
        command=lambda *_: _JOINT_LIST.lock_selected_joints(False),
    )
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        label="Lock Inverse Selected",
        parent=menu,
        command=lambda *_: _JOINT_LIST.lock_selected_joints(
            True,
            inverse=True,
        ),
    )
    cmds.menuItem(
        label="Unlock Inverse Selected",
        parent=menu,
        command=lambda *_: _JOINT_LIST.lock_selected_joints(
            False,
            inverse=True,
        ),
    )
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        label="Remove Selected",
        parent=menu,
        command=lambda *_: remove_selected_joints(),
    )
    cmds.menuItem(
        label="Remove Inverse Selected",
        parent=menu,
        command=lambda *_: remove_inverse_selected_joints(),
    )
    cmds.menuItem(
        label="Remove All",
        parent=menu,
        command=lambda *_: remove_all_joints(),
    )
    cmds.menuItem(divider=True, parent=menu)
    cmds.menuItem(
        label="Select Vertices",
        parent=menu,
        command=lambda *_: select_vertices(),
    )
    cmds.menuItem(
        label="Select Joints In The Scene",
        parent=menu,
        command=lambda *_: _JOINT_LIST.select_joints_in_scene(),
    )


def _on_sort_changed(*_):
    if not cmds.radioButtonGrp(CTRL_JOINT_SORT, exists=True):
        return
    selected = cmds.radioButtonGrp(
        CTRL_JOINT_SORT,
        query=True,
        select=True,
    )
    _state()["joint_sort_descending"] = selected == 2
    set_joint_list(builtins.list(_state().get("joints", [])))


def _on_search_changed(text=None, *_):
    if text is None and cmds.textField(CTRL_JOINT_SEARCH, exists=True):
        text = cmds.textField(
            CTRL_JOINT_SEARCH,
            query=True,
            text=True,
        )
    _state()["joint_filter_text"] = (text or "").strip()
    _apply_joint_filter()


def _apply_joint_filter() -> None:
    control = _TOOL_WINDOW.CTRL_JOINT_LIST
    if not cmds.treeView(control, exists=True):
        return

    needle = _state().get("joint_filter_text", "").casefold()
    item_to_path = _state().get("joint_item_to_path", {})
    path_to_display = _state().get("joint_path_to_display", {})

    for item_id, joint in item_to_path.items():
        if not _JOINT_LIST._tree_item_exists(control, item_id):
            continue
        display = path_to_display.get(joint, "")
        visible = (
            not needle
            or needle in display.casefold()
            or needle in joint.casefold()
        )
        cmds.treeView(
            control,
            edit=True,
            itemVisible=(item_id, bool(visible)),
        )


def _tree_scroll_position(control: str) -> int:
    if not cmds.treeView(control, exists=True):
        return 0
    try:
        return int(
            cmds.treeView(
                control,
                query=True,
                verticalScrollPosition=True,
            )
        )
    except Exception:
        return 0


def _restore_tree_scroll_position(control: str, position: int) -> None:
    if not cmds.treeView(control, exists=True):
        return
    try:
        cmds.treeView(
            control,
            edit=True,
            verticalScrollPosition=max(0, int(position)),
        )
    except Exception:
        pass


def _install_drag_selection_filter() -> None:
    """Add left-button range drag selection without changing click behavior."""

    global _DRAG_SELECTION_FILTER, _DRAG_SELECTION_VIEW

    try:
        QtWidgets, _QtGui, QtCore, binding_name = import_qt_modules()
        wrap_instance = _shiboken_wrap_instance(binding_name)
        pointer = omui.MQtUtil.findControl(_TOOL_WINDOW.CTRL_JOINT_LIST)
        if not pointer:
            return

        widget = wrap_instance(int(pointer), QtWidgets.QWidget)
        tree_view = (
            widget
            if isinstance(widget, QtWidgets.QTreeView)
            else widget.findChild(QtWidgets.QTreeView)
        )
        if tree_view is None:
            return

        if (
            _DRAG_SELECTION_FILTER is not None
            and _DRAG_SELECTION_VIEW is not None
        ):
            try:
                _DRAG_SELECTION_VIEW.viewport().removeEventFilter(
                    _DRAG_SELECTION_FILTER
                )
            except Exception:
                pass

        event_types = getattr(QtCore.QEvent, "Type", QtCore.QEvent)
        mouse_buttons = getattr(QtCore.Qt, "MouseButton", QtCore.Qt)
        left_button = mouse_buttons.LeftButton

        class JointDragSelectionFilter(QtCore.QObject):
            def __init__(self, view):
                super().__init__(view)
                self.view = view
                self.anchor_row = None
                self.press_position = None
                self.dragging = False

            def eventFilter(self, watched, event):
                event_type = event.type()

                if event_type == event_types.MouseButtonPress:
                    if event.button() != left_button:
                        return False
                    position = _mouse_event_position(event)
                    index = self.view.indexAt(position)
                    self.anchor_row = (
                        index.row() if index.isValid() else None
                    )
                    self.press_position = position
                    self.dragging = False
                    return False

                if event_type == event_types.MouseMove:
                    if self.anchor_row is None:
                        return False
                    if not (event.buttons() & left_button):
                        return False

                    position = _mouse_event_position(event)
                    if not self.dragging:
                        distance = (
                            position - self.press_position
                        ).manhattanLength()
                        if (
                            distance
                            < QtWidgets.QApplication.startDragDistance()
                        ):
                            return False
                        self.dragging = True

                    index = self.view.indexAt(position)
                    if not index.isValid():
                        return True

                    paths = _joint_paths_for_view_rows(
                        self.view,
                        QtCore,
                        self.anchor_row,
                        index.row(),
                    )
                    _JOINT_LIST.select_joint_paths(paths)
                    return True

                if event_type == event_types.MouseButtonRelease:
                    was_dragging = self.dragging
                    self.anchor_row = None
                    self.press_position = None
                    self.dragging = False
                    return bool(was_dragging)

                return False

        _DRAG_SELECTION_VIEW = tree_view
        _DRAG_SELECTION_FILTER = JointDragSelectionFilter(tree_view)
        tree_view.viewport().installEventFilter(_DRAG_SELECTION_FILTER)
    except Exception:
        _DRAG_SELECTION_FILTER = None
        _DRAG_SELECTION_VIEW = None


def _joint_paths_for_view_rows(view, QtCore, start_row: int, end_row: int):
    ordered = _state().get("joint_display_order", [])
    first, last = sorted((int(start_row), int(end_row)))
    root = QtCore.QModelIndex()
    result = []

    for row in range(first, last + 1):
        if row < 0 or row >= len(ordered):
            continue
        try:
            hidden = view.isRowHidden(row, root)
        except Exception:
            hidden = False
        if not hidden:
            result.append(ordered[row])
    return result


def _mouse_event_position(event):
    position = getattr(event, "position", None)
    if callable(position):
        return position().toPoint()
    return event.pos()


def _shiboken_wrap_instance(binding_name):
    if binding_name == "PySide6":
        from shiboken6 import wrapInstance

        return wrapInstance

    from shiboken2 import wrapInstance

    return wrapInstance


def _state():
    _require_configured()
    return _TOOL_WINDOW._STATE


def _require_configured() -> None:
    if _JOINT_LIST is None or _TOOL_WINDOW is None:
        raise RuntimeError(
            "AD Skin Tool joint-list refinements are not configured."
        )
