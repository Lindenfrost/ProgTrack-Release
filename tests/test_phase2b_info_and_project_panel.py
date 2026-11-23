from __future__ import annotations

import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidgetItem

from Plugins.Projects_Track.project_track_tab import (
    ProjectTrackTab,
    _PROJECT_LIST_PANEL_WIDTH,
)


ROOT = Path(__file__).resolve().parents[1]


class _Records:
    def get(self, namespace, record_id, default=None):
        return default

    def put(self, namespace, record_id, value):
        return 1


class _Backend:
    records = _Records()


class _App:
    backend = _Backend()
    animals = {}
    archived_animals = {}

    @staticmethod
    def _master_can(_permission):
        return True


class _History:
    @staticmethod
    def all_projects():
        return []

    @staticmethod
    def is_archived(_name):
        return False


def _messages(language: str) -> dict:
    return json.loads(
        (ROOT / "lang" / f"messages_{language}.json").read_text(encoding="utf-8")
    )


class InfoAndProjectPanelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tab = ProjectTrackTab(_App(), _messages("en"), _History())
        self.tab.resize(900, 620)
        self.tab.show()
        self.qt.processEvents()

    def tearDown(self):
        self.tab.close()
        self.tab.deleteLater()
        self.qt.processEvents()

    def test_all_about_sources_show_the_current_release_without_rc_marker(self):
        for filename in (
            "info.json", "info_en.json", "info_de.json", "info_it.json", "info_ru.json"
        ):
            content = (ROOT / filename).read_text(encoding="utf-8")
            self.assertIn("0.3.0", content, filename)
            self.assertNotIn("RC v.1.0", content, filename)
            self.assertNotIn("RC 1.0", content, filename)

    def test_project_panel_is_compact_and_archive_actions_are_stacked(self):
        panel = self.tab._list.parentWidget()
        self.assertEqual(_PROJECT_LIST_PANEL_WIDTH, 150)
        self.assertEqual(panel.width(), _PROJECT_LIST_PANEL_WIDTH)
        self.assertLess(self.tab._restore_btn.geometry().top(), self.tab._delete_btn.geometry().top())
        self.assertEqual(self.tab._restore_btn.geometry().left(), self.tab._delete_btn.geometry().left())
        self.assertEqual(self.tab._restore_btn.width(), self.tab._delete_btn.width())

    def test_all_languages_fit_and_keep_complete_action_tooltips(self):
        controls = (
            self.tab._new_project_btn,
            self.tab._archive_btn,
            self.tab._restore_btn,
            self.tab._delete_btn,
        )
        for language in ("en", "de", "it", "ru"):
            messages = _messages(language)
            self.tab.update_language(messages)
            self.qt.processEvents()
            for button in controls:
                text_width = button.fontMetrics().horizontalAdvance(button.text())
                icon_width = 24 if not button.icon().isNull() else 0
                self.assertLessEqual(text_width + icon_width + 30, button.width(), (language, button.text()))
                self.assertTrue(button.toolTip(), (language, button.text()))
                self.assertEqual(button.accessibleName(), button.toolTip())
            checkbox_width = self.tab._show_archived_chk.fontMetrics().horizontalAdvance(
                self.tab._show_archived_chk.text()
            )
            self.assertLessEqual(checkbox_width + 30, self.tab._show_archived_chk.width())
            self.assertEqual(
                self.tab._show_archived_chk.toolTip(),
                messages["project.tab.show_archived"],
            )

    def test_elided_project_name_remains_complete_in_tooltip(self):
        name = "A deliberately very long international research project name"
        self.tab._project_data["projects"][name] = {"status": "active"}
        item = QListWidgetItem(name)
        self.tab._style_project_item(item, name)
        self.assertTrue(item.toolTip().startswith(name + "\n"))
        self.assertIn("Active", item.data(Qt.ItemDataRole.AccessibleTextRole))


if __name__ == "__main__":
    unittest.main()
