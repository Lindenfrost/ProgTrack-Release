from __future__ import annotations

import copy
import unittest

from Plugins.Heritage_Track.heritage_track_widget import (
    HeritageTrackPlugin,
    ParentageCommandError,
)


class _MemoryRecords:
    def __init__(self):
        self.values = {}
        self.put_count = 0

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def put(self, namespace, record_id, payload, **_kwargs):
        self.put_count += 1
        self.values[(namespace, record_id)] = copy.deepcopy(payload)
        return self.put_count


class _Backend:
    def __init__(self):
        self.records = _MemoryRecords()


class _App:
    def __init__(self):
        self.backend = _Backend()
        self.messages = {}
        self.animals = {
            "Child": {
                "name": "Child", "species": "Callithrix jacchus",
                "sex": "female", "birth_date": "01.01.2020",
                "eizellspenderin": "Mother", "samenspender": "Father",
            },
            "Mother": {
                "name": "Mother", "species": "Callithrix jacchus",
                "sex": "female", "birth_date": "01.01.2010",
            },
            "Father": {
                "name": "Father", "species": "Callithrix jacchus",
                "sex": "male", "birth_date": "01.01.2010",
            },
        }
        self.archived = {
            "OldMother": {
                "name": "OldMother", "species": "Callithrix jacchus",
                "sex": "female", "birth_date": "01.01.2000",
                "death_date": "01.01.2022",
            }
        }
        self.audit = []
        self.persist_count = 0

    def _master_can(self, action):
        return action == "heritage.edit_links"

    def _master_audit(self, *event):
        self.audit.append(event)

    def _save_persistence(self, **_kwargs):
        self.persist_count += 1


