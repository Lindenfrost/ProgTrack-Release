import json
import tempfile
import unittest
from pathlib import Path

from Plugins.Heritage_Track.heritage_store import HeritageStore
from Plugins.Heritage_Track.layout_pipeline import (
    LayoutPipeline,
    nudge_nodes_off_child_line_segments,
    parse_complete_birth_date_ordinal,
)
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine


REPO_ROOT = Path(__file__).resolve().parents[1]


def _engine_for_parent_map(parent_map):
    animals = {name: {} for name in {"Dam", "Sire", *parent_map.keys()}}

    def parent_lookup(name, _record):
        return parent_map.get(name, {})

    engine = PedigreeEngine(animals, parent_lookup)
    engine.build()
    return engine, set(animals)


class HeritageTrackBirthdateLayoutTest(unittest.TestCase):
    def test_complete_birth_date_parser_rejects_year_only_values(self):
        ordinal = parse_complete_birth_date_ordinal("02.01.2021")

        self.assertIsInstance(ordinal, int)
        self.assertEqual(ordinal, parse_complete_birth_date_ordinal("2021-01-02"))
        self.assertIsNone(parse_complete_birth_date_ordinal("2021"))
        self.assertIsNone(parse_complete_birth_date_ordinal(2021))

    def test_birthdate_height_layout_places_younger_sibling_higher(self):
        parent_map = {
            "Older": {"egg_donor": "Dam", "sperm_donor": "Sire"},
            "Younger": {"egg_donor": "Dam", "sperm_donor": "Sire"},
        }
        engine, nodes = _engine_for_parent_map(parent_map)
        families = {
            "family::dam::sire": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Older", "Younger"],
            }
        }
        birth_ordinals = {
            "Older": parse_complete_birth_date_ordinal("01.01.2020"),
            "Younger": parse_complete_birth_date_ordinal("01.03.2020"),
        }

        enabled = LayoutPipeline().compute_positions(
            nodes=nodes,
            levels={},
            engine=engine,
            families=families,
            birth_ordinal_by_node=birth_ordinals,
            birthdate_height_layout=True,
        )
        disabled = LayoutPipeline().compute_positions(
            nodes=nodes,
            levels={},
            engine=engine,
            families=families,
            birth_ordinal_by_node=birth_ordinals,
            birthdate_height_layout=False,
        )

        self.assertGreater(enabled["Younger"][1], enabled["Older"][1])
        self.assertEqual(disabled["Younger"][1], disabled["Older"][1])
        self.assertGreater(enabled["Older"][1], enabled["Dam"][1])

    def test_same_birth_date_siblings_stay_aligned(self):
        parent_map = {
            "Sibling A": {"egg_donor": "Dam", "sperm_donor": "Sire"},
            "Sibling B": {"egg_donor": "Dam", "sperm_donor": "Sire"},
        }
        engine, nodes = _engine_for_parent_map(parent_map)
        families = {
            "family::dam::sire": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Sibling A", "Sibling B"],
            }
        }
        birth_ordinals = {
            "Sibling A": parse_complete_birth_date_ordinal("01.01.2020"),
            "Sibling B": parse_complete_birth_date_ordinal("01.01.2020"),
        }

        positions = LayoutPipeline().compute_positions(
            nodes=nodes,
            levels={},
            engine=engine,
            families=families,
            birth_ordinal_by_node=birth_ordinals,
            birthdate_height_layout=True,
        )

        self.assertEqual(positions["Sibling A"][1], positions["Sibling B"][1])

    def test_partner_node_is_nudged_off_child_connector_line(self):
        animal_positions = {
            "Sibling A": (0.0, 2.0),
            "Sibling B": (4.0, 2.0),
            "Pulled Partner": (2.0, 2.0),
            "Dam": (-1.0, 0.0),
            "Sire": (1.0, 0.0),
        }
        families = {
            "family::dam::sire": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Sibling A", "Sibling B"],
            }
        }
        family_positions = {"family::dam::sire": (0.0, 1.0)}

        adjusted = nudge_nodes_off_child_line_segments(
            animal_positions,
            families,
            family_positions,
        )
        protected = nudge_nodes_off_child_line_segments(
            animal_positions,
            families,
            family_positions,
            protected_nodes={"Pulled Partner"},
        )

        self.assertLess(adjusted["Pulled Partner"][1], animal_positions["Pulled Partner"][1])
        self.assertEqual(protected["Pulled Partner"], animal_positions["Pulled Partner"])

    def test_heritage_settings_include_birthdate_layout_and_exclude_archived(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HeritageStore(tmpdir)

            settings = store.get_settings()
            self.assertTrue(settings["birthdate_height_layout"])
            self.assertFalse(settings["exclude_archived"])

            store.set_settings({"birthdate_height_layout": False, "exclude_archived": True})
            updated = store.get_settings()
            self.assertFalse(updated["birthdate_height_layout"])
            self.assertTrue(updated["exclude_archived"])

    def test_birthdate_layout_language_keys_exist(self):
        for path in (REPO_ROOT / "lang").glob("messages_*.json"):
            with self.subTest(path=path.name):
                messages = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("heritage_track.settings.birthdate_height_layout", messages)
                self.assertIn("heritage_track.settings.birthdate_height_layout.tooltip", messages)


if __name__ == "__main__":
    unittest.main()
