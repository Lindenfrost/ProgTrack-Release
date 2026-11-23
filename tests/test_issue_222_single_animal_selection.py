"""Focused regression tests for #222 cross-tab selection normalization."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem, QTabWidget, QWidget


ROOT = Path(__file__).resolve().parents[1]


class Issue222SelectionNormalizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location(
            "progtrack_issue_222", ROOT / "ProgTrack.v.0.3.0.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def _app_with_loaded_tab(self, kind: str):
        app = type("FakeApp", (), {})()
        app.animals = {"A": {}, "B": {}, "C": {}}
        app.archived = {}
        app._defer_ui_update = lambda *_flags: False
        app.selected_animals = ["A", "B", "C"]
        app._plot_selection_order = ["A", "B", "C"]
        app._edit_selection_order = ["A", "B", "C"]
        app._selected_heritage_only = []
        app._selected_archived = []
        app.lst = QListWidget()
        for key in ("A", "B", "C"):
            item = QListWidgetItem(key)
            item.setData(Qt.ItemDataRole.UserRole, key)
            app.lst.addItem(item)
            item.setSelected(True)

        app.main_tabs = QTabWidget()
        report = QWidget()
        medi = QWidget()
        report_placeholder = QWidget()
        medi_placeholder = QWidget()
        app.main_tabs.addTab(QWidget(), "Plots")
        app.main_tabs.addTab(report, "Reports")
        app.main_tabs.addTab(medi, "Medi Track")
        app.reports_enabled = True
        app.reports_tab = report
        app.reports_tab_placeholder = report_placeholder
        app.has_medi_track_plugin = True
        app.medi_track_tab = medi
        app.medi_track_tab_placeholder = medi_placeholder
        app._single_animal_tab_kind = lambda widget: self.module.ProgTrackApp._single_animal_tab_kind(app, widget)
        app._sync_heritage_category_tab_state = lambda: (
            self.module.ProgTrackApp._sync_heritage_category_tab_state(app)
        )
        app._normalize_sidebar_selection_value = lambda value: (
            self.module.ProgTrackApp._normalize_sidebar_selection_value(app, value)
        )
        app._sidebar_projection_keys = lambda: (
            self.module.ProgTrackApp._sidebar_projection_keys(app)
        )
        app._normalize_selection_for_single_animal_tab = lambda widget=None: (
            self.module.ProgTrackApp._normalize_selection_for_single_animal_tab(app, widget)
        )
        return app, report, medi, report_placeholder, medi_placeholder

    def test_loaded_reports_transition_normalizes_shared_state_and_rows(self):
        app, report, _medi, _report_placeholder, _medi_placeholder = self._app_with_loaded_tab("reports")
        app.main_tabs.setCurrentWidget(report)
        received = []
        app._update_reports_for_animal = received.append
        app._prepare_unlocked_report_rows_for_reentry = lambda: None
        app._refresh_list = lambda **_kwargs: None
        app._prev_tab_was_reports = False
        app._prev_tab_was_heritage = False
        app.has_heritage_plugin = False
        app.has_cage_track_plugin = False
        app.flow_track_enabled = False
        app._pt_tab_needed = False

        self.module.ProgTrackApp._on_tab_changed(app, 1)

        self.assertEqual(app.selected_animals, ["C"])
        self.assertEqual(app._plot_selection_order, ["C"])
        self.assertEqual(app._edit_selection_order, ["C"])
        self.assertEqual(
            [item.text() for item in app.lst.selectedItems()], ["C"]
        )
        self.assertEqual(received, ["C"])

    def test_direct_list_change_while_reports_is_active_uses_same_contract(self):
        app, report, _medi, _report_placeholder, _medi_placeholder = self._app_with_loaded_tab("reports")
        app.main_tabs.setCurrentWidget(report)
        app._plot_x_viewport = None
        received = []
        app._update_reports_for_animal = received.append
        app._normalize_selection_for_single_animal_tab = None

        self.module.ProgTrackApp._on_select(app)

        self.assertEqual(app.selected_animals, ["C"])
        self.assertEqual(
            [item.text() for item in app.lst.selectedItems()], ["C"]
        )
        self.assertEqual(received, ["C"])

    def test_heritage_to_reports_keeps_the_normalized_subject(self):
        app, report, _medi, _report_placeholder, _medi_placeholder = self._app_with_loaded_tab("reports")
        app.main_tabs.setCurrentWidget(report)
        app.has_heritage_plugin = True
        app.heritage_track_tab = None
        app.heritage_track_tab_placeholder = QWidget()
        hidden = []
        app.heritage_plugin = SimpleNamespace(on_tab_hidden=lambda: hidden.append(True))
        app._prev_tab_was_heritage = True
        app._prev_tab_was_reports = False
        app._update_reports_for_animal = lambda _name: None
        app._refresh_list = lambda **_kwargs: None
        app.has_cage_track_plugin = False
        app.flow_track_enabled = False
        app._pt_tab_needed = False

        self.module.ProgTrackApp._on_tab_changed(app, 1)

        self.assertEqual(app.selected_animals, ["C"])
        self.assertEqual(app._plot_selection_order, ["C"])
        self.assertEqual(
            [item.text() for item in app.lst.selectedItems()], ["C"]
        )
        self.assertEqual(hidden, [True])

    def test_medi_placeholder_and_loaded_widgets_share_the_same_contract(self):
        app, _report, medi, _report_placeholder, medi_placeholder = self._app_with_loaded_tab("medi")
        for widget in (medi_placeholder, medi):
            app.selected_animals = ["A", "B", "C"]
            app._plot_selection_order = ["A", "B", "C"]
            app._edit_selection_order = ["A", "B", "C"]
            app.lst.clearSelection()
            for item in (app.lst.item(i) for i in range(app.lst.count())):
                item.setSelected(True)

            retained = self.module.ProgTrackApp._normalize_selection_for_single_animal_tab(
                app, widget
            )

            self.assertEqual(retained, "C")
            self.assertEqual(app.selected_animals, ["C"])
            self.assertEqual(app._plot_selection_order, ["C"])
            self.assertEqual(app._edit_selection_order, ["C"])
            self.assertEqual(
                [item.text() for item in app.lst.selectedItems()], ["C"]
            )

    def test_filtered_out_retained_animal_stays_internal_and_unhighlighted(self):
        app, _report, _medi, report_placeholder, _medi_placeholder = self._app_with_loaded_tab("reports")
        app.lst.clear()
        for key in ("A", "B"):
            item = QListWidgetItem(key)
            item.setData(Qt.ItemDataRole.UserRole, key)
            app.lst.addItem(item)
            item.setSelected(True)

        retained = self.module.ProgTrackApp._normalize_selection_for_single_animal_tab(
            app, report_placeholder
        )

        self.assertEqual(retained, "C")
        self.assertEqual(app.selected_animals, ["C"])
        self.assertEqual(app._plot_selection_order, ["C"])
        self.assertEqual(app._edit_selection_order, ["C"])
        self.assertEqual(app.lst.selectedItems(), [])
        self.assertEqual(app.lst.count(), 2)

    def test_archived_last_selection_has_no_unsupported_fallback(self):
        app, _report, _medi, report_placeholder, _medi_placeholder = self._app_with_loaded_tab("reports")
        app.archived = {"C": {"id": "archived-c"}}
        app.animals.pop("C")

        retained = self.module.ProgTrackApp._normalize_selection_for_single_animal_tab(
            app, report_placeholder
        )

        self.assertIsNone(retained)
        self.assertEqual(app.selected_animals, [])
        self.assertEqual(app._plot_selection_order, [])
        self.assertEqual(app._edit_selection_order, [])
        self.assertEqual(app._selected_heritage_only, [])
        self.assertEqual(app.lst.selectedItems(), [])


if __name__ == "__main__":
    unittest.main()
