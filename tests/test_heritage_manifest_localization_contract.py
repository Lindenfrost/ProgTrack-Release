"""Release-contract checks for Heritage Track metadata and documentation.

These checks intentionally avoid importing Qt.  They protect the manifest and
message/documentation contract that can regress independently of the visual
pedigree tests.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HERITAGE = ROOT / "Plugins" / "Heritage_Track"
CATALOGS = tuple(ROOT / "lang" / f"messages_{lang}.json" for lang in ("en", "de", "it", "ru"))
MANUALS = tuple(ROOT / "manual" / f"ProgTrack_User_Guide - {lang}.html" for lang in ("en", "de", "it", "ru"))


def _literal_message_keys() -> set[str]:
    """Collect literal message keys used by Heritage source modules."""

    pattern = re.compile(r"(?:messages|self\.messages)\.get\(\s*['\"]([^'\"]+)")
    keys: set[str] = set()
    for source in HERITAGE.glob("*.py"):
        keys.update(pattern.findall(source.read_text(encoding="utf-8")))
    return keys


class HeritageManifestLocalizationContractTest(unittest.TestCase):
    def test_manifest_matches_current_runtime_contract(self):
        manifest = json.loads((HERITAGE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.2.3")
        self.assertEqual(manifest["min_progtrack_version"], "0.2.3")
        self.assertEqual(set(manifest["permissions"]), {"heritage.view", "heritage.edit_links"})
        self.assertEqual(manifest["backend_namespaces"], ["heritage"])
        self.assertNotIn("species_filtering", manifest["features"])
        self.assertIn("draggable_nodes", manifest["capabilities"])
        self.assertIn("selection_scoped_position_persistence", manifest["capabilities"])

    def test_every_literal_key_and_dynamic_label_template_is_localized(self):
        used = _literal_message_keys()
        expected_dynamic = {
            f"heritage_track.settings.animal_label_detail.{value}"
            for value in ("nothing", "inbreeding_f", "birth_date", "animal_id")
        }
        self.assertTrue(expected_dynamic)
        for path in CATALOGS:
            catalog = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([], sorted(used - catalog.keys()), path.name)
            self.assertTrue(expected_dynamic <= catalog.keys(), path.name)

    def test_source_has_no_heritage_mojibake_and_docs_cover_supported_behavior(self):
        source = "\n".join(path.read_text(encoding="utf-8") for path in HERITAGE.glob("*.py"))
        self.assertNotIn("Kinship Ï", source)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("### Heritage Track", readme)
        for path in MANUALS:
            text = path.read_text(encoding="utf-8")
            self.assertIn('id="plugins"', text, path.name)
            self.assertIn("Heritage Track", text, path.name)
            self.assertIn("IPID", text, path.name)


if __name__ == "__main__":
    unittest.main()
