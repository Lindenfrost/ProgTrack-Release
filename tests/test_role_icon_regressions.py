"""Focused regressions for partner projection and Role Builder SVG handling."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QMenu, QPushButton, QTableWidget

from Plugins.core.animal_relationships import relationship_status_icon
from Plugins.core.ui_icons import icon as ui_icon, resolve_icon_path


ROOT = Path(__file__).resolve().parents[1]


class PartnerRelationshipIconTest(unittest.TestCase):
    @staticmethod
    def records(first_sex: str, second_sex: str, *, first_role: str, second_role: str):
        return {
            "first": {
                "sex": first_sex,
                "rolle": first_role,
                "verpaart_mit": "second",
            },
            "second": {
                "sex": second_sex,
                "rolle": second_role,
                "verpaart_mit": "first",
            },
        }

    def test_same_sex_pair_uses_partner_icon_independent_of_roles(self):
        for sex in ("female", "male", "weiblich", "männlich"):
            with self.subTest(sex=sex):
                records = self.records(
                    sex,
                    sex,
                    first_role="breeding_animal",
                    second_role="experimental_animal",
                )
                self.assertEqual(
                    relationship_status_icon(records, "first", records["first"]),
                    "status.partner",
                )
                self.assertEqual(
                    relationship_status_icon(records, "second", records["second"]),
                    "status.partner",
                )

    def test_opposite_sex_breeding_pair_keeps_breeding_icon(self):
        records = self.records(
            "female",
            "male",
            first_role="breeding_animal",
            second_role="experimental_animal",
        )
        self.assertEqual(
            relationship_status_icon(records, "first", records["first"]),
            "role.breeding",
        )

    def test_dedicated_partner_role_still_uses_partner_icon(self):
        records = self.records(
            "female",
            "male",
            first_role="partner_animal",
            second_role="breeding_animal",
        )
        self.assertEqual(
            relationship_status_icon(records, "first", records["first"]),
            "status.partner",
        )


class RoleSvgResolutionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location(
            "progtrack_role_icon_regression", ROOT / "ProgTrack.v.0.3.0.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def make_harness(self):
        module = self.module

        class Harness(module.StyleSettingsDialog):
            def __init__(self):
                QDialog.__init__(self)
                self.messages = {
                    "settings.role_setup.representation.svg": "SVG",
                    "settings.role_setup.representation.text": "Text",
                }
                self._role_setup_editable = True
                self.role_table = QTableWidget(1, 9, self)

        return Harness()

    def test_semantic_prefixed_bare_and_legacy_role_icons_all_resolve(self):
        paths = {
            resolve_icon_path("role.partner"),
            resolve_icon_path("svg:role_partner.svg"),
            resolve_icon_path("role_partner.svg"),
            resolve_icon_path("🐾"),
        }
        self.assertEqual(len(paths), 1)
        path = paths.pop()
        self.assertIsNotNone(path)
        self.assertTrue(path.is_file())
        for value in ("role.partner", "svg:role_partner.svg", "role_partner.svg", "🐾"):
            with self.subTest(value=value):
                self.assertFalse(ui_icon(value).isNull())

    def test_role_builder_shows_configured_svg_and_picker_click_does_not_crash(self):
        harness = self.make_harness()
        role = {
            "icon": "role_partner.svg",
            "representation_mode": "icon",
            "representation_text": "",
        }
        cell = harness._make_role_representation_cell(role)
        harness.role_table.setCellWidget(0, 2, cell)
        button = cell.findChild(QPushButton, "roleIconPickerButton")
        self.assertIsNotNone(button)
        self.assertFalse(button.icon().isNull())
        with patch.object(QMenu, "exec", return_value=None):
            button.click()
        self.assertEqual(button.property("roleIconValue"), "role_partner.svg")
        harness.close()


if __name__ == "__main__":
    unittest.main()
