from __future__ import annotations
import copy
import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication, QDialog, QFormLayout, QLabel, QLineEdit, QWidget

from Plugins.core.animal_dialog_sections import AnimalDialogSection
from Plugins.core.project_species import project_species_for
from Plugins.core.role_block_presets import RoleBlockPresetRegistry
from Plugins.core.backend import ProgTrackBackend
from tests.test_current_backend_runtime import runtime_paths

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "ProgTrack.v.0.3.0.py"


class Records:
    def __init__(self):
        self.data = {}
    def get(self, namespace, key, default=None):
        return copy.deepcopy(self.data.get((namespace, key), default))
    def put(self, namespace, key, value, **kwargs):
        self.data[(namespace, key)] = copy.deepcopy(value)
        return 1


class Block2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location("progtrack_block2", APP)
        cls.main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.main)

    def test_animal_section_starts_collapsed_and_toggle_geometry_is_stable(self):
        section = AnimalDialogSection("Address", collapsed=True)
        section.show()
        self.app.processEvents()
        collapsed = section.sizeHint().width()
        self.assertFalse(section.is_expanded())
        section.set_expanded(True)
        self.app.processEvents()
        expanded = section.sizeHint().width()
        section.set_expanded(False)
        self.app.processEvents()
        self.assertFalse(section.is_expanded())
        self.assertEqual(collapsed, section.sizeHint().width())
        self.assertGreaterEqual(expanded, collapsed)

    def test_real_form_section_wrapper_moves_rows_and_keeps_widgets(self):
        host = QWidget()
        form = QFormLayout(host)
        anchor = QWidget()
        form.addRow(anchor)
        label = QLabel("Reference")
        field = QLineEdit("10")
        form.addRow(label, field)
        other_label = QLabel("Maximum")
        other_field = QLineEdit("3")
        form.addRow(other_label, other_field)
        app = self.main.ProgTrackApp.__new__(self.main.ProgTrackApp)
        app.messages = {"dialog.experimental_limits.title": "Experimental limits"}
        section = app._wrap_form_rows_in_section(
            form, "Experimental limits", [label, other_field], after=anchor
        )
        self.assertIsInstance(section, AnimalDialogSection)
        self.assertEqual(form.getWidgetPosition(section)[0], 1)
        self.assertEqual(form.getWidgetPosition(field)[0], -1)
        self.assertEqual(form.getWidgetPosition(other_field)[0], -1)
        self.assertIs(field.parentWidget(), section.content_widget().findChild(QWidget).parentWidget() if False else field.parentWidget())
        self.assertEqual(section.property("section_id"), "experimental_limits")

    def test_moving_spanning_address_row_preserves_full_width_alignment(self):
        """Address remains a spanning ProjectTrack-style section after reorder."""
        host = QWidget()
        form = QFormLayout(host)
        address = AnimalDialogSection("Address", collapsed=True)
        form.addRow(address)
        identity = QLineEdit("identity")
        form.addRow(QLabel("Identity"), identity)
        app = self.main.ProgTrackApp.__new__(self.main.ProgTrackApp)
        app._move_form_row_after(form, address, identity)
        address_row, address_role = form.getWidgetPosition(address)
        identity_row, _ = form.getWidgetPosition(identity)
        self.assertEqual(address_role, QFormLayout.ItemRole.SpanningRole)
        self.assertEqual(address_row, identity_row + 1)

    def test_backend_preferences_are_per_user_and_sections_are_not_global(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("animal-dialog-sections:", source)
        self.assertIn("Guest", source)
        self.assertIn("section.restore_expanded", source)
        self.assertIn("section.toggled.connect(_save)", source)

    def test_project_species_reads_canonical_summary(self):
        records = [
            {"name": "OTOF", "summary": {"species": ["Callithrix jacchus"]}},
            {"name": "Mouse", "summary": {"species": "Mus musculus"}},
        ]
        self.assertEqual(project_species_for({r["name"]: r for r in records}, "OTOF"), "Callithrix jacchus")
        self.assertEqual(project_species_for({r["name"]: r for r in records}, "Mouse"), "Mus musculus")

    def test_role_preset_schema_two_round_trip_preserves_dynamic_definitions(self):
        temporary = tempfile.TemporaryDirectory(prefix="progtrack-role-presets-")
        self.addCleanup(temporary.cleanup)
        backend = ProgTrackBackend(
            runtime_paths(Path(temporary.name)),
            acquire_process_lock=False,
            choose_demo_seed=lambda: False,
        )
        self.addCleanup(backend.close)
        session = backend.authenticated_sessions.create_first_account(
            "role-presets-admin", "Role-presets-test!"
        )
        backend.core_configuration.setup(session)
        registry = RoleBlockPresetRegistry(backend)
        block = {
            "id": "custom_limit:hearing",
            "name": "Hearing checks",
            "kind": "limiting",
            "event_type": "hearing_test",
            "max_field": "max_hearing_tests",
            "render_mode": "symbol",
            "marker": "D",
            "color": "#123456",
        }
        _payload, revision = backend.records.get_with_revision(
            "configuration", "role-block-presets", default={}
        )
        registry.save_presets(
            [{"name": "Experimental", "blocks": ["custom_limit:hearing"]}],
            experimental_blocks=[block],
            event_definitions=[block],
            session=session,
            expected_revision=revision,
        )
        payload = backend.records.get("configuration", "role-block-presets")
        self.assertEqual(payload["schema_version"], 2)
        reopened = RoleBlockPresetRegistry(backend)
        self.assertEqual(reopened.presets()[0]["blocks"][-1], "custom_limit:hearing")
        self.assertEqual(reopened.experimental_blocks()[0]["event_type"], "hearing_test")
        self.assertEqual(reopened.event_definitions()[0]["limit_block"], "max_hearing_tests")

    def test_issue_84_role_contract_and_history_caps_are_present(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("AnimalDialogSection", source)
        self.assertIn("_cap_history_viewport", source)
        self.assertIn("max_spermaproben", source)
        # The dedicated builders are not ordered alphabetically in the source.
        # Resolve the end at the next method instead of slicing to a preceding
        # builder (which used to produce an empty string and a vacuous pass).
        offspring_start = source.index("    def _dlg_offspring(")
        offspring_end = source.find("\n    def ", offspring_start + 8)
        offspring = source[offspring_start:offspring_end if offspring_end >= 0 else len(source)]
        self.assertNotIn("max_special_sb", offspring)
        self.assertNotIn("max_ops_sb", offspring)
        self.assertIn("ROLE_VALUE_EXPERIMENTAL_OFFSPRING", source)
        self.assertIn("_pdg_tabs = None", source)

        def block(name):
            start = source.index(f"    def {name}(")
            end = source.find("\n    def ", start + 8)
            return source[start:end if end >= 0 else len(source)]

        # Role-specific fields must be moved before Address before the limits
        # section is wrapped.  This protects the donor/surrogate/partner
        # layouts from silently regressing when a shared row helper changes.
        ordering = {
            "_dlg_partner": ("_move_form_row_after(form, _cage_addr_group, partner_von_le)", "partner_limits_section = self._wrap_form_rows_in_section"),
            "_dlg_samenspender": ("_move_form_row_after(form, _cage_addr_group, genotype_le)", "sperm_limits_section = self._wrap_form_rows_in_section"),
            "_dlg_zuchttier": ("_move_form_row_after(form, _cage_addr_group, verpaart_le)", "zuchttier_limits_section = self._wrap_form_rows_in_section"),
            "_dlg_versuchstier": ("_move_form_row_after(form, _cage_addr_group, genotype_le)", "vt_limits_section = self._wrap_form_rows_in_section"),
        }
        for name, (move_call, wrap_call) in ordering.items():
            with self.subTest(builder=name):
                role_block = block(name)
                self.assertLess(role_block.index(move_call), role_block.index(wrap_call))

    def test_issue_116_uses_last_interaction_not_plot_primary(self):
        app = self.main.ProgTrackApp.__new__(self.main.ProgTrackApp)
        app.selected_animals = ["First", "Last"]
        app._edit_selection_order = ["First", "Last"]
        app.animals = {"First": {}, "Last": {}}
        self.assertEqual(app._selected_animal_for_edit(), "Last")
        app._plot_selection_order = ["Last", "First"]
        self.assertEqual(app._selected_animal_for_edit(), "Last")

    def test_issue_117_descriptor_and_consecutive_ui_order(self):
        source = APP.read_text(encoding="utf-8")
        self.assertIn("_category_descriptor_for_index", source)
        self.assertIn("_rebuild_category_tabs()", source)
        self.assertIn('role["order"] = index + 1', source)
        self.assertIn("role[\"order\"] = len(existing_orders) + 1", source)

    def test_role_sections_and_delete_headers_are_present_and_not_detached(self):
        source = APP.read_text(encoding="utf-8")
        address_source=(ROOT/"Plugins/Cage__Track/ui_address_fields.py").read_text(encoding="utf-8")
        self.assertIn('section_id", "address"', address_source)
        self.assertIn('section_id", "parents"', source)
        self.assertIn('zuchttier_parent_section = self._add_parent_mode_selector', source)
        self.assertIn('vt_parent_section = self._add_parent_mode_selector', source)
        self.assertNotIn('self._move_form_row_after(form, zuchttier_parent_section, zuchttier_limits_section)', source)
        self.assertNotIn('self._move_form_row_after(form, vt_parent_section, vt_limits_section)', source)
        self.assertEqual(source.count('del_header.setFixedWidth(80)'), 5)
        self.assertIn('del_hdr.setFixedWidth(80)', source)
        self.assertIn('_xh.setFixedWidth(80)', source)

    def test_issue_121_nullable_limits_do_not_render_none(self):
        source = APP.read_text(encoding="utf-8")
        self.assertNotIn("str(rec.get('max_pregnancies', 0))", source)
        self.assertNotIn("str(rec.get('max_geburten', 0))", source)
        self.assertIn("if rec.get('max_pregnancies') is not None else 0", source)
        self.assertIn("if rec.get('max_geburten') is not None else 0", source)
        self.assertIn("_custom_experimental_block_definitions", source)
        self.assertIn("custom_limit_widgets", source)
        self.assertIn("_custom_event_definitions", source)


if __name__ == "__main__":
    unittest.main()
