"""Focused regression tests for the new-animal parent species filter."""

from __future__ import annotations

import copy
import importlib.util
import os
import unittest
from pathlib import Path
from types import MethodType

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QComboBox


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "progtrack_main_issue_168", ROOT / "ProgTrack.v.0.3.0.py"
)
MAIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MAIN)


class _Backend:
    def __init__(self, snapshot):
        self.snapshot = copy.deepcopy(snapshot)

    def load_core_data(self):
        return copy.deepcopy(self.snapshot)


class _UnitAuthorization:
    trusted_local = False

    def __init__(self, visible_unit):
        self.visible_unit = visible_unit

    def can_read_unit(self, owner_unit_id):
        return str(owner_unit_id).casefold() == self.visible_unit.casefold()


class _Harness:
    def __init__(self):
        self.animals = {
            "CalliMother": {
                "name": "CalliMother", "species": "Callithrix jacchus",
                "sex": "female", "birth_date": "01.01.2010",
            },
            "CalliFather": {
                "name": "CalliFather", "species": "Callithrix jacchus",
                "sex": "male", "birth_date": "01.01.2010",
            },
            "MouseMother": {
                "name": "MouseMother", "species": "Mus musculus",
                "sex": "female", "birth_date": "01.01.2010",
            },
            "MouseFather": {
                "name": "MouseFather", "species": "Mus musculus",
                "sex": "male", "birth_date": "01.01.2010",
            },
        }
        self.archived = {}
        self.backend = _Backend({
            "animals": self.animals,
            "archived_animals": self.archived,
        })
        self.messages = {}

    @staticmethod
    def _normalize_species_value(value):
        text = str(value or "").strip()
        return "" if text.casefold() in {"", "none", "null"} else text

    @staticmethod
    def _load_species_options():
        return ["Callithrix jacchus", "Mus musculus"]

    @staticmethod
    def _std_widen(_widget):
        return None

    @staticmethod
    def _master_can(_action):
        return True


def _bind(app):
    for name in (
        "_load_core_parentage_records",
        "_record_in_unit_scope",
        "_core_parent_candidate_visible",
        "_core_parentage_records",
        "_core_parentage_revision",
        "_core_parentage_group",
        "_apply_core_parentage",
    ):
        setattr(app, name, MethodType(getattr(MAIN.ProgTrackApp, name), app))
    app._core_parentage_values = MAIN.ProgTrackApp._core_parentage_values


class NewAnimalParentSpeciesFilterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.app = _Harness()
        _bind(self.app)
        self.species = QComboBox()
        self.species.addItem("(Please select)", "")
        self.species.addItem("Callithrix jacchus", "Callithrix jacchus")
        self.species.addItem("Mus musculus", "Mus musculus")

    @staticmethod
    def _ids(combo):
        return {
            combo.itemData(index)
            for index in range(combo.count())
            if combo.itemData(index)
        }

    def test_new_parent_lists_are_empty_until_species_is_registered(self):
        group, fields = self.app._core_parentage_group(
            None,
            {},
            species_widget=self.species,
            strict_species=True,
            lock_committed=True,
        )
        self.assertEqual(fields["egg_donor"].count(), 1)
        self.assertTrue(group.findChildren(type(group.layout().itemAt(0).widget())))
        self.assertIn("registered species", group.toolTip())

        self.species.setCurrentIndex(1)
        self.qt_app.processEvents()
        self.assertEqual(self._ids(fields["egg_donor"]), {"CalliMother"})
        self.assertEqual(self._ids(fields["sperm_donor"]), {"CalliFather"})

    def test_species_change_refreshes_and_clears_old_parent_selection(self):
        _group, fields = self.app._core_parentage_group(
            None,
            {},
            species_widget=self.species,
            strict_species=True,
            lock_committed=True,
        )
        self.species.setCurrentIndex(1)
        self.qt_app.processEvents()
        mother = fields["egg_donor"]
        mother.setCurrentIndex(mother.findData("CalliMother"))
        self.assertTrue(mother.lineEdit().isReadOnly())
        self.species.setCurrentIndex(2)
        self.qt_app.processEvents()
        self.assertEqual(mother.currentData(), "")
        self.assertFalse(mother.lineEdit().isReadOnly())
        self.assertEqual(self._ids(mother), {"MouseMother"})

    def test_unregistered_species_stays_empty(self):
        self.species.addItem("Unregistered species", "Unregistered species")
        self.species.setCurrentIndex(3)
        _group, fields = self.app._core_parentage_group(
            None,
            {},
            species_widget=self.species,
            strict_species=True,
            lock_committed=True,
        )
        self.assertEqual(fields["egg_donor"].count(), 1)
        self.assertTrue(fields["egg_donor"].toolTip())

    def test_parent_candidates_apply_organizational_unit_visibility(self):
        self.app.authorization = _UnitAuthorization("unit-a")
        self.app.backend.snapshot["animals"]["CalliMother"]["organization_unit_id"] = "unit-a"
        self.app.backend.snapshot["animals"]["CalliFather"]["organization_unit_id"] = "unit-b"
        self.app.backend.snapshot["animals"]["MouseMother"]["organization_unit_id"] = "unit-a"
        visible = self.app._core_parentage_records()
        self.assertIn("CalliMother", visible)
        self.assertNotIn("CalliFather", visible)
        self.assertIn("MouseMother", visible)

    def test_backend_rejects_missing_species_before_parent_resolution(self):
        draft = {"species": "", "birth_date": "01.01.2020"}
        with self.assertRaises(ValueError):
            self.app._apply_core_parentage(
                "Child", draft, {"egg_donor": "CalliMother"}
            )


if __name__ == "__main__":
    unittest.main()
