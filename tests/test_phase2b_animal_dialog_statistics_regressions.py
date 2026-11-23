
import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from PyQt6.QtWidgets import QApplication, QDialog, QFormLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget, QComboBox
from Plugins.core.backend import ProgTrackBackend
from tests.test_current_backend_runtime import runtime_paths

_SPEC = importlib.util.spec_from_file_location("progtrack_main", ROOT / "ProgTrack.v.0.3.0.py")
_MAIN = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MAIN)


class AnimalDialogStatisticsRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def make_app(self):
        app = _MAIN.ProgTrackApp.__new__(_MAIN.ProgTrackApp)
        _MAIN.QMainWindow.__init__(app)
        self.addCleanup(app.deleteLater)
        app.messages = {
            "stats.surgery": "Surgery",
            "stats.measurement": "Measurement",
            "stats.sperm": "Sperm",
        }
        app._is_steroid_track_active = lambda: False
        app._animal_role_value = lambda record, default=None: record.get("rolle", default or "")
        app._custom_event_definitions = lambda include_retired=True: []
        app._normalize_report_event_type = lambda value: str(value or "").strip().casefold()
        return app

    def test_experimental_offspring_and_sperm_statistics_are_not_steroid_gated(self):
        app = self.make_app()
        experimental = {
            "rolle": "experimental_offspring",
            "events": [
                {"event_type": "surgery", "datum": datetime.now()},
                {"event_type": "measurement", "datum": datetime.now()},
            ],
            "max_op": 3,
            "max_measurements": 4,
        }
        sperm = {
            "rolle": "sperm_donor",
            "sperm": [{"datum": datetime.now()}],
            "max_spermaproben": 2,
        }
        self.assertEqual(app._get_event_statistics(experimental), "Surgery: 1/3, Measurement: 1/4")
        self.assertEqual(app._get_event_statistics(sperm), "Sperm: 1/2")

    def test_male_breeding_animal_has_no_pregnancy_or_birth_statistics(self):
        app = self.make_app()
        app._get_report_event_max = _MAIN.ProgTrackApp._get_report_event_max.__get__(app)
        male = {
            "rolle": "breeding_animal",
            "sex": "Male",
            "events": [
                {"event_type": "pregnancy", "datum": datetime.now()},
                {"event_type": "birth", "datum": datetime.now()},
            ],
            "max_pregnancies": 4,
            "max_geburten": 2,
        }
        self.assertEqual(app._get_event_statistics(male), "-")
        self.assertIsNone(app._get_report_event_max("pregnancy", male))
        self.assertIsNone(app._get_report_event_max("birth", male))

    def test_custom_limits_are_read_from_backend_map_and_stable_event_ids(self):
        app = self.make_app()
        app._role_dialog_blocks = lambda *_a, **_k: ['experimental-block:biopsy']
        app._custom_event_definitions = lambda include_retired=True: [{
            "id": "custom-event:biopsy",
            "stable_id": "custom-event-biopsy-v1",
            "event_type": "biopsy",
            "block_id": "experimental-block:biopsy",
            "kind": "limiting",
            "name": "Biopsy",
        }]
        animal = {
            "rolle": "experimental_offspring",
            # The event record uses the canonical event_type.  The definition's
            # stable_id identifies the definition revision; it is not the
            # event type stored on an animal event.
            "events": [{"event_type": "biopsy", "datum": datetime.now()}],
            "experimental_limits": {"experimental-block:biopsy": 3},
        }
        self.assertIn("Biopsy: 1/3", app._get_event_statistics(animal))
        self.assertIn("Biopsy: 1/3", app._get_event_statistics_localized(animal, {"stats.surgery": "OP"}))

    def test_custom_event_dialog_initializes_scroll_before_existing_rows(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        start = source.index("active_custom_events = self._active_custom_event_definitions_for_role(")
        block = source[start:source.index("save_btn = QPushButton", start)]
        self.assertIn("custom_event_scroll = None", block)
        self.assertLess(
            block.index("custom_event_scroll = None"),
            block.index("for event in existing_custom_role_events"),
        )

    def test_form_section_adopts_controls_as_embedded_widgets(self):
        app = self.make_app()
        dlg = QDialog()
        body = QWidget(dlg)
        form = QFormLayout(body)
        spin = _MAIN.QSpinBox()
        spin.setValue(4)
        form.addRow("Limit", spin)
        section = app._wrap_form_rows_in_section(form, "Experimental limits", [spin])
        self.assertIsNotNone(section)
        self.assertFalse(section.isWindow())
        self.assertIs(section.parentWidget(), body)
        self.assertFalse(spin.isWindow())
        self.assertIsNotNone(spin.parentWidget())
        self.assertTrue(section.content_widget().isAncestorOf(spin))
        dlg.close()

    def test_nested_sex_genotype_row_can_anchor_form_reordering(self):
        """Rows containing the shared sex/genotype wrapper remain movable."""
        app = self.make_app()
        app._genotype_options_for_species = lambda _species: ["WT/WT"]
        host = QDialog()
        body = QWidget(host)
        form = QFormLayout(body)
        sex = QComboBox()
        sex.addItem("Male", "Male")
        species = QComboBox()
        species.addItem("Callithrix jacchus", "Callithrix jacchus")
        genotype = app._add_sex_genotype_row(form, sex, species, "WT/WT")
        address = QWidget()
        form.addRow(address)
        app._move_form_row_after(form, address, genotype)
        self.assertEqual(form.getWidgetPosition(genotype.property("_form_row_widget"))[0], 0)
        self.assertEqual(form.getWidgetPosition(address)[0], 1)
        host.close()

    def test_experimental_dialog_moves_address_before_wrapping_limits(self):
        """Experimental limits must follow identity and Address in the form."""
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(
            encoding="utf-8"
        )
        start = source.index("    def _dlg_versuchstier(")
        end = source.find("\n    def ", start + 8)
        block = source[start:end if end >= 0 else len(source)]
        move = block.index("self._move_form_row_after(form, _cage_addr_group, genotype_le)")
        wrap = block.index("vt_limits_section = self._wrap_form_rows_in_section(")
        self.assertLess(move, wrap)

    def test_animal_dialogs_store_canonical_role_for_section_preferences(self):
        """Section preferences must not be keyed by localized window titles."""
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(
            encoding="utf-8"
        )
        expected = {
            "_dlg_partner": 'Role.PARTNER.value',
            "_dlg_samenspender": 'Role.SAMENSP.value',
            "_dlg_offspring": 'Role.OFFSPRING.value',
            "_dlg_zuchttier": 'Role.ZUCHTTIER.value',
            "_dlg_versuchstier": 'role_value',
            "_dlg_basic_animal_role": 'role_value',
            "_dlg_female_animal": 'role_now',
        }
        for function, role_expression in expected.items():
            with self.subTest(function=function):
                start = source.index(f"    def {function}(")
                end = source.find("\n    def ", start + 8)
                block = source[start:end if end >= 0 else len(source)]
                self.assertIn(
                    f'role={role_expression}',
                    block,
                )
                self.assertIn("editing=", block)
        self.assertIn('"animal_dialog_role", role_cb.currentData()', source)
        preferences_start = source.index("    def _animal_section_preferences(")
        preferences_end = source.find("\n    def ", preferences_start + 8)
        preferences = source[
            preferences_start:preferences_end if preferences_end >= 0 else len(source)
        ]
        self.assertIn('dlg.property("animal_dialog_mode")', preferences)
        self.assertNotIn("windowTitle", preferences)

    def test_animal_section_mode_is_independent_of_localized_title(self):
        class Records:
            def __init__(self):
                self.write_calls = []

            def get(self, *_args, default=None):
                return default

            def put(self, *args, **kwargs):
                self.write_calls.append((args, kwargs))
                raise AssertionError(
                    "dialog context setup must not mutate Core or preference data"
                )

        class Backend:
            def __init__(self):
                self.records = Records()
                self.load_calls = 0

            def load_core_data(self):
                self.load_calls += 1
                # Match the supported read-only Core parentage contract.  An
                # explicit empty projection is valid: the dialog context only
                # needs an authoritative snapshot token, not a parent row.
                return {"animals": {}, "archived_animals": {}}

        app = self.make_app()
        app.master_track = type("Master", (), {"current_username": "Researcher"})()
        app.backend = Backend()
        dialog = QDialog()
        dialog.setWindowTitle("Versuchstier bearbeiten")
        app._set_animal_dialog_context(
            dialog, role="experimental", editing=True
        )
        _payload, prefix = app._animal_section_preferences(dialog)
        self.assertEqual(prefix, "edit:experimental")
        self.assertGreaterEqual(app.backend.load_calls, 1)
        self.assertEqual(app.backend.records.write_calls, [])

    def test_offspring_weight_workflow_is_gated_for_both_builtin_roles(self):
        """Role Setup's dedicated block controls both offspring workflows."""
        app = self.make_app()
        enabled = {"offspring": True, "experimental_offspring": True}
        app._role_block_enabled = lambda role, block, mode="edit": (
            block == "offspring_weight_evaluation" and enabled.get(role, False)
        )
        for mode in ("new", "edit"):
            self.assertTrue(app._uses_offspring_weight_evaluation("offspring", mode))
            self.assertTrue(app._uses_offspring_weight_evaluation("experimental_offspring", mode))
        enabled["offspring"] = False
        self.assertFalse(app._uses_offspring_weight_evaluation("offspring", "new"))
        enabled["experimental_offspring"] = False
        self.assertFalse(app._uses_offspring_weight_evaluation("experimental_offspring", "edit"))
        self.assertFalse(app._uses_offspring_weight_evaluation("experimental"))

    def test_offspring_dialogs_have_no_standalone_infant_weight_path(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertNotIn("infant_weight", source.casefold())
        self.assertNotIn("Infant weight", source)
        self.assertIn('"offspring_weight_evaluation"', source)

    def test_builtin_offspring_recipes_default_to_the_new_block(self):
        from Plugins.core.animal_roles import default_dialog_blocks

        for role in ("offspring", "experimental_offspring"):
            with self.subTest(role=role):
                recipe = default_dialog_blocks(role)
                self.assertIn("offspring_weight_evaluation", recipe["new"])
                self.assertIn("offspring_weight_evaluation", recipe["edit"])

    def test_parentage_ui_is_core_owned_and_available_without_heritage(self):
        """Core parent controls are available independently of HeritageTrack."""
        app = self.make_app()
        app.has_heritage_plugin = False
        app.heritage_plugin = None
        self.assertTrue(app._heritage_parentage_ui_available())
        app.has_heritage_plugin = True
        app.heritage_plugin = object()
        self.assertTrue(app._heritage_parentage_ui_available())

    def test_identity_allocator_generates_ids_for_donor_surrogate_and_custom_paths(self):
        """All role dialogs share the backend-backed identity reservation boundary."""
        app = self.make_app()
        temporary = tempfile.TemporaryDirectory(prefix="progtrack-id-reservation-")
        self.addCleanup(temporary.cleanup)
        app.backend = ProgTrackBackend(
            runtime_paths(Path(temporary.name)),
            acquire_process_lock=False,
            choose_demo_seed=lambda: False,
        )
        self.addCleanup(app.backend.close)
        session = app.backend.authenticated_sessions.create_first_account(
            "id-reservation-admin", "Id-reservation-test!"
        )
        app.master_track = type("MasterTrack", (), {"session_context": session})()
        app.backend.core_configuration.setup(session)
        app._core_configuration_missing = False
        app._core_configuration_generation = 0
        app._publish_core_configuration(app.backend.core_configuration.snapshot(), redraw=False)
        app.animals = {}
        app.archived = {
            "Archived": {"id": "cj_26_0001_F_Archived"},
        }
        roles = (
            "sperm_donor", "surrogate", "egg_cell_donor", "partner",
            "breeding", "offspring", "experimental_offspring",
            "experimental", "custom",
        )
        for index, role in enumerate(roles, 1):
            for species, expected_prefix in (
                ("Callithrix jacchus", "cj_26_"),
                ("Mus musculus", "mm_26_"),
            ):
                line = QLineEdit()
                generated, metadata = app._id_for_animal_dialog(
                    line,
                    name=f"Role {role}",
                    species=species,
                    birth_date="01.01.2026",
                    sex=("Female", "Male", "Unknown")[(index - 1) % 3],
                    origin="DPZ",
                    editing=False,
                )
                self.assertTrue(generated, (role, species))
                self.assertIn(expected_prefix, generated, (role, species))
                self.assertIsNotNone(metadata, (role, species))

        self.assertNotEqual(generated, "cj_26_0001_F_Archived")

    def test_every_dedicated_new_animal_save_path_uses_shared_identity_allocator(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        for function in (
            "_dlg_partner", "_dlg_samenspender", "_dlg_offspring",
            "_dlg_zuchttier", "_dlg_versuchstier", "_dlg_female_animal",
            "_dlg_basic_animal_role",
        ):
            with self.subTest(function=function):
                start = source.index(f"    def {function}(")
                next_def = source.find("\n    def ", start + 8)
                block = source[start: next_def if next_def >= 0 else len(source)]
                self.assertIn("_id_for_animal_dialog(", block)

    def test_history_viewport_is_fixed_to_content_and_reacts_to_rows(self):
        app = self.make_app()
        host = QDialog()
        frame = QWidget()
        layout = QVBoxLayout(frame)
        layout.addWidget(QLabel("Date"))
        add = QPushButton("New")
        layout.addWidget(add)
        rows = []
        for index in range(7):
            row = QLineEdit(str(index))
            layout.insertWidget(layout.indexOf(add), row)
            rows.append(row)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(frame)
        app._cap_history_viewport(scroll, frame, len(rows))
        host.setLayout(QVBoxLayout())
        host.layout().addWidget(scroll)
        host.show()
        self.qt.processEvents()
        tall = scroll.height()
        self.assertGreater(tall, 0)
        self.assertEqual(scroll.minimumHeight(), scroll.maximumHeight())
        self.assertIsNotNone(scroll.property("_progtrack_viewport_target"))
        for row in rows[5:]:
            row.setParent(None)
        app._cap_history_viewport(scroll, frame, 5)
        self.qt.processEvents()
        # Five rows are now intentionally shown in full.  The previous test
        # expected a smaller height because the old cap accidentally counted
        # the header as one of the visible rows.
        self.assertGreaterEqual(scroll.height(), tall)
        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)
        host.close()

    def test_history_viewport_has_real_range_and_recalculates_after_row_changes(self):
        app = self.make_app()
        host = QDialog()
        frame = QWidget()
        layout = QVBoxLayout(frame)
        header = QVBoxLayout()
        header.addWidget(QLabel("Date"))
        layout.addLayout(header)
        rows = []
        for index in range(7):
            row = QVBoxLayout()
            row.addWidget(QLineEdit(str(index)))
            layout.addLayout(row)
            rows.append(row)
        add = QPushButton("New")
        layout.addWidget(add)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(frame)
        app._cap_history_viewport(scroll, frame, len(rows))
        host.setLayout(QVBoxLayout())
        host.layout().addWidget(scroll)
        host.show()
        self.qt.processEvents()
        self.qt.processEvents()

        bar = scroll.verticalScrollBar()
        self.assertEqual(scroll.verticalScrollBarPolicy().name, "ScrollBarAsNeeded")
        self.assertGreater(bar.maximum(), 0)
        self.assertGreater(frame.height(), scroll.viewport().height())
        bar.setValue(bar.maximum())
        old_value = bar.value()
        bar.triggerAction(bar.SliderAction.SliderSingleStepSub)
        self.qt.processEvents()
        self.assertLess(bar.value(), old_value)

        for row in rows[-2:]:
            while row.count():
                item = row.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            layout.removeItem(row)
        app._cap_history_viewport(scroll, frame, 5)
        self.qt.processEvents()
        self.qt.processEvents()
        self.assertEqual(scroll.verticalScrollBar().maximum(), 0)

        row = QVBoxLayout()
        row.addWidget(QLineEdit("new"))
        layout.insertLayout(layout.indexOf(add), row)
        app._cap_history_viewport(scroll, frame, 6)
        self.qt.processEvents()
        self.qt.processEvents()
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        host.close()

    def test_plot_stats_are_added_before_selection_stretch(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        stats = source.index("Summary statistics must be inserted immediately below the canvas.")
        stretch = source.index("if count in (1, 2):", stats)
        self.assertLess(stats, stretch)
        self.assertNotIn("if steroid_active:\n            # Filter out heritage-only animals", source[stats:stretch])



    def test_report_and_unified_prog_use_repaired_statistics_and_cap(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        pdg = (ROOT / "Plugins" / "PdG_converter" / "plugin.py").read_text(encoding="utf-8")
        self.assertIn("localized_stats = self._get_event_statistics_localized", source)
        self.assertIn("_cap_table_viewport(conv_table", pdg)

if __name__ == "__main__":
    unittest.main()
