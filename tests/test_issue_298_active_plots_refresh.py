"""Focused regression checks for #298 successful Edit Animal refreshes."""

from __future__ import annotations

import ast
import importlib.util
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMainWindow, QTabWidget, QWidget


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ProgTrack.v.0.3.0.py"
SPEC = importlib.util.spec_from_file_location("progtrack_issue_298", SOURCE)
MAIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MAIN)


class Issue298ActivePlotsRefreshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.host = MAIN.ProgTrackApp.__new__(MAIN.ProgTrackApp)
        QMainWindow.__init__(self.host)
        self.host.animals = {"Renamed": {}, "Peer": {}, "Hidden": {}}
        self.host.archived = {}
        self.host.selected_animals = ["Old", "Peer", "Hidden"]
        self.host._plot_selection_order = ["Old", "Peer", "Hidden"]
        self.host._edit_selection_order = ["Old", "Peer", "Hidden"]
        self.refresh_calls = []
        self.plot_calls = []
        self.host._refresh_list = lambda **kwargs: self.refresh_calls.append(kwargs)
        self.host._plot_selected = lambda: self.plot_calls.append("plot")

        self.host.main_tabs = QTabWidget()
        self.host.plots_tab = QWidget()
        self.other_tab = QWidget()
        self.host.main_tabs.addTab(self.host.plots_tab, "Plots / localized label")
        self.host.main_tabs.addTab(self.other_tab, "Other")

    def tearDown(self):
        self.host.main_tabs.deleteLater()
        self.host.deleteLater()
        self.qt.processEvents()

    def test_rename_and_filtered_selection_are_preserved_with_one_active_plot_refresh(self):
        self.host.main_tabs.setCurrentWidget(self.host.plots_tab)

        refreshed = MAIN.ProgTrackApp._complete_successful_animal_edit(
            self.host, "Old", "Renamed"
        )

        self.assertTrue(refreshed)
        self.assertEqual(self.host.selected_animals, ["Renamed", "Peer", "Hidden"])
        self.assertEqual(self.host._plot_selection_order, ["Renamed", "Peer", "Hidden"])
        self.assertEqual(self.host._edit_selection_order, ["Renamed", "Peer", "Hidden"])
        self.assertEqual(self.refresh_calls, [{"update_tab_visibility": True}])
        self.assertEqual(self.plot_calls, ["plot"])

    def test_non_plots_active_widget_keeps_selection_without_plot_redraw(self):
        self.host.main_tabs.setCurrentWidget(self.other_tab)

        refreshed = MAIN.ProgTrackApp._complete_successful_animal_edit(
            self.host, "Old", "Renamed"
        )

        self.assertFalse(refreshed)
        self.assertEqual(self.host.selected_animals, ["Renamed", "Peer", "Hidden"])
        self.assertEqual(self.refresh_calls, [{"update_tab_visibility": True}])
        self.assertEqual(self.plot_calls, [])

    def test_dialog_wrapper_only_finalizes_an_edit_marked_after_successful_save(self):
        cancelled = type("Dialog", (), {"exec": lambda _self: 0})()
        self.assertFalse(
            MAIN.ProgTrackApp._exec_animal_editor_dialog(self.host, cancelled)
        )
        self.assertEqual(self.refresh_calls, [])
        self.assertEqual(self.plot_calls, [])

        saved = type("Dialog", (), {"exec": lambda _self: 1})()
        saved._progtrack_successful_animal_edit = ("Old", "Renamed")
        self.assertTrue(
            MAIN.ProgTrackApp._exec_animal_editor_dialog(self.host, saved)
        )
        self.assertFalse(hasattr(saved, "_progtrack_successful_animal_edit"))
        self.assertEqual(self.refresh_calls, [{"update_tab_visibility": True}])
        self.assertEqual(self.plot_calls, ["plot"])

        # The completion marker is consumed, so a repeated wrapper call cannot
        # redraw or refresh a second time.
        self.assertFalse(
            MAIN.ProgTrackApp._exec_animal_editor_dialog(self.host, saved)
        )
        self.assertEqual(len(self.refresh_calls), 1)
        self.assertEqual(len(self.plot_calls), 1)

    def test_all_builtin_editors_defer_their_success_refresh_until_after_persistence(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        app_class = next(
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "ProgTrackApp"
        )
        required_editors = (
            "_dlg_partner",
            "_dlg_samenspender",
            "_dlg_offspring",
            "_dlg_zuchttier",
            "_dlg_versuchstier",
            "_dlg_basic_animal_role",
            "_dlg_female_animal",
        )

        for name in required_editors:
            with self.subTest(editor=name):
                method = next(
                    node for node in app_class.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == name
                )
                calls = [
                    node for node in ast.walk(method)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                ]
                self.assertTrue(
                    any(call.func.attr == "_exec_animal_editor_dialog" for call in calls),
                    f"{name} must finalize after its modal dialog returns",
                )
                marker_lines = [
                    node.lineno for node in ast.walk(method)
                    if isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Attribute)
                        and target.attr == "_progtrack_successful_animal_edit"
                        for target in node.targets
                    )
                ]
                persistence_lines = [
                    call.lineno for call in calls
                    if call.func.attr == "_save_persistence"
                ]
                self.assertTrue(marker_lines, f"{name} must mark successful edits")
                self.assertTrue(persistence_lines, f"{name} must persist its edit")
                self.assertGreater(min(marker_lines), min(persistence_lines))


if __name__ == "__main__":
    unittest.main()
