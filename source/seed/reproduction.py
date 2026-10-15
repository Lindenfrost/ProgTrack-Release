"""One declared reproductive cycle produces Core events and FlowTrack projections."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timedelta
from typing import Any

from .common import parse_record_date
from .config import CANONICAL_USERS, REPRODUCTION, SEED_CREATED, load_input
from .generators import add_animal


def monitoring(start: date, prefix: str, *, donor: bool) -> tuple[list, list, list]:
    spec = REPRODUCTION["monitoring"]
    blood, urine, events = [], [], []
    sample = 1
    for cycle in range(spec["cycles"]):
        cycle_start = start + timedelta(days=cycle * spec["cycle_days"])
        pgf_day = spec["pgf_day"]
        response_day = pgf_day + spec["post_pgf_days"]
        days = sorted(set(spec["sampling_days"]) | ({response_day} if donor else set()))
        for day in days:
            value = 4.0 if day <= 10 else min(38.0, 7.0 + (day - 10) * 2.4)
            urine_value = round(value * 1.15, 1)
            if donor and day == response_day:
                value, urine_value = 4.0, 5.0
            stamp = (cycle_start + timedelta(days=day)).isoformat()
            blood.append({"datum": stamp, "wert": round(value, 1), "probennummer": f"B-{prefix}-{sample:03d}"})
            urine.append({"datum": stamp, "wert": urine_value, "probennummer": f"U-{prefix}-{sample:03d}"})
            sample += 1
        if donor:
            events.append({"event_type": "pgf", "datum": (cycle_start + timedelta(days=pgf_day)).isoformat(), "_seed_key": f"monitoring/{prefix}/{cycle}/pgf"})
    return blood, urine, events


def add_reproduction_scenarios(core: dict[str, Any], key_map: dict[str, str]) -> dict[str, Any]:
    elros = next(k for k, v in core["animals"].items() if v["name"] == "Elros")
    beth = add_animal(core, key_map, "Beth", "12.03.2012", "Female", "breeding_animal")
    denethor = add_animal(core, key_map, "Denethor", "08.02.2018", "Male", "sperm_donor", parent_f=beth, parent_m=elros)
    donors = [add_animal(core, key_map, name, birth, "Female", "egg_cell_donor") for name, birth in REPRODUCTION["donors"]]
    surrogates = [add_animal(core, key_map, name, birth, "Female", "surrogate") for name, birth in REPRODUCTION["surrogates"]]
    boromir = add_animal(core, key_map, "Boromir", "11.03.2025", "Male", "offspring", parent_f=donors[0], parent_m=denethor)
    faramir = add_animal(core, key_map, "Faramir", "18.04.2026", "Male", "offspring", parent_f=donors[1], parent_m=denethor)
    core["animals"][boromir]["ziehmutter"] = surrogates[0]
    core["animals"][faramir]["ziehmutter"] = surrogates[1]
    for idx, ipid in enumerate(donors + surrogates):
        start = date.fromisoformat(REPRODUCTION["monitoring"]["start_date"]) + timedelta(days=idx * REPRODUCTION["monitoring"]["animal_offset_days"])
        blood, urine, events = monitoring(start, f"OTOF{idx + 1}", donor=ipid in donors)
        core["animals"][ipid]["daten"] = blood
        core["animals"][ipid]["pdg"] = urine
        core["animals"][ipid]["events"].extend(events)
    cycles = []
    existing_by_name = {v["name"]: k for k, v in core["animals"].items()}
    children = {"boromir": boromir, "faramir": faramir, "legolas": existing_by_name["Legolas"]}
    for spec in REPRODUCTION["cycles"] + REPRODUCTION.get("existing_cycles", []):
        cycle = copy.deepcopy(spec)
        existing = "egg_name" in spec
        cycle["egg_donor"] = existing_by_name[spec["egg_name"]] if existing else donors[spec["donor"]]
        cycle["sperm_donor"] = existing_by_name[spec["sperm_name"]] if existing else denethor
        egg_date = date.fromisoformat(spec["egg_date"])
        sperm_date = egg_date + timedelta(days=REPRODUCTION["sperm_delay_days"])
        cycle["sperm_date"] = sperm_date.isoformat()
        cycle["sample_id"] = "SP-OTOF-" + spec["key"].upper()
        course = "FSH-" + egg_date.isoformat()
        cycle["course_id"] = course
        donor = core["animals"][cycle["egg_donor"]]
        if existing:
            donor["rolle"] = "egg_cell_donor"
            if "role_id" in donor:
                donor["role_id"] = "egg_cell_donor"
        for day in range(9):
            donor["events"].append({"event_type": "fsh", "datum": (egg_date - timedelta(days=10-day)).isoformat(), "course_id": course, "_seed_key": f"otof/{spec['key']}/fsh/{day}"})
        donor["events"].append({"event_type": "oocyte_retrieval", "datum": egg_date.isoformat(), "course_id": course, "_seed_key": f"otof/{spec['key']}/retrieval"})
        sperm_animal = core["animals"][cycle["sperm_donor"]]
        existing_sample = next((s for s in sperm_animal["sperm"] if parse_record_date(s.get("datum")) == sperm_date), None)
        if existing_sample:
            if not existing_sample.get("sample_id"):
                existing_sample["sample_id"] = "SP-" + hashlib.sha256((cycle["sperm_donor"] + str(existing_sample["datum"])).encode()).hexdigest()[:12].upper()
            cycle["sample_id"] = existing_sample["sample_id"]
        else:
            sperm_animal["sperm"].append({"datum": cycle["sperm_date"], "sample_id": cycle["sample_id"], "motility": 88.0, "progressive": 81.0, "count": 1_240_000_000.0})
            sperm_animal["events"].append({"event_type": "sperm_donation", "datum": cycle["sperm_date"], "sample_id": cycle["sample_id"], "project": "OTOF-", "_seed_key": f"otof/{spec['key']}/sperm"})
        if spec["transfer_date"]:
            transfer_date = date.fromisoformat(spec["transfer_date"])
            cycle["surrogate_ipid"] = existing_by_name[spec["surrogate_name"]] if existing else surrogates[spec["surrogate"]]
            cycle["transfer_id"] = f"transfer_{cycle['surrogate_ipid']}_{transfer_date.isoformat()}T00:00:00"
            et_course = f"ET-{spec['key']}" if existing else f"ET-{spec['surrogate'] + 1}-{transfer_date.isoformat()}"
            maternal = core["animals"][cycle["surrogate_ipid"]]["events"]
            prior_transfer = next((e for e in maternal if e.get("event_type") == "embryo_transfer"), None) if existing else None
            if prior_transfer:
                prior_transfer.update(datum=transfer_date.isoformat(), course_id=et_course)
            result = "negative" if spec["outcome"] == "not-pregnant" else "positive"
            if not prior_transfer:
                maternal.append({"event_type": "embryo_transfer", "datum": transfer_date.isoformat(), "course_id": et_course, "_seed_key": f"otof/{spec['key']}/transfer"})
            maternal.append({"event_type": "pregnancy_verification", "datum": (transfer_date + timedelta(days=28)).isoformat(), "result": result, "course_id": et_course, "_seed_key": f"otof/{spec['key']}/verification"})
            if spec["outcome"] == "birth":
                child = children[spec["offspring"]]
                prior_birth = next((e for e in maternal if e.get("event_type") == "birth" and parse_record_date(e.get("datum")) == parse_record_date(core["animals"][child]["birth_date"])), None) if existing else None
                if prior_birth:
                    prior_birth.update(course_id=et_course, offspring=child)
                else:
                    maternal.append({"event_type": "birth", "datum": parse_record_date(core["animals"][child]["birth_date"]).isoformat(), "course_id": et_course, "offspring": child, "_seed_key": f"otof/{spec['key']}/birth"})
            elif spec["outcome"] == "abortion":
                maternal.append({"event_type": "abortion", "datum": (transfer_date + timedelta(days=63)).isoformat(), "course_id": et_course, "_seed_key": f"otof/{spec['key']}/abortion"})
        cycles.append(cycle)
    return {"denethor": denethor, **children, "donors": donors, "surrogates": surrogates, "cycles": cycles}


def egg_stage_data(spec: dict[str, Any]) -> dict[str, Any]:
    """Project the authored counts into FlowTrack's existing donor fields."""
    return {
        "gv_isolated": spec["gv_isolated"], "m1_isolated": spec["m1_isolated"],
        "m2_isolated": spec["m2_isolated"], "m1_to_m2": spec["m1_to_m2"],
        "in_vivo_m2_fertilized": spec["in_vivo_m2_fertilized"],
        "in_vitro_m2_fertilized": spec["fertilized"],
        "embryos_from_in_vivo_m2": 0, "embryos_from_in_vitro_m2": spec["embryos"],
        "frozen_from_in_vivo_m2": 0, "frozen_from_in_vitro_m2": 0,
        "comments": "Fictional IVF example; only in-vitro-matured MII produce embryos.",
    }


