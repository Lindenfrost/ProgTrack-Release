"""Focused Core-owned parentage checks for issue #156.

These tests intentionally exercise only the new Core command boundary and its
source-level ownership contract; they do not run the complete application.
"""

from __future__ import annotations

import copy
import importlib.util
import os
import unittest
from pathlib import Path
from types import MethodType
from PyQt6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("progtrack_main_issue_156", ROOT / "ProgTrack.v.0.3.0.py")
MAIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MAIN)


class _Backend:
    def __init__(self, snapshot):
        self.snapshot = copy.deepcopy(snapshot)

    def load_core_data(self):
        return copy.deepcopy(self.snapshot)


class _CoreHarness:
    def __init__(self):
        self.animals = {
            "Child": {
                "name": "Child", "species": "Callithrix jacchus", "sex": "female",
                "birth_date": "01.01.2020", "eizellspenderin": "", "samenspender": "",
                "ziehmutter": "", "ziehvater": "",
            },
            "Mother": {
                "name": "Mother", "species": "Callithrix jacchus", "sex": "female",
                "birth_date": "01.01.2010",
            },
            "Father": {
                "name": "Father", "species": "Callithrix jacchus", "sex": "male",
                "birth_date": "01.01.2010",
            },
            "MouseMother": {
                "name": "MouseMother", "species": "Mus musculus", "sex": "female",
                "birth_date": "01.01.2010",
            },
            "Unknown": {
                "name": "Unknown", "species": "Callithrix jacchus", "sex": "Unknown",
                "birth_date": "01.01.2010",
            },
        }
        self.archived = {}
        self.backend = _Backend({"animals": self.animals, "archived_animals": self.archived})
        self.messages = {}
        self.authorization = None

    def _master_can(self, action):
        return action in {"core.edit_animal_core", "core.edit_animal_housing"}


def _bind_core_helpers(harness):
    for name in (
        "_load_core_parentage_records",
        "_core_parent_candidate_visible",
        "_core_parentage_records",
        "_core_parentage_revision",
        "_apply_core_parentage",
    ):
        setattr(harness, name, MethodType(getattr(MAIN.ProgTrackApp, name), harness))
    # Static helper functions are assigned directly so they are not rebound.
    harness._core_parentage_values = MAIN.ProgTrackApp._core_parentage_values
    harness._core_parentage_record_values = MAIN.ProgTrackApp._core_parentage_record_values


class CoreParentageBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.app = _CoreHarness()
        _bind_core_helpers(self.app)
        self.app._std_widen = lambda _widget: None

    def test_valid_parentage_updates_only_unpublished_core_record(self):
        draft = copy.deepcopy(self.app.animals["Child"])
        revision = self.app._core_parentage_revision()
        self.app._apply_core_parentage(
            "Child", draft,
            {"egg_donor": "Mother", "sperm_donor": "Father"},
            expected_revision=revision,
        )
        self.assertEqual(draft["eizellspenderin"], "Mother")
        self.assertEqual(draft["samenspender"], "Father")
        self.assertEqual(self.app.animals["Child"]["eizellspenderin"], "")

    def test_species_sex_cycle_and_stale_revision_are_rejected(self):
        draft = copy.deepcopy(self.app.animals["Child"])
        with self.assertRaises(ValueError):
            self.app._apply_core_parentage("Child", draft, {"egg_donor": "MouseMother"})
        with self.assertRaises(ValueError):
            self.app._apply_core_parentage("Child", draft, {"egg_donor": "Unknown"})
        self.app.animals["Mother"]["eizellspenderin"] = "Child"
        self.app.backend.snapshot["animals"]["Mother"]["eizellspenderin"] = "Child"
        with self.assertRaises(ValueError):
            self.app._apply_core_parentage("Child", draft, {"egg_donor": "Mother", "sperm_donor": "Father"})
        with self.assertRaises(ValueError):
            self.app._apply_core_parentage("Child", draft, {"egg_donor": "Mother"}, expected_revision="stale")

    def test_core_dialog_contract_is_independent_from_heritage_mutation(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn("def _apply_core_parentage(", source)
        self.assertIn('dlg.setProperty("core_parentage_revision"', source)
        self.assertNotIn("heritage_plugin.set_parentage", source)
        self.assertNotIn("_ensure_parent_placeholders", source)

    def test_core_parent_group_builds_without_heritage_plugin(self):
        group, fields = MAIN.ProgTrackApp._core_parentage_group(
            self.app, "Child", self.app.animals["Child"]
        )
        self.assertEqual(set(fields), {"egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father"})
        self.assertEqual(fields["egg_donor"].count(), 2)  # empty + Mother
        self.assertEqual(group.title(), "Parents")

    def test_all_role_parent_catalogues_include_inactive_core_candidates(self):
        """Archived/deceased Core parents remain selectable historical identities."""
        self.app.archived["ArchivedMother"] = {
            "name": "ArchivedMother", "species": "Callithrix jacchus",
            "sex": "female", "birth_date": "01.01.2005", "archived": True,
        }
        self.app.animals["DeceasedMother"] = {
            "name": "DeceasedMother", "species": "Callithrix jacchus",
            "sex": "female", "birth_date": "01.01.2005", "death_date": "01.01.2024",
        }
        self.app.backend.snapshot = {
            "animals": self.app.animals,
            "archived_animals": self.app.archived,
        }
        _bind_core_helpers(self.app)
        _group, fields = MAIN.ProgTrackApp._core_parentage_group(
            self.app, "Child", self.app.animals["Child"]
        )
        candidates = {
            fields["egg_donor"].itemData(index)
            for index in range(fields["egg_donor"].count())
            if fields["egg_donor"].itemData(index)
        }
        self.assertIn("ArchivedMother", candidates)
        self.assertIn("DeceasedMother", candidates)
        labels = {
            fields["egg_donor"].itemData(index): fields["egg_donor"].itemText(index)
            for index in range(fields["egg_donor"].count())
            if fields["egg_donor"].itemData(index)
        }
        # Archive/lifecycle state is not evidence of death.  Only a recorded
        # death date receives the language-neutral deceased marker.
        self.assertNotIn(MAIN.DECEASED_STATUS_SYMBOL, labels["ArchivedMother"])
        self.assertIn(MAIN.DECEASED_STATUS_SYMBOL, labels["DeceasedMother"])

    def test_parentage_revision_tracks_identity_visibility_and_lifecycle_changes(self):
        baseline = self.app._core_parentage_revision()
        self.app.backend.snapshot["animals"]["Mother"]["project"] = "Other study"
        self.assertNotEqual(baseline, self.app._core_parentage_revision())

        baseline = self.app._core_parentage_revision()
        self.app.backend.snapshot["animals"]["Mother"]["organization_unit_id"] = "unit-b"
        self.assertNotEqual(baseline, self.app._core_parentage_revision())

        baseline = self.app._core_parentage_revision()
        self.app.backend.snapshot["animals"]["Mother"]["death_date"] = "01.01.2024"
        self.assertNotEqual(baseline, self.app._core_parentage_revision())

    def test_required_parentage_snapshot_rejects_malformed_sections(self):
        class MalformedBackend(_Backend):
            def load_core_data(self):
                return {"animals": ["not a mapping"]}

        self.app.backend = MalformedBackend({})
        with self.assertRaises(ValueError):
            self.app._core_parentage_revision()

    def test_parent_candidates_apply_project_and_unit_visibility_before_disclosure(self):
        self.app.animals["HiddenMother"] = {
            "name": "HiddenMother", "species": "Callithrix jacchus",
            "sex": "female", "birth_date": "01.01.2005",
            "project": "Hidden", "organization_unit_id": "unit-b",
        }
        self.app.backend.snapshot["animals"] = copy.deepcopy(self.app.animals)
        self.app._animal_visible_to_current_user = lambda record: record.get("project") != "Hidden"
        self.app._record_in_unit_scope = lambda record: record.get("organization_unit_id", "unit-a") == "unit-a"
        _group, fields = MAIN.ProgTrackApp._core_parentage_group(
            self.app, "Child", self.app.animals["Child"]
        )
        ids = {
            fields["egg_donor"].itemData(index)
            for index in range(fields["egg_donor"].count())
            if fields["egg_donor"].itemData(index)
        }
        self.assertIn("Mother", ids)
        self.assertNotIn("HiddenMother", ids)
        draft = copy.deepcopy(self.app.animals["Child"])
        with self.assertRaises(ValueError):
            self.app._apply_core_parentage(
                "Child", draft, {"egg_donor": "HiddenMother"}
            )

    def test_unresolved_editable_parent_text_is_not_silently_cleared(self):
        """Free text reaches validation instead of becoming an empty parent."""
        combo = MAIN.AnimalRelationshipCombo()
        combo.setEditable(True)
        combo.addItem("", "")
        combo.setEditText("Unknown Mother")
        values = self.app._core_parentage_values({"egg_donor": combo})
        self.assertEqual(values["egg_donor"], "Unknown Mother")
        with self.assertRaises(ValueError):
            self.app._apply_core_parentage("Child", copy.deepcopy(self.app.animals["Child"]), values)

    def test_editing_prefilled_combo_forwards_visible_text_not_stale_item_data(self):
        """Changing a selected parent cannot be hidden by QComboBox itemData."""
        combo = MAIN.AnimalRelationshipCombo()
        combo.setEditable(True)
        combo.addItem("", "")
        combo.addItem("Mother (mother-ipid)", "mother-ipid")
        combo.setCurrentIndex(1)
        combo.setEditText("Unknown Mother")

        values = self.app._core_parentage_values({"egg_donor": combo})

        self.assertEqual(values["egg_donor"], "Unknown Mother")
        with self.assertRaises(ValueError):
            self.app._apply_core_parentage(
                "Child",
                copy.deepcopy(self.app.animals["Child"]),
                values,
            )

    def test_parentage_revision_fails_closed_when_backend_snapshot_is_unavailable(self):
        class BrokenBackend(_Backend):
            def load_core_data(self):
                raise OSError("backend unavailable")

        self.app.backend = BrokenBackend({})
        with self.assertRaises(ValueError):
            self.app._core_parentage_revision()
        self.assertEqual(self.app._core_parentage_records(), {})


if __name__ == "__main__":
    unittest.main()
