from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import unittest
import zipfile
from pathlib import Path
from typing import Any

from Plugins.core.animal_identity import animal_identity_key
from source.build_sample_seed import normalized_example_public_id


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb"
SPECIES = {"Callitrix jacchus", "Macaca mulatta", "Papio hamadryas anubis", "Mus musculus"}
OLD_NAMES = (
    "Ad\u00e8le", "Anair\u00eb", "B\u00e9atrice", "Celebr\u00edan", "C\u00e9cile",
    "D\u00e1in I", "D\u00e1in II", "D\u00eds", "Elenw\u00eb", "Elur\u00e9d", "Elur\u00edn",
    "E\u00e4rendil", "E\u00e4rwen", "E\u00f6l", "Finw\u00eb", "Fr\u00f3r", "F\u00ebanor",
    "F\u00edli", "Ga\u00eblle", "Gis\u00e8le", "Gl\u00f3in I", "Gl\u00f3in II",
    "Grishn\u00e1kh", "Gr\u00f3in", "Gr\u00f3r", "Honor\u00e9", "H\u00e9l\u00e8ne", "K\u00edli",
    "L\u00fathien", "Mauh\u00far", "M\u00edriel", "No\u00e9mie", "N\u00e1in I", "N\u00e1in II",
    "N\u00e1in III", "R\u00fcdiger", "Thr\u00e1in I", "Thr\u00e1in II", "Thr\u00f3r",
    "Ugl\u00fak", "\u00c9lodie", "\u00cdrim\u00eb", "\u00d3in I", "\u00d3in II",
)


def ascii_name(value: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", value)
        if not unicodedata.combining(character)
    )


def walk_strings(value: Any):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk_strings(key)
            yield from walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)
    elif isinstance(value, str):
        yield value


def package_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(SEED) as archive:
        for name in archive.namelist():
            if name.endswith(".jsonl"):
                rows.extend(
                    json.loads(line)
                    for line in archive.read(name).decode("utf-8").splitlines()
                    if line.strip()
                )
    return rows


def db_strings(database_path: Path) -> list[str]:
    values: list[str] = []
    with sqlite3.connect(database_path) as conn:
        tables = [row[0] for row in conn.execute("select name from sqlite_master where type='table'")]
        for table in tables:
            columns = [row[1] for row in conn.execute(f"pragma table_info([{table}])")]
            if not columns:
                continue
            select = ", ".join(f"[{column}]" for column in columns)
            for row in conn.execute(f"select {select} from [{table}]"):
                values.extend(str(value) for value in row if isinstance(value, str))
    return values


class ExampleAnimalNameNormalizationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.disposable_seed_backend import open_disposable_seed_backend

        cls._temporary, cls.backend = open_disposable_seed_backend()
        database_path = cls.backend.paths.database_path
        cls.database_path = database_path
        cls.rows = package_rows()
        cls.seed_animals = {
            row["ipid"]: row["payload"]
            for row in cls.rows
            if row.get("namespace") == "core" and row.get("record_id") == "animals"
        }
        if not cls.seed_animals:
            cls.seed_animals = {
                row["ipid"]: row["payload"]
                for row in cls.rows
                if isinstance(row.get("payload"), dict) and row["payload"].get("name")
            }
        with sqlite3.connect(database_path) as conn:
            columns = [row[1] for row in conn.execute("pragma table_info(animals)")]
            cls.db_animals = {
                row[0]: dict(zip(columns, row))
                for row in conn.execute("select * from animals")
            }
        cls.backend_values = db_strings(database_path)

    @classmethod
    def tearDownClass(cls):
        cls.backend.close()
        cls._temporary.cleanup()

    def test_seed_and_backend_have_no_accented_animal_names(self):
        seed_names = [str(payload.get("name", "")) for payload in self.seed_animals.values()]
        db_names = [str(record.get("name", "")) for record in self.db_animals.values()]
        self.assertEqual(len([name for name in seed_names if any(ord(ch) > 127 and ch.isalpha() for ch in name)]), 0)
        self.assertEqual(len([name for name in db_names if any(ord(ch) > 127 and ch.isalpha() for ch in name)]), 0)
        self.assertEqual({ascii_name(old) for old in OLD_NAMES}, {name for name in seed_names if name in {ascii_name(old) for old in OLD_NAMES}})
        self.assertEqual(len(OLD_NAMES), 44)

    def test_corrected_ids_and_identity_keys_are_consistent(self):
        expected_names = {ascii_name(old) for old in OLD_NAMES}
        seen = set()
        for ipid, payload in self.seed_animals.items():
            name = str(payload.get("name", ""))
            if name not in expected_names:
                continue
            seen.add(name)
            expected_ipid = animal_identity_key(name, payload["species"], payload["birth_date"], payload["origin"])
            self.assertEqual(ipid, expected_ipid)
            expected_id = normalized_example_public_id(
                payload.get("id"), name=name, species=payload["species"],
                birth_date=payload["birth_date"], sex=payload.get("sex", ""),
                sequence=payload.get("generated_id_sequence"),
                absolute_sequence=payload.get("generated_id_absolute_sequence"),
            )
            self.assertEqual(payload.get("id"), expected_id)
        self.assertEqual(len(seen), 44)

        db_names = {str(record.get("name", "")) for record in self.db_animals.values()}
        self.assertTrue(expected_names <= db_names)
        for ipid, record in self.db_animals.items():
            payload = json.loads(record["record_json"])
            self.assertEqual(record["ipid"], payload.get("ipid"))
            self.assertEqual(record["name"], payload.get("name"))
            if record["name"] in expected_names:
                self.assertEqual(record["ipid"], animal_identity_key(record["name"], record["species"], record["birth_date"], record["origin"]))
                self.assertEqual(
                    payload.get("id"),
                    normalized_example_public_id(
                        payload.get("id"), name=record["name"], species=record["species"],
                        birth_date=record["birth_date"], sex=payload.get("sex", ""),
                        sequence=payload.get("generated_id_sequence"),
                        absolute_sequence=payload.get("generated_id_absolute_sequence"),
                    ),
                )

    def test_old_names_and_ids_are_absent_from_all_seed_and_backend_payloads(self):
        old_tokens = set(OLD_NAMES)
        for ipid, payload in self.seed_animals.items():
            name = payload["name"]
            if name not in {ascii_name(old) for old in OLD_NAMES}:
                continue
            prefix = "_".join(str(payload["id"]).split("_")[:4])
            old_token = re.sub(r"[^A-Za-z0-9]+", "_", next(old for old in OLD_NAMES if ascii_name(old) == name)).strip("_")
            old_tokens.add(prefix + "_" + old_token)
            old_tokens.add(next(old for old in OLD_NAMES if ascii_name(old) == name) + " | " + payload["species"] + " | " + payload["birth_date"] + " | " + payload["origin"])
        seed_values = []
        with zipfile.ZipFile(SEED) as archive:
            for archive_name in archive.namelist():
                if archive_name.endswith((".json", ".jsonl")):
                    seed_values.append(archive.read(archive_name).decode("utf-8"))
        backend_values = self.backend_values
        token_pattern = re.compile(
            r"(?<![A-Za-z0-9])(?:"
            + "|".join(
                re.escape(token)
                for token in sorted(old_tokens, key=len, reverse=True)
            )
            + r")(?![A-Za-z0-9])"
        )
        for label, corpus in (
            ("seed", "\n".join(seed_values)),
            ("backend", "\n".join(backend_values)),
        ):
            match = token_pattern.search(corpus)
            self.assertIsNone(
                match,
                (
                    f"legacy identity token {match.group(0)!r} remains in {label}"
                    if match
                    else ""
                ),
            )

    def test_full_identity_references_resolve_and_foreign_keys_are_valid(self):
        seed_keys = set(self.seed_animals)
        db_keys = set(self.db_animals)
        # Updating the initialization package does not mutate an already
        # initialized runtime. Resolve each dataset against its own identities;
        # disposable package-import coverage lives in the seed cleanup suite.
        identity_pattern = re.compile(
            r"^[^|{}\[\]\"\':,]+ \| (?:Callitrix jacchus|Macaca mulatta|"
            r"Papio hamadryas anubis|Mus musculus) \| \d{2}\.\d{2}\.\d{4} \| [^|{}\[\]\"\':,]+$"
        )
        package_identity_values: list[str] = []
        for row in self.rows:
            if isinstance(row.get("ipid"), str):
                package_identity_values.append(row["ipid"])
            payload = row.get("payload")
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except (TypeError, ValueError):
                    payload = None
            if isinstance(payload, (dict, list)):
                package_identity_values.extend(walk_strings(payload))
        for value in package_identity_values:
            if identity_pattern.fullmatch(value):
                self.assertIn(value, seed_keys, value)
        for value in self.backend_values:
            if identity_pattern.fullmatch(value):
                self.assertIn(value, db_keys, value)
        with sqlite3.connect(self.database_path) as conn:
            self.assertEqual(conn.execute("pragma foreign_key_check").fetchall(), [])
            self.assertIsNotNone(conn.execute("select sql from sqlite_master where type='trigger' and name='animals_identity_immutable'").fetchone())


if __name__ == "__main__":
    unittest.main()