class HeritageParentageCommandTest(unittest.TestCase):
    def setUp(self):
        self.app = _App()
        self.plugin = HeritageTrackPlugin(self.app)

    def test_resolves_names_and_projects_canonical_core_fields(self):
        self.assertTrue(self.plugin.set_parentage(
            actor="researcher",
            animal_id="Child",
            expected_revision=None,
            values={"egg_donor": "Mother", "sperm_donor": "Father"},
        ))
        payload = self.app.backend.records.values[("heritage", "graph")]
        self.assertEqual(payload["animals"]["Child"]["egg_donor"], "Mother")
        self.assertEqual(self.app.animals["Child"]["eizellspenderin"], "Mother")
        self.assertEqual(self.app.animals["Child"]["samenspender"], "Father")
        self.assertEqual(self.app.backend.records.put_count, 1)
        self.assertEqual(self.app.audit[0][0], "heritage.parentage_update")

    def test_invalid_parent_is_rejected_without_a_write(self):
        before = copy.deepcopy(self.app.animals["Child"])
        with self.assertRaises(ParentageCommandError):
            self.plugin.set_parentage(
                actor="researcher", animal_id="Child", values={
                    "egg_donor": "Father", "sperm_donor": "Father",
                },
            )
        self.assertEqual(self.app.backend.records.put_count, 0)
        self.assertEqual(self.app.animals["Child"], before)

    def test_permission_boundary_denies_before_mutation(self):
        self.app._master_can = lambda _action: False
        with self.assertRaises(ParentageCommandError):
            self.plugin.set_parentage(
                actor="researcher", animal_id="Child", values={"egg_donor": "Mother"},
            )
        self.assertEqual(self.app.backend.records.put_count, 0)

    def test_explicit_custom_ancestor_is_materialized_and_linked_once(self):
        self.assertTrue(self.plugin.set_parentage(
            actor="researcher", animal_id="Child", values={
                "egg_donor": "New Mother", "sperm_donor": "Father",
            }, allow_custom=True,
        ))
        animals = self.app.backend.records.values[("heritage", "graph")]["animals"]
        custom = [key for key, value in animals.items() if value.get("name") == "New Mother"]
        self.assertEqual(len(custom), 1)
        self.assertEqual(animals["Child"]["egg_donor"], custom[0])
        self.assertEqual(self.app.backend.records.put_count, 1)

    def test_cycle_and_stale_revision_are_rejected(self):
        self.plugin.set_parentage(
            actor="researcher", animal_id="Child", values={
                "egg_donor": "Mother", "sperm_donor": "Father",
            },
        )
        writes = self.app.backend.records.put_count
        with self.assertRaises(ParentageCommandError):
            self.plugin.set_parentage(
                actor="researcher", animal_id="Mother", values={"egg_donor": "Child"},
            )
        self.assertEqual(self.app.backend.records.put_count, writes)
        revision = self.plugin.store.get_all_entries()["Child"]["parentage_revision"]
        with self.assertRaises(ParentageCommandError):
            self.plugin.set_parentage(
                actor="researcher", animal_id="Child", expected_revision="stale",
                values={"egg_donor": "Mother"},
            )
        self.assertEqual(self.plugin.store.get_all_entries()["Child"]["parentage_revision"], revision)

    def test_stale_target_token_is_checked_against_latest_backend_snapshot(self):
        self.plugin.set_parentage(
            actor="researcher", animal_id="Child", values={
                "egg_donor": "Mother", "sperm_donor": "Father",
            },
        )
        stale_token = self.plugin.store.get_all_entries()["Child"]["parentage_revision"]

        # A second plugin instance represents another open session.  The first
        # instance's in-memory graph is intentionally left stale.
        other = HeritageTrackPlugin(self.app)
        other.set_parentage(
            actor="manager", animal_id="Child", values={
                "egg_donor": "Mother", "sperm_donor": "Father",
                "surrogate_mother": "OldMother",
            },
        )
        writes = self.app.backend.records.put_count
        with self.assertRaises(ParentageCommandError):
            self.plugin.set_parentage(
                actor="researcher", animal_id="Child",
                expected_revision=stale_token,
                values={"egg_donor": "Mother", "sperm_donor": "Father"},
            )
        self.assertEqual(self.app.backend.records.put_count, writes)

    def test_atomic_callback_rollback_keeps_cached_graph(self):
        before = copy.deepcopy(self.plugin.store.load())
        with self.assertRaises(RuntimeError):
            self.plugin.store.atomic_update(lambda _data: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertEqual(self.plugin.store.load(), before)
        self.assertEqual(self.app.backend.records.put_count, 0)

    def test_archived_parent_remains_a_candidate(self):
        options = self.plugin.parent_candidate_options("female", "Callithrix jacchus")
        self.assertIn("OldMother", options)

    def test_referenced_core_parent_becomes_dummy_and_reconnects(self):
        self.plugin.set_parentage(
            actor="researcher", animal_id="Child", values={
                "egg_donor": "Mother", "sperm_donor": "Father",
            },
        )
        mother = copy.deepcopy(self.app.animals["Mother"])
        self.assertTrue(self.plugin.promote_core_to_former_dummy("Mother", mother))
        dummy = self.plugin.store.get_all_entries()["Mother"]
        self.assertTrue(dummy["heritage_only"])
        self.assertEqual(self.plugin.store.get_parentage("Child")["egg_donor"], "Mother")

        self.app.animals.pop("Mother")
        self.app.animals["Mother"] = mother
        self.plugin.sync_from_record("Mother", mother, in_main_animals=True)
        self.assertFalse(self.plugin.store.get_all_entries()["Mother"]["heritage_only"])
        self.assertEqual(self.plugin.store.get_parentage("Child")["egg_donor"], "Mother")

    def test_sex_species_date_and_ambiguity_rules_are_centralized(self):
        self.app.animals["WrongSpecies"] = {
            "name": "WrongSpecies", "species": "Macaca mulatta",
            "sex": "female", "birth_date": "01.01.2000",
        }
        self.app.animals["Unknown"] = {
            "name": "Unknown", "species": "Callithrix jacchus",
            "sex": "unknown", "birth_date": "01.01.2000",
        }
        self.app.animals["YoungMother"] = {
            "name": "YoungMother", "species": "Callithrix jacchus",
            "sex": "female", "birth_date": "01.01.2025",
        }
        for parent in ("WrongSpecies", "Unknown", "YoungMother"):
            with self.assertRaises(ParentageCommandError):
                self.plugin.set_parentage(
                    actor="researcher", animal_id="Child",
                    values={"egg_donor": parent},
                )
        self.app.animals["Mother | Callithrix jacchus | 02.01.2010 | Seed"] = dict(
            self.app.animals["Mother"], name="Mother"
        )
        with self.assertRaises(ParentageCommandError):
            self.plugin.set_parentage(
                actor="researcher", animal_id="Child",
                values={"egg_donor": "Mother"},
            )


if __name__ == "__main__":
    unittest.main()
