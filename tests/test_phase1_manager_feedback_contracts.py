from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from Plugins.Cage__Track.cage_store import CageStore
from Plugins.Master_Track.permissions import (
    ROLE_LORD,
    ROLE_MASTER,
    ROLE_USER,
    can_manage_health_status,
)
from Plugins.core.identity_conventions import (
    DEFAULT_CONVENTIONS,
    next_generated_id,
    regenerated_id_for_edit,
    relationship_candidates,
    render_animal_id,
)
from Plugins.core.lifecycle_events import (
    apply_departure,
    apply_experiment_exit,
    ever_in_experiment,
)
from Plugins.core.project_species import (
    assignment_allowed,
    remove_mismatched_assignments,
    species_match,
)


class IdentityConventionTests(unittest.TestCase):
    def test_generated_offspring_id_and_unknown_sex(self):
        settings = dict(DEFAULT_CONVENTIONS)
        settings["yearly_sequences"] = {}
        generated = next_generated_id(
            settings,
            name="Ultron",
            species="Callithrix jacchus",
            birth_date="01.02.2026",
            sex="Unknown",
        )
        self.assertEqual(generated, "cj_26_0001_U_Ultron")
        self.assertEqual(settings["yearly_sequences"]["26"], 1)

    def test_generation_avoids_duplicates_and_regenerates_managed_id(self):
        settings = dict(DEFAULT_CONVENTIONS)
        settings["yearly_sequences"] = {}
        generated = next_generated_id(
            settings,
            name="Ultron",
            species="Callithrix jacchus",
            birth_date="01.02.2026",
            sex="Male",
            existing_ids={"cj_26_0001_M_Ultron"},
        )
        self.assertEqual(generated, "cj_26_0002_M_Ultron")
        old = {
            "id": generated,
            "generated_id_sequence": 2,
            "_base_name": "Ultron",
            "species": "Callithrix jacchus",
            "birth_date": "01.02.2026",
            "sex": "Male",
        }
        changed = dict(old, _base_name="Vision")
        self.assertEqual(
            regenerated_id_for_edit(settings, old, changed),
            "cj_26_0002_M_Vision",
        )

    def test_relationship_candidates_exclude_invalid_records(self):
        animals = {
            "female": {"sex": "Female"},
            "male": {"sex": "Male"},
            "unknown": {"sex": "Unknown"},
            "dead": {"sex": "Female", "death_date": "01.01.2020"},
            "archived": {"sex": "Female", "archived": True},
        }
        self.assertEqual(
            relationship_candidates(animals, required_sex="Female"),
            ["female"],
        )


class ProjectSpeciesTests(unittest.TestCase):
    def test_exact_species_match(self):
        self.assertTrue(species_match("", "anything"))
        self.assertTrue(species_match("Callithrix jacchus", "Callithrix jacchus"))
        self.assertFalse(species_match("Callithrix jacchus", "callithrix jacchus"))

    def test_assignment_and_cleanup(self):
        projects = {"P": {"summary": {"species": "Callithrix jacchus"}}}
        self.assertTrue(assignment_allowed(projects, "P", "Callithrix jacchus"))
        self.assertFalse(assignment_allowed(projects, "P", "Macaca mulatta"))
        animals = {
            "ok": {"project": "P", "species": "Callithrix jacchus"},
            "bad": {"project": "P", "species": "Macaca mulatta", "severity": "SV2"},
        }
        self.assertEqual(
            remove_mismatched_assignments(animals, "P", "Callithrix jacchus"),
            ["bad"],
        )
        self.assertEqual(animals["bad"]["project"], "")


class LifecycleTests(unittest.TestCase):
    def test_experiment_exit_requires_reason_and_sets_death(self):
        record = {"in_experiment": True, "project": "P"}
        with self.assertRaises(ValueError):
            apply_experiment_exit(record, exit_date="01.01.2026", reason="")
        apply_experiment_exit(
            record,
            exit_date="01.01.2026",
            reason="Totgeburt",
            actor="Manager",
        )
        self.assertFalse(record["in_experiment"])
        self.assertEqual(record["death_date"], "01.01.2026")
        self.assertEqual(record["project"], "")
        self.assertTrue(ever_in_experiment(record))

    def test_departure_records_recipient(self):
        record = {}
        apply_departure(
            record,
            departure_date="02.01.2026",
            reason="Abgabe",
            recipient="Other facility",
        )
        self.assertEqual(record["handover_recipient"], "Other facility")
        self.assertEqual(record["lifecycle_events"][0]["event_type"], "departure")


class PermissionAndCageTests(unittest.TestCase):
    def test_health_status_is_vet_only_with_admin_override(self):
        self.assertTrue(can_manage_health_status(ROLE_USER, ["vet"]))
        self.assertFalse(can_manage_health_status(ROLE_USER, ["manager"]))
        self.assertFalse(can_manage_health_status(ROLE_USER, [],))
        self.assertTrue(can_manage_health_status(ROLE_LORD, []))
        self.assertTrue(can_manage_health_status(ROLE_MASTER, []))

    def test_virtual_parent_and_empty_cages_are_not_inspection_eligible(self):
        with tempfile.TemporaryDirectory() as temp:
            store = CageStore(temp)
            building = store.create_building("B")
            room = store.create_room(building["id"], "R")
            real = store.create_cage(room["id"], "C")
            empty = store.create_cage(room["id"], "Empty")
            store.create_occupant("Animal", cage_id=real["id"])
            self.assertEqual(store.eligible_inspection_cages(), [real["id"]])
            store.set_structure_virtual(building["id"], "building", True)
            self.assertEqual(store.eligible_inspection_cages(), [])
            self.assertTrue(store.is_effectively_virtual(real["id"]))
            self.assertNotIn(empty["id"], store.eligible_inspection_cages())


if __name__ == "__main__":
    unittest.main()
