"""Focused Phase-2B Role Builder configuration contracts."""

from __future__ import annotations

import copy
import importlib.util
import os
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QRadioButton, QTableWidget, QTableWidgetItem

from Plugins.Cage__Track.cage_track_widget import CageTrackWidget
from Plugins.Projects_Track.project_track_tab import _project_role_color
from Plugins.core.animal_roles import (
    AnimalRoleRegistry,
    DEFAULT_ROLE_DEFINITIONS,
    REPRESENTATION_ICON,
    REPRESENTATION_TEXT,
    RoleOrderValidationError,
    parse_role_order,
    role_color_for_record,
)
from Plugins.core.ui_icons import canonical_icon_value, resolve_icon_path

ROOT = Path(__file__).resolve().parents[1]


class MemoryRecords:
    def __init__(self):
        self.values = {}
        self.put_count = 0

    def get(self, namespace, key, default=None):
        return copy.deepcopy(self.values.get((namespace, key), default))

    def put(self, namespace, key, value, **_kwargs):
        self.values[(namespace, key)] = copy.deepcopy(value)
        self.put_count += 1
        return self.put_count


class RoleRegistryContractTest(unittest.TestCase):
    def setUp(self):
        self.records = MemoryRecords()
        self.backend = SimpleNamespace(records=self.records)
        self.registry = AnimalRoleRegistry(self.backend, initial_payload={"roles": []})

    def test_explicit_integer_order_accepts_9990_and_rejects_lossy_values(self):
        self.assertEqual(parse_role_order("9990"), 9990)
        self.assertEqual(parse_role_order(-20), -20)
        for malformed in ("", "10.0", "ten", None, True):
            with self.subTest(malformed=malformed):
                with self.assertRaises(RoleOrderValidationError):
                    parse_role_order(malformed)

    def test_malformed_and_duplicate_order_never_reach_backend(self):
        roles = self.registry.roles()
        baseline_puts = self.records.put_count
        roles[0]["order"] = "not-an-integer"
        with self.assertRaises(RoleOrderValidationError):
            self.registry.save_roles(roles)
        self.assertEqual(self.records.put_count, baseline_puts)

        roles = self.registry.roles()
        roles[1]["order"] = roles[0]["order"]
        with self.assertRaises(RoleOrderValidationError):
            self.registry.save_roles(roles)
        self.assertEqual(self.records.put_count, baseline_puts)

    def test_manager_configuration_persists_and_reopens_exclusive_representation_and_colors(self):
        custom = self.registry.make_custom_role("Facility cohort", "role.partner")
        custom.update({
            "representation_mode": REPRESENTATION_TEXT,
            "representation_text": "Cohort",
            "icon": "",
            "colors": {
                "mode": "sex",
                "male": "#234F9A",
                "female": "#A13F72",
                "unknown": "#6B6B6B",
            },
        })
        self.registry.save_roles(self.registry.roles() + [custom])

        reopened = AnimalRoleRegistry(self.backend)
        saved = reopened.get_by_value(custom["value"])
        self.assertEqual(saved["representation_mode"], REPRESENTATION_TEXT)
        self.assertEqual(saved["representation_text"], "Cohort")
        self.assertEqual(saved["icon"], "")
        self.assertEqual(saved["colors"]["female"], "#A13F72")
        self.assertEqual(
            reopened.representation_for_value(custom["value"]),
            {"mode": REPRESENTATION_TEXT, "text": "Cohort", "icon": ""},
        )

        saved.update({
            "representation_mode": REPRESENTATION_ICON,
            "representation_text": "",
            "icon": "role.partner",
        })
        roles = [
            saved if role["value"] == saved["value"] else role
            for role in reopened.roles()
        ]
        reopened.save_roles(roles)
        icon_reopen = AnimalRoleRegistry(self.backend).get_by_value(custom["value"])
        self.assertEqual(icon_reopen["representation_mode"], REPRESENTATION_ICON)
        self.assertEqual(icon_reopen["icon"], "role.partner")
        self.assertEqual(icon_reopen["representation_text"], "")

    def test_cross_surface_role_color_projection_is_identical(self):
        custom = self.registry.make_custom_role("Color contract", "role.partner")
        custom["colors"] = {
            "mode": "sex",
            "male": "#234F9A",
            "female": "#A13F72",
            "unknown": "#6B6B6B",
        }
        self.registry.save_roles(self.registry.roles() + [custom])
        record = {"rolle": custom["value"], "sex": "female"}
        app = SimpleNamespace(animal_role_registry=self.registry)
        expected = role_color_for_record(record, self.registry)
        self.assertEqual(_project_role_color(record, app), expected)
        cage = SimpleNamespace(plugin=SimpleNamespace(app=app))
        self.assertEqual(
            CageTrackWidget._get_animal_role_color(cage, "animal", {"animal": record}),
            expected,
        )

    def test_reviewed_default_role_artwork_is_seeded_with_canonical_svg_ids(self):
        expected = {
            "egg_cell_donor": "role.female",
            "surrogate": "role.female",
            "sperm_donor": "role.male",
            "offspring": "role.offspring",
            "partner_animal": "role.partner",
            "breeding_animal": "role.breeding",
            "experimental_animal": "role.experimental",
        }
        defaults = {role["value"]: role for role in DEFAULT_ROLE_DEFINITIONS}
        for role_value, semantic_id in expected.items():
            self.assertEqual(defaults[role_value]["icon"], semantic_id)
            self.assertIsNotNone(resolve_icon_path(semantic_id))
        self.assertEqual(canonical_icon_value("role.fem"), "role.female")
        self.assertEqual(defaults["unknown"]["representation_text"], "●")

        seed = ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb"
        with zipfile.ZipFile(seed) as package:
            rows = [
                json.loads(line)
                for line in package.read("records/domain.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        payload = next(
            row["payload"] for row in rows
            if row.get("namespace") == "configuration"
            and row.get("record_id") == "animal-roles"
        )
        seeded = {role["value"]: role for role in payload["roles"]}
        self.assertEqual(
            {value: seeded[value]["icon"] for value in expected},
            expected,
        )
        self.assertEqual(seeded["unknown"]["representation_mode"], "text")
        self.assertEqual(seeded["unknown"]["representation_text"], "●")


class RoleBuilderOffscreenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location(
            "progtrack_role_builder_test", ROOT / "ProgTrack.v.0.2.3.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def make_harness(self, editable: bool):
        module = self.module

        class Harness(module.StyleSettingsDialog):
            def __init__(self):
                QDialog.__init__(self)
                self.messages = json.loads((ROOT / 'lang/messages_en.json').read_text(encoding='utf-8'))
                self.messages.update({
                    "settings.role_setup.representation.svg": "SVG",
                    "settings.role_setup.representation.text": "Text",
                    "settings.role_setup.color.default": "Role",
                    "settings.role_setup.color.male": "Male",
                    "settings.role_setup.color.female": "Female",
                    "settings.role_setup.color.unknown": "Unknown",
                })
                self._role_setup_editable = editable
                self.role_table = QTableWidget(0, 9, self)

        harness = Harness()

        def dispose():
            # This deliberately partial dialog must not outlive the test and
            # receive later application font/style changes as a full dialog.
            from PyQt6.QtCore import QEvent
            harness.close()
            harness.deleteLater()
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents()

        self.addCleanup(dispose)
        return harness

    @staticmethod
    def role_payload():
        return {
            "role_id": "cohort",
            "value": "custom.cohort",
            "label": "Facility cohort",
            "label_key": "role.custom.cohort",
            "icon": "role.partner",
            "representation_mode": REPRESENTATION_ICON,
            "representation_text": "",
            "colors": {
                "mode": "sex",
                "male": "#234F9A",
                "female": "#A13F72",
                "unknown": "#6B6B6B",
            },
            "order": 9990,
            "active": True,
            "built_in": False,
            "base_editor": "basic",
            "field_preset": "basic",
            "dialog_blocks": {"new": [], "edit": []},
        }

    def test_non_manager_controls_show_values_but_are_disabled(self):
        harness = self.make_harness(False)
        role = self.role_payload()
        representation = harness._make_role_representation_cell(role)
        color_cell = harness._make_role_color_cell(role)
        self.assertTrue(
            representation.findChild(QRadioButton, "roleRepresentationIconRadio").isChecked()
        )
        self.assertFalse(
            representation.findChild(QPushButton, "roleIconPickerButton").isEnabled()
        )
        self.assertFalse(
            representation.findChild(QRadioButton, "roleRepresentationTextRadio").isEnabled()
        )
        self.assertFalse(
            color_cell.findChild(QPushButton, "roleColorButton_male").isEnabled()
        )
        self.assertFalse(
            color_cell.findChild(QPushButton, "roleColorResetButton").isEnabled()
        )
        harness.close()

    def test_manager_text_choice_and_invalid_order_are_recoverable_without_edit_loss(self):
        harness = self.make_harness(True)
        role = self.role_payload()
        harness.role_table.setRowCount(1)
        active = QTableWidgetItem("")
        active.setCheckState(Qt.CheckState.Checked)
        harness.role_table.setItem(0, 0, active)
        harness.role_table.setItem(0, 1, QTableWidgetItem("bad order"))
        icon_item = QTableWidgetItem(role["icon"])
        harness.role_table.setItem(0, 2, icon_item)
        harness.role_table.setItem(0, 3, QTableWidgetItem(""))
        harness.role_table.setItem(0, 4, QTableWidgetItem(role["label"]))
        source = QTableWidgetItem(role["value"])
        source.setData(Qt.ItemDataRole.UserRole, dict(role))
        harness.role_table.setItem(0, 5, source)
        harness.role_table.setItem(0, 6, QTableWidgetItem("basic"))
        harness.role_table.setItem(0, 7, QTableWidgetItem(""))
        harness.role_table.setItem(0, 8, QTableWidgetItem(""))
        representation = harness._make_role_representation_cell(role)
        harness.role_table.setCellWidget(0, 2, representation)
        harness.role_table.setCellWidget(0, 3, harness._make_role_color_cell(role))
        harness._role_blocks_from_table_cell = lambda *_args: []
        harness._role_custom_preset_name_from_table_cell = lambda *_args: ""

        text_radio = representation.findChild(QRadioButton, "roleRepresentationTextRadio")
        text_edit = representation.findChild(QLineEdit, "roleRepresentationTextEdit")
        text_radio.setChecked(True)
        text_edit.setText("Facility text")
        with self.assertRaisesRegex(ValueError, "whole integer"):
            harness._role_rows_from_table(validate=True)
        self.assertEqual(harness.role_table.item(0, 1).text(), "bad order")
        self.assertEqual(text_edit.text(), "Facility text")

        harness.role_table.item(0, 1).setText("9990")
        saved = harness._role_rows_from_table(validate=True)[0]
        self.assertEqual(saved["order"], 9990)
        self.assertEqual(saved["representation_mode"], REPRESENTATION_TEXT)
        self.assertEqual(saved["representation_text"], "Facility text")
        self.assertEqual(saved["icon"], "")
        harness.close()

    def test_active_role_checkbox_is_centered_and_persists_through_save(self):
        harness = self.make_harness(True)
        role = self.role_payload()
        harness.parent_app = harness
        harness._normalize_role_dialog_blocks = lambda value: []
        harness._custom_role_block_presets = []
        harness._add_role_table_row(role)
        active_item = harness.role_table.item(0, 0)
        self.assertIsNotNone(active_item)
        self.assertEqual(
            active_item.textAlignment() & Qt.AlignmentFlag.AlignHorizontal_Mask,
            Qt.AlignmentFlag.AlignHCenter,
        )
        self.assertEqual(active_item.checkState(), Qt.CheckState.Checked)
        active_item.setCheckState(Qt.CheckState.Unchecked)
        harness._role_blocks_from_table_cell = lambda *_args: []
        harness._role_custom_preset_name_from_table_cell = lambda *_args: ""
        saved = harness._role_rows_from_table(validate=False)[0]
        self.assertFalse(saved["active"])
        harness.close()


    def test_custom_experimental_block_editor_uses_selector_color_picker_and_conditional_symbol(self):
        harness = self.make_harness(True)
        captured = {}
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        evidence = Path(temporary.name)  # Never overwrite historical evidence on a rerun.

        def fake_color(*_args, **_kwargs):
            return QColor("#ABCDEF")

        def fake_exec(dialog):
            captured["dialog"] = dialog
            event_combo = dialog.findChild(QComboBox, "customExperimentalBlockEventTypeCombo")
            render_combo = dialog.findChild(QComboBox, "customExperimentalBlockRenderCombo")
            marker = dialog.findChild(QComboBox, "customExperimentalBlockSymbolCombo")
            marker_label = dialog.findChild(QLabel, "customExperimentalBlockSymbolLabel")
            color_button = dialog.findChild(QPushButton, "customExperimentalBlockColorButton")
            mirror = dialog.findChild(QCheckBox, 'customExperimentalBlockMirrorMedical')
            self.assertIsNotNone(mirror)
            self.assertFalse(mirror.isChecked())
            self.assertIsNotNone(event_combo)
            self.assertIsNotNone(render_combo)
            self.assertIsNotNone(marker)
            self.assertIsNotNone(marker_label)
            self.assertIsNotNone(color_button)
            label_texts = {label.text() for label in dialog.findChildren(QLabel)}
            self.assertIn("Event type", label_texts)
            self.assertIn("Color", label_texts)
            self.assertNotIn("Color (#RRGGBB):", label_texts)
            self.assertNotIn("Maximum field:", label_texts)
            # Name is the only editable text field.  Event type and maximum
            # field are no longer free-text controls in this editor.
            self.assertEqual(len(dialog.findChildren(QLineEdit)), 1)
            self.assertIsNone(dialog.findChild(QLineEdit, "customExperimentalBlockEventType"))
            self.assertIsNone(dialog.findChild(QLineEdit, "customExperimentalBlockMaximum"))
            self.assertEqual(event_combo.currentData(), "limiting")

            dialog.show()
            render_combo.setCurrentIndex(render_combo.findData("line"))
            QApplication.processEvents()
            self.assertTrue(marker.isHidden())
            self.assertTrue(marker_label.isHidden())
            dialog.grab().save(str(evidence / "line_mode.png"))

            render_combo.setCurrentIndex(render_combo.findData("symbol"))
            QApplication.processEvents()
            self.assertFalse(marker.isHidden())
            self.assertFalse(marker_label.isHidden())
            dialog.grab().save(str(evidence / "symbol_mode_before_color.png"))
            color_button.click()
            self.assertEqual(color_button.property("color"), "#ABCDEF")
            dialog.grab().save(str(evidence / "symbol_mode_after_color.png"))
            dialog.accept()
            return QDialog.DialogCode.Accepted

        original_exec = self.module.QDialog.exec
        original_get_color = self.module.QColorDialog.getColor
        self.module.QDialog.exec = fake_exec
        self.module.QColorDialog.getColor = staticmethod(fake_color)
        try:
            result = harness._custom_experimental_block_editor(
                harness,
                {
                    "id": "custom_limit:biopsy",
                    "name": "Biopsy",
                    "kind": "limiting",
                    "event_type": "biopsy",
                    "max_field": "max_biopsy",
                    "render_mode": "line",
                    "marker": "D",
                    "color": "#123456",
                },
            )
        finally:
            self.module.QDialog.exec = original_exec
            self.module.QColorDialog.getColor = original_get_color
            harness.close()

        self.assertEqual(result["event_type"], "biopsy")
        self.assertEqual(result["kind"], "limiting")
        self.assertEqual(result["max_field"], "max_biopsy")
        self.assertEqual(result["render_mode"], "symbol")
        self.assertEqual(result["marker"], "D")
        self.assertEqual(result["color"], "#ABCDEF")
        self.assertFalse(result['mirror_in_medical_history'])


if __name__ == "__main__":
    unittest.main()
