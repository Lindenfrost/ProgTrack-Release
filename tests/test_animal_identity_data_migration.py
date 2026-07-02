import json
import unittest
from pathlib import Path

from Plugins.core.animal_identity import animal_base_name, split_animal_identity_key
from Plugins.core.project_visibility import animal_matches_name_filter


ROOT = Path(__file__).resolve().parents[1]


def load_json(relative_path):
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


def assert_ipid_key(testcase, key):
    parts = split_animal_identity_key(key)
    testcase.assertIsNotNone(parts, f"not an IPID key: {key!r}")
    return parts


def assert_ipid_name_pair(testcase, key, record):
    base_name, _, _ = assert_ipid_key(testcase, key)
    testcase.assertIsInstance(record, dict)
    testcase.assertEqual(record.get("ipid"), key)
    testcase.assertEqual(record.get("name"), base_name)
    testcase.assertNotEqual(record.get("name"), key)


class AnimalIdentityDataMigrationTest(unittest.TestCase):
    def test_main_animal_json_uses_ipid_keys_and_human_readable_names(self):
        data = load_json("progtrack_daten.json")

        for section in ("animals", "archived_animals"):
            with self.subTest(section=section):
                animals = data.get(section, {})
                self.assertTrue(animals, f"{section} should contain migrated records")
                for key, record in animals.items():
                    base_name, species, birth_date = assert_ipid_key(self, key)
                    assert_ipid_name_pair(self, key, record)
                    self.assertEqual(record.get("_base_name"), base_name)
                    self.assertEqual(record.get("display_name"), base_name)
                    self.assertEqual(record.get("species"), species)
                    self.assertEqual(record.get("birth_date"), birth_date)
                    if species == "Unknown species" or birth_date == "01.01.1900":
                        self.assertTrue(
                            record.get("identity_review_required"),
                            f"placeholder identity must be review-marked for {key}",
                        )

    def test_plugin_json_stores_ipid_and_short_name_where_animals_are_persisted(self):
        heritage = load_json("Plugins/Heritage_Track/heritage_animals.json")
        for key, record in heritage.get("animals", {}).items():
            assert_ipid_name_pair(self, key, record)

        medi = load_json("Plugins/Medi_Track/medi_history.json")
        for key, record in medi.get("animals", {}).items():
            assert_ipid_name_pair(self, key, record)

        reports = load_json("Plugins/Animal_Reports/animal_report_data.json")
        for key, record in reports.items():
            assert_ipid_name_pair(self, key, record)

        pdg_models = load_json("Plugins/PdG_converter/data/models.json")
        for key, record in pdg_models.items():
            assert_ipid_name_pair(self, key, record)

    def test_cage_project_and_surgery_json_references_use_ipid_plus_short_name(self):
        cage = load_json("Plugins/Cage__Track/cage.json")
        for key, occupant in cage.get("occupants", {}).items():
            if occupant.get("type") == "real":
                assert_ipid_name_pair(self, key, occupant)
        for key in cage.get("movement_history", {}):
            assert_ipid_key(self, key)

        projects = load_json("Plugins/Projects_Track/projects_history.json")
        for project in projects.get("projects", {}).values():
            for record in project.get("animals", []):
                assert_ipid_name_pair(self, record.get("ipid"), record)

        for schedule_path in (
            "Plugins/Surgery_Planner/Surgery_Planner.schedule.json",
            "Plugins/Surgery_Planner/Surgery_Pre_Planner.schedule.json",
        ):
            schedule = load_json(schedule_path)
            for entry in schedule.get("schedule", []):
                assert_ipid_name_pair(self, entry.get("ipid"), entry)
                self.assertEqual(entry.get("animal"), entry.get("ipid"))

    def test_animal_name_filter_matches_short_name_and_ipid(self):
        key = "Luna | Macaca mulatta | 01.01.2020"
        record = {
            "ipid": key,
            "name": "Luna",
            "_base_name": "Luna",
            "display_name": "Luna",
            "species": "Macaca mulatta",
            "birth_date": "01.01.2020",
        }

        for query in ("Luna", "Macaca", "01.01.2020", "Luna | Macaca", key):
            with self.subTest(query=query):
                self.assertTrue(animal_matches_name_filter(key, record, query))
        self.assertFalse(animal_matches_name_filter(key, record, "Momo"))

    def test_project_history_short_name_derives_from_ipid(self):
        key = "Ekaterina | Callitrix jacchus | 02.04.2016"
        self.assertEqual(animal_base_name(key), "Ekaterina")


if __name__ == "__main__":
    unittest.main()
