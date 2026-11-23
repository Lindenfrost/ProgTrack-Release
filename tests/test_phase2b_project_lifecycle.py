from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


class ProjectLifecycleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lifecycle = _load(
            ROOT / "Plugins" / "core" / "project_lifecycle.py",
            "project_lifecycle_under_test",
        )

    def test_normalization_and_guarded_transition(self):
        self.assertEqual(
            self.lifecycle.normalize_project_status(" ACTIVE "), "active"
        )
        self.assertEqual(
            self.lifecycle.normalize_project_status("nonsense"), "draft"
        )
        record = {"status": "active", "animals": ["A"], "id": "P-1"}
        with self.assertRaises(PermissionError):
            self.lifecycle.set_project_status(
                record, "closed", can_manage=False
            )
        self.assertEqual(record["status"], "active")
        with self.assertRaises(ValueError):
            self.lifecycle.set_project_status(
                record, "approval-pending", can_manage=True
            )
        old, new = self.lifecycle.set_project_status(
            record, "closed", can_manage=True
        )
        self.assertEqual((old, new), ("active", "closed"))
        self.assertEqual(record["animals"], ["A"])
        self.assertEqual(record["id"], "P-1")

    def test_ui_and_localization_contract(self):
        source = (ROOT / "Plugins" / "Projects_Track" /
                  "project_track_tab.py").read_text(encoding="utf-8")
        for token in (
            "QRadioButton", "set_project_status", "font.setItalic",
            "font.setBold", "AccessibleTextRole", "change_project_status",
        ):
            self.assertIn(token, source)
        for language in ("en", "de", "it", "ru"):
            messages = json.loads(
                (ROOT / "lang" / f"messages_{language}.json")
                .read_text(encoding="utf-8")
            )
            for status in self.lifecycle.PROJECT_LIFECYCLE_STATUSES:
                self.assertTrue(messages[f"project.status.{status}"])
                self.assertTrue(messages[f"project.status.{status}.tooltip"])

    def test_seed_builder_assigns_explicit_active_status(self):
        source = (ROOT / "source" / "seed" / "projections.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('record["status"] = "active"', source)
        self.assertIn('"status": "active",', source)
        with zipfile.ZipFile(
            ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb"
        ) as package:
            rows = [
                json.loads(line)
                for line in package.read("records/domain.jsonl")
                .decode("utf-8").splitlines()
            ]
        catalog = next(
            row["payload"] for row in rows
            if row["namespace"] == "projects" and row["record_id"] == "catalog"
        )
        self.assertEqual(set(catalog["projects"]), {
            "Backcrossing", "OTOF-", "Oakshield", "Ringbearer", "Shire"
        })
        self.assertTrue(all(
            project.get("status") == "active"
            for project in catalog["projects"].values()
        ))


if __name__ == "__main__":
    unittest.main()
