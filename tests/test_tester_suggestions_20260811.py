import json
import re
import zipfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TesterSuggestionAssetTest(unittest.TestCase):
    def test_unknown_sex_asset_is_registered_and_used(self):
        manifest = json.loads((ROOT / "icons" / "ui" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["icons"]["role.unknown"]["file"], "role_unknown.svg")
        svg = (ROOT / "icons" / "ui" / "role_unknown.svg").read_text(encoding="utf-8")
        self.assertIn("<path", svg)
        self.assertIn("<circle", svg)
        self.assertIn("#4caf82", svg)
        self.assertIn("aria-label=\"Unknown sex role symbol\"", svg)
        main = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn('(\"U\", \"role.unknown\")', main)
        self.assertNotIn('(\"U\", \"heritage.placeholder_animal\")', main)


class TesterSuggestionSeedTest(unittest.TestCase):
    def test_mouse_experimental_pair_cages_and_reciprocal_partners(self):
        with zipfile.ZipFile(ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb") as zf:
            animals_by_ipid = {
                row["payload"]["ipid"]: row["payload"]
                for row in map(json.loads, zf.read("records/animals.jsonl").decode().splitlines())
                if row["payload"].get("species") == "Mus musculus"
            }
            domains = [
                row for row in map(json.loads, zf.read("records/domain.jsonl").decode().splitlines())
                if row.get("namespace") == "seed" and row.get("record_id") == "shire-colony"
            ]
            named_animals = domains[0]["payload"]["named_animals"]
            animals = {
                alias: animals_by_ipid[ipid]
                for alias, ipid in named_animals.items()
            }
            cage_record = next(
                row["payload"]
                for row in map(json.loads, zf.read("records/domain.jsonl").decode().splitlines())
                if row.get("namespace") == "housing" and row.get("record_id") == "cage"
            )
        occupants = cage_record["occupants"]
        cages = {name: occupants[animal["ipid"]]["cage_id"] for name, animal in animals.items()}
        self.assertEqual(cages["Frodo"], cages["Sam"])
        self.assertEqual(cages["Merry"], cages["Pippin"])
        self.assertNotEqual(cages["Frodo"], cages["Merry"])
        for left, right in (("Frodo", "Sam"), ("Sam", "Frodo"), ("Merry", "Pippin"), ("Pippin", "Merry")):
            self.assertEqual(animals[left].get("verpaart_mit"), animals[right]["ipid"])


class TesterSuggestionDialogContractTest(unittest.TestCase):
    def test_scroll_and_readonly_contracts_are_present(self):
        main = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertEqual(main.count("self._add_scrollable_animal_form("), 7)
        self.assertIn("animalDialogFormScroll", main)
        heritage = (ROOT / "Plugins" / "Heritage_Track" / "heritage_track_widget.py").read_text(encoding="utf-8")
        self.assertIn("genotype_editable", heritage)
        self.assertIn("is_heritage_only", heritage)
        branding = (ROOT / "Plugins" / "core" / "institution_branding.py").read_text(encoding="utf-8")
        self.assertIn("_logo_source_path", branding)
        self.assertIn("self._logo_source_path.name", branding)


if __name__ == "__main__":
    unittest.main()
