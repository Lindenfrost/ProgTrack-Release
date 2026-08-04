"""Build the single fictional ProgTrack 0.2.1 backend seed.

Archived legacy JSON files are optional seed-authoring inputs only. The
generated interchange package is the sole runtime initialization source and
is imported through the same backend-neutral service used for complete-
installation transfers. New sample-data edits should be made directly in the
backend/seed package; this reader exists only to rebuild a seed from the
archived baseline when explicitly needed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
LEGACY_ARCHIVE_ROOT = Path(
    os.environ.get(
        "PROGTRACK_LEGACY_JSON_ARCHIVE",
        str(ROOT.parent / "Archive" / "ProgTrack-legacy-json"),
    )
).expanduser().resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from Plugins.core.animal_identity import animal_identity_key
from Plugins.core.backend.facade import ProgTrackBackend
from Plugins.core.runtime_paths import resolve_runtime_paths


OUTPUT = ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb"
REPORT = ROOT / "Resources" / "Seed" / "integrity_report.json"
MATRIX = ROOT / "Resources" / "Seed" / "SCENARIO_COVERAGE.md"
SEED_CREATED = "2026-07-30T00:00:00+00:00"
SEED_PACKAGE_ID = "8f99615e-13b0-52c8-b778-d7d51efb8b74"
ELDARION_BIRTH_DATE = date(2026, 3, 1)

RETRIEVAL_DATES = (
    date(2024, 12, 1),
    date(2025, 1, 10),
    date(2025, 2, 12),
    date(2025, 3, 15),
)
TRANSFER_SCENARIOS = (
    (0, date(2024, 10, 18), "birth", "boromir"),
    (1, date(2025, 11, 25), "birth", "faramir"),
    (2, date(2025, 1, 20), "not-pregnant", ""),
    (2, date(2025, 4, 20), "not-pregnant", ""),
    (3, date(2024, 10, 10), "abortion", ""),
    (3, date(2025, 3, 10), "abortion", ""),
)

CANONICAL_USERS = (
    {
        "username": "Admin", "display_name": "Administrator Administratorson",
        "role": "lord", "jobs": [], "pronouns": "Mr.",
        "email": "admin@dpz.eu", "phone": "-000",
        "mobile": "0176234234234", "unit": "IT",
        "profession": "Software engineer", "created_at": "2026-04-16",
    },
    {
        "username": "Researcher", "display_name": "Dr. Researcher Sciencedottir",
        "role": "user", "jobs": ["researcher"], "pronouns": "Mrs.",
        "email": "res@dpz.eu", "phone": "-111",
        "mobile": "01753457685634", "unit": "TTS",
        "profession": "Biologist", "created_at": "2026-04-16",
    },
    {
        "username": "Vet", "display_name": "Dr. Veterinary Medicinsson",
        "role": "user", "jobs": ["vet"], "pronouns": "Mr.",
        "email": "vet@dpz.eu", "phone": "-222",
        "mobile": "0176345345475456", "unit": "HUS",
        "profession": "Veterinary surgeon", "created_at": "2026-04-16",
    },
    {
        "username": "Manager", "display_name": "Dr. Manager Plansdottir",
        "role": "user", "jobs": ["manager"], "pronouns": "Ms.",
        "email": "man@dpz.eu", "phone": "-333",
        "mobile": "0146234234345235", "unit": "HUS",
        "profession": "Biologist", "created_at": "2026-04-16",
    },
    {
        "username": "Keeper", "display_name": "Keeper Breedsson",
        "role": "user", "jobs": ["keeper"], "pronouns": "Mr.",
        "email": "keep@dpz.eu", "phone": "-444",
        "mobile": "01782342343463425", "unit": "HUS",
        "profession": "Animal caretaker", "created_at": "2026-04-16",
    },
    {
        "username": "Tester", "display_name": "Tester Aitisson",
        "role": "user", "jobs": ["tester"], "pronouns": "Mr.",
        "email": "test@dpz.eu", "phone": "-999",
        "mobile": "016745346234234", "unit": "IT",
        "profession": "Software engineer", "created_at": "2026-04-16",
    },
    {
        "username": "Veti", "display_name": "Dr. Veterinary Medicinsdottir",
        "role": "animal_welfare_officer", "jobs": ["vet"], "pronouns": "",
        "email": "veti@dpz.eu", "phone": "", "mobile": "", "unit": "HUS",
        "profession": "Veterinary Surgeon", "created_at": "2026-07-22",
    },
)


def load_json(relative: str, default: Any) -> Any:
    """Read an archived legacy authoring input, never a runtime JSON store."""
    try:
        return json.loads((LEGACY_ARCHIVE_ROOT / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return copy.deepcopy(default)


def dated_weights(birth: str, species: str, sex: str) -> list[dict[str, Any]]:
    born = datetime.strptime(birth, "%d.%m.%Y")
    if species == "Mus musculus":
        values = [1.5, 5.5, 11.0, 18.0, 23.0, 27.0]
        offsets = [0, 7, 14, 28, 56, 120]
    elif species.startswith("Callitrix"):
        adult = 440.0 if sex == "Female" else 480.0
        values = [37, 78, 108, 155, 210, 285, 370, adult]
        offsets = [0, 21, 45, 90, 150, 240, 365, 600]
    elif species.startswith("Macaca"):
        adult = 6500.0 if sex == "Female" else 9000.0
        values = [480, 760, 1200, 2100, 3500, 5000, adult]
        offsets = [0, 30, 90, 180, 365, 730, 1460]
    else:
        adult = 14000.0 if sex == "Female" else 22000.0
        values = [800, 1300, 2400, 4500, 8000, 12000, adult]
        offsets = [0, 30, 90, 180, 365, 730, 1460]
    return [
        {"datum": (born + timedelta(days=offset)).isoformat(), "wert": value}
        for offset, value in zip(offsets, values)
    ]


def complete_record(record: dict[str, Any], *, name: str, species: str,
                    birth: str, origin: str) -> dict[str, Any]:
    result = copy.deepcopy(record)
    result.update({
        "name": name,
        "_base_name": name,
        "display_name": name,
        "species": species,
        "birth_date": birth,
        "origin": origin,
    })
    result.setdefault("rolle", "breeding_animal")
    result.setdefault("sex", "Unknown")
    result.setdefault("events", [])
    result.setdefault("daten", [])
    result.setdefault("pdg", [])
    result.setdefault("sperm", [])
    result.setdefault("sick", False)
    result.setdefault("abnormal_current", False)
    if not result.get("gewicht"):
        result["gewicht"] = dated_weights(birth, species, result["sex"])
    return result


def normalize_core() -> tuple[dict[str, Any], dict[str, str]]:
    source = load_json("progtrack_daten.json", {"animals": {}, "archived_animals": {}})
    output = {"version": "5.0", "animals": {}, "archived_animals": {},
              "settings": {"language": "en", "seed_version": "0.2.1"}}
    key_map: dict[str, str] = {}
    for section in ("animals", "archived_animals"):
        for old_key, raw in sorted(source.get(section, {}).items()):
            record = dict(raw)
            name = str(record.get("name") or old_key.split(" | ")[0]).strip()
            species = str(record.get("species") or "").strip()
            if species == "Unknown species":
                species = "Mus musculus"
            birth = str(record.get("birth_date") or old_key.split(" | ")[-1]).strip()
            origin = str(record.get("origin") or "Sample Facility").strip()
            new_key = animal_identity_key(name, species, birth, origin)
            key_map[old_key] = new_key
            record = complete_record(
                record, name=name, species=species, birth=birth, origin=origin
            )
            if name == "Olga":
                for field in ("daten", "pdg"):
                    record[field] = [
                        value for value in record.get(field, [])
                        if not str(value.get("datum", "")).startswith("2026-07-01")
                    ]
            record["ipid"] = new_key
            record["species"] = species
            output[section][new_key] = record
    return output, key_map


def rewrite_references(value: Any, key_map: dict[str, str]) -> Any:
    if isinstance(value, str):
        return key_map.get(value, value)
    if isinstance(value, list):
        return [rewrite_references(item, key_map) for item in value]
    if isinstance(value, dict):
        return {
            key_map.get(str(key), str(key)): rewrite_references(item, key_map)
            for key, item in value.items()
        }
    return value


def add_animal(core: dict[str, Any], key_map: dict[str, str], name: str,
               birth: str, sex: str, role: str, *, origin: str = "DPZ",
               parent_f: str = "", parent_m: str = "") -> str:
    species = "Callitrix jacchus"
    key = animal_identity_key(name, species, birth, origin)
    record = complete_record({
        "rolle": role,
        "id": f"cj_{birth[-2:]}_seed_{name.lower().replace(' ', '_')}",
        "sex": sex,
        "genotype": "WT/WT",
        "eizellspenderin": parent_f,
        "samenspender": parent_m,
        "project": "OTOF-" if role in {"sperm_donor", "egg_cell_donor", "surrogate"} else "Zucht",
        "in_experiment": role in {"sperm_donor", "egg_cell_donor", "surrogate"},
    }, name=name, species=species, birth=birth, origin=origin)
    record["ipid"] = key
    core["animals"][key] = record
    key_map[key] = key
    return key


def add_mouse_animal(
    core: dict[str, Any],
    key_map: dict[str, str],
    name: str,
    birth: str,
    sex: str,
    role: str,
    *,
    parent_f: str = "",
    parent_m: str = "",
    project: str = "Zucht",
    in_experiment: bool = False,
    partner: str = "",
) -> str:
    """Add one deterministic Mus musculus example record."""
    species = "Mus musculus"
    origin = "DPZ"
    key = animal_identity_key(name, species, birth, origin)
    record = complete_record({
        "rolle": role,
        "id": f"mm_{birth[-4:]}_{name.lower().replace(' ', '_')}",
        "sex": sex,
        "genotype": "WT/WT",
        "eizellspenderin": parent_f,
        "samenspender": parent_m,
        "project": project,
        "in_experiment": bool(in_experiment),
        "partner_von": partner,
        "verpaart_mit": partner,
        "events": [{"typ": "birth", "datum": datetime.strptime(
            birth, "%d.%m.%Y"
        ).date().isoformat()}],
    }, name=name, species=species, birth=birth, origin=origin)
    record["ipid"] = key
    core["animals"][key] = record
    key_map[key] = key
    return key


def add_mouse_colony(core: dict[str, Any], key_map: dict[str, str]) -> dict[str, Any]:
    """Extend the seed with a connected, species-appropriate mouse colony.

    Existing Mus musculus sample records remain as disposable examples but are
    normalized to breeding animals. The new Tolkien-family cohort provides
    ancestry, partners, the Ringbearer experiment, and a deliberately single
    Bilbo housing exception for Cage Track.
    """
    for record in {**core.get("animals", {}), **core.get("archived_animals", {})}.values():
        if str(record.get("species") or "").strip() == "Mus musculus":
            record["rolle"] = "breeding_animal"
            record["role_id"] = "breeding_animal"
            record["project"] = "Zucht"
            record["in_experiment"] = False

    pairs = [
        ("Bilbo", "10.01.2021", "Male", "Belladonna", "12.01.2021", "Female"),
        ("Drogo", "01.04.2021", "Male", "Primula", "03.04.2021", "Female"),
        ("Saradoc", "01.07.2021", "Male", "Esmeralda", "03.07.2021", "Female"),
        ("Paladin", "01.10.2021", "Male", "Eglantine", "03.10.2021", "Female"),
        ("Hamfast", "01.12.2021", "Male", "Rosie", "03.12.2021", "Female"),
        ("Otho", "01.02.2022", "Male", "Lobelia", "03.02.2022", "Female"),
    ]
    parents: dict[str, tuple[str, str]] = {}
    for male, male_birth, _male_sex, female, female_birth, _female_sex in pairs:
        male_key = add_mouse_animal(core, key_map, male, male_birth, "Male", "breeding_animal")
        female_key = add_mouse_animal(core, key_map, female, female_birth, "Female", "breeding_animal")
        core["animals"][male_key]["verpaart_mit"] = female_key
        core["animals"][female_key]["partner_von"] = male_key
        parents[male] = (male_key, female_key)

    children = [
        ("Frodo", "10.01.2024", "Male", "Bilbo", True),
        ("Sam", "10.02.2024", "Male", "Hamfast", True),
        ("Merry", "10.03.2024", "Male", "Saradoc", True),
        ("Pippin", "10.04.2024", "Male", "Paladin", True),
        ("Fredegar", "10.05.2024", "Male", "Drogo", False),
        ("Elanor", "10.06.2024", "Female", "Otho", False),
        ("Diamond", "10.07.2024", "Female", "Otho", False),
    ]
    child_keys: dict[str, str] = {}
    for name, birth, sex, parent_name, experimental in children:
        father, mother = parents[parent_name]
        child_keys[name] = add_mouse_animal(
            core, key_map, name, birth, sex,
            "experimental_animal" if experimental else "breeding_animal",
            parent_f=mother,
            parent_m=father,
            project="Ringbearer" if experimental else "Zucht",
            in_experiment=experimental,
        )

    partner_specs = [
        ("Giulia", "10.01.2024", "Female", "Frodo"),
        ("Marco", "10.02.2024", "Male", "Sam"),
        ("Chiara", "10.03.2024", "Female", "Merry"),
        ("Luca", "10.04.2024", "Male", "Pippin"),
        ("Giovanni", "10.05.2024", "Male", "Fredegar"),
        ("Sofia", "10.06.2024", "Female", "Elanor"),
        ("Elena", "10.07.2024", "Female", "Diamond"),
    ]
    for name, birth, sex, paired_name in partner_specs:
        paired_key = child_keys[paired_name]
        partner_key = add_mouse_animal(
            core, key_map, name, birth, sex, "breeding_animal",
            project="Zucht", partner=paired_key,
        )
        if sex == "Male":
            core["animals"][paired_key]["partner_von"] = partner_key
        else:
            core["animals"][paired_key]["verpaart_mit"] = partner_key

    return {"experimental": [child_keys[name] for name in ("Frodo", "Sam", "Merry", "Pippin")],
            "bilbo": next(key for key in core["animals"] if key.startswith("Bilbo | Mus musculus |"))}


def normalize_mouse_public_ids(core: dict[str, Any]) -> None:
    """Apply the configured public-ID shape to every mouse in the seed.

    The mouse scenario was initially authored with ad-hoc ``mm_2021_name``
    values.  Public IDs follow the same convention as the Callitrix examples:
    species token, two-digit birth year, four-digit birth-year sequence, sex,
    and an ID-safe animal name.  Archived mice are included in the sequence so
    the complete fictional dataset remains deterministic and collision-free.
    """
    all_animals = {
        **core.get("animals", {}),
        **core.get("archived_animals", {}),
    }
    mice = [
        (ipid, record)
        for ipid, record in all_animals.items()
        if str(record.get("species") or "").strip() == "Mus musculus"
    ]
    counters: dict[str, int] = {}
    for _ipid, record in sorted(
        mice,
        key=lambda item: (
            parse_record_date(item[1].get("birth_date")) or date.max,
            str(item[1].get("name") or "").casefold(),
            str(item[1].get("sex") or "").casefold(),
            str(item[0]),
        ),
    ):
        birth = parse_record_date(record.get("birth_date"))
        if birth is None:
            continue
        year = f"{birth.year % 100:02d}"
        counters[year] = counters.get(year, 0) + 1
        name_token = re.sub(
            r"[^A-Za-z0-9]+", "_", str(record.get("name") or "")
        ).strip("_") or "Animal"
        sex = str(record.get("sex") or "").strip().casefold()
        sex_token = (
            "M" if sex in {"male", "m"}
            else "F" if sex in {"female", "f"}
            else "U"
        )
        record["id"] = f"mm_{year}_{counters[year]:04d}_{sex_token}_{name_token}"


def monitoring(start: date, prefix: str, *, donor: bool) -> tuple[list, list, list]:
    blood, urine, events = [], [], []
    sample = 1
    for cycle in range(3):
        cycle_start = start + timedelta(days=cycle * 28)
        for day in (0, 4, 7, 10, 14, 18, 23, 27):
            value = 4.0 if day <= 10 else min(38.0, 7.0 + (day - 10) * 2.4)
            stamp = (cycle_start + timedelta(days=day)).isoformat()
            blood.append({"datum": stamp, "wert": round(value, 1),
                          "probennummer": f"B-{prefix}-{sample:03d}"})
            urine.append({"datum": stamp, "wert": round(value * 1.15, 1),
                          "probennummer": f"U-{prefix}-{sample:03d}"})
            sample += 1
        if donor:
            pgf_date = cycle_start + timedelta(days=24)
            events.append({"typ": "pgf", "datum": pgf_date.isoformat()})
            blood.append({"datum": (pgf_date + timedelta(days=3)).isoformat(), "wert": 4.0})
            urine.append({"datum": (pgf_date + timedelta(days=3)).isoformat(), "wert": 5.0})
    return blood, urine, events


def add_reproduction_scenarios(core: dict[str, Any], key_map: dict[str, str]) -> dict[str, str]:
    find = lambda name: next(
        key for key in core["animals"] if key.startswith(name + " |")
    )
    elros = find("Elros")
    invented_parent = add_animal(core, key_map, "Beth", "12.03.2012", "Female", "breeding_animal")
    denethor = add_animal(
        core, key_map, "Denethor", "08.02.2018", "Male", "sperm_donor",
        parent_f=invented_parent, parent_m=elros,
    )
    donors = [
        add_animal(core, key_map, name, birth, "Female", "egg_cell_donor")
        for name, birth in (
            ("Tiffany", "17.04.2017"), ("Nicole", "09.06.2018"),
            ("Megan", "22.08.2019"), ("Lauren", "03.10.2019"),
        )
    ]
    surrogates = [
        add_animal(core, key_map, name, birth, "Female", "surrogate")
        for name, birth in (
            ("Rachel", "14.02.2017"), ("Amanda", "19.05.2018"),
            ("Heather", "26.07.2018"), ("Alaine", "30.09.2019"),
        )
    ]
    boromir = add_animal(
        core, key_map, "Boromir", "11.03.2025", "Male", "offspring",
        parent_f=donors[0], parent_m=denethor,
    )
    faramir = add_animal(
        core, key_map, "Faramir", "18.04.2026", "Male", "offspring",
        parent_f=donors[1], parent_m=denethor,
    )
    core["animals"][boromir]["ziehmutter"] = surrogates[0]
    core["animals"][faramir]["ziehmutter"] = surrogates[1]

    for idx, key in enumerate(donors + surrogates):
        blood, urine, events = monitoring(
            date(2024, 8, 1) + timedelta(days=idx * 3),
            f"OTOF{idx + 1}",
            donor=key in donors,
        )
        core["animals"][key]["daten"] = blood
        core["animals"][key]["pdg"] = urine
        core["animals"][key]["events"].extend(events)
        core["animals"][key]["pgf"] = [
            event["datum"] for event in events if event["typ"] == "pgf"
        ]
    for donor, retrieval_date in zip(donors, RETRIEVAL_DATES):
        stimulation_start = retrieval_date - timedelta(days=10)
        core["animals"][donor]["events"].extend(
            {
                "typ": "fsh",
                "datum": (stimulation_start + timedelta(days=day)).isoformat(),
                "course_id": f"FSH-{retrieval_date.isoformat()}",
            }
            for day in range(9)
        )
        core["animals"][donor]["events"].append({
            "typ": "oocyte_retrieval",
            "datum": retrieval_date.isoformat(),
            "course_id": f"FSH-{retrieval_date.isoformat()}",
        })

    donation_date = date(2024, 11, 20)
    donation_sample = "SP-OTOF-DENETHOR-001"
    core["animals"][denethor]["sperm"] = [{
        "datum": donation_date.isoformat(),
        "sample_id": donation_sample,
        "motility": 88.0,
        "progressive": 81.0,
        "count": 1_240_000_000.0,
    }]
    core["animals"][denethor]["events"].append({
        "typ": "sperm_donation",
        "datum": donation_date.isoformat(),
        "sample_id": donation_sample,
        "project": "OTOF-",
    })

    for surrogate_index, transfer_date, outcome, offspring_key in TRANSFER_SCENARIOS:
        surrogate = surrogates[surrogate_index]
        course_id = f"ET-{surrogate_index + 1}-{transfer_date.isoformat()}"
        core["animals"][surrogate]["events"].append({
            "typ": "embryo_transfer",
            "datum": transfer_date.isoformat(),
            "course_id": course_id,
        })
        result = "negative" if outcome == "not-pregnant" else "positive"
        core["animals"][surrogate]["events"].append({
            "typ": "pregnancy_verification",
            "datum": (transfer_date + timedelta(days=28)).isoformat(),
            "result": result,
            "course_id": course_id,
        })
        if outcome == "birth":
            birth_date = datetime.strptime(
                core["animals"][
                    boromir if offspring_key == "boromir" else faramir
                ]["birth_date"],
                "%d.%m.%Y",
            ).date()
            core["animals"][surrogate]["events"].append({
                "typ": "birth",
                "datum": birth_date.isoformat(),
                "course_id": course_id,
                "offspring": boromir if offspring_key == "boromir" else faramir,
            })
        elif outcome == "abortion":
            core["animals"][surrogate]["events"].append({
                "typ": "abortion",
                "datum": (transfer_date + timedelta(days=63)).isoformat(),
                "course_id": course_id,
            })
    return {
        "denethor": denethor, "boromir": boromir, "faramir": faramir,
        "donors": donors, "surrogates": surrogates,
    }


def build_flow(scenario: dict[str, Any]) -> dict[str, Any]:
    denethor = scenario["denethor"]
    donors, surrogates = scenario["donors"], scenario["surrogates"]
    return {
        "version": "3.0",
        "last_updated": SEED_CREATED,
        "manual_data": {
            "sperm_donors": {
                denethor: {"donations": {
                    "2024-11-20": {
                        "sample_id": "SP-OTOF-DENETHOR-001",
                        "applied_to_in_vitro_m2": 20,
                        "fertilized_in_vitro_m2": 14,
                        "embryos_from_in_vitro_m2": 8,
                    }
                }}
            },
            "egg_donors": {
                donors[0]: {"retrievals": {"2024-12-01": {"course_id": "FSH-2024-12-01", "oocytes": 12, "embryos": 6}}},
                donors[1]: {"retrievals": {"2025-01-10": {"course_id": "FSH-2025-01-10", "oocytes": 11, "embryos": 5}}},
                donors[2]: {"retrievals": {"2025-02-12": {"course_id": "FSH-2025-02-12", "oocytes": 2, "embryos": 0, "outcome": "poor-yield"}}},
                donors[3]: {"retrievals": {"2025-03-15": {"course_id": "FSH-2025-03-15", "oocytes": 13, "embryos": 1, "outcome": "low-development"}}},
            },
            "surrogates": {
                surrogates[0]: {"transfers": {"2024-10-18": {"course_id": "ET-1-2024-10-18", "verification": "positive", "outcome": "birth", "offspring": scenario["boromir"]}}},
                surrogates[1]: {"transfers": {"2025-11-25": {"course_id": "ET-2-2025-11-25", "verification": "positive", "outcome": "birth", "offspring": scenario["faramir"]}}},
                surrogates[2]: {"transfers": {
                    "2025-01-20": {"course_id": "ET-3-2025-01-20", "verification": "negative", "outcome": "not-pregnant"},
                    "2025-04-20": {"course_id": "ET-3-2025-04-20", "verification": "negative", "outcome": "not-pregnant"},
                }},
                surrogates[3]: {"transfers": {
                    "2024-10-10": {"course_id": "ET-4-2024-10-10", "verification": "positive", "outcome": "abortion"},
                    "2025-03-10": {"course_id": "ET-4-2025-03-10", "verification": "positive", "outcome": "abortion"},
                }},
            },
            "embryo_transfers": {},
        },
    }


def seed_users() -> list[dict[str, Any]]:
    users = []
    for profile in CANONICAL_USERS:
        username = profile["username"]
        salt = hashlib.sha256(f"progtrack-seed:{username}".encode()).digest()[:16]
        password_hash = hashlib.pbkdf2_hmac(
            "sha256", b"123456", salt, 260_000
        ).hex()
        users.append({
            **profile,
            "permissions": {"granted": [], "revoked": []},
            "password_hash": password_hash,
            "salt": salt.hex(),
            "last_login": None,
            "must_change_password": False,
        })
    return users


def parse_record_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    return None


def normalize_mature_offspring_roles(core: dict[str, Any]) -> list[str]:
    """Promote non-Callitrix offspring older than one year at Eldarion's birth.

    The fictional seed uses one fixed reference date so role categories remain
    reproducible.  Callitrix offspring are intentionally excluded because the
    family/reproduction examples use that role for their own scenarios.
    """
    cutoff = ELDARION_BIRTH_DATE.replace(year=ELDARION_BIRTH_DATE.year - 1)
    changed: list[str] = []
    all_animals = {
        **core.get("animals", {}),
        **core.get("archived_animals", {}),
    }
    for ipid, record in all_animals.items():
        species = str(record.get("species") or "").strip().lower()
        if species.startswith(("callitrix", "callithrix")):
            continue
        if str(record.get("rolle") or "").strip().lower() != "offspring":
            continue
        birth = parse_record_date(record.get("birth_date"))
        if birth is not None and birth < cutoff:
            record["rolle"] = "breeding_animal"
            if "role_id" in record:
                record["role_id"] = "breeding_animal"
            changed.append(str(ipid))
    return changed


def researcher_record_dates(animal: dict[str, Any]) -> list[date]:
    """Dates from scientific/clinical fields that researchers can populate."""
    found: list[date] = []
    for field in ("daten", "pdg", "gewicht", "sperm", "events", "pgf", "op", "embryo"):
        values = animal.get(field, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, dict):
                parsed = parse_record_date(
                    value.get("datum") or value.get("date")
                )
            else:
                parsed = parse_record_date(value)
            if parsed is not None:
                found.append(parsed)
    return found


def _stable_weight(value: float, ipid: str, stamp: date) -> float:
    digest = hashlib.sha256(f"{ipid}:{stamp.isoformat()}".encode()).digest()
    percent = (digest[0] % 7 - 3) / 100.0
    return round(max(0.1, value * (1.0 + percent)), 1)


def extend_weights_through_latest_data(
    ipid: str, animal: dict[str, Any]
) -> None:
    dates = researcher_record_dates(animal)
    if not dates:
        return
    latest = max(dates)
    parsed_weights = sorted(
        (
            (parse_record_date(value.get("datum")), float(value.get("wert")))
            for value in animal.get("gewicht", [])
            if isinstance(value, dict)
            and parse_record_date(value.get("datum")) is not None
            and isinstance(value.get("wert"), (int, float))
        ),
        key=lambda item: item[0],
    )
    if not parsed_weights:
        animal["gewicht"] = dated_weights(
            animal["birth_date"], animal["species"], animal.get("sex", "Unknown")
        )
        parsed_weights = [
            (parse_record_date(item["datum"]), float(item["wert"]))
            for item in animal["gewicht"]
        ]
    last_date, last_value = parsed_weights[-1]
    if last_date >= latest:
        return
    cursor = last_date
    additions: list[dict[str, Any]] = []
    while cursor + timedelta(days=180) < latest:
        cursor += timedelta(days=180)
        last_value = _stable_weight(last_value, ipid, cursor)
        additions.append({"datum": cursor.isoformat(), "wert": last_value})
    if cursor < latest:
        last_value = _stable_weight(last_value, ipid, latest)
        additions.append({"datum": latest.isoformat(), "wert": last_value})
    animal.setdefault("gewicht", []).extend(additions)


def complete_scientific_histories(core: dict[str, Any]) -> None:
    all_animals = {**core["animals"], **core["archived_animals"]}
    for sequence, (ipid, animal) in enumerate(sorted(all_animals.items())):
        events = animal.setdefault("events", [])
        existing_event_keys = {
            (
                str(event.get("typ") or event.get("type") or ""),
                str(event.get("datum") or event.get("date") or ""),
                str(event.get("sample_id") or ""),
            )
            for event in events if isinstance(event, dict)
        }

        # A PGF administration is both a plotted PGF marker and a scientific
        # intervention event.
        for pgf_value in animal.get("pgf", []) or []:
            stamp = (
                pgf_value.get("datum") if isinstance(pgf_value, dict)
                else pgf_value
            )
            key = ("pgf", str(stamp), "")
            if parse_record_date(stamp) and key not in existing_event_keys:
                events.append({"typ": "pgf", "datum": str(stamp)})
                existing_event_keys.add(key)

        # Every sperm sample is one collection event; measurements from the
        # sample share the same sample ID.
        sperm = animal.setdefault("sperm", [])
        if animal.get("rolle") == "sperm_donor" and not sperm:
            collection = date(2025, 6, 15) + timedelta(days=sequence % 120)
            sperm.append({
                "datum": collection.isoformat(),
                "sample_id": f"SP-SEED-{sequence + 1:03d}",
                "motility": float(75 + sequence % 18),
                "progressive": float(55 + sequence % 24),
                "count": float(180_000_000 + (sequence % 9) * 55_000_000),
            })
        for sample_index, sample in enumerate(sperm):
            if not isinstance(sample, dict):
                continue
            stamp = str(sample.get("datum") or "")
            if not parse_record_date(stamp):
                continue
            sample_id = str(sample.get("sample_id") or "").strip()
            if not sample_id:
                sample_id = f"SP-{hashlib.sha256((ipid + stamp).encode()).hexdigest()[:12].upper()}"
                sample["sample_id"] = sample_id
            key = ("sperm_donation", stamp, sample_id)
            if key not in existing_event_keys:
                events.append({
                    "typ": "sperm_donation",
                    "datum": stamp,
                    "sample_id": sample_id,
                })
                existing_event_keys.add(key)

        events.sort(key=lambda event: (
            str(event.get("datum") or event.get("date") or ""),
            str(event.get("typ") or event.get("type") or ""),
        ))
        extend_weights_through_latest_data(ipid, animal)


def complete_housing(
    housing: dict[str, Any], active_animals: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = copy.deepcopy(housing)
    structures = result.setdefault("structures", {})
    cages = structures.setdefault("cages", {})
    occupants = result.setdefault("occupants", {})
    # Dedicated mouse facility: experimental Ringbearer animals together,
    # normal breeding group housing, and Bilbo's deliberate single-cage case.
    structures.setdefault("buildings", {}).update({
        "bld_mus": {
            "display_name": "Building 05 - Mouse House",
            "id": "bld_mus", "order": 5, "virtual": False,
        },
    })
    structures.setdefault("units", {}).update({
        "unit_mus_colony": {
            "display_name": "Mouse Colony Unit",
            "id": "unit_mus_colony", "order": 0,
            "parent_building_id": "bld_mus", "virtual": False,
        },
    })
    structures.setdefault("rooms", {}).update({
        "room_mus_colony": {
            "display_name": "Mouse Colony Room",
            "id": "room_mus_colony", "order": 0,
            "parent_building_id": "bld_mus",
            "parent_unit_id": "unit_mus_colony", "virtual": False,
        },
    })
    for cage_id, display_name, order in (
        ("cage_mus_experimental", "Mouse Experimental Group", 0),
        ("cage_mus_breeding", "Mouse Breeding Group", 1),
        ("cage_mus_bilbo", "Mouse Single Housing - Bilbo", 2),
    ):
        cages.setdefault(cage_id, {
            "display_name": display_name, "id": cage_id, "order": order,
            "parent_room_id": "room_mus_colony", "virtual": False,
        })
    # Archived and stale legacy occupants are deliberately not carried into the
    # clean seed.
    occupants = {
        key: value for key, value in occupants.items() if key in active_animals
    }
    cage_choices = {
        "Callitrix jacchus": next(
            (key for key in cages if key.startswith("cage_cj_") and key != "cage_unassigned"),
            "cage_unassigned",
        ),
        "Macaca mulatta": next(
            (key for key in cages if key.startswith("cage_mm_")), "cage_unassigned"
        ),
        "Papio hamadryas anubis": next(
            (key for key in cages if key.startswith("cage_pa_")), "cage_unassigned"
        ),
        "Mus musculus": next(
            (key for key in cages if key.startswith("cage_sample_")), "cage_unassigned"
        ),
    }
    # Existing sample mice are moved into the dedicated Mouse House below;
    # never leave them mixed with another species in the legacy sample cage.
    for ipid in list(occupants):
        animal = active_animals.get(ipid)
        if animal and str(animal.get("species") or "") == "Mus musculus":
            occupants.pop(ipid, None)
    def valid_cage(cage_id: str) -> bool:
        cage = cages.get(cage_id, {})
        room = structures.get("rooms", {}).get(cage.get("parent_room_id"), {})
        unit = structures.get("units", {}).get(room.get("parent_unit_id"), {})
        building = structures.get("buildings", {}).get(
            unit.get("parent_building_id"), {}
        )
        return bool(cage and room and unit and building and cage_id != "cage_unassigned")

    for ipid, animal in active_animals.items():
        if ipid in occupants and valid_cage(str(occupants[ipid].get("cage_id", ""))):
            continue
        if animal.get("species") == "Mus musculus":
            name = str(animal.get("name") or "")
            if name == "Bilbo":
                cage_id = "cage_mus_bilbo"
            elif animal.get("in_experiment"):
                cage_id = "cage_mus_experimental"
            else:
                cage_id = "cage_mus_breeding"
        else:
            cage_id = cage_choices.get(animal["species"], "cage_unassigned")
        occupants[ipid] = {
            "occupant_id": ipid,
            "ipid": ipid,
            "name": animal["name"],
            "type": "real",
            "cage_id": cage_id,
            "moved_at": "2026-01-01",
            "animal_id": animal["id"],
        }
    result["occupants"] = occupants
    result.setdefault("movement_history", {})
    return result


def complete_heritage(core: dict[str, Any]) -> dict[str, Any]:
    animals = {}
    for ipid, animal in {**core["animals"], **core["archived_animals"]}.items():
        animals[ipid] = {
            "ipid": ipid,
            "name": animal["name"],
            "species": animal["species"],
            "birth_date": animal["birth_date"],
            "sex": str(animal.get("sex", "")).lower(),
            "genotype": animal.get("genotype", ""),
            "egg_donor": animal.get("eizellspenderin", ""),
            "sperm_donor": animal.get("samenspender", ""),
            "surrogate_mother": animal.get("ziehmutter", ""),
            "surrogate_father": animal.get("ziehvater", ""),
            "heritage_only": False,
            "source": "seed",
            "updated_at": SEED_CREATED,
        }
    return {
        "version": "2.0.0",
        "updated_at": SEED_CREATED,
        "animals": animals,
        "genotype_colors": {
            "H-/H-": "#ff8ce8", "WT/H-": "#ffd9ee", "WT/WT": "#ffffff"
        },
    }


def add_resolved_histories(
    medical: dict[str, Any], reports: dict[str, Any], core: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    med = copy.deepcopy(medical)
    med.setdefault("version", "2.0")
    animals = med.setdefault("animals", {})
    report_data = copy.deepcopy(reports)
    roles = (
        ("Keeper Breedsson", "Transient appetite reduction", "Routine observation"),
        ("Dr. Veterinary Medicinsson", "Minor superficial injury", "Cleaned; uncomplicated recovery"),
        ("Dr. Veterinary Medicinsdottir", "Temporary welfare observation", "Reviewed and resolved"),
        ("Dr. Researcher Sciencedottir", "Short experimental observation", "No continuing finding"),
    )
    for index, (ipid, animal) in enumerate(sorted(core["animals"].items())[:4]):
        actor, condition, note = roles[index]
        entries = [
            {
                "id": f"seed-med-{index}-start",
                "date": f"2026-0{index + 1}-10",
                "entry_type": "observation",
                "status_type": "sick" if index < 2 else "abnormal",
                "condition_label_snapshot": condition,
                "note": note,
                "signature": actor,
            },
            {
                "id": f"seed-med-{index}-resolved",
                "date": f"2026-0{index + 1}-13",
                "entry_type": "resolved",
                "status_type": "resolved",
                "condition_label_snapshot": condition,
                "note": "Resolved without sequelae.",
                "signature": (
                    "Dr. Veterinary Medicinsson" if index < 2 else actor
                ),
            },
        ]
        animals[ipid] = {
            "animal_id": animal["id"],
            "ipid": ipid,
            "name": animal["name"],
            "entries": entries,
            "documents": [],
        }
        report_data[ipid] = {
            "ipid": ipid,
            "name": animal["name"],
            "locked_dates": [f"1{index}.0{index + 1}.2026"],
            "edits": {
                f"1{index}.0{index + 1}.2026": {
                    "daily_data": note,
                    "scores": "",
                    "signatures": actor,
                }
            },
        }
    return med, report_data


def domain_records(core: dict[str, Any], key_map: dict[str, str],
                   scenario: dict[str, Any]) -> dict[tuple[str, str], Any]:
    records: dict[tuple[str, str], Any] = {}
    legacy = {
        ("projects", "catalog"): load_json("Plugins/Projects_Track/project_data.json", {"version": 1, "projects": {}}),
        ("projects", "history"): load_json("Plugins/Projects_Track/projects_history.json", {"version": 1, "projects": {}}),
        ("housing", "cage"): load_json("Plugins/Cage__Track/cage.json", {}),
        ("housing", "inspections"): load_json("Plugins/Cage__Track/inspection.json", {"records": []}),
        ("medical", "history"): load_json("Plugins/Medi_Track/medi_history.json", {"version": "1.7", "animals": {}}),
        ("reports", "animal-reports"): load_json("Plugins/Animal_Reports/animal_report_data.json", {}),
        ("heritage", "graph"): load_json("Plugins/Heritage_Track/heritage_animals.json", {}),
        ("samples", "organs"): load_json("Plugins/Sample_Track/organs.json", []),
        ("samples", "other"): load_json("Plugins/Sample_Track/other.json", []),
    }
    for key, value in legacy.items():
        records[key] = rewrite_references(value, key_map)
    project_catalog = records[("projects", "catalog")]
    project_catalog.setdefault("projects", {})["Ringbearer"] = {
        "summary": {
            "title": "Ringbearer mouse colony",
            "species": "Mus musculus",
            "comment": "Fictional example project for experimental mouse cohort.",
            "contact1_login": "Researcher",
            "contact2_login": "Vet",
            "contacts_other_logins": [],
        },
        "assoc_users": {
            "applicant_login": "Manager",
            "planning_login": "Researcher",
            "staff_logins": ["Keeper", "Veti"],
        },
        "iacuc": {
            "short_title": "Ringbearer",
            "welfare_login": "Veti",
            "pi_login": "Researcher",
        },
        "animals_config": {
            "approved_count": 4,
            "departed_with_sev_no_legal": 0,
            "roles": ["experimental_animal"],
        },
        "arrive": {},
        "created_at": "22.07.2026 10:00",
        "created_by": "Dr. Manager Plansdottir",
        "modified_at": "22.07.2026 10:00",
        "modified_by": "Dr. Manager Plansdottir",
    }
    project_history = records[("projects", "history")]
    project_history.setdefault("projects", {})["Ringbearer"] = {
        "animals": [
            {
                "ipid": ipid,
                "name": animal["name"],
                "date_entered": "22.07.2026",
                "date_left": None,
                "status": "active",
                "last_severity": None,
                "had_in_experiment": True,
                "previous_in_experiment": False,
                "previous_project_snapshot": [],
                "previous_experimental_snapshot": [],
            }
            for ipid, animal in sorted(core["animals"].items())
            if animal.get("project") == "Ringbearer"
        ],
        "archived": False,
    }
    records[("housing", "cage")] = complete_housing(
        records[("housing", "cage")], core["animals"]
    )
    records[("heritage", "graph")] = complete_heritage(core)
    medical, reports = add_resolved_histories(
        records[("medical", "history")],
        records[("reports", "animal-reports")],
        core,
    )
    records[("medical", "history")] = medical
    records[("reports", "animal-reports")] = reports
    records[("reproduction", "flow")] = build_flow(scenario)
    records[("security", "users")] = seed_users()
    records[("configuration", "global-settings")] = {"language": "en"}
    records[("configuration", "disabled-plugins")] = []
    records[("seed", "metadata")] = {
        "version": "0.2.1",
        "fictional": True,
        "created_at": SEED_CREATED,
        "scenario_ids": [
            "denethor-family", "otof-success", "otof-poor-yield",
            "otof-low-development", "surrogate-nonpregnant", "surrogate-abortions",
            "mild-resolved-medical", "all-species-weights", "four-level-housing",
            "mus-musculus-ringbearer-colony", "mouse-house-group-housing",
        ],
    }
    return records


def validate(core: dict[str, Any], records: dict[tuple[str, str], Any],
             scenario: dict[str, Any]) -> dict[str, Any]:
    all_animals = {**core["animals"], **core["archived_animals"]}
    errors = []
    for ipid, record in all_animals.items():
        if len(ipid.split(" | ")) != 4:
            errors.append(f"invalid IPID: {ipid}")
        if not record.get("gewicht"):
            errors.append(f"missing weight history: {ipid}")
        weight_dates = [
            parse_record_date(item.get("datum"))
            for item in record.get("gewicht", [])
            if isinstance(item, dict)
        ]
        scientific_dates = researcher_record_dates(record)
        if (
            scientific_dates
            and (
                not any(weight_dates)
                or max(value for value in weight_dates if value) < max(scientific_dates)
            )
        ):
            errors.append(f"weight history ends before scientific data: {ipid}")
        for field in ("name", "species", "birth_date", "origin", "id", "rolle", "sex"):
            if not str(record.get(field, "")).strip():
                errors.append(f"missing {field}: {ipid}")
        for ref_field in ("eizellspenderin", "samenspender", "ziehmutter", "ziehvater"):
            reference = str(record.get(ref_field, "")).strip()
            if reference and reference not in all_animals:
                errors.append(f"dangling {ref_field}: {ipid} -> {reference}")
    denethor = all_animals[scenario["denethor"]]
    if denethor.get("samenspender") not in all_animals:
        errors.append("Denethor father missing")
    for offspring in ("boromir", "faramir"):
        record = all_animals[scenario[offspring]]
        if record.get("samenspender") != scenario["denethor"]:
            errors.append(f"{offspring} father mismatch")
    cutoff = ELDARION_BIRTH_DATE.replace(year=ELDARION_BIRTH_DATE.year - 1)
    for ipid, record in all_animals.items():
        species = str(record.get("species") or "").strip().lower()
        if species.startswith(("callitrix", "callithrix")):
            continue
        birth = parse_record_date(record.get("birth_date"))
        if (
            birth is not None
            and birth < cutoff
            and str(record.get("rolle") or "").strip().lower() == "offspring"
        ):
            errors.append(
                f"mature non-Callitrix offspring still has offspring role: {ipid}"
            )
    for donor, retrieval_date in zip(scenario["donors"], RETRIEVAL_DATES):
        events = all_animals[donor].get("events", [])
        course_id = f"FSH-{retrieval_date.isoformat()}"
        fsh = [
            event for event in events
            if event.get("typ") == "fsh" and event.get("course_id") == course_id
        ]
        retrievals = [
            event for event in events
            if event.get("typ") == "oocyte_retrieval"
            and event.get("course_id") == course_id
        ]
        if len(fsh) != 9 or len(retrievals) != 1:
            errors.append(f"incomplete FSH/retrieval course: {donor}")
    for surrogate_index, transfer_date, outcome, _offspring in TRANSFER_SCENARIOS:
        surrogate = scenario["surrogates"][surrogate_index]
        course_id = f"ET-{surrogate_index + 1}-{transfer_date.isoformat()}"
        events = [
            event for event in all_animals[surrogate].get("events", [])
            if event.get("course_id") == course_id
        ]
        transfers = [event for event in events if event.get("typ") == "embryo_transfer"]
        verifications = [
            event for event in events
            if event.get("typ") == "pregnancy_verification"
        ]
        expected_result = "negative" if outcome == "not-pregnant" else "positive"
        if len(transfers) != 1 or len(verifications) != 1:
            errors.append(f"incomplete transfer/verification course: {course_id}")
        elif verifications[0].get("result") != expected_result:
            errors.append(f"pregnancy result mismatch: {course_id}")
        if outcome == "birth" and not any(
            event.get("typ") == "birth" for event in events
        ):
            errors.append(f"birth event missing: {course_id}")
        if outcome == "abortion" and not any(
            event.get("typ") == "abortion" for event in events
        ):
            errors.append(f"abortion event missing: {course_id}")
    for ipid, record in all_animals.items():
        sperm_events = {
            (
                str(event.get("datum") or ""),
                str(event.get("sample_id") or ""),
            )
            for event in record.get("events", [])
            if isinstance(event, dict) and event.get("typ") == "sperm_donation"
        }
        for sample in record.get("sperm", []) or []:
            if not isinstance(sample, dict):
                continue
            sample_key = (
                str(sample.get("datum") or ""),
                str(sample.get("sample_id") or ""),
            )
            if not sample_key[1] or sample_key not in sperm_events:
                errors.append(f"sperm sample without donation event: {ipid}")

    expected_users = {profile["username"]: profile for profile in CANONICAL_USERS}
    users = records.get(("security", "users"), [])
    actual_users = {str(user.get("username")): user for user in users}
    if set(actual_users) != set(expected_users):
        errors.append(
            "canonical user set mismatch: "
            f"{sorted(actual_users)} != {sorted(expected_users)}"
        )
    for username, profile in expected_users.items():
        user = actual_users.get(username, {})
        for field in (
            "display_name", "role", "jobs", "pronouns", "email", "phone",
            "mobile", "unit", "profession",
        ):
            if user.get(field) != profile.get(field):
                errors.append(f"user profile mismatch: {username}.{field}")
        try:
            salt = bytes.fromhex(str(user.get("salt") or ""))
            expected_hash = hashlib.pbkdf2_hmac(
                "sha256", b"123456", salt, 260_000
            ).hex()
        except ValueError:
            expected_hash = ""
        if expected_hash != user.get("password_hash"):
            errors.append(f"example password mismatch: {username}")
        if user.get("must_change_password"):
            errors.append(f"unexpected forced password change: {username}")
    housing = records[("housing", "cage")]
    structures = housing.get("structures", {})
    for ipid in core["animals"]:
        occupant = housing.get("occupants", {}).get(ipid)
        if not occupant:
            errors.append(f"active animal not housed: {ipid}")
            continue
        cage_id = occupant.get("cage_id")
        cage = structures.get("cages", {}).get(cage_id, {})
        room = structures.get("rooms", {}).get(cage.get("parent_room_id"), {})
        unit = structures.get("units", {}).get(room.get("parent_unit_id"), {})
        building = structures.get("buildings", {}).get(
            unit.get("parent_building_id"), {}
        )
        if not all((cage, room, unit, building)) or cage_id == "cage_unassigned":
            errors.append(f"incomplete four-level housing: {ipid}")
    mice = {
        ipid: record for ipid, record in all_animals.items()
        if str(record.get("species") or "") == "Mus musculus"
    }
    mouse_id_pattern = re.compile(r"^mm_\d{2}_\d{4}_[MFU]_[A-Za-z0-9_]+$")
    for ipid, record in mice.items():
        public_id = str(record.get("id") or "")
        if not mouse_id_pattern.fullmatch(public_id):
            errors.append(f"mouse public ID does not follow conventions: {ipid} -> {public_id}")
            continue
        sex = str(record.get("sex") or "").strip().casefold()
        expected_sex = "M" if sex in {"male", "m"} else "F" if sex in {"female", "f"} else "U"
        if f"_{expected_sex}_" not in public_id:
            errors.append(f"mouse public ID sex mismatch: {ipid} -> {public_id}")
    active_mice = {
        ipid: record for ipid, record in core["animals"].items()
        if str(record.get("species") or "") == "Mus musculus"
    }
    required_mouse_names = {"Frodo", "Sam", "Merry", "Pippin", "Fredegar"}
    if not required_mouse_names.issubset({r.get("name") for r in mice.values()}):
        errors.append("Mus musculus youngest-generation names are incomplete")
    ringbearers = {
        record.get("name") for record in mice.values()
        if record.get("project") == "Ringbearer"
    }
    if ringbearers != {"Frodo", "Sam", "Merry", "Pippin"}:
        errors.append(f"Ringbearer cohort mismatch: {sorted(ringbearers)}")
    for ipid, record in mice.items():
        if record.get("name") in {"Frodo", "Sam", "Merry", "Pippin"}:
            if record.get("rolle") != "experimental_animal" or not record.get("in_experiment"):
                errors.append(f"Ringbearer animal is not experimental: {record.get('name')}")
        elif record.get("rolle") != "breeding_animal":
            errors.append(f"mouse has non-breeding role: {record.get('ipid')}")
        if ipid not in active_mice:
            continue
        occupant = housing.get("occupants", {}).get(ipid, {})
        expected_cage = (
            "cage_mus_bilbo" if record.get("name") == "Bilbo"
            else "cage_mus_experimental" if record.get("in_experiment")
            else "cage_mus_breeding"
        )
        if occupant.get("cage_id") != expected_cage:
            errors.append(
                f"mouse housing mismatch: {record.get('name')} -> "
                f"{occupant.get('cage_id')} (expected {expected_cage})"
            )
    return {
        "schema": "progtrack-seed-integrity/1",
        "seed_version": "0.2.1",
        "fictional": True,
        "counts": {
            "active_animals": len(core["animals"]),
            "archived_animals": len(core["archived_animals"]),
            "domain_records": len(records),
            "weighted_animals": sum(bool(v.get("gewicht")) for v in all_animals.values()),
            "species": len({v["species"] for v in all_animals.values()}),
            "canonical_users": len(actual_users),
            "scientifically_current_weights": sum(
                bool(researcher_record_dates(value))
                and max(
                    parse_record_date(item.get("datum"))
                    for item in value.get("gewicht", [])
                    if isinstance(item, dict)
                    and parse_record_date(item.get("datum")) is not None
                ) >= max(researcher_record_dates(value))
                for value in all_animals.values()
            ),
            "sperm_donation_events": sum(
                event.get("typ") == "sperm_donation"
                for value in all_animals.values()
                for event in value.get("events", [])
                if isinstance(event, dict)
            ),
            "pregnancy_verifications": sum(
                event.get("typ") == "pregnancy_verification"
                for value in all_animals.values()
                for event in value.get("events", [])
                if isinstance(event, dict)
            ),
        },
        "errors": errors,
        "valid": not errors,
    }


def main() -> int:
    core, key_map = normalize_core()
    # Rewrite inherited pedigree references before adding new canonical animals.
    core = rewrite_references(core, key_map)
    mouse_scenario = add_mouse_colony(core, key_map)
    normalize_mouse_public_ids(core)
    scenario = add_reproduction_scenarios(core, key_map)
    scenario["mouse"] = mouse_scenario
    normalize_mature_offspring_roles(core)
    complete_scientific_histories(core)
    records = domain_records(core, key_map, scenario)
    result = validate(core, records, scenario)
    if not result["valid"]:
        raise SystemExit("\n".join(result["errors"]))

    with tempfile.TemporaryDirectory(prefix="progtrack-seed-") as temporary:
        app_root = Path(temporary) / "app"
        app_root.mkdir()
        env = {
            "PROGTRACK_PORTABLE": "1",
            "PROGTRACK_BACKEND_PROFILE": "standalone_sqlite",
            "PROGTRACK_SQLITE_PATH": str(Path(temporary) / "seed.sqlite3"),
            "PROGTRACK_MANAGED_ROOT": str(Path(temporary) / "managed"),
        }
        paths = resolve_runtime_paths(app_root, environ=env)
        backend = ProgTrackBackend(paths, acquire_process_lock=False)
        try:
            backend.save_core_data(core)
            for (namespace, record_id), payload in sorted(records.items()):
                backend.records.put(namespace, record_id, payload)
            OUTPUT.parent.mkdir(parents=True, exist_ok=True)
            backend.interchange.export_package(
                OUTPUT,
                package_id=SEED_PACKAGE_ID,
                created_at=SEED_CREATED,
            )
        finally:
            backend.close()

    result["package_sha256"] = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    REPORT.write_text(
        json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    MATRIX.write_text(
        """# ProgTrack 0.2.1 fictional seed scenario coverage

