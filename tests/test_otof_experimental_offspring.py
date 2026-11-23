"""Focused regression checks for the OTOF example-role update."""

from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path

from Plugins.core.backend.core_configuration import ANIMAL_ROLES, read_seed_configuration

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {"Thranduil", "Ekaterina", "Olga", "Svetlana", "Legolas"}


class OtofExperimentalOffspringTest(unittest.TestCase):
    def test_static_role_and_localized_labels(self):
        roles = read_seed_configuration(ROOT)[ANIMAL_ROLES]["roles"]
        role = next(item for item in roles if item["value"] == "experimental_offspring")
        self.assertEqual(role["icon"], "role.experimental_offspring")
        self.assertTrue((ROOT / "icons/ui/role_experimental_offspring.svg").is_file())
        for language in ("en", "de", "it", "ru"):
            messages = json.loads(
                (ROOT / "lang" / f"messages_{language}.json").read_text(encoding="utf-8")
            )
            self.assertTrue(messages["role.experimental_offspring"])

    def test_seed_otof_assignments(self):
        with zipfile.ZipFile(ROOT / "Resources/Seed/progtrack_seed.ptdb") as package:
            records = [
                json.loads(line)
                for line in package.read("records/animals.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        found = {
            record["payload"]["name"]: record["payload"]
            for record in records
            if record.get("payload", {}).get("name") in TARGETS
        }
        self.assertEqual(set(found), TARGETS)
        self.assertTrue(all(item["in_experiment"] for item in found.values()))
        self.assertEqual(found["Legolas"]["rolle"], "experimental_offspring")

    def test_active_database_matches_seed_scope(self):
        from tests.disposable_seed_backend import open_disposable_seed_backend

        temporary, backend = open_disposable_seed_backend()
        try:
            snapshot = backend.load_core_data()
        finally:
            backend.close()
            temporary.cleanup()
        rows = list(snapshot["animals"].values()) + list(
            snapshot["archived_animals"].values()
        )
        found = {
            item["name"]: item
            for item in rows
            if item.get("name") in TARGETS
        }
        self.assertEqual(set(found), TARGETS)
        self.assertTrue(all(item["in_experiment"] for item in found.values()))
        self.assertEqual(found["Legolas"]["rolle"], "experimental_offspring")

    def test_category_dialog_uses_immutable_ipid_and_compact_height(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn('public_id = str(a.get("ipid")', source)
        self.assertIn(
            "self._apply_dialog_width(dlg, width=360, minimum_height=0)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
