"""Regression coverage for edit-dialog measurement saves (#210)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "progtrack_issue_210", ROOT / "ProgTrack.v.0.3.0.py"
)
MAIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MAIN)

from Plugins.Master_Track.permissions import (  # noqa: E402
    DEFAULT_JOB_BUNDLES,
    JOB_ANIMAL_WELFARE,
    JOB_VET,
    resolve_effective_permissions,
)


class _ValueWidget:
    def __init__(self, value: str):
        self._value = value

    def currentData(self):
        return self._value

    def currentText(self):
        return self._value


class _ParentageHarness:
    _core_parentage_values = staticmethod(MAIN.ProgTrackApp._core_parentage_values)
    _core_parentage_record_values = staticmethod(
        MAIN.ProgTrackApp._core_parentage_record_values
    )
    _apply_core_parentage_if_changed = MAIN.ProgTrackApp._apply_core_parentage_if_changed

    def __init__(self):
        self.calls = []

    def _apply_core_parentage(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class EditDialogMeasurementPermissionsTest(TestCase):
    def test_unchanged_parentage_is_not_revalidated_for_measurement_only_users(self):
        app = _ParentageHarness()
        fields = {
            "egg_donor": _ValueWidget("Mother"),
            "sperm_donor": _ValueWidget("Father"),
            "surrogate_mother": _ValueWidget("Surrogate"),
            "surrogate_father": _ValueWidget(""),
        }
        original = {
            "eizellspenderin": "Mother",
            "samenspender": "Father",
            "ziehmutter": "Surrogate",
            "ziehvater": "",
        }

        changed = app._apply_core_parentage_if_changed(
            "Drogo",
            dict(original),
            original,
            fields,
            expected_revision="dialog-revision",
        )

        self.assertFalse(changed)
        self.assertEqual(app.calls, [])

    def test_changed_parentage_still_delegates_to_protected_command(self):
        app = _ParentageHarness()
        fields = {
            "egg_donor": _ValueWidget("NewMother"),
            "sperm_donor": _ValueWidget("Father"),
        }
        original = {
            "eizellspenderin": "Mother",
            "samenspender": "Father",
        }
        draft = dict(original)

        changed = app._apply_core_parentage_if_changed(
            "Drogo",
            draft,
            original,
            fields,
            expected_revision="dialog-revision",
        )

        self.assertTrue(changed)
        self.assertEqual(len(app.calls), 1)
        args, kwargs = app.calls[0]
        self.assertEqual(args[0], "Drogo")
        self.assertEqual(args[2]["egg_donor"], "NewMother")
        self.assertEqual(kwargs["expected_revision"], "dialog-revision")

    def test_job_matrix_keeps_measurement_and_parentage_permissions_distinct(self):
        expected = {
            "vet": (True, False),
            "researcher": (True, False),
            "keeper": (True, True),
            "manager": (False, True),
            JOB_ANIMAL_WELFARE: (False, False),
        }
        for job, (can_measure, can_parentage) in expected.items():
            permissions = resolve_effective_permissions("user", [job], [], [])
            self.assertEqual(
                "core.edit_animal_measurements" in permissions,
                can_measure,
                job,
            )
            self.assertEqual(
                "core.edit_animal_housing" in permissions,
                can_parentage,
                job,
            )

        vet_awo = resolve_effective_permissions(
            "user", [JOB_VET, JOB_ANIMAL_WELFARE], [], []
        )
        self.assertIn("core.edit_animal_measurements", vet_awo)
        self.assertNotIn("core.edit_animal_housing", vet_awo)
        self.assertEqual(
            set(DEFAULT_JOB_BUNDLES),
            {"vet", "researcher", "keeper", "manager", JOB_ANIMAL_WELFARE},
        )

    def test_all_edit_dialog_saves_use_change_aware_parentage_gate(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        dialog_names = (
            "_dlg_partner",
            "_dlg_samenspender",
            "_dlg_offspring",
            "_dlg_zuchttier",
            "_dlg_versuchstier",
            "_dlg_basic_animal_role",
            "_dlg_female_animal",
        )
        for index, dialog_name in enumerate(dialog_names):
            start = source.index(f"    def {dialog_name}(")
            end = (
                source.index("\n    def ", start + 1)
                if index + 1 < len(dialog_names)
                else len(source)
            )
            segment = source[start:end]
            self.assertIn("_apply_core_parentage_if_changed(", segment, dialog_name)
            self.assertNotIn("_apply_core_parentage(\n", segment, dialog_name)
            self.assertIn("core.edit_animal_measurements", segment, dialog_name)

    def test_primary_role_matrix_has_expected_edit_entry_behavior(self):
        cases = (
            ("lord", [], True, True),
            ("master", [], True, True),
            ("user", ["vet"], True, False),
            ("user", ["researcher"], True, False),
            ("user", ["keeper"], True, True),
            ("user", ["manager"], False, True),
            ("guest", [], False, False),
        )
        for role, jobs, can_measure, can_parentage in cases:
            permissions = resolve_effective_permissions(role, jobs, [], [])
            can = lambda permission: "*" in permissions or permission in permissions
            self.assertEqual(
                can("core.edit_animal_measurements"),
                can_measure,
                (role, jobs),
            )
            self.assertEqual(
                can("core.edit_animal_housing"),
                can_parentage,
                (role, jobs),
            )
