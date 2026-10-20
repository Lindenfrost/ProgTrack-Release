from __future__ import annotations
import copy
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from itertools import permutations
from pathlib import Path
from typing import Any
from Plugins.core.animal_identity import animal_identity_key
from Plugins.core.animal_relationships import resolve_animal_reference
from Plugins.core.animal_roles import SCHEMA_VERSION as ROLE_SCHEMA_VERSION, canonical_role_value, normalize_role_definition
from Plugins.core.experimental_limits import KNOWN_LIMIT_FIELDS, default_experimental_limits, synchronize_experimental_limits
from Plugins.core.project_periods import ensure_project_periods
from Plugins.core.identity_conventions import render_animal_id
from .config import *
from .common import *
from .generators import researcher_record_dates
from .enrollments import enrollment_errors
import math


def event_date_audit(value: Any, path: str = "$", container: str = "") -> dict[str, Any]:
    """Audit occurrence records before filtering, including nested plugin data.

    Definitions, optional outcome dates and open-ended memberships are not
    occurrences. Never repair an unknown date with today's date or drop a row.
    """
    errors: list[str] = []
    checked = 0

    def valid_date(raw: Any) -> bool:
        if isinstance(raw, (date, datetime)):
            return True
        if not isinstance(raw, str) or not raw.strip():
            return False
        text = raw.strip()
        try:
            datetime.fromisoformat(text)
            return True
        except ValueError:
            pass
        for fmt in ("%d.%m.%Y", "%d/%m/%Y", "%d.%m.%Y %H:%M"):
            try:
                datetime.strptime(text, fmt)
                return True
            except ValueError:
                pass
        return False

    if isinstance(value, dict):
        fields: tuple[str, ...] = ()
        if container in {"events", "lifecycle_events"} or "event_type" in value:
            fields = ("datum", "date")
        elif "entry_type" in value:  # Medi Track history occurrence
            fields = ("date",)
        elif "scope_type" in value and "cage_ids" in value:
            fields = ("date_sort", "date")
        elif "sample_number" in value and "animal_name" in value:
            fields = ("collection_date",)
        elif container == "freezer_main":
            # Storage is not a transfer occurrence; each freezing has its own date.
            fields = ()
        elif isinstance(value.get("embryos"), list) or ("transfer_date" in value and value.get("embryos")):
            fields = ("transfer_date",)
        elif value.get("cryopreserved") is True:
            fields = ("freeze_date",)
        if fields:
            checked += 1
            if not any(valid_date(value.get(field)) for field in fields):
                errors.append(f"Missing or invalid event date: {path} ({'/'.join(fields)})")
        for field in ("egg_donation_date", "sperm_donation_id"):
            if field in value:
                checked += 1
                if not valid_date(value[field]):
                    errors.append(f"Missing or invalid event date: {path}.{field}")
        for key, item in value.items():
            child = event_date_audit(item, f"{path}.{key}", str(key))
            checked += child["checked"]
            errors.extend(child["errors"])
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_path = f"{path}[{index}]"
            if container in {"events", "lifecycle_events"} and not isinstance(item, dict):
                errors.append(f"Invalid event record: {child_path}")
            child = event_date_audit(item, child_path, container)
            checked += child["checked"]
            errors.extend(child["errors"])
    return {"checked": checked, "errors": errors}


def audit_seed_sources() -> dict[str, Any]:
    """Scan every manifest input, including records later pruned from output."""
    inputs = {name: event_date_audit(load_input(name), f"source:{name}")
              for name in MANIFEST["inputs"]}
    return {"inputs_scanned": len(inputs),
            "checked": sum(row["checked"] for row in inputs.values()),
            "errors": [error for row in inputs.values() for error in row["errors"]]}


def measurement_errors(core: dict[str, Any]) -> list[str]:
    errors = []
    for section in ("animals", "archived_animals"):
        for ipid, animal in core[section].items():
            for field in ("daten", "pdg", "gewicht", "sperm"):
                seen = set()
                for row in animal.get(field, []):
                    day = parse_record_date(row.get("datum")) if isinstance(row, dict) else None
                    if day is None:
                        errors.append(f"Invalid measurement date: {ipid}.{field}: {row}")
                        continue
                    if day in seen:
                        errors.append(f"Duplicate seed measurement: {ipid}.{field} / {day.isoformat()}")
                    seen.add(day)
                    fields = ("motility", "progressive", "count") if field == "sperm" else ("wert",)
                    for key in fields:
                        value = row.get(key)
                        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value)):
                            errors.append(f"Invalid measurement value: {ipid}.{field}.{key} / {day.isoformat()}")
    return errors


