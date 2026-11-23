"""Focused geometry checks for the All-tab archived-row regression."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (
    QApplication, QLabel, QSizePolicy, QHBoxLayout, QVBoxLayout, QWidget,
    QListWidget, QListWidgetItem, QTabWidget, QCheckBox,
)
from PyQt6.QtGui import QFont


ROOT = Path(__file__).resolve().parents[1]


def _compact_name_row(text: str, *, status: str = "") -> QWidget:
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(4, 2, 4, 2)
    name = QLabel(text)
    name.setMinimumWidth(0)
    name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
    layout.addWidget(name, 1)
    if status:
        marker = QLabel(status)
        marker.setFixedWidth(18)
        layout.addWidget(marker, 0)
    return row


class AllTabArchivedRowLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_long_archived_row_does_not_push_active_status_outside_panel(self):
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        active = _compact_name_row("Active animal", status="+")
        archived = _compact_name_row(
            "An archived animal with a deliberately very long display name"
        )
        layout.addWidget(active)
        layout.addWidget(archived)
        panel.resize(180, 70)
        panel.show()
        self.qt_app.processEvents()
        try:
            status = active.layout().itemAt(1).widget()
            name = active.layout().itemAt(0).widget()
            self.assertLessEqual(status.geometry().right(), active.width())
            self.assertLessEqual(name.geometry().right(), status.geometry().left())
            self.assertLessEqual(archived.sizeHint().width(), panel.width())
        finally:
            panel.close()
            panel.deleteLater()
            self.qt_app.processEvents()

    def test_archived_row_uses_the_same_all_tab_compact_policy(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        start = source.index("                    arch_name_lbl = (")
        end = source.index("                    arch_item.setSizeHint", start)
        archived_builder = source[start:end]
        self.assertIn("_BoundedNameLabel", archived_builder)
        self.assertNotIn("QSizePolicy.Policy.Ignored", archived_builder)
        self.assertIn("arch_h.addWidget(arch_name_lbl, 1)", archived_builder)
        self.assertIn("arch_h.addStretch()", archived_builder)

    def test_production_row_sync_keeps_mixed_rows_inside_viewport(self):
        module = __import__("importlib.util").util.spec_from_file_location(
            "progtrack_issue_175", ROOT / "ProgTrack.v.0.3.0.py"
        )
        loaded = __import__("importlib.util").util.module_from_spec(module)
        assert module.loader is not None
        module.loader.exec_module(loaded)
        app = loaded.ProgTrackApp.__new__(loaded.ProgTrackApp)
        app.lst = QListWidget()
        app.category_tab = QTabWidget()
        for label in ("F", "M", "O", "P", "B", "E", "All"):
            app.category_tab.addTab(QWidget(), label)
        app._all_category_tab_index = lambda: 6
        for text, status in (
            ("A very long active identity " * 3, "+"),
            ("A very long archived identity " * 3, ""),
        ):
            item = QListWidgetItem(app.lst)
            row = _compact_name_row(text, status=status)
            app.lst.setItemWidget(item, row)
        app.lst.resize(180, 80)
        app.lst.show()
        self.qt_app.processEvents()
        try:
            app._sync_animal_list_row_widths()
            viewport_right = app.lst.viewport().width()
            for index in range(app.lst.count()):
                item = app.lst.item(index)
                row = app.lst.itemWidget(item)
                self.assertLessEqual(item.sizeHint().width(), viewport_right)
                self.assertLessEqual(row.width(), viewport_right)
        finally:
            app.lst.close()
            app.lst.deleteLater()
            app.category_tab.deleteLater()
            self.qt_app.processEvents()

    def test_viewport_resize_defers_row_sync_until_layout_settles(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "progtrack_issue_175_viewport", ROOT / "ProgTrack.v.0.3.0.py"
        )
        loaded = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(loaded)

        calls = []
        lst = loaded._ViewportSyncListWidget()
        lst.set_viewport_sync_callback(lambda: calls.append(lst.viewport().width()))
        lst.resize(180, 80)
        lst.show()
        self.qt_app.processEvents()
        try:
            calls.clear()
            lst.resize(260, 80)
            self.assertEqual(calls, [])
            self.qt_app.processEvents()
            self.assertTrue(calls)
            self.assertEqual(calls[-1], lst.viewport().width())
        finally:
            lst.close()
            lst.deleteLater()
            self.qt_app.processEvents()

    def test_bounded_name_reelides_when_font_metrics_change_without_resize(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "progtrack_issue_175_font", ROOT / "ProgTrack.v.0.3.0.py"
        )
        loaded = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(loaded)
        label = loaded._BoundedNameLabel("A very long Unicode identity 名称")
        label.resize(110, 24)
        label.show()
        self.qt_app.processEvents()
        try:
            before = label.text()
            font = QFont(label.font())
            font.setPointSize(font.pointSize() + 5)
            label.setFont(font)
            self.qt_app.processEvents()
            after = label.text()
            self.assertEqual(label.toolTip(), "A very long Unicode identity 名称")
            self.assertEqual(
                after,
                label.fontMetrics().elidedText(
                    label.toolTip(), loaded.Qt.TextElideMode.ElideRight,
                    label.contentsRect().width(),
                ),
            )
            self.assertNotEqual(before, after)
        finally:
            label.close()
            label.deleteLater()
            self.qt_app.processEvents()

    def test_production_refresh_matrix_projects_and_archived_filters(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "progtrack_issue_175_matrix", ROOT / "ProgTrack.v.0.3.0.py"
        )
        loaded = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(loaded)
        from PyQt6.QtWidgets import QComboBox, QPushButton
        from Plugins.Projects_Track.ProjectsTrack_plugin import ProjectsTrackPlugin

        app = loaded.ProgTrackApp.__new__(loaded.ProgTrackApp)
        loaded.QtWidgets.QMainWindow.__init__(app)
        app.messages = {"archived.no_animals": "No archived animals"}
        app.animals = {
            "Active-1": {"id": "A1", "rolle": "egg_cell_donor"},
            "Active-2": {"id": "A2", "rolle": "egg_cell_donor"},
        }
        app.archived = {
            "Archived-" + ("Unicode名 " * 8): {"id": "Z1", "rolle": "egg_cell_donor"},
        }
        app.selected_animals = []
        app._selected_archived = []
        app._selected_heritage_only = []
        app._disabled_plugins = set()
        app.has_projects_plugin = False
        app.projects_plugin = None
        app.has_heritage_plugin = False
        app.has_medi_track_plugin = False
        app.category_tab = QTabWidget()
        for label in ("F", "M", "O", "P", "B", "E", "All"):
            app.category_tab.addTab(QWidget(), label)
        app.category_tab.setCurrentIndex(6)
        app.lst = loaded._ViewportSyncListWidget()
        app.chk_show_archived = QCheckBox()
        app.cmb_arch = QComboBox()
        app.btn_restore = QPushButton(); app.btn_delete = QPushButton(); app.btn_edit = QPushButton()
        app.btn_edit_animal = QPushButton()
        app.phase_buttons = {}
        app.main_tabs = QTabWidget(); app.main_tabs.addTab(QWidget(), "Plots")
        app._current_project_filter = "All"
        app._all_category_tab_index = lambda: 6
        app._category_descriptor_for_index = lambda *_args: None
        app._rebuild_category_tabs = lambda: None
        app._update_category_tab_visibility = lambda: None
        app._animal_matches_sex_filter = lambda _data: True
        app._animal_role_value = lambda data: data.get("rolle", "spender")
        app._is_steroid_track_active = lambda: False
        app._current_animal_name_filter_text = lambda: ""
        # The production sidebar now requests a fresh authoritative scope.
        # Accept the current keyword-only contract rather than weakening it.
        app._project_visibility_scope = lambda *, fresh=False: (True, set())
        app._display_name = lambda name: name
        app._normalize_sidebar_selection_value = lambda value: value
        app._get_status = lambda _name: "ACTIVE"
        app._status_icon_ids = lambda *_args: []
        app._status_text_without_icon_tokens = lambda status: status
        app._get_status_description = lambda _status: "status"
        app._relationship_icon_tooltip = lambda *_args: ""
        app._status_icon_tooltip = lambda _status: "status"
        app._in_experiment_icon_tooltip = lambda _data: "experiment"
        app._master_can = lambda _permission: True
        app._can_use_primary_edit_button = lambda: True
        app._update_report_table = lambda: None
        app._show_reports_splash = lambda: None
        app.phase_from_combined_or_blood = lambda *_args: None
        original_sync = app._sync_animal_list_row_widths
        sync_calls = []
        def tracked_sync():
            sync_calls.append("sync")
            return original_sync()
        app._sync_animal_list_row_widths = tracked_sync
        app.lst.set_viewport_sync_callback(app._sync_animal_list_row_widths)
        app.resize(320, 220)
        app.lst.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        class ProjectHost:
            class Records:
                def get(self, _namespace, _record_id, default=None):
                    return default
                def put(self, _namespace, _record_id, _value, **_kwargs):
                    return 1
            backend = type("Backend", (), {"records": Records()})()
            animals = app.animals
            messages = {"projects_track": "Projects"}
            _disabled_plugins = set()
            def _apply_project_filter(self, _project):
                return None
            def _sync_animal_list_row_widths(self):
                app._sync_animal_list_row_widths()
        host = QWidget()
        host_layout = QHBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        # Exercise the production geometry relationship: the animal list and
        # Projects strip share the same horizontal layout, so toggling the
        # strip changes the list viewport before the app's sync hook runs.
        host_layout.addWidget(app.lst, 1)
        projects_plugin = ProjectsTrackPlugin(ProjectHost())
        project_strip = projects_plugin.create_sidebar_tabs(host)
        host_layout.addWidget(project_strip)
        host.resize(320, 220)
        host.show()
        self.qt_app.processEvents()
        try:
            for expanded in (True, False):
                for archived in (False, True):
                    with self.subTest(expanded=expanded, archived=archived):
                        before_toggle_sync = len(sync_calls)
                        projects_plugin._sidebar_toggle_btn.setChecked(expanded)
                        self.qt_app.processEvents()
                        projects_plugin._sidebar_toggle_btn.setChecked(not expanded)
                        self.qt_app.processEvents()
                        projects_plugin._sidebar_toggle_btn.setChecked(expanded)
                        self.qt_app.processEvents()
                        self.assertGreater(
                            len(sync_calls), before_toggle_sync,
                            "sidebar transitions must resync row widths after layout",
                        )
                        app.chk_show_archived.setChecked(archived)
                        before_refresh_sync = len(sync_calls)
                        app._refresh_list()
                        self.qt_app.processEvents()
                        self.assertGreater(
                            len(sync_calls), before_refresh_sync,
                            "production refresh must resync rows after rebuilding",
                        )
                        viewport_right = app.lst.viewport().width()
                        for index in range(app.lst.count()):
                            item = app.lst.item(index)
                            row = app.lst.itemWidget(item)
                            if row is None:
                                continue
                            self.assertLessEqual(item.sizeHint().width(), viewport_right)
                            self.assertLessEqual(row.width(), viewport_right)
                            tail = row.layout().itemAt(row.layout().count() - 1).widget()
                            self.assertIsNotNone(tail)
                            self.assertLessEqual(tail.geometry().right(), row.width())
                        self.assertGreater(viewport_right, 0)
            # Compare the ordinary role-tab contract with the All-tab
            # contract at the same constrained host width.  The production
            # status text is deliberately present so the trailing region is
            # measured rather than inferred from a helper row.
            app.category_tab.setCurrentIndex(0)
            app.chk_show_archived.setChecked(True)
            self.assertEqual(app.category_tab.currentIndex(), 0)
            self.assertEqual(
                [app._animal_role_value(data) for data in app.animals.values()],
                ["egg_cell_donor", "egg_cell_donor"],
            )
            self.assertIn(
                app._animal_role_value(app.animals["Active-1"]),
                (loaded.Role.SPENDER.value, loaded.Role.AMME.value),
            )
            app._refresh_list()
            self.qt_app.processEvents()
            role_viewport = app.lst.viewport().width()
            role_items = [
                app.lst.itemWidget(app.lst.item(i))
                for i in range(app.lst.count())
                if app.lst.itemWidget(app.lst.item(i)) is not None
            ]
            self.assertGreater(role_viewport, 0)
            self.assertTrue(role_items)
            app.category_tab.setCurrentIndex(6)
            app._refresh_list()
            self.qt_app.processEvents()
            all_viewport = app.lst.viewport().width()
            self.assertGreater(all_viewport, 0)
            for index in range(app.lst.count()):
                item = app.lst.item(index)
                row = app.lst.itemWidget(item)
                if row is None:
                    continue
                self.assertLessEqual(row.width(), all_viewport)
                name = row.layout().itemAt(0).widget()
                self.assertEqual(name.toolTip(), item.toolTip() or name.toolTip())
                self.assertNotEqual(
                    name.sizePolicy().horizontalPolicy(),
                    QSizePolicy.Policy.Ignored,
                )

            # Repeated production transitions must not accumulate row widgets
            # or change the final ordering/width contract.
            expected_order = [
                app.lst.item(i).data(0x0100) for i in range(app.lst.count())
            ]
            for _ in range(10):
                projects_plugin._sidebar_toggle_btn.setChecked(False)
                projects_plugin._sidebar_toggle_btn.setChecked(True)
                app.chk_show_archived.setChecked(False)
                app._refresh_list()
                app.chk_show_archived.setChecked(True)
                app._refresh_list()
            self.qt_app.processEvents()
            self.assertEqual(
                [app.lst.item(i).data(0x0100) for i in range(app.lst.count())],
                expected_order,
            )
            before_resize = len(sync_calls)
            app.resizeEvent(loaded.QtGui.QResizeEvent(
                loaded.QSize(360, 220), loaded.QSize(320, 220)
            ))
            self.assertGreater(len(sync_calls), before_resize)
        finally:
            host.close(); app.lst.close(); app.deleteLater(); self.qt_app.processEvents()


if __name__ == "__main__":
    unittest.main()