def build_flow(scenario: dict[str, Any], core: dict[str, Any]) -> dict[str, Any]:
    manual: dict[str, Any] = {"sperm_donors": {}, "egg_donors": {}, "transfers_by_id": {}, "total_embryo_count": 0}
    # All recorded sperm collections get explicit metrics. Unused samples have
    # zero allocation, not invented egg-donor or embryo connections.
    for ipid, animal in core["animals"].items():
        if animal.get("rolle") == "sperm_donor":
            donations = manual["sperm_donors"].setdefault(ipid, {"donations": {}})["donations"]
            for sample in animal.get("sperm", []):
                donations[parse_record_date(sample["datum"]).isoformat()] = {
                    "sample_id": sample["sample_id"],
                    "applied_to_in_vivo_m2": 0, "fertilized_in_vivo_m2": 0,
                    "embryos_from_in_vivo_m2": 0,
                    "applied_to_in_vitro_m2": 0, "fertilized_in_vitro_m2": 0,
                    "embryos_from_in_vitro_m2": 0,
                }
    names = {animal["name"]: ipid for ipid, animal in core["animals"].items()}
    for spec in REPRODUCTION["collection_only"]:
        donor = names[spec["egg_name"]]
        manual["egg_donors"].setdefault(donor, {"surgeries": {}})["surgeries"][spec["egg_date"]] = egg_stage_data(spec)
    for cycle in scenario["cycles"]:
        egg, sperm = cycle["egg_donor"], cycle["sperm_donor"]
        manual["egg_donors"].setdefault(egg, {"surgeries": {}})["surgeries"][cycle["egg_date"]] = {
            **egg_stage_data(cycle), "course_id": cycle["course_id"],
        }
        manual["sperm_donors"].setdefault(sperm, {"donations": {}})["donations"][cycle["sperm_date"]] = {
            "sample_id": cycle["sample_id"], "applied_to_in_vitro_m2": cycle["m1_to_m2"],
            "fertilized_in_vitro_m2": cycle["fertilized"], "embryos_from_in_vitro_m2": cycle["embryos"],
            "applied_to_in_vivo_m2": cycle["in_vivo_m2_inseminated"],
            "fertilized_in_vivo_m2": cycle["in_vivo_m2_fertilized"],
            "embryos_from_in_vivo_m2": 0,
        }
        if not cycle["transfer_date"]:
            continue
        embryos = []
        for index in range(cycle["embryos"]):
            implanted = cycle["outcome"] in {"birth", "abortion"} and index == 0
            embryo = {
                "embryo_id": f"OTOF_{cycle['key'].replace('-', '_')}_E{index + 1:03d}",
                "egg_donor_name": egg, "egg_donation_date": cycle["egg_date"],
                "sperm_donor_name": sperm, "sperm_donation_id": cycle["sperm_date"],
                "stage": "in_vitro_m2", "implanted": implanted, "cryopreserved": False,
                "freeze_date": None, "comment": f"Fictional IVF {cycle['outcome']} cycle {cycle['key']}.",
            }
            if cycle["offspring"] and implanted:
                embryo["offspring_ipid"] = scenario[cycle["offspring"]]
            embryos.append(embryo)
        manual["transfers_by_id"][cycle["transfer_id"]] = {
            "surrogate_name": cycle["surrogate_ipid"], "transfer_date": cycle["transfer_date"] + "T00:00:00",
            "embryos": embryos,
        }
        manual.setdefault(cycle["surrogate_ipid"], {"transfers": {}})["transfers"][cycle["transfer_id"]] = {
            "embryo_count": len(embryos), "implanted_count": sum(e["implanted"] for e in embryos),
        }
        manual["total_embryo_count"] += len(embryos)
    return {"version": "3.0", "last_updated": SEED_CREATED, "manual_data": manual}