def donor_stage_errors(core: dict[str, Any], manual: dict[str, Any]) -> list[str]:
    """Seed-only conservation/coverage rules, not runtime laboratory policy."""
    errors = []
    egg_fields = ("gv_isolated", "m1_isolated", "m2_isolated", "m1_to_m2",
                  "in_vivo_m2_fertilized", "in_vitro_m2_fertilized",
                  "embryos_from_in_vivo_m2", "embryos_from_in_vitro_m2",
                  "frozen_from_in_vivo_m2", "frozen_from_in_vitro_m2")
    sperm_fields = ("applied_to_in_vivo_m2", "fertilized_in_vivo_m2",
                    "embryos_from_in_vivo_m2", "applied_to_in_vitro_m2",
                    "fertilized_in_vitro_m2", "embryos_from_in_vitro_m2")
    for role, group, rows_key, fields in (
        ("egg_cell_donor", "egg_donors", "surgeries", egg_fields),
        ("sperm_donor", "sperm_donors", "donations", sperm_fields),
    ):
        expected_donors = {k for k, a in core["animals"].items() if a.get("rolle") == role}
        donors = manual.get(group, {})
        if set(donors) != expected_donors:
            errors.append(f"Incomplete Flow donor catalogue: {group}")
        for ipid in expected_donors:
            animal = core["animals"][ipid]
            entries = (animal.get("sperm", []) if role == "sperm_donor" else
                       [e for e in animal.get("events", []) if e.get("event_type") in {"surgery", "oocyte_retrieval"}])
            expected_dates = {parse_record_date(e["datum"]).isoformat() for e in entries}
            rows = donors.get(ipid, {}).get(rows_key, {})
            if set(rows) != expected_dates:
                errors.append(f"Incomplete Flow collection metrics: {ipid}")
            for stamp, row in rows.items():
                label = f"{ipid} / {stamp}"
                if any(type(row.get(k)) is not int or row[k] < 0 for k in fields):
                    errors.append(f"Missing or invalid donor stage count: {label}")
                    continue
                if row["embryos_from_in_vivo_m2"] != 0:
                    errors.append(f"Seed embryos must come only from IVF/in-vitro MII: {label}")
                linked = sum(
                    e.get("stage") == "in_vitro_m2"
                    and e.get("egg_donor_name" if role == "egg_cell_donor" else "sperm_donor_name") == ipid
                    and e.get("egg_donation_date" if role == "egg_cell_donor" else "sperm_donation_id") == stamp
                    for t in manual.get("transfers_by_id", {}).values()
                    for e in t.get("embryos", [])
                )
                if row["embryos_from_in_vitro_m2"] < linked:
                    errors.append(f"Linked embryo nodes exceed donor production: {label}")
                if role == "egg_cell_donor":
                    if row["m2_isolated"] > REPRODUCTION["in_vivo_m2_limit_per_collection"]:
                        errors.append(f"Seed in-vivo MII exceeds the low-count limit: {label}")
                    if (row["m1_to_m2"] > row["gv_isolated"] + row["m1_isolated"]
                            or row["in_vitro_m2_fertilized"] > row["m1_to_m2"]
                            or row["embryos_from_in_vitro_m2"] > row["in_vitro_m2_fertilized"]
                            or row["in_vivo_m2_fertilized"] > row["m2_isolated"]):
                        errors.append(f"Impossible egg-donor stage counts: {label}")
                    frozen = sum(
                        e.get("egg_donor_name") == ipid and e.get("egg_donation_date") == stamp
                        for e in manual.get("transfers_by_id", {}).get("freezer_main", {}).get("embryos", []))
                    if row["frozen_from_in_vivo_m2"] or row["frozen_from_in_vitro_m2"] != frozen:
                        errors.append(f"Frozen donor counts differ from canonical freezer: {label}")
                else:
                    sample = next((s for s in entries if parse_record_date(s["datum"]).isoformat() == stamp), {})
                    if row.get("sample_id") != sample.get("sample_id"):
                        errors.append(f"Sperm metrics have a mismatched sample ID: {label}")
                    if (row["fertilized_in_vivo_m2"] > row["applied_to_in_vivo_m2"]
                            or row["applied_to_in_vivo_m2"] > REPRODUCTION["in_vivo_m2_limit_per_collection"]
                            or row["fertilized_in_vitro_m2"] > row["applied_to_in_vitro_m2"]
                            or row["embryos_from_in_vitro_m2"] > row["fertilized_in_vitro_m2"]):
                        errors.append(f"Impossible sperm-donor stage counts: {label}")
    if REPRODUCTION["fertilization_method"] != "IVF":
        errors.append("The declared seed fertilization method must be IVF")
    return errors


