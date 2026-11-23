"""Focused menu-structure regression checks for Tools grouping (#236)."""

from __future__ import annotations

from pathlib import Path
from types import MethodType
from unittest import TestCase

from PyQt6.QtWidgets import QApplication, QMainWindow, QMenu


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "ProgTrack.v.0.3.0.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")

import importlib.util  # noqa: E402

SPEC = importlib.util.spec_from_file_location("progtrack_issue_236", SOURCE_PATH)
MAIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MAIN)


class _MenuHarness(QMainWindow):
    def __init__(self):
        super().__init__()
        self._PLUGIN_ACTION_ATTRS = MAIN.ProgTrackApp._PLUGIN_ACTION_ATTRS
        self._CHECKED_PLUGIN_MENU_GROUPS = MAIN.ProgTrackApp._CHECKED_PLUGIN_MENU_GROUPS
        self._CHECKED_PLUGIN_MENU_LABELS = MAIN.ProgTrackApp._CHECKED_PLUGIN_MENU_LABELS
        self.messages = {
            "menu.tools.animal_reports": "Animal Reports",
            "menu.tools.medi_track": "Medi Track",
            "menu.tools.projects_track": "Project Track",
            "menu.tools.cage_track": "Cage Track",
            "menu.tools.heritage_track": "Heritage Track",
            "menu.tools.flow_track": "Flow Track",
            "menu.tools.steroid_track": "Steroid Track",
        }
        self._disabled_plugins = set()
        self.reports_enabled = True
        self.has_medi_track_plugin = True
        self.has_projects_plugin = True
        self.has_cage_track_plugin = True
        self.has_heritage_plugin = True
        self.flow_track_enabled = True
        self.has_steroid_track_plugin = True
        self.toggle_calls = []
        self._style_plugin_action = MethodType(
            MAIN.ProgTrackApp._style_plugin_action, self
        )
        self._ensure_checked_plugin_menu_action = MethodType(
            MAIN.ProgTrackApp._ensure_checked_plugin_menu_action, self
        )
        self._checked_plugin_menu_available = MethodType(
            MAIN.ProgTrackApp._checked_plugin_menu_available, self
        )
        self._rebuild_checked_plugin_menu = MethodType(
            MAIN.ProgTrackApp._rebuild_checked_plugin_menu, self
        )

    def _toggle_plugin_enabled(self, plugin_key: str, enabled: bool) -> None:
        self.toggle_calls.append((plugin_key, enabled))


class Issue236ToolsMenuGroupingTest(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @staticmethod
    def _menu_entries(menu):
        return [
            None if action.isSeparator() else action.text()
            for action in menu.actions()
        ]

    def test_all_available_plugins_have_requested_group_order_and_separators(self):
        harness = _MenuHarness()
        menu = QMenu(harness)
        harness._rebuild_checked_plugin_menu(menu)

        self.assertEqual(
            self._menu_entries(menu),
            [
                "Animal Reports", "Medi Track", "Project Track", None,
                "Cage Track", "Heritage Track", None,
                "Flow Track", "Steroid Track",
            ],
        )
        self.assertTrue(all(action.isCheckable() for action in menu.actions() if not action.isSeparator()))

    def test_empty_middle_group_gets_no_empty_separator(self):
        harness = _MenuHarness()
        harness.has_cage_track_plugin = False
        harness.has_heritage_plugin = False
        menu = QMenu(harness)
        harness._rebuild_checked_plugin_menu(menu)

        self.assertEqual(
            self._menu_entries(menu),
            [
                "Animal Reports", "Medi Track", "Project Track", None,
                "Flow Track", "Steroid Track",
            ],
        )

    def test_rebuild_reuses_actions_and_preserves_checked_state(self):
        harness = _MenuHarness()
        harness.has_medi_track_plugin = False
        harness._disabled_plugins.add("flow_track")
        menu = QMenu(harness)
        harness._rebuild_checked_plugin_menu(menu)
        flow_action = harness.flow_track_action

        harness.has_medi_track_plugin = True
        harness._rebuild_checked_plugin_menu(menu)

        self.assertIs(flow_action, harness.flow_track_action)
        self.assertFalse(harness.flow_track_action.isChecked())
        self.assertEqual(
            [entry for entry in self._menu_entries(menu) if entry],
            [
                "Animal Reports", "Medi Track", "Project Track",
                "Cage Track", "Heritage Track", "Flow Track", "Steroid Track",
            ],
        )
        self.assertEqual(
            sum(action.isSeparator() for action in menu.actions()),
            2,
        )

    def test_initial_refresh_and_late_medi_setup_use_shared_helper(self):
        self.assertGreaterEqual(
            SOURCE.count("self._rebuild_checked_plugin_menu(tools_menu)"),
            2,
        )
        medi_setup = SOURCE[ SOURCE.index("    def _setup_medi_track_ui_if_needed"): ]
        medi_setup = medi_setup[: medi_setup.index("    def _init_sample_track_plugin")]
        self.assertIn("self._rebuild_checked_plugin_menu(tools_menu)", medi_setup)
        self.assertNotIn("tools_menu.addAction(self.medi_track_action)", medi_setup)
