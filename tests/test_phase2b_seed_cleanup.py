"""Regression coverage for the final Phase 2B/2C fictional-seed cleanup."""

from __future__ import annotations

import json
import copy
import sqlite3
import tempfile
import unittest
import zipfile
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime
from fractions import Fraction
from functools import lru_cache
from itertools import permutations
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb"
REMOVED_NAMES = {"Andy", "Betta", "RoleProbe", "Test"}
HERITAGE_DUMMY_KINDS = {"direct", "former_core"}
HERITAGE_DUMMY_PERSISTENCE_KINDS = {
    "temporary_dummy", "direct_dummy", "former_core_dummy",
}


def _package_records() -> tuple[list[dict[str, Any]], dict[tuple[str, str], Any]]:
    with zipfile.ZipFile(SEED) as package:
        animals = [
            json.loads(line)
            for line in package.read("records/animals.jsonl").decode("utf-8").splitlines()
            if line.strip()
        ]
        domain_rows = [
            json.loads(line)
            for line in package.read("records/domain.jsonl").decode("utf-8").splitlines()
            if line.strip()
        ]
    domains = {
        (row["namespace"], row["record_id"]): row["payload"]
        for row in domain_rows
    }
    return animals, domains


def _removed_identity(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    first = text.split(" | ", 1)[0].strip()
    if first in REMOVED_NAMES or text in REMOVED_NAMES:
        return True
    lowered = text.casefold()
    return any(lowered.endswith("_" + name.casefold()) for name in REMOVED_NAMES)


def _find_removed(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            # Localized labels may legitimately contain a word that matches a
            # retired animal name (for example the generic role label "Test").
            # They are not seed animal records or animal references.
            if (
                isinstance(key, tuple)
                and len(key) == 2
                and key[0] == "configuration"
                and str(key[1]).startswith("user-message-overrides:")
            ):
                continue
            if _removed_identity(key):
                found.append(f"{path}.{key} (key)")
            found.extend(_find_removed(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_removed(item, f"{path}[{index}]"))
    elif _removed_identity(value):
        found.append(path)
    return found


def _find_heritage_dummies(value: Any, path: str = "$") -> list[str]:
    """Find canonical Heritage-owned dummy markers anywhere in seed domains."""
    found: list[str] = []
    if isinstance(value, dict):
        heritage_only = bool(value.get("heritage_only"))
        dummy_kind = str(value.get("dummy_kind") or "").strip().casefold()
        persistence_kind = str(value.get("persistence_kind") or "").strip().casefold()
        if heritage_only and (
            dummy_kind in HERITAGE_DUMMY_KINDS
            or persistence_kind in HERITAGE_DUMMY_PERSISTENCE_KINDS
        ):
            found.append(path)
        for key, item in value.items():
            found.extend(_find_heritage_dummies(item, f"{path}.{key}"))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(_find_heritage_dummies(item, f"{path}[{index}]"))
    return found


class Phase2BSeedCleanupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.rows, cls.domains = _package_records()
        cls.animals = [row["payload"] for row in cls.rows]
        cls.by_ipid = {animal["ipid"]: animal for animal in cls.animals}
        cls.by_name = {animal["name"]: animal for animal in cls.animals}

    def test_disposable_probe_animals_and_all_references_are_absent(self):
        self.assertEqual(len(self.rows), 695)
        self.assertEqual(sum(not bool(row.get("archived")) for row in self.rows), 605)
        self.assertEqual(sum(bool(row.get("archived")) for row in self.rows), 90)
        self.assertTrue(REMOVED_NAMES.isdisjoint(self.by_name))
        self.assertEqual(_find_removed(self.animals), [])
        self.assertEqual(_find_removed(self.domains), [])

    def test_domain_animal_indexes_are_referentially_complete(self):
        all_ipids = set(self.by_ipid)
        active_ipids = {
            row["ipid"] for row in self.rows if not bool(row.get("archived"))
        }

        heritage = self.domains[("heritage", "graph")]["animals"]
        medical = self.domains[("medical", "history")]["animals"]
        reports = self.domains[("reports", "animal-reports")]
        housing = self.domains[("housing", "cage")]
        self.assertTrue(
            all(
                isinstance(entry, dict)
                and bool(entry.get("heritage_only"))
                and str(entry.get("dummy_kind") or "").strip().casefold()
                in HERITAGE_DUMMY_KINDS
                and str(entry.get("persistence_kind") or "").strip().casefold()
                in HERITAGE_DUMMY_PERSISTENCE_KINDS
                and key not in all_ipids
                and str(entry.get("ipid") or "").strip() not in all_ipids
                for key, entry in heritage.items()
            )
        )
        self.assertLessEqual(set(medical), all_ipids)
        self.assertLessEqual(set(reports), all_ipids)
        self.assertEqual(set(housing["occupants"]), active_ipids)
        self.assertLessEqual(set(housing["movement_history"]), all_ipids)
        for movements in housing["movement_history"].values():
            for movement in movements:
                self.assertLessEqual(
                    set(movement.get("cage_mates_snapshot", [])), all_ipids
                )

        projects = self.domains[("projects", "history")]["projects"]
        for project in projects.values():
            for entry in project.get("animals", []):
                self.assertIn(entry["ipid"], all_ipids)

    def test_shipped_seed_contains_no_heritage_dummy_animals_or_references(self):
        heritage = self.domains[("heritage", "graph")]["animals"]
        self.assertEqual(_find_heritage_dummies(self.domains), [])
        self.assertEqual(
            [
                key for key, value in heritage.items()
                if isinstance(value, dict) and bool(value.get("heritage_only"))
            ],
            [],
        )

        report = json.loads(
            (ROOT / "Resources" / "Seed" / "integrity_report.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(report["counts"]["heritage_owned_dummy_animals"], 0)
        self.assertEqual(report["errors"], [])
        self.assertTrue(report["valid"])

    def test_every_project_species_matches_its_assigned_animals(self):
        projects = self.domains[("projects", "catalog")]["projects"]
        assignments: dict[str, list[dict[str, Any]]] = {
            name: [
                animal
                for animal in self.animals
                if str(animal.get("project") or "").strip() == name
            ]
            for name in projects
        }

        self.assertEqual(
            set(projects), {"Backcrossing", "OTOF-", "Oakshield", "Ringbearer", "Shire"}
        )
        for name, project in projects.items():
            with self.subTest(project=name):
                project_species = str(
                    project.get("summary", {}).get("species") or ""
                ).strip()
                self.assertTrue(project_species)
                self.assertTrue(assignments[name])
                self.assertEqual(
                    {
                        str(animal.get("species") or "").strip()
                        for animal in assignments[name]
                    },
                    {project_species},
                )

    def test_active_project_history_matches_current_animal_assignments(self):
        catalog_projects = self.domains[("projects", "catalog")]["projects"]
        history_projects = self.domains[("projects", "history")]["projects"]
        active_ipids = {
            row["ipid"] for row in self.rows if not bool(row.get("archived"))
        }

        for name in catalog_projects:
            expected = {
                animal["ipid"]
                for animal in self.animals
                if animal["ipid"] in active_ipids
                and str(animal.get("project") or "").strip() == name
            }
            actual_entries = [
                entry
                for entry in history_projects[name].get("animals", [])
                if not entry.get("date_left")
                and str(entry.get("status") or "active").strip().casefold()
                == "active"
            ]
            with self.subTest(project=name):
                self.assertEqual(len(actual_entries), len({e["ipid"] for e in actual_entries}))
                self.assertEqual({entry["ipid"] for entry in actual_entries}, expected)

    def test_shire_population_and_five_generation_parentage_are_bounded(self):
        shire = self.domains[("seed", "shire-colony")]
        mice = {ipid: animal for ipid, animal in self.by_ipid.items()
                if animal["species"] == "Mus musculus"}
        self.assertEqual(len(mice), 471)
        self.assertLessEqual(len(mice), 500)
        self.assertEqual(len(shire["founder_pairs"]), 8)
        founders = [ipid for pair in shire["founder_pairs"] for ipid in pair]
        self.assertEqual(len(set(founders)), 16)
        for father, mother in shire["founder_pairs"]:
            self.assertEqual(mice[father]["sex"], "Male")
            self.assertEqual(mice[mother]["sex"], "Female")
            self.assertFalse(mice[father]["samenspender"])
            self.assertFalse(mice[mother]["eizellspenderin"])
        self.assertEqual(Counter(shire["generation_by_ipid"].values()),
                         {0: 16, 1: 101, 2: 88, 3: 87, 4: 89, 5: 66, 6: 10})

        @lru_cache(None)
        def depth(ipid):
            parents = [mice[ipid].get(field) for field in ("samenspender", "eizellspenderin")]
            return 1 + max(depth(parent) for parent in parents if parent) if any(parents) else 0

        for ipid, generation in shire["generation_by_ipid"].items():
            self.assertEqual(depth(ipid), generation, ipid)
            animal = mice[ipid]
            self.assertRegex(animal["name"], r"^[A-Za-z ]+$")
            self.assertFalse(any(character.isdigit() for character in animal["name"]))
            for field, sex in (("samenspender", "Male"), ("eizellspenderin", "Female")):
                parent = animal.get(field)
                if parent:
                    self.assertIn(parent, mice)
                    self.assertEqual(mice[parent]["sex"], sex)
                    self.assertLess(datetime.strptime(mice[parent]["birth_date"], "%d.%m.%Y"),
                                    datetime.strptime(animal["birth_date"], "%d.%m.%Y"))
        self.assertEqual(len({animal["name"] for animal in mice.values()}), len(mice))
        self.assertEqual(len({animal["id"] for animal in mice.values()}), len(mice))
        self.assertEqual(self.by_name["Elanor Gamgee"]["samenspender"], self.by_name["Sam Gamgee"]["ipid"])
        self.assertEqual(self.by_name["Elanor Gamgee"]["eizellspenderin"], self.by_name["Rosie Cotton"]["ipid"])

    def test_shire_surnames_follow_fathers_for_every_son_and_daughter(self):
        shire = self.domains[("seed", "shire-colony")]
        mice = {ipid: animal for ipid, animal in self.by_ipid.items()
                if animal["species"] == "Mus musculus"}
        surnames = shire["surname_by_ipid"]
        self.assertEqual(set(surnames), set(mice))
        for ipid, record in mice.items():
            father = record.get("samenspender")
            if father:
                self.assertEqual(surnames[ipid], surnames[father], ipid)
            self.assertGreaterEqual(len(record["name"].split()), 2, ipid)
            self.assertEqual(record["name"].split()[1], surnames[ipid], ipid)
            self.assertEqual(record["_base_name"], record["name"])
            self.assertEqual(record["display_name"], record["name"])
        for name, surname in {"Frodo": "Baggins", "Sam": "Gamgee",
                              "Merry": "Brandybuck", "Pippin": "Took",
                              "Elanor": "Gamgee"}.items():
            self.assertEqual(surnames[self.by_name[f"{name} {surname}"]["ipid"]], surname)
            self.assertNotIn(name, self.by_name)

    def test_shire_repeated_given_and_family_names_have_varied_unique_monikers(self):
        from source.build_sample_seed import SHIRE_NAME_MONIKERS
        shire = self.domains[("seed", "shire-colony")]
        groups = defaultdict(list)
        used = set()
        for ipid in shire["generation_by_ipid"]:
            if ipid in shire["named_animals"].values():
                continue
            given, surname, moniker = self.by_ipid[ipid]["name"].split()
            self.assertIn(moniker, SHIRE_NAME_MONIKERS)
            groups[(given, surname)].append(moniker)
            used.add(moniker)
        self.assertTrue(any(len(values) >= 3 for values in groups.values()))
        for values in groups.values():
            self.assertEqual(len(values), len(set(values)))
        self.assertTrue({"Generous", "Admirable", "Noble", "Trustworthy"}.issubset(used))
        self.assertEqual(len(SHIRE_NAME_MONIKERS), len(set(SHIRE_NAME_MONIKERS)))

    def test_shire_validator_rejects_broken_family_names_and_duplicate_names(self):
        from source.build_sample_seed import validate_shire_colony
        mice = {ipid: copy.deepcopy(animal) for ipid, animal in self.by_ipid.items()
                if animal["species"] == "Mus musculus"}
        shire = copy.deepcopy(self.domains[("seed", "shire-colony")])
        child = next(ipid for ipid in mice
                     if ipid not in shire["named_animals"].values() and mice[ipid].get("samenspender"))
        shire["surname_by_ipid"][child] = "Wrongfamily"
        self.assertTrue(any("paternal surname" in error for error in validate_shire_colony(mice, shire)))
        shire = copy.deepcopy(self.domains[("seed", "shire-colony")])
        mice[child]["name"] = "Test Wrongfamily Noble"
        self.assertTrue(any("displayed surname" in error for error in validate_shire_colony(mice, shire)))
        other = next(ipid for ipid in mice if ipid != child and ipid not in shire["named_animals"].values())
        mice[child]["name"] = mice[other]["name"]
        self.assertTrue(any("duplicate full mouse name" in error for error in validate_shire_colony(mice, shire)))

    def test_shire_litters_and_total_breeder_selection_match_core(self):
        shire = self.domains[("seed", "shire-colony")]
        by_pair = defaultdict(list)
        all_children = set()
        self.assertEqual(len(shire["litters"]), 40)
        self.assertEqual(len({litter["litter_id"] for litter in shire["litters"]}), 40)
        for litter in shire["litters"]:
            children = litter["offspring"]
            self.assertGreaterEqual(len(children), 10)
            self.assertLessEqual(len(children), 12)
            self.assertEqual(len(set(children)), len(children))
            self.assertFalse(all_children.intersection(children))
            all_children.update(children)
            pair = litter["father"], litter["mother"]
            by_pair[pair].append(litter)
            for child in children:
                record = self.by_ipid[child]
                self.assertEqual((record["samenspender"], record["eizellspenderin"]), pair)
                self.assertEqual(datetime.strptime(record["birth_date"], "%d.%m.%Y").date().isoformat(),
                                 litter["birth_date"])
        actual_breeders = {parent for litter in shire["litters"]
                          for parent in (litter["father"], litter["mother"])}
        self.assertEqual({(p["father"], p["mother"]) for p in shire["pairings"]}, set(by_pair))
        self.assertEqual(Counter(p["offspring_generation"] for p in shire["pairings"]),
                         {1: 8, 2: 8, 3: 8, 4: 8, 5: 4, 6: 1})
        for pairing in shire["pairings"]:
            litters = by_pair[pairing["father"], pairing["mother"]]
            self.assertIn(len(litters), (1, 2))
            children = {child for litter in litters for child in litter["offspring"]}
            selected = set(pairing["selected_for_breeding"])
            self.assertLessEqual(len(selected), 4)
            self.assertEqual(selected, children & actual_breeders)
            # Independently check every Core child of this pair, not just the
            # count written into the generator's own metadata.
            core_children = {ipid for ipid, animal in self.by_ipid.items()
                             if (animal.get("samenspender"), animal.get("eizellspenderin"))
                             == (pairing["father"], pairing["mother"])}
            self.assertEqual(children, core_children)

    def test_shire_pairing_minimizes_available_kinship_and_avoids_close_relatives(self):
        shire = self.domains[("seed", "shire-colony")]

        @lru_cache(None)
        def ancestors(ipid, levels):
            parents = {self.by_ipid[ipid].get(field) for field in ("samenspender", "eizellspenderin")} - {"", None}
            return parents | set().union(*(ancestors(parent, levels - 1) for parent in parents)) if levels > 1 else parents

        @lru_cache(None)
        def depth(ipid):
            parents = ancestors(ipid, 1)
            return 1 + max(map(depth, parents)) if parents else 0

        @lru_cache(None)
        def coefficient(left, right):
            parents = ancestors(left, 1)
            if left == right:
                return (1 + coefficient(*sorted(parents))) / 2 if len(parents) == 2 else Fraction(1, 2)
            if (depth(left), left) < (depth(right), right):
                return coefficient(right, left)
            return sum(coefficient(parent, right) for parent in parents) / 2 if parents else Fraction(0)

        by_generation = defaultdict(list)
        for pairing in shire["pairings"]:
            by_generation[pairing["offspring_generation"]].append(pairing)
            father, mother = pairing["father"], pairing["mother"]
            self.assertFalse(ancestors(father, 2) & ancestors(mother, 2))
            actual = coefficient(father, mother)
            self.assertEqual(actual, Fraction(pairing["coancestry"]))
            self.assertLessEqual(actual, Fraction(1, 32))
        for generation in range(2, 6):
            selected = [ipid for pair in by_generation[generation - 1] for ipid in pair["selected_for_breeding"]]
            males = [ipid for ipid in selected if self.by_ipid[ipid]["sex"] == "Male"]
            females = [ipid for ipid in selected if self.by_ipid[ipid]["sex"] == "Female"]
            costs = [[coefficient(male, female) for female in females] for male in males]
            optimum = min((max(costs[index][mate] for index, mate in enumerate(perm)),
                           sum(costs[index][mate] for index, mate in enumerate(perm)))
                          for perm in permutations(range(len(females))))
            actual = [coefficient(pair["father"], pair["mother"]) for pair in by_generation[generation]]
            self.assertEqual((max(actual), sum(actual)), optimum)

    def test_shire_roles_units_and_ringbearer_transfer_history_are_consistent(self):
        projects = self.domains[("projects", "catalog")]["projects"]
        history = self.domains[("projects", "history")]["projects"]
        shire = projects["Shire"]
        self.assertEqual(shire["summary"]["species"], "Mus musculus")
        self.assertEqual(shire["animals_config"]["approved_count"], 500)
        users = {user["username"]: user for user in self.domains[("security", "users")]}
        units = {unit["unit_id"] for unit in self.domains[("security", "organization-units")]["units"]}
        self.assertIn(shire["iacuc"]["unit"], units)
        assignments = {
            shire["iacuc"]["pi_login"]: "researcher",
            shire["iacuc"]["di_login"]: "vet",
            shire["iacuc"]["welfare_login"]: "animal_welfare_officer",
            shire["assoc_users"]["applicant_login"]: "manager",
            "Keeper": "keeper",
        }
        for login, job in assignments.items():
            self.assertIn(job, users[login]["jobs"])
            self.assertEqual(users[login]["role"], "user")
            self.assertEqual(users[login]["permissions"], {"granted": [], "revoked": []})
            self.assertIn(users[login]["unit_id"], units)
        self.assertEqual(users[shire["iacuc"]["pi_login"]]["unit_id"], shire["iacuc"]["unit"])
        ring = {self.by_name[name]["ipid"] for name in ("Frodo Baggins", "Sam Gamgee", "Merry Brandybuck", "Pippin Took")}
        former = {entry["ipid"]: entry for entry in history["Shire"]["animals"] if entry.get("date_left")}
        self.assertEqual(set(former), ring)
        for entry in history["Ringbearer"]["animals"]:
            self.assertEqual(entry["previous_project_snapshot"], [former[entry["ipid"]]])
            self.assertFalse(former[entry["ipid"]]["had_in_experiment"])
            animal = self.by_ipid[entry["ipid"]]
            self.assertEqual(animal["project_history"][0]["project"], "Shire")
            self.assertEqual(animal["project"], "Ringbearer")
            self.assertEqual(animal["rolle"], "experimental_animal")
        for animal in self.animals:
            if animal["species"] == "Mus musculus" and animal["ipid"] not in ring:
                self.assertEqual(animal["project"], "Shire")
                self.assertFalse(animal["in_experiment"])

    def test_shire_housing_is_not_one_overcrowded_mixed_sex_group(self):
        housing = self.domains[("housing", "cage")]
        cages = defaultdict(list)
        for animal in self.animals:
            if animal["species"] == "Mus musculus":
                occupant = housing["occupants"][animal["ipid"]]
                self.assertEqual(occupant["animal_id"], animal["id"])
                cage_id = occupant["cage_id"]
                self.assertEqual(housing["structures"]["cages"][cage_id]["parent_room_id"], "room_mus_colony")
                cages[cage_id].append(animal)
        for cage, members in cages.items():
            self.assertLessEqual(len(members), 5, cage)
            if len({animal["sex"] for animal in members}) > 1:
                self.assertEqual(len(members), 2, cage)
                self.assertEqual(members[0]["verpaart_mit"], members[1]["ipid"])
                self.assertEqual(members[1]["verpaart_mit"], members[0]["ipid"])

    def test_shire_validator_rejects_litter_selection_and_cap_regressions(self):
        from source.build_sample_seed import validate_shire_colony
        mice = {ipid: animal for ipid, animal in self.by_ipid.items() if animal["species"] == "Mus musculus"}
        shire = self.domains[("seed", "shire-colony")]
        self.assertEqual(validate_shire_colony(mice, shire), [])
        broken = copy.deepcopy(shire)
        broken["litters"][0]["offspring"] = broken["litters"][0]["offspring"][:9]
        self.assertTrue(any("10-12" in error for error in validate_shire_colony(mice, broken)))
        broken = copy.deepcopy(shire)
        broken["pairings"][0]["selected_for_breeding"] = broken["litters"][0]["offspring"][:5]
        self.assertTrue(any("four" in error for error in validate_shire_colony(mice, broken)))
        expanded = {**mice, **{f"extra-{index}": self.by_name["Rosie Cotton"] for index in range(30)}}
        self.assertTrue(any("500" in error for error in validate_shire_colony(expanded, shire)))

    def test_shire_authoring_is_deterministic(self):
        from source.build_sample_seed import add_mouse_colony, add_shire_colony
        baseline, keys = {"animals": {}, "archived_animals": {}}, {}
        add_mouse_colony(baseline, keys)
        first, second = copy.deepcopy(baseline), copy.deepcopy(baseline)
        self.assertEqual(add_shire_colony(first, keys.copy()), add_shire_colony(second, keys.copy()))
        self.assertEqual(first, second)

    def test_seed_event_ids_preserve_authored_ids_and_new_ids_are_deterministic(self):
        from source.build_sample_seed import assign_seed_event_ids
        sam = copy.deepcopy(self.by_name["Sam Gamgee"])
        previous_id = sam["events"][0].pop("event_id")
        core = {"animals": {sam["ipid"]: sam}, "archived_animals": {}}
        assign_seed_event_ids(core)
        self.assertEqual(sam["events"][0]["event_id"], previous_id)
        sam["events"].append({"event_type": "surgery", "datum": "2024-10-01"})
        other = copy.deepcopy(core)
        assign_seed_event_ids(core)
        assign_seed_event_ids(other)
        self.assertEqual(core, other)
        self.assertNotEqual(sam["events"][1]["event_id"], previous_id)

    def test_seed_import_into_disposable_sqlite_preserves_integrity_and_identity(self):
        from Plugins.core.backend.facade import ProgTrackBackend
        from Plugins.core.runtime_paths import resolve_runtime_paths
        with tempfile.TemporaryDirectory(prefix="shire-seed-test-") as temporary:
            app_root = Path(temporary) / "app"
            app_root.mkdir()
            paths = resolve_runtime_paths(app_root, environ={
                "PROGTRACK_PORTABLE": "1", "PROGTRACK_BACKEND_PROFILE": "standalone_sqlite",
                "PROGTRACK_SQLITE_PATH": str(Path(temporary) / "fixture.sqlite3"),
                "PROGTRACK_MANAGED_ROOT": str(Path(temporary) / "managed"),
            })
            backend = ProgTrackBackend(paths, acquire_process_lock=False)
            try:
                result = backend.interchange.import_package(SEED)
                self.assertTrue(result.valid, result.errors)
                loaded = backend.load_core_data()
                from source.seed.enrollments import enrollment_errors
                loaded_domains = {
                    key: backend.records.get(*key) for key in self.domains
                }
                self.assertEqual(enrollment_errors(loaded, loaded_domains), [])
                self.assertEqual(loaded_domains['reproduction', 'flow'], self.domains['reproduction', 'flow'])
                self.assertEqual(len(loaded["animals"]), 605)
                self.assertEqual(len(loaded["archived_animals"]), 90)
                self.assertEqual(set(loaded["animals"]) | set(loaded["archived_animals"]), set(self.by_ipid))
                for ipid, animal in loaded["animals"].items():
                    for field in ("samenspender", "eizellspenderin", "verpaart_mit", "partner_von"):
                        if animal.get(field):
                            self.assertIn(animal[field], self.by_ipid)
                with closing(sqlite3.connect(paths.database_path)) as connection:
                    self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
                    self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                    with self.assertRaises(sqlite3.IntegrityError):
                        connection.execute("UPDATE animals SET name=? WHERE ipid=?", ("Changed", self.by_name["Sam Gamgee"]["ipid"]))
                    self.assertEqual(connection.execute("SELECT name FROM animals WHERE ipid=?", (self.by_name["Sam Gamgee"]["ipid"],)).fetchone()[0], "Sam Gamgee")
            finally:
                backend.close()

    def test_boromir_and_faramir_keep_only_maternal_birth_chains(self):
        incompatible = {
            "embryo_transfer", "pregnancy_verification", "birth", "abortion"
        }
        for name in ("Boromir", "Faramir"):
            offspring = self.by_name[name]
            self.assertEqual(offspring["sex"], "Male")
            self.assertFalse(
                incompatible.intersection(
                    event.get("event_type")
                    for event in offspring.get("events", [])
                    if isinstance(event, dict)
                )
            )
            surrogate_ipid = offspring["ziehmutter"]
            self.assertIn(surrogate_ipid, self.by_ipid)
            maternal_births = [
                event
                for event in self.by_ipid[surrogate_ipid].get("events", [])
                if event.get("event_type") == "birth"
                and event.get("offspring") == offspring["ipid"]
            ]
            self.assertEqual(len(maternal_births), 1)
            expected_birth = datetime.strptime(
                offspring["birth_date"], "%d.%m.%Y"
            ).date().isoformat()
            self.assertEqual(maternal_births[0]["datum"], expected_birth)


class SeedCompilerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from source.seed.compiler import build_bundle
        cls.bundle = build_bundle()

    def test_316_compiled_and_packaged_seed_is_runtime_ready_without_first_save(self):
        from Plugins.core.project_periods import ensure_project_periods
        package, _domains = _package_records()
        records = [r for section in ('animals', 'archived_animals')
                   for r in self.bundle.core[section].values()]
        for record in records + [r['payload'] for r in package]:
            for field in ('chip_nr', 'special_status', 'sick_times'):
                self.assertIn(field, record)
            before = copy.deepcopy(record)
            ensure_project_periods(record)
            self.assertEqual(record, before, record['ipid'])

    def test_316_seed_gate_rejects_missing_creation_defaults_or_period_metadata(self):
        from source.seed.validation import validate
        for section in ('animals', 'archived_animals'):
            for field in ('chip_nr', 'special_status', 'sick_times', 'project_period_id'):
                with self.subTest(section=section, field=field):
                    broken = copy.deepcopy(self.bundle.core)
                    record = next(iter(broken[section].values()))
                    if field == 'project_period_id':
                        # Archived seed animals have no current project. A
                        # synthetic negative fixture exercises that gate too.
                        record['project'] = 'Backcrossing'
                        record['project_entry_date'] = '01.01.2025'
                    record.pop(field, None)
                    result = validate(broken, self.bundle.domains, self.bundle.scenario)
                    self.assertFalse(result['valid'])
                    self.assertTrue(any('Runtime-ready seed field missing' in e or
                                        'Unprepared seed project periods' in e for e in result['errors']))

    def test_experiment_enrollment_covers_all_current_animals_and_surfaces(self):
        from source.seed.enrollments import enrollment_errors, enrollment_rows
        self.assertEqual(enrollment_errors(self.bundle.core, self.bundle.domains), [])
        rows = list(enrollment_rows(self.bundle.core))
        self.assertEqual(len(rows), 19)
        self.assertEqual(self.bundle.report["counts"]["experimental_animals_with_enrollment_dates"], 19)
        for ipid, record, spec, entered, started, _actors in rows:
            with self.subTest(animal=record["name"]):
                self.assertEqual(record["project_entry_date"], entered.strftime("%d.%m.%Y"))
                self.assertLessEqual(entered, started)
                self.assertTrue(record["in_experiment"])
                self.assertEqual(record["project"], spec["project"])
                self.assertTrue(record["edits"][entered.isoformat()]["project_note"])
                self.assertTrue(record["edits"][started.isoformat()]["experiment_note"])

    def test_experiment_enrollment_preserves_completed_history_and_ringbearer_transfers(self):
        from source.seed.config import load_input
        from source.seed.common import parse_record_date
        from source.seed.projections import _normalise_project_history_entries
        by_name = {record["name"]: record for record in self.bundle.core["animals"].values()}
        azog = by_name["Azog"]
        authored = load_input("animals-active-papio")[azog["ipid"]]
        self.assertEqual(azog["lifecycle_events"], authored["lifecycle_events"])
        # Existing seed normalization excludes deleted Anode/Zucht projects;
        # this change must retain the complete supported Oakshield history.
        from Plugins.core.project_periods import ensure_project_periods
        expected = copy.deepcopy(authored)
        expected["project_history"] = _normalise_project_history_entries(authored["project_history"])
        project_catalog = self.bundle.domains[("projects", "catalog")]["projects"]
        for period in expected["project_history"]:
            project = project_catalog.get(period.get("project"), {})
            if project.get("project_id"):
                period["project_id"] = project["project_id"]
        # #316 already prepares period identities at seed creation. Compare
        # the complete prepared history, not a pre-normalization fixture.
        ensure_project_periods(expected)
        self.assertEqual(azog["project_history"], expected["project_history"])
        projects = self.bundle.domains[("projects", "history")]["projects"]
        for name in ("Frodo Baggins", "Sam Gamgee", "Merry Brandybuck", "Pippin Took"):
            record = by_name[name]
            with self.subTest(animal=name):
                current = next(entry for entry in projects["Ringbearer"]["animals"] if entry["ipid"] == record["ipid"])
                former = next(entry for entry in projects["Shire"]["animals"] if entry["ipid"] == record["ipid"])
                self.assertEqual(current["date_entered"], former["date_left"])
                self.assertEqual(record["project_history"][0]["leave_date"], current["date_entered"])
                self.assertEqual(current["previous_project_snapshot"], [former])
                self.assertLess(parse_record_date(current["date_entered"]), parse_record_date(record["events"][0]["datum"]))

    def test_experiment_enrollment_projection_is_idempotent(self):
        from source.seed.enrollments import apply_experiment_enrollments, project_experiment_enrollments
        core, domains = copy.deepcopy(self.bundle.core), copy.deepcopy(self.bundle.domains)
        before = copy.deepcopy((core, domains))
        for _ in range(2):
            apply_experiment_enrollments(core)
            project_experiment_enrollments(core, domains)
            self.assertEqual((core, domains), before)

    def test_experiment_enrollment_gate_rejects_bad_plans(self):
        from unittest.mock import patch
        from source.seed.config import load_input
        from source.seed.enrollments import apply_experiment_enrollments
        plan = load_input("experiment-enrollments")
        tiffany = next(ipid for ipid in plan["animals"] if ipid.startswith("Tiffany |"))
        for problem in ("missing", "before_birth", "before_project", "after_monitoring", "invalid"):
            bad = copy.deepcopy(plan)
            entry = bad["animals"][tiffany]
            if problem == "missing":
                del bad["animals"][tiffany]
            elif problem == "before_birth":
                entry["project_entered"] = "2000-01-01"
            elif problem == "before_project":
                entry["experiment_started"] = "2024-07-01"
            elif problem == "after_monitoring":
                entry["experiment_started"] = "2024-08-02"
            else:
                entry["experiment_started"] = "not-a-date"
            with self.subTest(problem=problem), patch("source.seed.enrollments.load_input", return_value=bad):
                with self.assertRaises(ValueError):
                    apply_experiment_enrollments(copy.deepcopy(self.bundle.core))

    def test_experiment_enrollment_gate_rejects_cross_plugin_drift(self):
        from source.seed.enrollments import enrollment_errors
        tiffany = next(ipid for ipid in self.bundle.core["animals"] if ipid.startswith("Tiffany |"))
        for component in ("core_project", "core_start", "project", "medical", "reports", "former"):
            core, domains = copy.deepcopy(self.bundle.core), copy.deepcopy(self.bundle.domains)
            record = core["animals"][tiffany]
            if component == "core_project":
                record["project_entry_date"] = "01.01.2026"
            elif component == "core_start":
                record["lifecycle_events"] = []
            elif component == "project":
                next(entry for entry in domains[("projects", "history")]["projects"]["OTOF-"]["animals"] if entry["ipid"] == tiffany)["date_entered"] = "01.01.2026"
            elif component == "medical":
                domains[("medical", "history")]["animals"][tiffany]["entries"] = []
            elif component == "reports":
                record["edits"] = {}
            else:
                record["project_history"] = [{"project": "Backcrossing", "leave_date": "01.01.2026"}]
            with self.subTest(component=component):
                self.assertTrue(enrollment_errors(core, domains))

    def test_all_manifest_and_compiled_event_occurrences_have_dates(self):
        from source.seed.validation import audit_seed_sources, event_date_audit
        from source.seed.config import MANIFEST
        source = audit_seed_sources()
        self.assertEqual(source["inputs_scanned"], len(MANIFEST["inputs"]))
        self.assertGreater(source["checked"], 27)
        self.assertEqual(source["errors"], [])
        compiled = event_date_audit({"core": self.bundle.core, "domains": self.bundle.domains})
        self.assertGreater(compiled["checked"], 151)
        self.assertEqual(compiled["errors"], [])

    def test_packaged_seed_occurrences_have_dates(self):
        from source.seed.validation import event_date_audit
        rows, domains = _package_records()
        result = event_date_audit({"animals": [row["payload"] for row in rows], "domains": domains})
        self.assertGreater(result["checked"], 151)
        self.assertEqual(result["errors"], [])

    def test_compiled_gate_rejects_orphan_before_domain_date_dereference(self):
        from source.seed.validation import validate
        for section in ("animals", "archived_animals"):
            broken = copy.deepcopy(self.bundle.core)
            ipid = next(iter(broken[section]))
            broken[section][ipid].setdefault("events", []).append({"event_type": "sperm_donation"})
            result = validate(broken, self.bundle.domains, self.bundle.scenario)
            self.assertFalse(result["valid"])
            self.assertIn(f"{section}.{ipid}.events", result["errors"][0])

    def test_seed_record_normalization_never_silently_discards_invalid_event(self):
        from source.seed.common import complete_record
        for event in (None, {"event_type": "surgery"},
                      {"event_type": "surgery", "datum": "2026-02-30"},
                      {"datum": "2026-01-01"}):
            record = {"events": [event]}
            before = copy.deepcopy(record)
            with self.assertRaisesRegex(ValueError, "Invalid event record|Missing or invalid event"):
                complete_record(record, name="Example", species="Callitrix jacchus",
                                birth="01.01.2020", origin="DPZ")
            self.assertEqual(record, before)

    def test_event_date_audit_rejects_missing_blank_invalid_and_non_record_events(self):
        from source.seed.validation import event_date_audit
        for section in ("animals", "archived_animals"):
            for date_value in (None, "", "2026-02-30", "2026-01-01garbage"):
                with self.subTest(section=section, value=date_value):
                    payload = {section: {"a": {"events": [{"event_type": "surgery", "datum": date_value}]}}}
                    before = copy.deepcopy(payload)
                    result = event_date_audit(payload)
                    self.assertEqual(len(result["errors"]), 1)
                    self.assertIn(f"{section}.a.events[0]", result["errors"][0])
                    self.assertEqual(payload, before)
        self.assertTrue(event_date_audit({"events": [None]})["errors"])
        self.assertTrue(event_date_audit({"nested": {"lifecycle_events": [{"event_type": "exit"}]}})["errors"])

    def test_plugin_occurrences_are_checked_but_definitions_and_optional_dates_are_not(self):
        from source.seed.validation import event_date_audit
        records = {"entries": [{"entry_type": "medical", "date": "10.01.2026"}],
                   "inspection": {"scope_type": "room", "cage_ids": [], "date": "10/01/2026"},
                   "sample": {"sample_number": "1", "animal_name": "a", "collection_date": "2026-01-10"},
                   "transfer": {"transfer_date": "2026-01-10", "embryos": [{"egg_donation_date": "2026-01-01", "sperm_donation_id": "2026-01-02"}]},
                   "event_recipe": {"available_events": ["surgery"]},
                   "membership": {"date_left": None}, "cycle": {"transfer_date": None, "embryos": 0},
                   "lifecycle_events": [{"event_type": "exit", "date": "2026-01-10T14:20:00+02:00"}]}
        self.assertEqual(event_date_audit(records)["errors"], [])
        for row, field in (("entries", "date"), ("inspection", "date"), ("sample", "collection_date"), ("transfer", "transfer_date")):
            broken = copy.deepcopy(records)
            target = broken[row][0] if row == "entries" else broken[row]
            del target[field]
            self.assertTrue(event_date_audit(broken)["errors"], row)

    def test_source_date_gate_runs_before_filtering_or_publication(self):
        from unittest.mock import patch
        from source.seed.compiler import build_bundle
        from source.seed.validation import audit_seed_sources
        original = __import__("source.seed.validation", fromlist=["load_input"]).load_input
        def missing_source_event(name):
            payload = original(name)
            if name == "animals-archived-mus":
                payload["orphan"] = {"events": [{"event_type": "surgery"}]}
            return payload
        with patch("source.seed.validation.load_input", side_effect=missing_source_event):
            self.assertIn("source:animals-archived-mus.orphan.events[0]", audit_seed_sources()["errors"][0])
            with patch("source.seed.compiler.normalize_core") as normalize:
                with self.assertRaisesRegex(ValueError, "source event dates failed"):
                    build_bundle()
                normalize.assert_not_called()

    def test_no_same_kind_same_day_measurements_in_active_or_archived_seed(self):
        from source.seed.validation import measurement_errors
        self.assertEqual(measurement_errors(self.bundle.core), [])

    def test_monitoring_post_pgf_is_one_numbered_reading(self):
        from source.seed.reproduction import monitoring
        from datetime import date, timedelta
        blood, urine, events = monitoring(date(2024, 8, 1), "TEST", donor=True)
        self.assertEqual((len(blood), len(urine), len(events)), (24, 24, 3))
        for pgf in events:
            stamp = (date.fromisoformat(pgf["datum"]) + timedelta(days=3)).isoformat()
            for readings, expected in ((blood, 4), (urine, 5)):
                matches = [r for r in readings if r["datum"] == stamp]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0]["wert"], expected)
                self.assertTrue(matches[0]["probennummer"])

    def test_measurement_calendar_duplicates_fail_but_other_kinds_and_events_survive(self):
        from source.seed.validation import measurement_errors
        core = {"animals": {"a": {"daten": [{"datum": "2026-01-01", "wert": 4}], "pdg": [{"datum": "2026-01-01", "wert": 5}], "events": [{"datum": "2026-01-01", "event_type": "a"}, {"datum": "2026-01-01", "event_type": "b"}]}}, "archived_animals": {}}
        self.assertEqual(measurement_errors(core), [])
        core["animals"]["a"]["daten"].append({"datum": "01.01.2026", "wert": 38})
        self.assertTrue(any("Duplicate" in e for e in measurement_errors(core)))

    def test_missing_malformed_undeclared_and_escaping_inputs_fail(self):
        from source.seed.config import load_input
        with tempfile.TemporaryDirectory(prefix="seed-input-test-") as temporary:
            folder = Path(temporary)
            manifest = {"schema": "progtrack-seed-source/1", "inputs": {"example": {"path": "example.json"}}}
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing or invalid"):
                load_input("example", folder)
            (folder / "example.json").write_text("broken", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing or invalid"):
                load_input("example", folder)
            with self.assertRaisesRegex(ValueError, "Undeclared"):
                load_input("not-declared", folder)
            manifest["inputs"]["example"]["path"] = "../escape.json"
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes"):
                load_input("example", folder)

    def test_event_ids_are_stable_for_logical_keys_without_previous_package(self):
        from source.seed.reproduction import assign_seed_event_ids
        first = {"animals": {"test": {"events": [{"event_type": "surgery", "datum": "2026-01-01", "_seed_key": "test/procedure"}]}}, "archived_animals": {}}
        second = copy.deepcopy(first)
        second["animals"]["test"]["events"][0].update(datum="2026-01-02", comment="corrected")
        assign_seed_event_ids(first)
        assign_seed_event_ids(second)
        self.assertEqual(first["animals"]["test"]["events"][0]["event_id"], second["animals"]["test"]["events"][0]["event_id"])

    def test_unrelated_animal_does_not_shift_fallback_sperm_collection(self):
        from source.seed.generators import complete_scientific_histories
        animal = copy.deepcopy(self.bundle.core["animals"][self.bundle.scenario["denethor"]])
        animal["sperm"], animal["events"] = [], []
        first = {"animals": {"Z-test": animal}, "archived_animals": {}}
        second = copy.deepcopy(first)
        other = copy.deepcopy(animal)
        other["rolle"] = "breeding_animal"
        second["animals"]["A-unrelated"] = other
        complete_scientific_histories(first)
        complete_scientific_histories(second)
        self.assertEqual(first["animals"]["Z-test"], second["animals"]["Z-test"])

    def test_every_core_transfer_has_explicit_donor_linked_embryos(self):
        from source.seed.validation import flow_errors
        flow = self.bundle.domains[("reproduction", "flow")]
        self.assertEqual(flow_errors(self.bundle.core, flow, self.bundle.scenario), [])
        core_ids = {f"transfer_{ipid}_{datetime.fromisoformat(e['datum']).isoformat()}" for ipid, a in self.bundle.core["animals"].items() for e in a.get("events", []) if e["event_type"] == "embryo_transfer"}
        self.assertEqual(set(flow["manual_data"]["transfers_by_id"]) - {"freezer_main"}, core_ids)
        self.assertEqual(flow["manual_data"]["total_embryo_count"], 21)

    def test_flow_validation_rejects_timing_link_identity_and_count_regressions(self):
        from source.seed.validation import flow_errors
        source = self.bundle.domains[("reproduction", "flow")]
        transfer_id = next(key for key in source["manual_data"]["transfers_by_id"] if key != "freezer_main")
        for field, value in (("transfer_date", "2020-01-01"), ("surrogate_name", "wrong")):
            broken = copy.deepcopy(source)
            broken["manual_data"]["transfers_by_id"][transfer_id][field] = value
            self.assertTrue(flow_errors(self.bundle.core, broken, self.bundle.scenario))
        for field, value in (("egg_donor_name", None), ("sperm_donation_id", "wrong"), ("embryo_id", "")):
            broken = copy.deepcopy(source)
            broken["manual_data"]["transfers_by_id"][transfer_id]["embryos"][0][field] = value
            self.assertTrue(flow_errors(self.bundle.core, broken, self.bundle.scenario))

    def test_complete_donor_stage_metrics_match_both_paths_and_all_collections(self):
        from source.seed.validation import donor_stage_errors
        manual = self.bundle.domains[("reproduction", "flow")]["manual_data"]
        self.assertEqual(donor_stage_errors(self.bundle.core, manual), [])
        self.assertEqual(len(manual["egg_donors"]), 6)
        self.assertEqual(sum(len(d["surgeries"]) for d in manual["egg_donors"].values()), 9)
        self.assertEqual(sum(len(d["donations"]) for d in manual["sperm_donors"].values()), 11)
        for cycle in self.bundle.scenario["cycles"]:
            egg = manual["egg_donors"][cycle["egg_donor"]]["surgeries"][cycle["egg_date"]]
            sperm = manual["sperm_donors"][cycle["sperm_donor"]]["donations"][cycle["sperm_date"]]
            self.assertEqual(egg["m1_isolated"], cycle["m1_isolated"])
            self.assertLessEqual(egg["m2_isolated"], 2)
            self.assertLessEqual(sperm["applied_to_in_vivo_m2"], egg["m2_isolated"])
            self.assertEqual(sperm["fertilized_in_vivo_m2"], egg["in_vivo_m2_fertilized"])
            self.assertEqual(sperm["applied_to_in_vitro_m2"], egg["m1_to_m2"])
            self.assertEqual(sperm["fertilized_in_vitro_m2"], egg["in_vitro_m2_fertilized"])
            self.assertEqual(sperm["embryos_from_in_vitro_m2"], egg["embryos_from_in_vitro_m2"])
            self.assertEqual((egg["embryos_from_in_vivo_m2"], sperm["embryos_from_in_vivo_m2"]), (0, 0))
        for transfer in manual["transfers_by_id"].values():
            self.assertTrue(all(e["stage"] == "in_vitro_m2" and "IVF" in e["comment"] for e in transfer["embryos"]))
        egg_rows = [r for d in manual["egg_donors"].values() for r in d["surgeries"].values()]
        self.assertEqual(sum(r["in_vitro_m2_fertilized"] for r in egg_rows), 38)
        self.assertEqual(sum(r["m2_isolated"] for r in egg_rows), 9)
        self.assertEqual(sum(r["in_vivo_m2_fertilized"] for r in egg_rows), 4)
        self.assertEqual(sum(r["embryos_from_in_vitro_m2"] for r in egg_rows), 21)

    def test_donor_stage_validation_rejects_missing_counts_overflow_and_in_vivo_embryos(self):
        from source.seed.validation import flow_errors
        source = self.bundle.domains[("reproduction", "flow")]
        cycle = self.bundle.scenario["cycles"][0]
        for group, date_field, donor_field, count_field, value in (
            ("egg_donors", "egg_date", "egg_donor", "m1_isolated", None),
            ("egg_donors", "egg_date", "egg_donor", "m2_isolated", 3),
            ("egg_donors", "egg_date", "egg_donor", "m1_to_m2", 50),
            ("egg_donors", "egg_date", "egg_donor", "in_vitro_m2_fertilized", 11),
            ("egg_donors", "egg_date", "egg_donor", "embryos_from_in_vivo_m2", 1),
            ("egg_donors", "egg_date", "egg_donor", "frozen_from_in_vitro_m2", 1),
            ("sperm_donors", "sperm_date", "sperm_donor", "applied_to_in_vivo_m2", 3),
            ("sperm_donors", "sperm_date", "sperm_donor", "fertilized_in_vivo_m2", 3),
            ("sperm_donors", "sperm_date", "sperm_donor", "fertilized_in_vitro_m2", 11),
            ("sperm_donors", "sperm_date", "sperm_donor", "embryos_from_in_vivo_m2", 1),
            ("sperm_donors", "sperm_date", "sperm_donor", "embryos_from_in_vitro_m2", 7),
            ("sperm_donors", "sperm_date", "sperm_donor", "applied_to_in_vitro_m2", True),
        ):
            with self.subTest(group=group, field=count_field):
                broken = copy.deepcopy(source)
                rows_key = "surgeries" if group == "egg_donors" else "donations"
                row = broken["manual_data"][group][cycle[donor_field]][rows_key][cycle[date_field]]
                if value is None:
                    row.pop(count_field)
                else:
                    row[count_field] = value
                self.assertTrue(flow_errors(self.bundle.core, broken, self.bundle.scenario))
        broken = copy.deepcopy(source)
        broken["manual_data"]["sperm_donors"][self.bundle.scenario["cycles"][-1]["sperm_donor"]]["donations"].pop("2025-09-18")
        self.assertTrue(flow_errors(self.bundle.core, broken, self.bundle.scenario))

    def test_legolas_cycle_keeps_parentage_and_reuses_thranduil_sample(self):
        b = self.bundle
        cycle = next(c for c in b.scenario["cycles"] if c["key"] == "svetlana-legolas")
        child = b.core["animals"][b.scenario["legolas"]]
        self.assertEqual((child["eizellspenderin"], child["samenspender"], child["ziehmutter"]), (cycle["egg_donor"], cycle["sperm_donor"], cycle["surrogate_ipid"]))
        self.assertEqual(child["birth_date"], "04.03.2026")
        self.assertEqual(b.core["animals"][cycle["egg_donor"]]["rolle"], "egg_cell_donor")
        samples = [s for s in b.core["animals"][cycle["sperm_donor"]]["sperm"] if str(s["datum"]).startswith("2025-10-02")]
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]["sample_id"], cycle["sample_id"])
        transfers = [e for e in b.core["animals"][cycle["surrogate_ipid"]]["events"] if e["event_type"] == "embryo_transfer"]
        self.assertEqual(len(transfers), 1)
        self.assertEqual(transfers[0]["datum"], "2025-10-10")

    def test_312_mixed_frozen_cycle_package_and_validation(self):
        from source.seed.validation import flow_errors
        flow = self.bundle.domains[("reproduction", "flow")]
        manual = flow['manual_data']
        frozen = manual['transfers_by_id']['freezer_main']
        self.assertEqual(len(frozen['embryos']), 2)
        self.assertIsNone(frozen['transfer_date'])
        cycle = next(c for c in self.bundle.scenario['cycles'] if c['key'] == 'tiffany-boromir')
        self.assertTrue(all(e['egg_donor_name'] == cycle['egg_donor'] and
                            e['sperm_donor_name'] == cycle['sperm_donor'] and
                            e['freeze_date'] == cycle['freeze_date'] and
                            e['cryopreserved'] and not e['implanted'] and
                            not e.get('offspring_ipid') for e in frozen['embryos']))
        all_embryos = [e for transfer in manual['transfers_by_id'].values() for e in transfer['embryos']]
        self.assertEqual(len({e['embryo_id'] for e in all_embryos}), 21)
        _animals, packaged = _package_records()
        self.assertEqual(packaged['reproduction', 'flow'], flow)
        for field, value in [('freeze_date', None), ('freeze_date', '2020-01-01'),
                             ('implanted', True), ('egg_donor_name', 'wrong')]:
            with self.subTest(field=field, value=value):
                broken = copy.deepcopy(flow)
                broken['manual_data']['transfers_by_id']['freezer_main']['embryos'][0][field] = value
                self.assertTrue(flow_errors(self.bundle.core, broken, self.bundle.scenario))

    def test_real_flow_event_reader_and_donor_metrics_accept_seed(self):
        from types import SimpleNamespace
        from Plugins.Flow_Track.flow_track_widget import FlowTrackWidget
        flow = FlowTrackWidget.__new__(FlowTrackWidget)
        roles = SimpleNamespace(SPENDER=SimpleNamespace(value="egg_cell_donor"), SAMENSP=SimpleNamespace(value="sperm_donor"), AMME=SimpleNamespace(value="surrogate"))
        flow.parent_app = SimpleNamespace(Role=roles, animals=self.bundle.core["animals"])
        flow.manual_data = copy.deepcopy(self.bundle.domains[("reproduction", "flow")]["manual_data"])
        flow.messages = {}
        flow._populate_events_from_progtrack()
        self.assertEqual({t["id"] for t in flow.embryo_transfers}, set(flow.manual_data["transfers_by_id"]) - {"freezer_main"})
        self.assertTrue(flow._validate_data()[0])
        for cycle in self.bundle.scenario["cycles"]:
            self.assertTrue(any(e["animal_name"] == cycle["egg_donor"] and e["date"].date().isoformat() == cycle["egg_date"] for e in flow.egg_surgeries))
        total = flow._calculate_sperm_donor_efficiency(self.bundle.scenario["denethor"])["total"]
        self.assertEqual(total["total_transferred_ivm"], 16)
        self.assertEqual(total["total_frozen_ivm"], 2)
        self.assertEqual(total["total_usable_ivm"], 18)
        self.assertEqual(total["total_ivm_m2_inseminated"], 51)
        self.assertEqual(total["total_ivm_m2_fertilized"], 34)
        self.assertEqual(total["total_in_vivo_m2_inseminated"], 7)
        self.assertEqual(total["total_in_vivo_m2_fertilized"], 3)
        self.assertAlmostEqual(total["fert_rate_in_vivo"], 3 / 7 * 100)
        other = flow._calculate_sperm_donor_efficiency(self.bundle.scenario["cycles"][-1]["sperm_donor"])
        self.assertEqual(len(other["per_donation"]), 4)
        self.assertEqual(other["total"]["total_ivm_m2_fertilized"], 4)
        self.assertEqual(other["total"]["total_in_vivo_m2_inseminated"], 1)
        self.assertEqual(other["total"]["total_in_vivo_m2_fertilized"], 1)
        self.assertEqual(flow._count_unlinked_embryos(self.bundle.scenario["denethor"]), 0)
        egg = flow._calculate_egg_donor_efficiency(self.bundle.scenario["donors"][0])["total"]
        self.assertEqual(egg["total_transferred_ivm"], 6)
        self.assertEqual(egg["total_frozen_ivm"], 2)
        self.assertEqual(egg["total_usable_ivm"], 8)

    def test_compiler_builds_clean_without_archive_or_previous_output_and_checks_repeatability(self):
        import os
        import shutil
        import subprocess
        with tempfile.TemporaryDirectory(prefix="seed-clean-checkout-") as temporary:
            checkout = Path(temporary)
            shutil.copytree(ROOT / "source" / "seed", checkout / "source" / "seed", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copyfile(ROOT / "source" / "build_sample_seed.py", checkout / "source" / "build_sample_seed.py")
            shutil.copytree(ROOT / "Plugins" / "core", checkout / "Plugins" / "core", ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "certificates"))
            embryo = checkout / "Plugins" / "Embryo_Track"
            embryo.mkdir()
            shutil.copyfile(ROOT / "Plugins" / "Embryo_Track" / "cranimetry_reference.json", embryo / "cranimetry_reference.json")
            env = {**os.environ, "PYTHONPATH": str(checkout), "PROGTRACK_LEGACY_JSON_ARCHIVE": str(checkout / "missing"), "PROGTRACK_SEED_EVENT_REFERENCE": str(checkout / "missing.ptdb")}
            command = [__import__('sys').executable, str(checkout / "source" / "build_sample_seed.py")]
            first = subprocess.run(command, cwd=checkout, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            output = checkout / "Resources" / "Seed"
            before = {p.name: p.read_bytes() for p in output.iterdir()}
            second = subprocess.run(command + ["--check"], cwd=checkout, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertEqual(before, {p.name: p.read_bytes() for p in output.iterdir()})
            (output / "SCENARIO_COVERAGE.md").write_text("stale", encoding="utf-8")
            failure = subprocess.run(command + ["--check"], cwd=checkout, env=env, capture_output=True, text=True, timeout=120)
            self.assertNotEqual(failure.returncode, 0)
            self.assertEqual((output / "SCENARIO_COVERAGE.md").read_text(), "stale")

    def test_real_flow_renderer_draws_both_donor_connections_for_all_embryos(self):
        from types import SimpleNamespace
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        import matplotlib.path
        import matplotlib.patches
        from Plugins.Flow_Track.flow_track_widget import FlowTrackWidget
        flow = FlowTrackWidget.__new__(FlowTrackWidget)
        roles = SimpleNamespace(SPENDER=SimpleNamespace(value="egg_cell_donor"), SAMENSP=SimpleNamespace(value="sperm_donor"), AMME=SimpleNamespace(value="surrogate"))
        flow.parent_app = SimpleNamespace(Role=roles, animals=self.bundle.core["animals"], matplotlib=matplotlib)
        flow.manual_data = copy.deepcopy(self.bundle.domains[("reproduction", "flow")]["manual_data"])
        flow.messages = {}
        flow.settings = {"render_full_graph": True, "show_lifebars": False, "show_animal_names": False, "freezer_visible": False}
        flow.temp_positions, flow.embryo_visibility = {}, {}
        flow.node_artists, flow.embryo_artists = {}, {}
        figure = Figure(figsize=(12, 8))
        canvas = FigureCanvasAgg(figure)
        flow.ax = figure.add_subplot(111)
        before = copy.deepcopy(flow.manual_data)
        flow._populate_events_from_progtrack()
        flow._render_flow_track()
        canvas.draw()
        self.assertEqual(len(flow.embryo_artists), 19)
        self.assertEqual(len(flow.ax.patches), 57)
        colors = Counter(tuple(p.get_edgecolor()[:3]) for p in flow.ax.patches)
        from matplotlib.colors import to_rgb
        for color in ("deeppink", "black", "mediumpurple"):
            self.assertEqual(colors[to_rgb(color)], 19)
        flow.settings['freezer_visible'] = True
        flow.embryo_visibility['FREEZER'] = True
        flow.canvas = canvas
        flow.stack = SimpleNamespace(setCurrentWidget=lambda *_: None)
        flow.content_widget = object()
        flow.current_xlim, flow.current_ylim = (0, 1), (0, 1)
        flow.lifebar_data = {}
        flow._redraw_canvas()  # Includes the production owner's axes/registry cleanup.
        self.assertEqual(len(flow.embryo_artists), 21)
        colors = Counter(tuple(p.get_edgecolor()[:3]) for p in flow.ax.patches)
        for color in ('deeppink', 'black'):
            self.assertEqual(colors[to_rgb(color)], 21)
        self.assertEqual(flow.manual_data, before)

    def test_housing_defaults_preserve_placements_independent_of_fixture_key_order(self):
        from source.seed.config import load_input, DEFAULT_HOUSING_CAGES
        from source.seed.projections import complete_housing
        original = load_input("housing-cage")
        reversed_fixture = copy.deepcopy(original)
        cages = reversed_fixture["structures"]["cages"]
        reversed_fixture["structures"]["cages"] = dict(reversed(list(cages.items())))
        expected = self.bundle.domains[("housing", "cage")]
        self.assertEqual(complete_housing(reversed_fixture, self.bundle.core["animals"]), expected)
        for ipid, animal in self.bundle.core["animals"].items():
            if animal["species"] == "Mus musculus":
                continue
            previous = original["occupants"].get(ipid)
            cage = previous["cage_id"] if previous and previous["cage_id"] != "cage_unassigned" else DEFAULT_HOUSING_CAGES[animal["species"]]
            self.assertEqual(expected["occupants"][ipid]["cage_id"], cage)

    def test_project_role_list_order_is_explicit_not_mapping_order(self):
        from unittest.mock import patch
        from source.seed import projections
        from source.seed.config import load_input, PROJECT_DATA_PROFILES
        profiles = copy.deepcopy(PROJECT_DATA_PROFILES)
        for profile in profiles.values():
            profile["roles"] = dict(reversed(list(profile["roles"].items())))
        catalog = load_input("projects-catalog")
        with patch.object(projections, "PROJECT_DATA_PROFILES", profiles):
            projections._enrich_project_catalog(catalog, load_input("projects-history"), self.bundle.core)
        for name, profile in profiles.items():
            self.assertEqual(catalog["projects"][name]["animals_config"]["roles"], self.bundle.domains[("projects", "catalog")]["projects"][name]["animals_config"]["roles"])
            self.assertEqual([r["role"] for r in catalog["projects"][name]["animals_config"]["roles"]], profile["role_order"])
        profiles["OTOF-"]["role_order"].append("surrogate")
        with patch.object(projections, "PROJECT_DATA_PROFILES", profiles), self.assertRaises(ValueError):
            projections._enrich_project_catalog(catalog, load_input("projects-history"), self.bundle.core)

    def test_pinned_medical_witnesses_do_not_change_when_population_order_changes(self):
        from source.seed.projections import add_resolved_histories
        core = copy.deepcopy(self.bundle.core)
        med, reports = add_resolved_histories({}, {}, core, self.bundle.scenario["shire"])
        core["animals"]["AAAA-unrelated"] = copy.deepcopy(next(iter(core["animals"].values())))
        other_med, other_reports = add_resolved_histories({}, {}, core, self.bundle.scenario["shire"])
        self.assertEqual((med, reports), (other_med, other_reports))

if __name__ == "__main__":
    unittest.main()