def flow_errors(core: dict[str, Any], flow: dict[str, Any], scenario: dict[str, Any]) -> list[str]:
    errors = []
    animals = {**core["animals"], **core["archived_animals"]}
    manual = flow.get("manual_data", {})
    errors.extend(donor_stage_errors(core, manual))
    transfers = manual.get("transfers_by_id", {})
    expected = {c["transfer_id"] for c in scenario["cycles"] if c["transfer_date"]}
    if any(c.get("frozen_embryos") for c in scenario["cycles"]):
        expected.add("freezer_main")
    if set(transfers) != expected:
        errors.append("Flow transfer catalogue does not match the declared reproductive cycles")
    seen_embryos = set()
    for cycle in scenario["cycles"]:
        label = cycle["key"]
        egg, sperm = cycle["egg_donor"], cycle["sperm_donor"]
        egg_day, sperm_day = parse_record_date(cycle["egg_date"]), parse_record_date(cycle["sperm_date"])
        if (sperm_day - egg_day).days != 1:
            errors.append(f"Sperm collection must be one day after eggs: {label}")
        if not any(e.get("event_type") == "oocyte_retrieval" and parse_record_date(e.get("datum")) == egg_day for e in animals[egg].get("events", [])):
            errors.append(f"Flow egg collection has no Core retrieval: {label}")
        if not any(s.get("sample_id") == cycle["sample_id"] and parse_record_date(s.get("datum")) == sperm_day for s in animals[sperm].get("sperm", [])):
            errors.append(f"Flow sperm collection has no Core sample: {label}")
        counts = [cycle[k] for k in ("gv_isolated", "m1_to_m2", "fertilized", "embryos")]
        if any(type(v) is not int or v < 0 for v in counts) or counts != sorted(counts, reverse=True):
            errors.append(f"Impossible oocyte/fertilization/embryo counts: {label}")
        egg_metrics = manual.get("egg_donors", {}).get(egg, {}).get("surgeries", {}).get(cycle["egg_date"], {})
        sperm_metrics = manual.get("sperm_donors", {}).get(sperm, {}).get("donations", {}).get(cycle["sperm_date"], {})
        egg_expected = {
            "gv_isolated": cycle["gv_isolated"], "m1_isolated": cycle["m1_isolated"],
            "m2_isolated": cycle["m2_isolated"], "m1_to_m2": cycle["m1_to_m2"],
            "in_vitro_m2_fertilized": cycle["fertilized"],
            "in_vivo_m2_fertilized": cycle["in_vivo_m2_fertilized"],
            "embryos_from_in_vitro_m2": cycle["embryos"],
        }
        sperm_expected = {
            "applied_to_in_vitro_m2": cycle["m1_to_m2"],
            "fertilized_in_vitro_m2": cycle["fertilized"],
            "embryos_from_in_vitro_m2": cycle["embryos"],
            "applied_to_in_vivo_m2": cycle["in_vivo_m2_inseminated"],
            "fertilized_in_vivo_m2": cycle["in_vivo_m2_fertilized"],
        }
        if any(egg_metrics.get(k) != v for k, v in egg_expected.items()) or any(sperm_metrics.get(k) != v for k, v in sperm_expected.items()):
            errors.append(f"Flow donor stage counts disagree with recipe: {label}")
        if cycle["in_vivo_m2_inseminated"] > cycle["m2_isolated"]:
            errors.append(f"More in-vivo MII inseminated than collected: {label}")
        if not cycle["transfer_date"]:
            if cycle["embryos"]:
                errors.append(f"Produced embryos have no declared destination: {label}")
            continue
        transfer = transfers.get(cycle["transfer_id"], {})
        transfer_day = parse_record_date(transfer.get("transfer_date"))
        if transfer_day is None or not 6 <= (transfer_day - sperm_day).days <= 9:
            errors.append(f"Transfer must be 6-9 days after sperm collection/fertilization: {label}")
        if transfer.get("surrogate_name") != cycle["surrogate_ipid"]:
            errors.append(f"Flow transfer recipient mismatch: {label}")
        if not any(e.get("event_type") == "embryo_transfer" and parse_record_date(e.get("datum")) == transfer_day for e in animals[cycle["surrogate_ipid"]].get("events", [])):
            errors.append(f"Flow transfer has no matching Core transfer: {label}")
        frozen = [e for e in transfers.get("freezer_main", {}).get("embryos", [])
                  if e.get("egg_donor_name") == egg and e.get("egg_donation_date") == cycle["egg_date"]]
        expected_frozen = cycle.get("frozen_embryos", 0)
        if len(frozen) != expected_frozen or len(transfer.get("embryos", [])) != cycle["embryos"] - expected_frozen:
            errors.append(f"Flow transfer/freezer allocation differs from recipe: {label}")
        for embryo in frozen:
            if (not embryo.get("cryopreserved") or embryo.get("implanted") or embryo.get("offspring_ipid")
                    or parse_record_date(embryo.get("freeze_date")) != parse_record_date(cycle.get("freeze_date"))
                    or parse_record_date(embryo.get("freeze_date")) is None
                    or parse_record_date(embryo.get("freeze_date")) < sperm_day):
                errors.append(f"Invalid seed freezing/provenance: {label}")
        embryos = transfer.get("embryos", []) + frozen
        if len(embryos) != cycle["embryos"]:
            errors.append(f"Flow embryo count mismatch: {label}")
        for embryo in embryos:
            embryo_id = embryo.get("embryo_id")
            if not embryo_id or embryo_id in seen_embryos:
                errors.append(f"Missing or duplicate Flow embryo identity: {label}")
            seen_embryos.add(embryo_id)
            for key, value in (("egg_donor_name", egg), ("sperm_donor_name", sperm), ("egg_donation_date", cycle["egg_date"]), ("sperm_donation_id", cycle["sperm_date"]), ("stage", "in_vitro_m2")):
                if embryo.get(key) != value:
                    errors.append(f"Flow embryo {embryo_id} has mismatched {key}")
            child = embryo.get("offspring_ipid")
            if child and (child not in animals or animals[child].get("eizellspenderin") != egg or animals[child].get("samenspender") != sperm or animals[child].get("ziehmutter") != cycle["surrogate_ipid"]):
                errors.append(f"Flow embryo {embryo_id} conflicts with Core offspring parentage")
    all_embryos = [e for t in transfers.values() for e in t.get("embryos", [])]
    if len(all_embryos) != len(seen_embryos) or {e.get("embryo_id") for e in all_embryos} != seen_embryos:
        errors.append("Unmatched or duplicated Flow embryo allocation")
    freezer = transfers.get("freezer_main")
    if freezer and (freezer.get("surrogate_name") != "FREEZER" or freezer.get("transfer_date") is not None):
        errors.append("Freezer storage must not be a surrogate transfer")
    paired_sperm_dates = {(c["sperm_donor"], c["sperm_date"]) for c in scenario["cycles"]}
    for donor, data in manual.get("sperm_donors", {}).items():
        for stamp, metrics in data.get("donations", {}).items():
            if (donor, stamp) not in paired_sperm_dates and any(metrics.get(k, 0) for k in (
                "applied_to_in_vivo_m2", "fertilized_in_vivo_m2", "embryos_from_in_vivo_m2",
                "applied_to_in_vitro_m2", "fertilized_in_vitro_m2", "embryos_from_in_vitro_m2",
            )):
                errors.append(f"Unused seed sperm sample has an invented allocation: {donor} / {stamp}")
    names = {a["name"]: k for k, a in core["animals"].items()}
    for spec in REPRODUCTION["collection_only"]:
        metrics = manual.get("egg_donors", {}).get(names[spec["egg_name"]], {}).get("surgeries", {}).get(spec["egg_date"], {})
        for target, source in (("gv_isolated", "gv_isolated"), ("m1_isolated", "m1_isolated"),
                               ("m2_isolated", "m2_isolated"), ("m1_to_m2", "m1_to_m2"),
                               ("in_vitro_m2_fertilized", "fertilized"),
                               ("in_vivo_m2_fertilized", "in_vivo_m2_fertilized"),
                               ("embryos_from_in_vitro_m2", "embryos")):
            if metrics.get(target) != spec[source]:
                errors.append(f"Collection-only donor metrics disagree with recipe: {spec['egg_name']}.{target}")
        if spec["fertilized"] or spec["in_vivo_m2_fertilized"] or spec["embryos"]:
            errors.append("Collection-only seed examples must not invent fertilization or embryos")
    if manual.get("total_embryo_count") != len(seen_embryos):
        errors.append("Flow global embryo counter does not match its embryos")
    return errors


