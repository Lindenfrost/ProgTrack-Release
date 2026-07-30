"""Build the single fictional ProgTrack 0.2.1 backend seed.

Legacy JSON files are authoring inputs only. The generated interchange package
is the sole runtime initialization source and is imported through the same
backend-neutral service used for complete-installation transfers.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
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


def load_json(relative: str, default: Any) -> Any:
    try:
        return json.loads((ROOT / relative).read_text(encoding="utf-8"))
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
    invented_parent = add_animal(core, key_map, "Finduilas II", "12.03.2012", "Female", "breeding_animal")
    denethor = add_animal(
        core, key_map, "Denethor", "08.02.2018", "Male", "sperm_donor",
        parent_f=invented_parent, parent_m=elros,
    )
    donors = [
        add_animal(core, key_map, name, birth, "Female", "egg_cell_donor")
        for name, birth in (
            ("Ioreth", "17.04.2017"), ("Morwen II", "09.06.2018"),
            ("Nellas", "22.08.2019"), ("Aerin II", "03.10.2019"),
        )
    ]
    surrogates = [
        add_animal(core, key_map, name, birth, "Female", "surrogate")
        for name, birth in (
            ("Lalaith II", "14.02.2017"), ("Idril II", "19.05.2018"),
            ("Melian II", "26.07.2018"), ("Haleth II", "30.09.2019"),
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
                        "applied_to_in_vitro_m2": 20,
                        "fertilized_in_vitro_m2": 14,
                        "embryos_from_in_vitro_m2": 8,
                    }
                }}
            },
            "egg_donors": {
                donors[0]: {"retrievals": {"2024-12-01": {"oocytes": 12, "embryos": 6}}},
                donors[1]: {"retrievals": {"2025-01-10": {"oocytes": 11, "embryos": 5}}},
                donors[2]: {"retrievals": {"2025-02-12": {"oocytes": 2, "embryos": 0, "outcome": "poor-yield"}}},
                donors[3]: {"retrievals": {"2025-03-15": {"oocytes": 13, "embryos": 1, "outcome": "low-development"}}},
            },
            "surrogates": {
                surrogates[0]: {"transfers": {"2024-12-12": {"outcome": "birth", "offspring": scenario["boromir"]}}},
                surrogates[1]: {"transfers": {"2025-09-20": {"outcome": "birth", "offspring": scenario["faramir"]}}},
                surrogates[2]: {"transfers": {
                    "2025-01-20": {"outcome": "not-pregnant"},
                    "2025-04-20": {"outcome": "not-pregnant"},
                }},
                surrogates[3]: {"transfers": {
                    "2024-10-10": {"outcome": "abortion"},
                    "2025-03-10": {"outcome": "abortion"},
                }},
            },
            "embryo_transfers": {},
        },
    }


def seed_users() -> list[dict[str, Any]]:
    users = []
    for index, (username, role, jobs) in enumerate((
        ("lord", "lord", []), ("master", "master", []),
        ("manager", "user", ["manager"]), ("vet", "user", ["veterinarian"]),
        ("awo", "animal_welfare_officer", []),
        ("researcher", "user", ["researcher"]), ("keeper", "user", ["keeper"]),
    )):
        salt = hashlib.sha256(f"progtrack-seed:{username}".encode()).digest()[:16]
        password_hash = hashlib.pbkdf2_hmac(
            "sha256", b"123456", salt, 260_000
        ).hex()
        users.append({
            "username": username,
            "display_name": f"Fictional {username.title()}",
            "role": role,
            "jobs": jobs,
            "permissions": {"granted": [], "revoked": []},
            "password_hash": password_hash,
            "salt": salt.hex(),
            "created_at": "2026-01-01",
            "last_login": None,
            "must_change_password": username != "lord",
            "unit": "Fictional Animal Facility",
        })
    return users


def complete_housing(
    housing: dict[str, Any], active_animals: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = copy.deepcopy(housing)
    structures = result.setdefault("structures", {})
    cages = structures.setdefault("cages", {})
    occupants = result.setdefault("occupants", {})
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
        ("keeper", "Transient appetite reduction", "Routine observation"),
        ("vet", "Minor superficial injury", "Cleaned; uncomplicated recovery"),
        ("awo", "Temporary welfare observation", "Reviewed and resolved"),
        ("researcher", "Short experimental observation", "No continuing finding"),
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
                "signature": f"Fictional {actor.title()}",
            },
            {
                "id": f"seed-med-{index}-resolved",
                "date": f"2026-0{index + 1}-13",
                "entry_type": "resolved",
                "status_type": "resolved",
                "condition_label_snapshot": condition,
                "note": "Resolved without sequelae.",
                "signature": "Fictional Vet" if index < 2 else f"Fictional {actor.title()}",
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
                    "signatures": f"Fictional {actor.title()}",
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
        },
        "errors": errors,
        "valid": not errors,
    }


def main() -> int:
    core, key_map = normalize_core()
    # Rewrite inherited pedigree references before adding new canonical animals.
    core = rewrite_references(core, key_map)
    scenario = add_reproduction_scenarios(core, key_map)
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
| Weight history | Every active and archived animal |
| Species | Callitrix, Macaca, Papio, Mus |
| Denethor family | Elros descendant; Boromir and Faramir with distinct donors/surrogates |
| OTOF- success | Two live-birth paths |
| OTOF- failure | Poor yield, low embryo development, repeated non-pregnancy, two abortions |
| Monitoring | Three 28-day cycles; 10–11 day follicular phase; donor PGF decline |
| Users/roles | Lord, Master, Manager, Vet, AWO, Researcher, Keeper |
| Backend parity | One canonical interchange package for both adapters |
""",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