The package is entirely fictional and is the single clean initialization
source for Standalone SQLite and Shared PostgreSQL development/demo systems.

| Scenario | Coverage |
|---|---|
| Complete immutable identity | Every animal; four-block IPID including origin |
| Weight history | Every active and archived animal through its latest researcher-entered scientific/clinical record |
| Species | Callitrix, Macaca, Papio, Mus |
| Mus musculus | Hobbit/Lord-of-the-Rings names, Italian partner names, realistic mouse weights and connected ancestry |
| Ringbearer | Frodo, Sam, Merry and Pippin are adult experimental mice in one project and one group cage |
| Mouse House | Dedicated building/unit/room; breeding group, Ringbearer group, and Bilbo's deliberate single cage |
| Denethor family | Elros descendant; Boromir and Faramir with distinct donors/surrogates |
| OTOF- success | Two complete transfer, pregnancy-verification, and live-birth paths |
| OTOF- failure | Poor yield, low embryo development, repeated negative pregnancy verification, two verified pregnancies followed by abortions |
| Monitoring | Three 28-day cycles; 10–11 day follicular phase; complete FSH/retrieval and PGF chains |
| Sperm collection | One donation event per sample ID, including Denethor's OTOF donation |
| Users/roles | Canonical Admin, Researcher, Vet, Manager, Keeper, Tester, and Veti accounts; password `123456` |
| Backend parity | One canonical interchange package for both adapters |
""",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