def validate(core: dict[str, Any], records: dict[tuple[str, str], Any], scenario: dict[str, Any]) -> dict[str, Any]:
    dates = event_date_audit({"core": core, "domains": records}, "compiled")
    if dates["errors"]:
        return {"valid": False, "errors": dates["errors"], "counts": {}, "event_date_audit": dates}
    report = _validate_existing(core, records, scenario)
    for section in ('animals', 'archived_animals'):
        for ipid, animal in core[section].items():
            for field in ('chip_nr', 'special_status', 'sick_times'):
                if field not in animal:
                    report['errors'].append(f"Runtime-ready seed field missing: {ipid}/{field}")
            projected = copy.deepcopy(animal)
            ensure_project_periods(projected)
            if projected != animal:
                report['errors'].append(f"Unprepared seed project periods: {ipid}")
    report["event_date_audit"] = dates
    report["errors"].extend(measurement_errors(core))
    report["errors"].extend(enrollment_errors(core, records))
    report["counts"]["experimental_animals_with_enrollment_dates"] = sum(
        bool(record.get("in_experiment"))
        for section in ("animals", "archived_animals")
        for record in core[section].values()
    )
    flow = records[("reproduction", "flow")]
    report["errors"].extend(flow_errors(core, flow, scenario))
    report["valid"] = not report["errors"]
    report["counts"].update({"flow_transfers": sum(key != "freezer_main" for key in flow["manual_data"]["transfers_by_id"]), "flow_frozen_embryos": len(flow["manual_data"]["transfers_by_id"].get("freezer_main", {}).get("embryos", [])), "flow_embryos": flow["manual_data"]["total_embryo_count"], "reproductive_collections": len(scenario["cycles"])})
    egg_rows = [row for donor in flow["manual_data"]["egg_donors"].values()
                for row in donor["surgeries"].values()]
    report["counts"].update({
        "flow_egg_collections": len(egg_rows),
        "flow_sperm_collections": sum(len(d["donations"]) for d in flow["manual_data"]["sperm_donors"].values()),
        "ivf_m2_fertilized": sum(r["in_vitro_m2_fertilized"] for r in egg_rows),
        "in_vivo_m2_collected": sum(r["m2_isolated"] for r in egg_rows),
        "in_vivo_m2_fertilized": sum(r["in_vivo_m2_fertilized"] for r in egg_rows),
        "in_vivo_m2_embryos": sum(r["embryos_from_in_vivo_m2"] for r in egg_rows),
    })
    return report