def seed_users() -> list[dict[str, Any]]:
    users = []
    for profile in CANONICAL_USERS:
        salt = hashlib.sha256(f"progtrack-seed:{profile['username']}".encode()).digest()[:16]
        users.append({**profile, "permissions": {"granted": [], "revoked": []}, "password_hash": hashlib.pbkdf2_hmac("sha256", b"123456", salt, 260_000).hex(), "salt": salt.hex(), "last_login": None, "must_change_password": False})
    return users


def assign_seed_event_ids(core: dict[str, Any]) -> None:
    pinned = load_input("event-identities")
    seen = set()
    for section in ("animals", "archived_animals"):
        for ipid, animal in core[section].items():
            for event in animal.get("events", []):
                logical_key = event.pop("_seed_key", None)
                key = json.dumps([ipid, event.get("event_type"), event.get("datum"), event.get("sample_id", ""), event.get("course_id", "")], ensure_ascii=False, separators=(",", ":"))
                if not event.get("event_id"):
                    event["event_id"] = pinned.get(key) or hashlib.sha256(("progtrack-fictional-seed-event:" + (logical_key or key)).encode()).hexdigest()[:32]
                if event["event_id"] in seen:
                    raise ValueError(f"Duplicate seed event identity: {ipid} / {event['event_id']}")
                seen.add(event["event_id"])
