"""Regression contracts for capability-aware Unified Prog role blocks."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QTabWidget

from Plugins.PdG_converter.plugin import PdGCapability
from Plugins.core.animal_roles import default_dialog_blocks

ROOT = Path(__file__).resolve().parents[1]


class _ParentStub:
    def __init__(self, steroid_active: bool):
        self._steroid_active = bool(steroid_active)

    def _is_steroid_track_active(self):
        return self._steroid_active

    @staticmethod
    def _normalize_role_dialog_blocks(blocks):
        return list(blocks or [])


class UnifiedProgDependencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location(
            "progtrack_unified_prog_dependency_test",
            ROOT / "ProgTrack.v.0.3.0.py",
        )
        cls.main_module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.main_module)

    def _style_harness(self, steroid_active: bool):
        module = self.main_module

        class Harness(module.StyleSettingsDialog):
            def __init__(self):
                QDialog.__init__(self)
                self.parent_app = _ParentStub(steroid_active)
                self.messages = {
                    "settings.role_setup.block.computed_values.name":
                    "Unified Progesterone (calculated)",
                    "settings.role_setup.block.computed_values.description":
                    "Shows Unified Progesterone: blood-equivalent values calculated from urine PdG.",
                }
                self._custom_experimental_blocks = []

        return Harness()

    def test_standard_recipes_and_seed_require_computed_values_only_for_pdg_roles(self):
        for preset in ("egg_cell_donor", "surrogate"):
            with self.subTest(preset=preset):
                self.assertIn("computed_values", default_dialog_blocks(preset)["new"])
                self.assertIn("computed_values", default_dialog_blocks(preset)["edit"])
        for preset in ("partner", "sperm_donor", "offspring", "breeding", "experimental"):
            with self.subTest(preset=preset):
                self.assertNotIn("computed_values", default_dialog_blocks(preset)["new"])
                self.assertNotIn("computed_values", default_dialog_blocks(preset)["edit"])

        with zipfile.ZipFile(ROOT / "Resources/Seed/progtrack_seed.ptdb") as package:
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
        self.assertIn("computed_values", seeded["egg_cell_donor"]["dialog_blocks"]["edit"])
        self.assertIn("computed_values", seeded["surrogate"]["dialog_blocks"]["edit"])
        self.assertNotIn("computed_values", seeded["partner_animal"]["dialog_blocks"]["edit"])

    def test_role_setup_hides_unavailable_block_but_preserves_existing_selection(self):
        active = self._style_harness(True)
        inactive = self._style_harness(False)
        self.assertIn("computed_values", active._role_setup_available_block_ids())
        self.assertNotIn("computed_values", inactive._role_setup_available_block_ids())
        self.assertEqual(
            active._role_block_label("computed_values"),
            "Unified Progesterone (calculated)",
        )
        self.assertIn("urine PdG", active._role_block_description("computed_values"))
        active.close()
        inactive.close()

    def test_pdg_capability_can_toggle_unified_tab_without_rebuilding_pdg_tab(self):
        messages = {
            "pdg_converter.tab.pdg": "PdG",
            "pdg_converter.tab.unified_prog": "Unified Prog.",
        }

        class FakePlugin:
            def __init__(self):
                self.app = SimpleNamespace(messages=messages, animals={"A": {"pdg": []}})
                self.converter = SimpleNamespace(predict=lambda values, _params: values)

            @staticmethod
            def get_parameters(_name):
                return None

        capability = PdGCapability(FakePlugin())
        tabs = QTabWidget()
        pdg_tab, conv_tab = capability.extend_animal_dialog(
            tabs,
            {"name": "A", "pdg": []},
            "A",
            None,
            add_unified_prog=False,
            retain_unified_prog=True,
        )
        self.assertIsNotNone(pdg_tab)
        self.assertIsNotNone(conv_tab)
        self.assertEqual(tabs.count(), 1)
        capability.set_unified_prog_visible(tabs, conv_tab, True)
        self.assertEqual(tabs.count(), 2)
        self.assertEqual(tabs.tabText(1), "Unified Prog.")
        capability.set_unified_prog_visible(tabs, conv_tab, False)
        self.assertEqual(tabs.count(), 1)
        tabs.deleteLater()


if __name__ == "__main__":
    unittest.main()
