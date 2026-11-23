from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import (
    QApplication,
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QMainWindow,
    QWidget,
)

from Plugins.Projects_Track.ProjectsTrack_plugin import ProjectsTrackPlugin
from Plugins.Projects_Track.project_track_tab import (
    ProjectTrackTab,
    _PROJECT_IACUC_FIELD_MIN_WIDTH,
)


ROOT = Path(__file__).resolve().parents[1]


def _messages(language: str) -> dict:
    return json.loads(
        (ROOT / "lang" / f"messages_{language}.json").read_text(encoding="utf-8")
    )


def _load_main_module():
    spec = importlib.util.spec_from_file_location(
        "progtrack_sidebar_project_refinements",
        ROOT / "ProgTrack.v.0.3.0.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _Records:
    def get(self, _namespace, _record_id, default=None):
        return default

    def put(self, _namespace, _record_id, _value):
        return 1


class _Backend:
    records = _Records()


class _ProjectApp:
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

    @staticmethod
    def get_animals(_name):
        return []


class SidebarAndProjectRefinementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        cls.main_module = _load_main_module()

    def _main_harness(self, language: str = "en"):
        module = self.main_module

        class Harness(module.ProgTrackApp):
            def __init__(self):
                QMainWindow.__init__(self)
                self.messages = _messages(language)
                self.has_projects_plugin = False
                self.projects_plugin = None
                self._disabled_plugins = set()
                self.has_pdg_plugin = False
                self.refresh_calls = 0

            def _rebuild_category_tabs(self):
                for label in ("F", "M", "O", "P", "B", "E", "All"):
                    self.category_tab.addTab(label)

            @staticmethod
            def _is_steroid_track_active():
                return False

            def _refresh_list(self):
                self.refresh_calls += 1

        return Harness()

    def test_main_sidebar_is_compact_and_filters_use_svg_icons(self):
        harness = self._main_harness()
        sidebar_layout = harness._build_sidebar()
        sidebar_widget = harness._make_main_sidebar_widget(sidebar_layout)
        sidebar_widget.resize(300, 700)
        sidebar_widget.show()
        self.qt.processEvents()
        try:
            self.assertEqual(self.main_module._MAIN_ANIMAL_SIDEBAR_WIDTH, 300)
            self.assertEqual(self.main_module._MAIN_ANIMAL_LIST_MIN_WIDTH, 130)
            self.assertEqual(sidebar_widget.minimumWidth(), 300)
            self.assertEqual(sidebar_widget.maximumWidth(), 300)
            self.assertEqual(harness.lst.minimumWidth(), 130)
            self.assertEqual(harness.animal_name_filter_edit.minimumWidth(), 64)
            self.assertGreaterEqual(harness.animal_name_filter_edit.width(), 64)
            self.assertEqual(
                harness.animal_name_filter_edit.placeholderText(), "Animal name"
            )
            self.assertIs(
                harness.animal_name_filter_edit.parentWidget(),
                harness.animal_filter_row,
            )
            self.assertIs(
                harness.sex_filter_widget.parentWidget(),
                harness.animal_filter_row,
            )
            self.assertEqual(harness.sex_filter_widget.geometry().top(), 0)
            self.assertEqual(set(harness.sex_filter_checkboxes), {"F", "M", "U"})
            for checkbox in harness.sex_filter_checkboxes.values():
                self.assertTrue(checkbox.isChecked())
                self.assertFalse(checkbox.icon().isNull())
                self.assertTrue(checkbox.toolTip())
                self.assertEqual(checkbox.accessibleName(), checkbox.toolTip())
                self.assertIn("padding-left: 2px", checkbox.styleSheet())
                self.assertIn("padding-right: 2px", checkbox.styleSheet())
                # The indicator and 20 px SVG must both fit inside the
                # control; a narrower fixed width clips the SVG on Windows.
                self.assertGreaterEqual(checkbox.width(), checkbox.sizeHint().width())
            self.assertGreaterEqual(
                harness.sex_filter_widget.width(),
                sum(
                    checkbox.width()
                    for checkbox in harness.sex_filter_checkboxes.values()
                ),
            )
        finally:
            sidebar_widget.close()
            sidebar_widget.deleteLater()
            harness.deleteLater()
            self.qt.processEvents()

    def test_sex_predicate_is_shared_by_normal_and_heritage_only_rows(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn(
            "if not self._animal_matches_sex_filter(data):",
            source,
        )
        self.assertGreaterEqual(
            source.count("self._animal_matches_sex_filter(data)"), 2
        )

    def test_project_species_strip_releases_its_content_width_when_collapsed(self):
        class SidebarApp:
            backend = _Backend()
            animals = {
                "Arwen": {
                    "name": "Arwen",
                    "species": "Callitrix jacchus",
                    "project": "OTOF-",
                },
                "Frodo": {
                    "name": "Frodo",
                    "species": "Mus musculus",
                    "project": "Ringbearer",
                },
            }
            messages = _messages("en")
            _disabled_plugins = set()

            def _apply_project_filter(self, _project):
                pass

        host = QWidget()
        host_layout = QHBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(4)
        plugin = ProjectsTrackPlugin(SidebarApp())
        strip = plugin.create_sidebar_tabs(host)
        animal_list = QListWidget(host)
        host_layout.addWidget(strip)
        host_layout.addWidget(animal_list, 1)
        host.resize(300, 620)
        host.show()
        self.qt.processEvents()
        try:
            toggle_width = plugin._sidebar_toggle_btn.sizeHint().width()
            expanded_width = strip.width()
            expanded_list_width = animal_list.width()
            self.assertEqual(
                expanded_width,
                toggle_width
                + plugin._sp_content_w.width()
                + plugin._proj_content_w.width(),
            )

            plugin._sidebar_toggle_btn.setChecked(False)
            self.qt.processEvents()
            self.assertFalse(plugin._sp_content_w.isVisible())
            self.assertFalse(plugin._proj_content_w.isVisible())
            self.assertEqual(strip.width(), toggle_width)
            self.assertEqual(strip.minimumWidth(), toggle_width)
            self.assertEqual(strip.maximumWidth(), toggle_width)
            self.assertGreater(animal_list.width(), expanded_list_width + 60)

            plugin._sidebar_toggle_btn.setChecked(True)
            self.qt.processEvents()
            self.assertTrue(plugin._sp_content_w.isVisible())
            self.assertTrue(plugin._proj_content_w.isVisible())
            self.assertEqual(strip.width(), expanded_width)
        finally:
            host.close()
            host.deleteLater()
            self.qt.processEvents()

    def test_project_save_feedback_is_inline_and_non_blocking(self):
        source = (ROOT / "Plugins" / "Projects_Track" /
                  "project_track_tab.py").read_text(encoding="utf-8")
        self.assertIn('setObjectName("projectSaveStatus")', source)
        self.assertIn("status_label.setVisible(True)", source)
        self.assertNotIn("QMessageBox.information(\n            self,\n            \"\",\n            _m(self._messages, \"project.info.saved\"", source)

    def test_plot_pan_accepts_empty_left_button_and_keeps_middle_button(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn("event.button in (1, 2)", source)
        self.assertIn("def _plot_press_is_empty(event)", source)
        self.assertIn("_plot_press_is_empty(event)", source)
        self.assertIn("event.button == _pan_button", source)
        self.assertIn("artist.contains(event)[0]", source)

    def test_sex_filter_is_localized_and_refreshes_for_all_three_classes(self):
        harness = self._main_harness()
        widget = harness._create_animal_sex_filter_widget()
        try:
            expected_placeholders = {
                "en": "Animal name",
                "de": "Tiername",
                "it": "Nome animale",
                "ru": "\u0418\u043c\u044f \u0436\u0438\u0432\u043e\u0442\u043d\u043e\u0433\u043e",
            }
            for language in ("en", "de", "it", "ru"):
                messages = _messages(language)
                self.assertEqual(
                    messages["sidebar.animal_name_filter.placeholder"],
                    expected_placeholders[language],
                )
                harness.messages = messages
                harness._update_animal_sex_filter_texts()
                expected_group = messages["sidebar.sex_filter.label"].rstrip(":")
                self.assertEqual(widget.accessibleName(), expected_group)
                self.assertEqual(widget.accessibleDescription(), expected_group)
                for token, suffix in (("F", "female"), ("M", "male"), ("U", "unknown")):
                    expected = messages[f"sidebar.sex_filter.{suffix}.tooltip"]
                    checkbox = harness.sex_filter_checkboxes[token]
                    self.assertEqual(checkbox.toolTip(), expected)
                    self.assertEqual(checkbox.accessibleName(), expected)

            start = harness.refresh_calls
            harness.sex_filter_checkboxes["F"].setChecked(False)
            self.qt.processEvents()
            self.assertEqual(harness.refresh_calls, start + 1)
            self.assertFalse(harness._animal_matches_sex_filter({"sex": "female"}))
            self.assertTrue(harness._animal_matches_sex_filter({"sex": "male"}))
            self.assertTrue(harness._animal_matches_sex_filter({"sex": None}))

            harness.sex_filter_checkboxes["U"].setChecked(False)
            self.assertFalse(harness._animal_matches_sex_filter({}))
        finally:
            widget.close()
            widget.deleteLater()
            harness.deleteLater()
            self.qt.processEvents()

    def test_project_actions_share_one_row_and_iacuc_fields_grow(self):
        tab = ProjectTrackTab(_ProjectApp(), _messages("en"), _History())
        tab.resize(1100, 700)
        tab.show()
        self.qt.processEvents()
        try:
            self.assertIs(tab._new_project_btn.parentWidget(), tab._project_action_row)
            self.assertIs(tab._refresh_project_btn.parentWidget(), tab._project_action_row)
            self.assertEqual(
                tab._new_project_btn.geometry().top(),
                tab._refresh_project_btn.geometry().top(),
            )
            self.assertGreater(tab._new_project_btn.width(), tab._refresh_project_btn.width())
            self.assertEqual(tab._refresh_project_btn.size().width(), 30)
            self.assertEqual(tab._refresh_project_btn.size().height(), 30)

            tab._project_data["projects"]["Demo"] = {"status": "active"}
            tab._build_detail("Demo")
            self.qt.processEvents()

            self.assertEqual(_PROJECT_IACUC_FIELD_MIN_WIDTH, 160)
            for form in tab._iacuc_column_forms:
                self.assertEqual(
                    form.fieldGrowthPolicy(),
                    QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow,
                )
            fields = list(tab._iacuc_fields.values()) + [
                tab._iacuc_pi,
                tab._iacuc_di,
                tab._iacuc_welfare,
            ]
            for field in fields:
                self.assertGreaterEqual(field.minimumWidth(), 160)
        finally:
            tab.close()
            tab.deleteLater()
            self.qt.processEvents()


if __name__ == "__main__":
    unittest.main()
