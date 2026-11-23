"""Focused regression tests for #242 Heritage startup category-tab state."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QListWidget, QTabBar, QTabWidget, QWidget


ROOT = Path(__file__).resolve().parents[1]


class Issue242HeritageCategoryTabsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location(
            "progtrack_issue_242", ROOT / "ProgTrack.v.0.3.0.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def _harness(self, role_count=6, base_stylesheet=""):
        app = type("CategoryTabHarness", (), {})()
        app._defer_ui_update = lambda *flags: self.module.ProgTrackApp._defer_ui_update(app, *flags)
        app.category_tab = QTabBar()
        app.category_tab.setStyleSheet(base_stylesheet)
        for index in range(role_count):
            app.category_tab.addTab(f"Role {index}")
        app.category_tab.addTab("All")

        app.main_tabs = QTabWidget()
        app.plots_tab = QWidget()
        app.heritage_track_tab_placeholder = QWidget()
        app.main_tabs.addTab(app.plots_tab, "Plots")
        app.main_tabs.addTab(app.heritage_track_tab_placeholder, "Heritage Track")
        app.heritage_track_tab = None
        app._all_category_tab_index = lambda: (
            self.module.ProgTrackApp._all_category_tab_index(app)
        )
        app._sync_heritage_category_tab_state = lambda: (
            self.module.ProgTrackApp._sync_heritage_category_tab_state(app)
        )
        app._category_descriptor_for_index = lambda index, custom_values=None: (
            self.module.ProgTrackApp._category_descriptor_for_index(
                app, index, custom_values
            )
        )
        return app

    def _assert_heritage_category_state(self, app):
        all_index = app._all_category_tab_index()
        self.assertEqual(app.category_tab.currentIndex(), all_index)
        self.assertTrue(app.category_tab.isTabEnabled(all_index))
        for index in range(app.category_tab.count()):
            if index != all_index:
                self.assertFalse(app.category_tab.isTabEnabled(index), index)
        self.assertIn("QTabBar::tab:disabled", app.category_tab.styleSheet())
        self.assertIn("color: grey", app.category_tab.styleSheet())

    def test_session_restore_cannot_restore_role_category_over_heritage_all(self):
        app = self._harness(role_count=7, base_stylesheet="QTabBar { padding: 1px; }")
        app.messages = {
            "tab.plots": "Plots",
            "tab.reports": "Reports",
            "tab.flow_track": "Flow Track",
            "tab.heritage_track": "Heritage Track",
            "tab.cage_track": "Cage Track",
        }

        # This reproduces the ordering that caused #242: restore the Heritage
        # page, then restore a previously selected role category.
        self.module.ProgTrackApp._restore_session_ui(
            app,
            {"last_active_tab": "tab.heritage_track", "last_category_index": 2},
        )

        self._assert_heritage_category_state(app)

    def test_placeholder_and_loaded_heritage_widget_share_state_and_restore_style(self):
        app = self._harness(base_stylesheet="QTabBar { padding: 2px; }")
        app.main_tabs.setCurrentWidget(app.heritage_track_tab_placeholder)
        app._sync_heritage_category_tab_state()
        self._assert_heritage_category_state(app)

        # Exercise the real lazy placeholder replacement, including its
        # post-replacement synchronization call.
        index = app.main_tabs.indexOf(app.heritage_track_tab_placeholder)
        loaded_heritage_tab = QWidget()
        app.heritage_track_tab = loaded_heritage_tab
        self.module.ProgTrackApp._replace_lazy_tab(
            app, index, loaded_heritage_tab, "Heritage Track"
        )
        self._assert_heritage_category_state(app)

        app.main_tabs.setCurrentWidget(app.plots_tab)
        app._sync_heritage_category_tab_state()
        self.assertTrue(
            all(app.category_tab.isTabEnabled(i) for i in range(app.category_tab.count()))
        )
        self.assertEqual(
            app.category_tab.styleSheet(), "QTabBar { padding: 2px; }"
        )

    def test_normal_main_tab_change_path_applies_and_clears_heritage_state(self):
        app = self._harness()
        loaded_heritage_tab = QWidget()
        app.main_tabs.addTab(loaded_heritage_tab, "Heritage Track")
        app.heritage_track_tab = loaded_heritage_tab
        app.has_heritage_plugin = True
        app.reports_enabled = False
        app.flow_track_enabled = False
        app.has_cage_track_plugin = False
        app.has_medi_track_plugin = False
        app._pt_tab_needed = False
        app.selected_animals = []
        app._prev_tab_was_heritage = False
        app._prev_tab_was_reports = False
        app.lst = QListWidget()
        app._selected_heritage_only = []
        app._single_animal_tab_kind = lambda _widget: None
        app._normalize_selection_for_single_animal_tab = lambda _widget: None
        app._refresh_list = lambda **_kwargs: None
        app._plot_selected = lambda: None

        heritage_index = app.main_tabs.indexOf(loaded_heritage_tab)
        app.main_tabs.setCurrentIndex(heritage_index)
        self.module.ProgTrackApp._on_tab_changed(app, heritage_index)
        self._assert_heritage_category_state(app)

        plots_index = app.main_tabs.indexOf(app.plots_tab)
        app.main_tabs.setCurrentIndex(plots_index)
        self.module.ProgTrackApp._on_tab_changed(app, plots_index)
        self.assertTrue(
            all(app.category_tab.isTabEnabled(i) for i in range(app.category_tab.count()))
        )

    def test_category_rebuild_keeps_all_enabled_and_new_custom_role_disabled(self):
        app = self._harness()
        app.main_tabs.setCurrentWidget(app.heritage_track_tab_placeholder)
        app._sync_heritage_category_tab_state()
        app._category_custom_role_values = []
        app._category_tab_categories = lambda: (
            [("", f"tip-{index}") for index in range(6)]
            + [("Custom", "custom role"), ("All", "all roles")],
            ["custom_role"],
        )

        self.module.ProgTrackApp._rebuild_category_tabs(app)

        self.assertEqual(app.category_tab.count(), 8)
        self._assert_heritage_category_state(app)


if __name__ == "__main__":
    unittest.main()