def validate_shire_colony(mice: dict[str, Any], shire: dict[str, Any]) -> list[str]:
    """Reject incomplete litter/selection provenance before package export."""
    errors: list[str] = []
    if len(mice) > SHIRE_MOUSE_LIMIT:
        errors.append("Shire: complete mouse seed exceeds 500")
    founder_pairs = shire.get("founder_pairs", [])
    founders = [ipid for pair in founder_pairs for ipid in pair]
    if len(founder_pairs) != 8 or len(founders) != 16 or len(set(founders)) != 16:
        errors.append("Shire: expected eight distinct founder pairs")
    generations = shire.get("generation_by_ipid", {})
    seen: set[str] = set()
    by_pair: dict[tuple[str, str], list[str]] = {}
    litter_ids: set[str] = set()
    for litter in shire.get("litters", []):
        father, mother = litter["father"], litter["mother"]
        if litter["litter_id"] in litter_ids:
            errors.append("Shire: duplicate litter ID")
        litter_ids.add(litter["litter_id"])
        if mice.get(father, {}).get("sex") != "Male" or mice.get(mother, {}).get("sex") != "Female":
            errors.append("Shire: unresolved or incompatible litter parents")
        children = litter.get("offspring", [])
        if not 10 <= len(children) <= 12 or len(set(children)) != len(children):
            errors.append("Shire: litter must contain 10-12 distinct offspring")
        pair = (father, mother)
        by_pair.setdefault(pair, []).append(litter["litter_id"])
        for ipid in children:
            if ipid in seen:
                errors.append("Shire: offspring appears in multiple litters")
            seen.add(ipid)
            record = mice.get(ipid, {})
            if (record.get("samenspender"), record.get("eizellspenderin")) != pair:
                errors.append(f"Shire: litter parentage mismatch: {ipid}")
            born = parse_record_date(record.get("birth_date"))
            if born != parse_record_date(litter.get("birth_date")):
                errors.append(f"Shire: litter birth date mismatch: {ipid}")
            if generations.get(ipid) != litter["generation"]:
                errors.append(f"Shire: generation mismatch: {ipid}")
            for parent in pair:
                parent_birth = parse_record_date(mice.get(parent, {}).get("birth_date"))
                if not parent_birth or not born or parent_birth >= born:
                    errors.append(f"Shire: parent does not predate offspring: {ipid}")
    if any(not 1 <= len(litters) <= 2 for litters in by_pair.values()):
        errors.append("Shire: pair must have 1-2 litters")
    if set(generations) != set(founders) | seen:
        errors.append("Shire: generation catalogue does not match founders and offspring")
    paired: set[tuple[str, str]] = set()
    for pairing in shire.get("pairings", []):
        pair = pairing["father"], pairing["mother"]
        if pair in paired or pair not in by_pair:
            errors.append("Shire: duplicate or unpopulated breeding pair")
        paired.add(pair)
        selected = pairing.get("selected_for_breeding", [])
        if len(selected) > 4 or len(set(selected)) != len(selected):
            errors.append("Shire: more than four distinct selected breeders per pair")
        for ipid in selected:
            record = mice.get(ipid, {})
            if (record.get("samenspender"), record.get("eizellspenderin")) != pair:
                errors.append("Shire: selected breeder is not this pair's offspring")
    if paired != set(by_pair):
        errors.append("Shire: missing breeding pair provenance")
    for ipid in shire.get("terminal_ringbearers", []):
        if generations.get(ipid) != 5 or mice.get(ipid, {}).get("project") != "Ringbearer":
            errors.append("Shire: Ringbearer is not a fifth-generation study animal")
    return errors


