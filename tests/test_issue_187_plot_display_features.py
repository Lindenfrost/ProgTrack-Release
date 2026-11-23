"""Production-group checks for independent Plot display features (#187)."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
import copy
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QCheckBox, QMainWindow, QRadioButton
from PyQt6.QtCore import QCoreApplication, QEvent

ROOT = Path(__file__).resolve().parents[1]


class _Artist:
    def __init__(self):
        self.visible = True

    def set_visible(self, visible):
        self.visible = bool(visible)


class PlotDisplayFeaturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])
        import importlib.util
        spec = importlib.util.spec_from_file_location("progtrack_issue_187", ROOT / "ProgTrack.v.0.3.0.py")
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def _app(self):
        app = self.module.ProgTrackApp.__new__(self.module.ProgTrackApp)
        app.selected_animals = []
        app.animals = {}
        app.chk_weight = QCheckBox(); app.chk_weight.setChecked(True)
        app.chk_ref_weight = QCheckBox(); app.chk_ref_weight.setChecked(True)
        app.chk_events = QCheckBox(); app.chk_events.setChecked(True)
        app.chk_sperm_measurements = QCheckBox(); app.chk_sperm_measurements.setChecked(True)
        app.rb_weight_on = QRadioButton(); app.rb_weight_off = QRadioButton()
        app.rb_weight_on.setChecked(True)
        app.rb_blood_on = QRadioButton(); app.rb_blood_off = QRadioButton()
        app._is_steroid_track_active = lambda: False
        app.has_pdg_plugin = False
        app.current_canvas = None
        app.weight_lines = [_Artist()]
        app.weight_ref_bands = [_Artist()]
        app.ev_lines = [_Artist()]
        app.ev_texts = [_Artist()]
        app.sperm_artists = [_Artist(), _Artist()]
        app._get_current_events_checkbox = lambda: app.chk_events
        return app

    def test_weight_and_reference_visibility_are_independent(self):
        app = self._app()
        QMainWindow.__init__(app)
        app.chk_weight.setChecked(False)
        app._apply_plot_display_state()
        self.assertFalse(app.weight_lines[0].visible)
        self.assertTrue(app.weight_ref_bands[0].visible)
        app.chk_weight.setChecked(True); app.chk_ref_weight.setChecked(False)
        app._apply_plot_display_state()
        self.assertTrue(app.weight_lines[0].visible)
        self.assertFalse(app.weight_ref_bands[0].visible)

    def test_sperm_group_is_independent_from_events(self):
        app = self._app()
        app.chk_events.setChecked(False)
        app._apply_plot_display_state()
        self.assertFalse(app.ev_lines[0].visible)
        self.assertTrue(all(artist.visible for artist in app.sperm_artists))
        app.chk_sperm_measurements.setChecked(False)
        app._apply_plot_display_state()
        self.assertTrue(all(not artist.visible for artist in app.sperm_artists))

    def test_real_matplotlib_artist_groups_and_weight_style_controls(self):
        """The production state hook updates real render artists and radios."""
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure

        app = self._app()
        QMainWindow.__init__(app)
        figure = Figure()
        canvas = FigureCanvasAgg(figure)
        axis = figure.add_subplot(111)
        weight_line, = axis.plot([0, 1], [1, 2])
        reference_band = axis.fill_between([0, 1], [0, 0], [1, 1])
        event_line, = axis.plot([0, 1], [2, 3])
        event_text = axis.text(0.5, 2.5, "event")
        sperm_line, = axis.plot([0, 1], [3, 4])
        app.current_canvas = canvas
        app.weight_lines = [weight_line]
        app.weight_ref_bands = [reference_band]
        app.ev_lines = [event_line, sperm_line]
        app.ev_texts = [event_text]
        app.sperm_artists = [sperm_line]
        app.chk_weight.setChecked(False)
        app.chk_ref_weight.setChecked(True)
        app.chk_events.setChecked(False)
        app.chk_sperm_measurements.setChecked(True)
        app._apply_plot_display_state()
        self.assertFalse(weight_line.get_visible())
        self.assertTrue(reference_band.get_visible())
        self.assertFalse(event_line.get_visible())
        self.assertFalse(event_text.get_visible())
        self.assertTrue(sperm_line.get_visible())
        self.assertFalse(app.rb_weight_on.isEnabled())
        self.assertFalse(app.rb_weight_off.isEnabled())
        app.chk_weight.setChecked(True)
        app._apply_plot_display_state()
        self.assertTrue(weight_line.get_visible())
        self.assertTrue(app.rb_weight_on.isEnabled())
        self.assertTrue(app.rb_weight_off.isEnabled())

    def test_labels_exist_in_all_supported_catalogues(self):
        for language in ("en", "de", "it", "ru"):
            messages = json.loads((ROOT / "lang" / f"messages_{language}.json").read_text(encoding="utf-8"))
            self.assertTrue(messages.get("checkbox.reference_weight"))
            self.assertTrue(messages.get("checkbox.sperm_measurements"))

    def test_source_persists_semantic_display_keys(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn('display_chk_ref_weight', source)
        self.assertIn('display_chk_sperm_measurements', source)
        self.assertIn('self.sperm_artists', source)

    def test_declarative_role_matrix_and_saved_false_restore(self):
        features = self.module.ProgTrackApp._PLOT_DISPLAY_FEATURES
        self.assertIn("sperm_donor", features["sperm_measurements"]["roles"])
        self.assertEqual(features["weight_overlay"]["roles"], {
            "offspring", "experimental_offspring"
        })
        self.assertNotIn("sperm_donor", features["weight_overlay"]["roles"])
        app = self._app()
        QMainWindow.__init__(app)
        app.main_tabs = None
        app.category_tab = None
        app.messages = {}
        app._restore_session_ui({
            "display_chk_prog": False,
            "display_chk_weight": False,
            "display_chk_events": False,
            "display_chk_ref_weight": False,
            "display_chk_sperm_measurements": False,
        })
        self.assertFalse(app.chk_weight.isChecked())
        self.assertFalse(app.chk_ref_weight.isChecked())
        self.assertFalse(app.chk_sperm_measurements.isChecked())

    def test_canonical_role_applicability_matrix_has_no_label_aliases(self):
        app = self._app()
        app._uses_offspring_weight_evaluation = lambda role: role in {
            "offspring", "experimental_offspring"
        }
        self.assertIn("sperm_measurements", app._plot_features_for_role("sperm_donor"))
        self.assertNotIn("weight_overlay", app._plot_features_for_role("sperm_donor"))
        for role in ("offspring", "experimental_offspring"):
            features = app._plot_features_for_role(role)
            self.assertIn("weight_overlay", features)
            self.assertIn("events", features)
        unrelated = app._plot_features_for_role("partner_animal")
        self.assertNotIn("sperm_measurements", unrelated)
        self.assertNotIn("weight_overlay", unrelated)

    def test_production_availability_switches_with_mixed_roles_and_missing_reference(self):
        app = self._app()
        app.has_pdg_plugin = False
        app.selected_animals = ["offspring", "donor", "ordinary"]
        app.animals = {
            "offspring": {"rolle": "offspring", "gewicht": [{"wert": 4}]},
            "donor": {"rolle": "sperm_donor", "sperm": [{"datum": "2024-01-01"}]},
            "ordinary": {"rolle": "spender", "gewicht": []},
        }
        app._is_steroid_track_active = lambda: True
        app._animal_role_value = lambda record: str(record.get("rolle") or "unknown")
        app._uses_offspring_weight_evaluation = lambda role: role == "offspring"
        available = app._plot_feature_availability()
        self.assertTrue(available["has_reference_weight"])
        self.assertTrue(available["has_sperm_measurements"])
        self.assertTrue(available["has_events"])

        app.selected_animals = ["ordinary"]
        available = app._plot_feature_availability()
        self.assertFalse(available["has_reference_weight"])
        self.assertFalse(available["has_sperm_measurements"])
        self.assertFalse(available["has_events"])

        # A role with no weight/reference record must fail closed for the
        # overlay while leaving unrelated data gates independent.
        app.selected_animals = ["offspring"]
        app.animals["offspring"]["gewicht"] = []
        available = app._plot_feature_availability()
        self.assertFalse(available["has_weight"])
        self.assertFalse(available["has_reference_weight"])

    def test_display_state_hides_stale_groups_when_role_applicability_changes(self):
        app = self._app()
        app.selected_animals = ["donor"]
        app.animals = {"donor": {"rolle": "sperm_donor"}}
        app._animal_role_value = lambda record: str(record.get("rolle") or "unknown")
        app._uses_offspring_weight_evaluation = lambda role: role in {
            "offspring", "experimental_offspring"
        }
        app.chk_weight.setChecked(True)
        app.chk_ref_weight.setChecked(True)
        app.chk_events.setChecked(True)
        app.chk_sperm_measurements.setChecked(True)
        app._apply_plot_display_state()
        self.assertTrue(app.weight_lines[0].visible)
        self.assertFalse(app.weight_ref_bands[0].visible)
        self.assertFalse(app.ev_lines[0].visible)
        self.assertTrue(all(artist.visible for artist in app.sperm_artists))

        app.selected_animals = ["offspring"]
        app.animals["offspring"] = {"rolle": "offspring"}
        app._apply_plot_display_state()
        self.assertTrue(app.weight_lines[0].visible)
        self.assertTrue(app.weight_ref_bands[0].visible)
        self.assertTrue(app.ev_lines[0].visible)
        self.assertTrue(app.ev_texts[0].visible)
        self.assertTrue(all(not artist.visible for artist in app.sperm_artists))

    def test_role_valid_controls_are_visible_but_disabled_without_rows(self):
        """Missing data disables a valid feature without hiding its control."""
        class CategoryTab:
            def currentIndex(self):
                return 6

        app = self._app()
        QMainWindow.__init__(app)
        self.addCleanup(self._dispose_event_host, app)
        app.selected_animals = ["experimental_offspring"]
        app.animals = {
            "experimental_offspring": {
                "rolle": "experimental_offspring",
                "gewicht": [],
                "events": [],
            }
        }
        app.category_tab = CategoryTab()
        app._all_category_tab_index = lambda: 6
        app._animal_role_value = lambda record: str(record.get("rolle") or "unknown")
        app._uses_offspring_weight_evaluation = lambda role: role in {
            "offspring", "experimental_offspring"
        }
        app._is_steroid_track_active = lambda: True

        app._update_plot_display_control_visibility()

        self.assertTrue(app.chk_ref_weight.isVisible())
        self.assertFalse(app.chk_ref_weight.isEnabled())
        self.assertTrue(app.chk_events.isVisible())
        self.assertFalse(app.chk_events.isEnabled())
        self.assertFalse(app.chk_sperm_measurements.isVisible())

    def test_all_tab_uses_union_events_control_for_experimental_offspring(self):
        """Experimental offspring gets Events in the existing mixed All tab."""
        class CategoryTab:
            def currentIndex(self):
                return 6

        app = self._app()
        QMainWindow.__init__(app)
        self.addCleanup(self._dispose_event_host, app)
        app.selected_animals = ["experimental_offspring"]
        app.animals = {
            "experimental_offspring": {
                "rolle": "experimental_offspring",
                "events": [{"event_id": "test-all-experimental-offspring-measurement",
                            "event_type": "measurement", "datum": datetime(2026, 1, 10),
                            "recorded_role": "experimental_offspring"}],
            }
        }
        app.category_tab = CategoryTab()
        app._all_category_tab_index = lambda: 6
        app._animal_role_value = lambda record: str(record.get("rolle") or "unknown")
        app._uses_offspring_weight_evaluation = lambda role: role in {
            "offspring", "experimental_offspring"
        }
        app._is_steroid_track_active = lambda: True

        self.assertEqual(len(self.module.plot_event_entries(app.animals["experimental_offspring"])), 1)
        self.assertTrue(app._plot_feature_availability()["has_events"])
        app._update_plot_display_control_visibility()

        self.assertTrue(app.chk_events.isVisible())
        self.assertTrue(app.chk_events.isEnabled())
        self.assertTrue(app.chk_ref_weight.isVisible())
        self.assertFalse(app.chk_ref_weight.isEnabled())

    @staticmethod
    def _dispose_event_host(app):
        for name in ("chk_weight", "chk_ref_weight", "chk_events", "chk_sperm_measurements",
                     "rb_weight_on", "rb_weight_off", "rb_blood_on", "rb_blood_off"):
            getattr(app, name).deleteLater()
        app.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_all_tab_empty_and_undated_events_remain_disabled_without_mutation(self):
        for events in ([], [{"event_type": "measurement"}]):
            with self.subTest(events=events):
                app = self._app()
                QMainWindow.__init__(app)
                self.addCleanup(self._dispose_event_host, app)
                app.selected_animals = ["child"]
                app.animals = {"child": {"rolle": "experimental_offspring", "events": copy.deepcopy(events)}}
                before = copy.deepcopy(app.animals)
                app.category_tab = type("AllTab", (), {"currentIndex": lambda self: 6})()
                app._all_category_tab_index = lambda: 6
                app._animal_role_value = lambda record: record["rolle"]
                app._uses_offspring_weight_evaluation = lambda role: True
                self.assertEqual(self.module.plot_event_entries(app.animals["child"]), [])
                self.assertFalse(app._plot_feature_availability()["has_events"])
                app._update_plot_display_control_visibility()
                self.assertTrue(app.chk_events.isVisible())
                self.assertFalse(app.chk_events.isEnabled())
                self.assertTrue(app.chk_events.isChecked())
                self.assertEqual(app.animals, before)

    def test_non_applicable_switch_hides_progesterone_artists(self):
        """Role changes cannot leave old progesterone artists visible."""
        app = self._app()
        app.selected_animals = ["offspring"]
        app.animals = {"offspring": {"rolle": "offspring"}}
        app._animal_role_value = lambda record: str(record.get("rolle") or "unknown")
        app._uses_offspring_weight_evaluation = lambda role: role in {
            "offspring", "experimental_offspring"
        }
        prog = _Artist()
        pdg = _Artist()
        app.prog_lines = [prog]
        app.pdg_lines = [pdg]

        app._apply_plot_display_state()

        self.assertFalse(prog.visible)
        self.assertFalse(pdg.visible)

    def test_export_scope_and_real_xlsx_output_honor_visibility(self):
        from openpyxl import load_workbook

        app = self._app()
        QMainWindow.__init__(app)
        app.animals = {
            "Allowed": {"id": "A1", "project": "P1", "rolle": "spender"},
            "Denied": {"id": "D1", "project": "P2", "rolle": "spender"},
        }
        app.messages = {}
        app.report_locked_dates = set()
        app.report_edits = {}
        app._project_visibility_scope = lambda: (set(), {"P1"})
        app._is_steroid_track_active = lambda: False
        app._load_report_data = lambda _name: None
        app._display_name = lambda name: name
        app._report_index_for_animal = lambda _name, _data: ({}, "rev")
        app._generate_daily_data = lambda *_args, **_kwargs: "daily"
        self.assertEqual(
            app._permission_scope_export_ids(["Allowed", "Denied"]),
            ["Allowed"],
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "reports.xlsx"
            app._export_reports_xlsx(
                ["Allowed"], date(2026, 1, 1), date(2026, 1, 1), str(output)
            )
            workbook = load_workbook(output, read_only=True)
            self.assertEqual(workbook.sheetnames, ["Allowed"])
            self.assertEqual(workbook["Allowed"]["B2"].value, "A1")
            workbook.close()
            with self.assertRaises(PermissionError):
                app._export_reports_xlsx(
                    ["Allowed", "Denied"], date(2026, 1, 1), date(2026, 1, 1),
                    str(output),
                )


if __name__ == "__main__":
    unittest.main()