def _validate_existing(core: dict[str, Any], records: dict[tuple[str, str], Any],
             scenario: dict[str, Any]) -> dict[str, Any]:
    all_animals = {**core["animals"], **core["archived_animals"]}
    heritage_payload = records.get(("heritage", "graph"), {})
    heritage_animals = (
        heritage_payload.get("animals", {})
        if isinstance(heritage_payload, dict)
        else {}
    )
    heritage_animals = heritage_animals if isinstance(heritage_animals, dict) else {}
    heritage_only = {
        str(ipid).strip(): value
        for ipid, value in heritage_animals.items()
        if str(ipid).strip()
        and isinstance(value, dict)
        and bool(value.get("heritage_only"))
    }
    all_identity_keys = set(all_animals) | set(heritage_only)
    errors = []
    core_ipids = {
        str(record.get("ipid", "") or "").strip()
        for record in all_animals.values()
        if isinstance(record, dict) and str(record.get("ipid", "") or "").strip()
    }
    for key, entry in heritage_only.items():
        if key in all_animals or str(entry.get("ipid", "") or "").strip() in core_ipids:
            errors.append(f"Heritage Core shadow remains in seed: {key}")
        if str(entry.get("dummy_kind", "") or "").strip().casefold() not in HERITAGE_DUMMY_KINDS:
            errors.append(f"Heritage dummy kind missing: {key}")
        if str(entry.get("persistence_kind", "") or "").strip().casefold() not in HERITAGE_DUMMY_PERSISTENCE_KINDS:
            errors.append(f"Heritage dummy persistence kind missing: {key}")
    if heritage_only:
        errors.append(
            "Heritage-owned dummy animals must not be shipped in the seed: "
            f"{len(heritage_only)} record(s) remain"
        )
    # The Ringbearer fixture is used to exercise experimental procedure plots.
    # Validate its canonical lifecycle explicitly so a seed cannot silently
    # lose the surgery events or reintroduce mouse birth rows.
    ringbearer = {
        str(record.get("name") or "").strip(): record
        for record in all_animals.values()
        if str(record.get("species") or "").strip() == "Mus musculus"
        and str(record.get("project") or "").strip() == "Ringbearer"
    }
    for name, expected_surgery_date in RINGBEARER_SURGERY_DATES.items():
        record = ringbearer.get(name)
        if record is None:
            errors.append(f"Ringbearer experimental mouse missing: {name}")
            continue
        events = [
            event for event in record.get("events", []) or []
            if isinstance(event, dict)
        ]
        birth_dates = [
            parse_record_date(event.get("datum") or event.get("date"))
            for event in events
            if str(event.get("event_type") or "").strip().casefold() == "birth"
        ]
        surgery_dates = [
            parse_record_date(event.get("datum") or event.get("date"))
            for event in events
            if str(event.get("event_type") or "").strip().casefold() == "surgery"
        ]
        if birth_dates:
            errors.append(f"Mouse birth event must not be present: {name}")
        if len(surgery_dates) != 1 or surgery_dates[0] != parse_record_date(expected_surgery_date):
            errors.append(f"Ringbearer surgery event is not canonical: {name}")
    for ipid, record in all_animals.items():
        if str(record.get("species") or "").strip() != "Mus musculus":
            continue
        if any(
            isinstance(event, dict)
            and str(event.get("event_type") or "").strip().casefold() == "birth"
            for event in record.get("events", []) or []
        ):
            errors.append(f"Mouse birth event must not be present: {ipid}")
    for (namespace, record_id), payload in sorted(records.items()):
        for path in removed_animal_reference_paths(payload):
            errors.append(
                "removed animal reference remains: "
                f"{namespace}/{record_id}{path[1:]}"
            )
        for path in heritage_dummy_reference_paths(
            payload,
            {
                str(identity).strip()
                for identity in scenario.get("retired_heritage_dummy_identities", [])
                if str(identity).strip()
            },
        ):
            errors.append(
                "dangling Heritage dummy reference remains: "
                f"{namespace}/{record_id}{path[1:]}"
            )
    for ipid, record in all_animals.items():
        if str(record.get("name") or "").strip() in REMOVED_ANIMAL_NAMES:
            errors.append(f"removed animal still present: {ipid}")
        assigned_project = str(record.get("project") or "").strip()
        if assigned_project and assigned_project not in SEED_PROJECTS:
            errors.append(f"unsupported project assignment: {ipid} -> {assigned_project}")
        for history_item in record.get("project_history", []) or []:
            if isinstance(history_item, dict):
                project = str(history_item.get("project") or "").strip()
                if project and project not in SEED_PROJECTS:
                    errors.append(
                        f"unsupported project history: {ipid} -> {project}"
                    )
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
        role_value = canonical_role_value(record.get("rolle"), default="unknown")
        expected_limits = default_experimental_limits(role_value)
        actual_limits = record.get("experimental_limits")
        if not isinstance(actual_limits, dict):
            errors.append(f"experimental limits are not a map: {ipid}")
            actual_limits = {}
        for limit_key in expected_limits:
            value = actual_limits.get(limit_key)
            if value is None:
                errors.append(f"missing experimental limit: {ipid}.{limit_key}")
                continue
            try:
                if float(value) < 0:
                    errors.append(f"negative experimental limit: {ipid}.{limit_key}")
            except (TypeError, ValueError):
                errors.append(f"invalid experimental limit: {ipid}.{limit_key}")
            if record.get(limit_key) != value:
                errors.append(f"experimental limit projection mismatch: {ipid}.{limit_key}")
        unsupported_flat = sorted(
            field for field in KNOWN_LIMIT_FIELDS
            if field not in expected_limits and field in record
        )
        if unsupported_flat:
            errors.append(
                f"unsupported experimental limit fields for {ipid}: {unsupported_flat}"
            )
        for ref_field in (
            "eizellspenderin", "samenspender", "ziehmutter", "ziehvater",
            "partner_von", "verpaart_mit",
        ):
            reference = str(record.get(ref_field, "")).strip()
            if reference and reference not in all_animals:
                errors.append(f"dangling {ref_field}: {ipid} -> {reference}")
        for relationship_field in ("partner_von", "verpaart_mit"):
            partner = str(record.get(relationship_field) or "").strip()
            if not partner or partner not in all_animals:
                continue
            partner_record = all_animals[partner]
            reciprocal = any(
                str(partner_record.get(field) or "").strip() == ipid
                for field in ("partner_von", "verpaart_mit")
            )
            if not reciprocal:
                errors.append(
                    f"one-sided {relationship_field}: {ipid} -> {partner}"
                )
    denethor = all_animals[scenario["denethor"]]
    if denethor.get("samenspender") not in all_animals:
        errors.append("Denethor father missing")
    for offspring in ("boromir", "faramir"):
        record = all_animals[scenario[offspring]]
        if record.get("samenspender") != scenario["denethor"]:
            errors.append(f"{offspring} father mismatch")
        incompatible = {
            "embryo_transfer", "pregnancy_verification", "birth", "abortion"
        }
        if any(
                isinstance(event, dict) and event.get("event_type") in incompatible
            for event in record.get("events", []) or []
        ):
            errors.append(f"incompatible reproductive event on male {offspring}")
        surrogate = str(record.get("ziehmutter") or "").strip()
        if not surrogate or surrogate not in all_animals:
            errors.append(f"{offspring} surrogate missing")
        else:
            maternal_births = [
                event
                for event in all_animals[surrogate].get("events", []) or []
                if isinstance(event, dict)
                and event.get("event_type") == "birth"
                and event.get("offspring") == scenario[offspring]
            ]
            if len(maternal_births) != 1:
                errors.append(
                    f"{offspring} maternal birth record mismatch: "
                    f"expected 1, found {len(maternal_births)}"
                )
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
    for cycle in scenario["cycles"]:
        donor = cycle["egg_donor"]
        retrieval_date = parse_record_date(cycle["egg_date"])
        events = all_animals[donor].get("events", [])
        course_id = f"FSH-{retrieval_date.isoformat()}"
        fsh = [
            event for event in events
            if event.get("event_type") == "fsh" and event.get("course_id") == course_id
        ]
        retrievals = [
            event for event in events
            if event.get("event_type") == "oocyte_retrieval"
            and event.get("course_id") == course_id
        ]
        if len(fsh) != 9 or len(retrievals) != 1:
            errors.append(f"incomplete FSH/retrieval course: {donor}")
    for record_id in ("catalog", "history"):
        payload = records.get(("projects", record_id), {})
        project_names = set((payload.get("projects") or {}).keys())
        if project_names != set(SEED_PROJECTS):
            errors.append(
                f"project {record_id} mismatch: {sorted(project_names)}"
            )
    project_catalog = records.get(("projects", "catalog"), {})
    catalog_projects = project_catalog.get("projects", {})
    for project_name, project in catalog_projects.items():
        summary = project.get("summary", {}) if isinstance(project, dict) else {}
        project_species = str(summary.get("species") or "").strip()
        assigned_animals = {
            ipid: record
            for ipid, record in all_animals.items()
            if str(record.get("project") or "").strip() == project_name
        }
        assigned_species = {
            str(record.get("species") or "").strip()
            for record in assigned_animals.values()
        }
        if not project_species:
            errors.append(f"missing project species: {project_name}")
        if not assigned_animals:
            errors.append(f"project has no assigned animals: {project_name}")
        if "" in assigned_species:
            errors.append(f"project has animal with missing species: {project_name}")
        if len(assigned_species) > 1:
            errors.append(
                f"project has mixed assigned species: {project_name} -> "
                f"{sorted(assigned_species)}"
            )
        if project_species and any(
            species and species != project_species
            for species in assigned_species
        ):
            errors.append(
                f"project species does not match assigned animals: "
                f"{project_name} -> {project_species} / {sorted(assigned_species)}"
            )
    project_history = records.get(("projects", "history"), {})
    for project_name, project in (project_history.get("projects") or {}).items():
        active_history_ipids: list[str] = []
        for index, entry in enumerate(project.get("animals", []) or []):
            ipid = str(entry.get("ipid") or "").strip() if isinstance(entry, dict) else ""
            if ipid not in all_animals:
                errors.append(
                    "dangling project-history animal: "
                    f"{project_name}[{index}] -> {ipid or '<empty>'}"
                )
            if (
                isinstance(entry, dict)
                and not entry.get("date_left")
                and str(entry.get("status") or "active").strip().casefold()
                == "active"
            ):
                active_history_ipids.append(ipid)
        expected_active_ipids = {
            ipid
            for ipid, record in core["animals"].items()
            if str(record.get("project") or "").strip() == project_name
        }
        if len(active_history_ipids) != len(set(active_history_ipids)):
            errors.append(
                f"duplicate active project-history animal: {project_name}"
            )
        if set(active_history_ipids) != expected_active_ipids:
            missing = sorted(expected_active_ipids - set(active_history_ipids))
            unexpected = sorted(set(active_history_ipids) - expected_active_ipids)
            errors.append(
                f"active project-history mismatch: {project_name}; "
                f"missing={missing}; unexpected={unexpected}"
            )
    for namespace, record_id, container_key in (
        ("heritage", "graph", "animals"),
        ("medical", "history", "animals"),
    ):
        payload = records.get((namespace, record_id), {})
        references = set((payload.get(container_key) or {}).keys())
        valid_references = all_identity_keys if namespace == "heritage" else set(all_animals)
        for reference in sorted(references - valid_references):
            errors.append(
                f"dangling {namespace} animal: {reference}"
            )
    reports = records.get(("reports", "animal-reports"), {})
    for reference in sorted(set(reports) - set(all_animals)):
        errors.append(f"dangling report animal: {reference}")
    for surrogate_index, transfer_date, outcome, _offspring in TRANSFER_SCENARIOS:
        surrogate = scenario["surrogates"][surrogate_index]
        course_id = f"ET-{surrogate_index + 1}-{transfer_date.isoformat()}"
        events = [
            event for event in all_animals[surrogate].get("events", [])
            if event.get("course_id") == course_id
        ]
        transfers = [event for event in events if event.get("event_type") == "embryo_transfer"]
        verifications = [
            event for event in events
            if event.get("event_type") == "pregnancy_verification"
        ]
        expected_result = "negative" if outcome == "not-pregnant" else "positive"
        if len(transfers) != 1 or len(verifications) != 1:
            errors.append(f"incomplete transfer/verification course: {course_id}")
        elif verifications[0].get("result") != expected_result:
            errors.append(f"pregnancy result mismatch: {course_id}")
        if outcome == "birth" and not any(
            event.get("event_type") == "birth" for event in events
        ):
            errors.append(f"birth event missing: {course_id}")
        if outcome == "abortion" and not any(
            event.get("event_type") == "abortion" for event in events
        ):
            errors.append(f"abortion event missing: {course_id}")
    for ipid, record in all_animals.items():
        sperm_events = {
            (
                str(event.get("datum") or ""),
                str(event.get("sample_id") or ""),
            )
            for event in record.get("events", [])
            if isinstance(event, dict) and event.get("event_type") == "sperm_donation"
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
            "mobile", "unit", "unit_id", "profession",
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
        if not str(user.get("unit_id") or "").startswith("org-"):
            errors.append(f"invalid canonical organization unit: {username}")
    units = records.get(("security", "organization-units"), {})
    actual_units = {str(item.get("unit_id")) for item in units.get("units", []) if isinstance(item, dict)}
    expected_units = {str(item["unit_id"]) for item in CANONICAL_ORGANIZATION_UNITS}
    if actual_units != expected_units:
        errors.append(f"organization unit catalog mismatch: {sorted(actual_units)} != {sorted(expected_units)}")
    if SEED_PROJECT_UNIT_ID not in expected_units:
        errors.append(f"seed project Unit ID is not registered: {SEED_PROJECT_UNIT_ID}")
    project_catalog = records.get(("projects", "catalog"), {})
    seed_projects = project_catalog.get("projects", {})
    for project_name, profile in PROJECT_DATA_PROFILES.items():
        project = seed_projects.get(project_name)
        iacuc = project.get("iacuc", {}) if isinstance(project, dict) else {}
        actual_unit_id = str(iacuc.get("unit") or "")
        expected_unit_id = str(profile["unit_id"])
        if actual_unit_id != expected_unit_id or actual_unit_id not in expected_units:
            errors.append(
                f"seed project IACUC Unit mismatch: {project_name} "
                f"has {actual_unit_id!r}, expected registered {expected_unit_id!r}"
            )
    housing = records[("housing", "cage")]
    structures = housing.get("structures", {})
    for reference in sorted(set(housing.get("occupants", {})) - set(core["animals"])):
        errors.append(f"dangling housed animal: {reference}")
    for reference, movements in (housing.get("movement_history", {}) or {}).items():
        if reference not in all_animals:
            errors.append(f"dangling housing-history animal: {reference}")
        for movement in movements if isinstance(movements, list) else []:
            if not isinstance(movement, dict):
                continue
            for cage_mate in movement.get("cage_mates_snapshot", []) or []:
                if cage_mate not in all_animals:
                    errors.append(
                        f"dangling housing cage-mate: {reference} -> {cage_mate}"
                    )
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
    otof = {
        str(record.get("name") or ""): record
        for record in all_animals.values()
        if str(record.get("project") or "") == "OTOF-"
    }
    for expected_name in OTOF_EXPERIMENTAL_ANIMAL_NAMES:
        record = otof.get(expected_name)
        if not record or not record.get("in_experiment"):
            errors.append(f"OTOF example cohort is not currently experimental: {expected_name}")
    if otof.get("Legolas", {}).get("rolle") != "experimental_offspring":
        errors.append("OTOF example cohort role mismatch: Legolas")

    role_configuration = records.get(("configuration", "animal-roles"), {})
    configured_roles = role_configuration.get("roles", []) if isinstance(role_configuration, dict) else []
    configured_by_value = {
        canonical_role_value(role.get("value"), default="") : role
        for role in configured_roles
        if isinstance(role, dict) and canonical_role_value(role.get("value"), default="")
    }
    for role_value in sorted({canonical_role_value(record.get("rolle"), default="unknown") for record in all_animals.values()}):
        expected = default_experimental_limits(role_value)
        configured = configured_by_value.get(role_value)
        if configured is None:
            errors.append(f"missing Role Setup definition: {role_value}")
            continue
        actual = configured.get("experimental_limit_defaults")
        if actual != expected:
            errors.append(
                f"Role Setup limit defaults mismatch: {role_value} -> {actual!r}"
            )

    mice = {
        ipid: record for ipid, record in all_animals.items()
        if str(record.get("species") or "") == "Mus musculus"
    }
    mouse_id_pattern = re.compile(r"^mm_\d{2}_\d{4}_[MFU]_[A-Za-z0-9_]+$")
    for ipid, record in mice.items():
        if any(character.isdigit() for character in str(record.get("name") or "")):
            errors.append(f"Mus musculus name contains a numeric identifier: {ipid}")
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
    errors.extend(validate_shire_colony(mice, records.get(("seed", "shire-colony"), {})))
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
            else "cage_mus_experimental_frodo_sam"
            if record.get("name") in {"Frodo", "Sam"}
            else "cage_mus_experimental_merry_pippin"
            if record.get("name") in {"Merry", "Pippin"}
            else str(occupant.get("cage_id") or "")
        )
        if record.get("name") not in {"Bilbo", "Frodo", "Sam", "Merry", "Pippin"}:
            if not expected_cage.startswith("cage_mus_shire_"):
                errors.append(f"Shire mouse lacks a colony cage: {ipid}")
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
            "heritage_owned_dummy_animals": len(heritage_only),
            "domain_records": len(records),
            "weighted_animals": sum(bool(v.get("gewicht")) for v in all_animals.values()),
            "species": len({v["species"] for v in all_animals.values()}),
            "canonical_users": len(actual_users),
            "mice": len(mice),
            "shire_litters": len(scenario.get("shire", {}).get("litters", [])),
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
                event.get("event_type") == "sperm_donation"
                for value in all_animals.values()
                for event in value.get("events", [])
                if isinstance(event, dict)
            ),
            "pregnancy_verifications": sum(
                event.get("event_type") == "pregnancy_verification"
                for value in all_animals.values()
                for event in value.get("events", [])
                if isinstance(event, dict)
            ),
        },
        "errors": errors,
        "valid": not errors,
    }
