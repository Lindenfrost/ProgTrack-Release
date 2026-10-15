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
from Plugins.core.identity_conventions import render_animal_id
from .config import *
from .common import *
from .reproduction import build_flow, seed_users

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
        ("cage_mus_experimental_frodo_sam", "C001", 0),
        ("cage_mus_experimental_merry_pippin", "C002", 1),
        ("cage_mus_breeding", "C003", 2),
        ("cage_mus_bilbo", "C004", 3),
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
    # Fixture object order is not a placement policy. Defaults are authored
    # explicitly; valid individual placements below always take precedence.
    cage_choices = DEFAULT_HOUSING_CAGES
    # Existing sample mice are moved into the dedicated Mouse House below;
    # never leave them mixed with another species in the legacy sample cage.
    for ipid in list(occupants):
        animal = active_animals.get(ipid)
        if animal and str(animal.get("species") or "") == "Mus musculus":
            occupants.pop(ipid, None)
    # The enlarged Shire colony must not be put into one mixed-sex cage.
    # Preserve the existing experimental cages/Bilbo witness; seed ordinary
    # breeding pairs separately and remaining animals in small same-sex groups.
    mouse_cage_assignments: dict[str, str] = {}
    colony = {
        ipid: animal for ipid, animal in sorted(active_animals.items())
        if animal.get("species") == "Mus musculus"
        and animal.get("name") not in {"Bilbo", "Frodo", "Sam", "Merry", "Pippin"}
    }

    def colony_cage(members: list[str]) -> None:
        index = len({*mouse_cage_assignments.values()}) + 1
        cage_id = f"cage_mus_shire_{index:03d}"
        cages[cage_id] = {
            "display_name": f"S{index:03d}", "id": cage_id, "order": index + 3,
            "parent_room_id": "room_mus_colony", "virtual": False,
        }
        mouse_cage_assignments.update({ipid: cage_id for ipid in members})

    for ipid, animal in colony.items():
        partner = str(animal.get("verpaart_mit") or animal.get("partner_von") or "")
        if ipid not in mouse_cage_assignments and partner in colony and partner not in mouse_cage_assignments:
            if animal.get("sex") != colony[partner].get("sex"):
                colony_cage([ipid, partner])
    for sex in ("Male", "Female"):
        unpaired = [ipid for ipid, animal in colony.items()
                    if animal.get("sex") == sex and ipid not in mouse_cage_assignments]
        for index in range(0, len(unpaired), 5):
            colony_cage(unpaired[index:index + 5])
    def valid_cage(cage_id: str) -> bool:
        cage = cages.get(cage_id, {})
        room = structures.get("rooms", {}).get(cage.get("parent_room_id"), {})
        unit = structures.get("units", {}).get(room.get("parent_unit_id"), {})
        building = structures.get("buildings", {}).get(
            unit.get("parent_building_id"), {}
        )
        return bool(cage and room and unit and building and cage_id != "cage_unassigned")

    for species, cage_id in cage_choices.items():
        if not valid_cage(cage_id):
            raise ValueError(f"Invalid seed housing default for {species}: {cage_id}")

    for ipid, animal in active_animals.items():
        if ipid in occupants and valid_cage(str(occupants[ipid].get("cage_id", ""))):
            continue
        if animal.get("species") == "Mus musculus":
            name = str(animal.get("name") or "")
            if name == "Bilbo":
                cage_id = "cage_mus_bilbo"
            elif name in {"Frodo", "Sam"}:
                cage_id = "cage_mus_experimental_frodo_sam"
            elif name in {"Merry", "Pippin"}:
                cage_id = "cage_mus_experimental_merry_pippin"
            elif animal.get("in_experiment"):
                cage_id = "cage_mus_experimental_frodo_sam"
            else:
                cage_id = mouse_cage_assignments.get(ipid, "cage_mus_breeding")
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


def complete_heritage(
    core: dict[str, Any] | None = None,
    legacy: Any = None,
) -> dict[str, Any]:
    """Return the clean shipped Heritage graph.

    Heritage Track still supports direct and former-Core dummy records when a
    user explicitly creates them at runtime.  Those records are authoring
    fixtures, however, not canonical example data.  The archived Heritage
    JSON contains stale dummy animals and is therefore deliberately not
    projected into the package at all.  Core animals are projected read-only
    by Heritage Track from the backend's canonical Core records.

    ``core`` and ``legacy`` remain accepted for callers of the seed helper,
    but are intentionally ignored so an archived dummy cannot re-enter the
    shipped package through an alternate invocation.
    """
    _ = core, legacy
    return {
        "version": "2.0.0",
        "updated_at": SEED_CREATED,
        "animals": {},
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
    for index, (ipid, animal) in enumerate((ipid, core["animals"][ipid]) for ipid in ('Adalgrim Baggins Tall | Mus musculus | 01.02.2020 | DPZ', 'Adalgrim Boffin Fat | Mus musculus | 01.08.2020 | DPZ', 'Adalgrim Boffin Tall | Mus musculus | 01.09.2019 | DPZ', 'Adalgrim Bolger Tall | Mus musculus | 01.09.2019 | DPZ')):
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


def _project_arrive_fields(profile: dict[str, Any]) -> dict[str, str]:
    focus = profile["focus"]
    title = profile["title"]
    count = profile["approved_count"]
    return {
        "study_design": f"Prospective fictional example protocol: {focus}",
        "sample_size": f"Approved example cohort: {count} animals; actual assignments are visible in Project Track.",
        "inclusion_exclusion": "Include healthy animals meeting the recorded species, age, role, and welfare criteria. Exclude animals with incompatible health findings or housing constraints.",
        "randomisation": "Where an experimental comparison is applicable, assign animals using a documented sequence; breeding and husbandry records remain family-based.",
        "blinding": "Measurement and outcome review should be performed using animal IDs and role context; unblinding is documented when welfare action requires it.",
        "outcome_measures": f"Record the measurements, reproductive events, welfare observations, and project status needed to evaluate {title}.",
        "statistical_methods": "Summarise longitudinal measurements descriptively and preserve the full animal-level records for later analysis.",
        "experimental_animals": f"The approved example cohort contains {count} animals across the configured role groups.",
        "experimental_procedures": f"Procedures are fictional and limited to those represented by the project's recorded events: {focus.lower()}",
        "results": "Example dataset: records are intentionally populated for workflow testing; no real scientific conclusion is claimed.",
        "abstract": f"{title}. This fictional protocol demonstrates complete project metadata, role assignments, welfare oversight, and animal-linked records.",
        "background": f"The example project provides a controlled context for {focus.lower()}",
        "objectives": focus,
        "ethical_statement": "Animal use is fictional seed data. Any real implementation must follow current institutional, legal, and veterinary approvals.",
        "housing_husbandry": "Animals are maintained in the configured facility structures for the relevant species and role; husbandry entries remain linked to the animal records.",
        "animal_care": "Keeper and veterinary staff review routine care, health status, deviations, and welfare observations throughout the project.",
        "interpretation": "Interpret results together with animal history, role, housing, welfare observations, and protocol deviations; do not infer beyond the recorded example data.",
        "protocol_registration": f"Internal fictional protocol {profile['protocol']} / {profile['internal']}.",
        "data_access": "Project-associated investigators can view project-linked animal data; Manager controls project configuration and AWO retains welfare oversight.",
        "declaration_interests": "No conflicts of interest are recorded for this fictional example protocol.",
    }


def _normalise_project_history_entries(entries: Any) -> list[dict[str, Any]]:
    """Keep only supported project history entries."""
    if not isinstance(entries, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for raw in entries:
        if not isinstance(raw, dict):
            continue
        project = canonical_project_name(raw.get("project"))
        if not project:
            continue
        item = copy.deepcopy(raw)
        item["project"] = project
        for field in ("previous_project_snapshot", "previous_experimental_snapshot"):
            if field in item:
                item[field] = _normalise_project_history_entries(item[field])
        key = (
            project,
            str(item.get("ipid") or item.get("name") or ""),
            str(item.get("date_entered") or item.get("entry_date") or ""),
            str(item.get("date_left") or item.get("leave_date") or ""),
            str(item.get("status") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _apply_otof_example_experiment_status(core: dict[str, Any]) -> None:
    """Keep the reviewed OTOF example cohort marked as currently experimental."""
    all_animals = {
        **core.get("animals", {}),
        **core.get("archived_animals", {}),
    }
    for record in all_animals.values():
        if not isinstance(record, dict):
            continue
        name = str(record.get("name") or "").strip()
        if name not in OTOF_EXPERIMENTAL_ANIMAL_NAMES:
            continue
        record["project"] = "OTOF-"
        record["in_experiment"] = True
        if name == "Legolas":
            record["rolle"] = "experimental_offspring"
            record["role_id"] = "experimental_offspring"


def _normalise_seed_project_assignments(core: dict[str, Any]) -> None:
    """Remove obsolete project assignments from all seed animal records."""
    for section in ("animals", "archived_animals"):
        for record in core.get(section, {}).values():
            if not isinstance(record, dict):
                continue
            record["project"] = canonical_project_name(record.get("project"))
            if "project_history" in record:
                record["project_history"] = _normalise_project_history_entries(
                    record.get("project_history")
                )


def _normalise_seed_partner_relationships(core: dict[str, Any]) -> None:
    """Make each sample partner/breeding relationship reciprocal by role."""
    all_animals = {
        **core.get("animals", {}),
        **core.get("archived_animals", {}),
    }
    edges: set[tuple[str, str]] = set()
    for subject, record in all_animals.items():
        for field in ("partner_von", "verpaart_mit"):
            target = resolve_animal_reference(all_animals, record.get(field))
            if target and target != subject:
                edges.add(tuple(sorted((subject, target))))
    for left, right in sorted(edges):
        left_record = all_animals[left]
        right_record = all_animals[right]
        left_is_partner = canonical_role_value(
            left_record.get("rolle")
        ) == "partner_animal"
        right_is_partner = canonical_role_value(
            right_record.get("rolle")
        ) == "partner_animal"
        for record, counterpart in (
            (left_record, right), (right_record, left)
        ):
            for field in ("partner_von", "verpaart_mit"):
                if resolve_animal_reference(all_animals, record.get(field)) == counterpart:
                    record[field] = ""
        if left_is_partner and not right_is_partner:
            left_record["partner_von"] = right
            right_record["verpaart_mit"] = left
        elif right_is_partner and not left_is_partner:
            right_record["partner_von"] = left
            left_record["verpaart_mit"] = right
        elif left_is_partner and right_is_partner:
            left_record["partner_von"] = right
            right_record["partner_von"] = left
        else:
            left_record["verpaart_mit"] = right
            right_record["verpaart_mit"] = left


def _normalise_project_payload(payload: Any, *, history: bool) -> dict[str, Any]:
    """Filter a legacy project payload to the supported seed projects."""
    if not isinstance(payload, dict):
        payload = {"version": 1, "projects": {}}
    result = {key: copy.deepcopy(value) for key, value in payload.items() if key != "projects"}
    result.setdefault("version", 1)
    projects: dict[str, dict[str, Any]] = {}
    raw_projects = payload.get("projects")
    if not isinstance(raw_projects, dict):
        raw_projects = {}
    for old_name, raw_record in raw_projects.items():
        name = canonical_project_name(old_name)
        if not name or not isinstance(raw_record, dict):
            continue
        record = copy.deepcopy(raw_record)
        if history:
            record["animals"] = _normalise_project_history_entries(
                record.get("animals")
            )
        existing = projects.setdefault(name, {})
        # Keep supported metadata and merge history entries when a legacy
        # source contains duplicate records for the same project.
        for key, value in record.items():
            if key == "animals" and history:
                existing[key] = _normalise_project_history_entries(
                    list(existing.get(key) or []) + list(value or [])
                )
            elif key not in existing or not existing[key]:
                existing[key] = value
    result["projects"] = projects
    return result


def _remove_obsolete_project_medical_entries(medical: Any) -> Any:
    """Drop medical audit rows that only describe deleted projects."""
    if not isinstance(medical, dict):
        return medical
    result = copy.deepcopy(medical)
    for animal in (result.get("animals") or {}).values():
        if not isinstance(animal, dict) or not isinstance(animal.get("entries"), list):
            continue
        kept = []
        for entry in animal["entries"]:
            if not isinstance(entry, dict):
                kept.append(entry)
                continue
            text = " ".join(
                str(entry.get(field) or "")
                for field in ("condition_label_snapshot", "note")
            )
            obsolete = any(
                project in text for project in ("Anode", "Crossbreeding", "Zucht")
            )
            if obsolete and entry.get("entry_type") in {
                "project_assigned", "project_left", "severity_changed"
            }:
                continue
            kept.append(entry)
        animal["entries"] = kept
    return result


def _ensure_project_history(project_history: dict[str, Any], project_name: str,
                            core: dict[str, Any], start_date: str) -> None:
    projects = project_history.setdefault("projects", {})
    record = projects.setdefault(project_name, {"animals": [], "archived": False})
    entries = record.setdefault("animals", [])
    known = {
        str(item.get("ipid") or "")
        for item in entries
        if isinstance(item, dict) and item.get("ipid")
    }
    all_animals = {
        **core.get("animals", {}),
        **core.get("archived_animals", {}),
    }
    for ipid, animal in sorted(all_animals.items()):
        if str(animal.get("project") or "").strip() != project_name:
            continue
        ipid = str(ipid)
        if ipid in known:
            continue
        archived = ipid in core.get("archived_animals", {})
        entries.append({
            "ipid": ipid,
            "name": animal.get("name", ""),
            "date_entered": start_date,
            "date_left": start_date if archived else None,
            "status": "former" if archived else "active",
            "last_severity": None,
            "had_in_experiment": bool(animal.get("in_experiment")),
            "previous_in_experiment": False,
            "previous_project_snapshot": [],
            "previous_experimental_snapshot": [],
        })
        known.add(ipid)
    record["animals"] = sorted(
        entries,
        key=lambda item: (str(item.get("name") or "").casefold(), str(item.get("ipid") or "")),
    )


def _enrich_project_catalog(project_catalog: dict[str, Any],
                            project_history: dict[str, Any],
                            core: dict[str, Any]) -> None:
    """Populate complete, deterministic Project Track example metadata."""
    projects = project_catalog.setdefault("projects", {})
    for name, profile in PROJECT_DATA_PROFILES.items():
        role_order = profile["role_order"]
        if len(role_order) != len(set(role_order)) or set(role_order) != set(profile["roles"]):
            raise ValueError(f"Project role_order must name every role exactly once: {name}")
        record = projects.setdefault(name, {})
        record["status"] = "active"
        record["summary"] = {
            **(record.get("summary") if isinstance(record.get("summary"), dict) else {}),
            "title": profile["title"],
            "species": profile["species"],
            "comment": profile["comment"],
            "contact1_login": "Researcher",
            "contact2_login": "Vet",
            "contacts_other_logins": ["Manager", "Keeper", "Veti"],
        }
        record["iacuc"] = {
            **(record.get("iacuc") if isinstance(record.get("iacuc"), dict) else {}),
            "short_title": name,
            "protocol_id": profile["protocol"],
            "internal_number": profile["internal"],
            "authorization_nr": profile["authorization"],
            "pi_login": "Researcher",
            "di_login": "Vet",
            "welfare_login": "Veti",
            "unit": profile["unit_id"],
            "purpose": profile["focus"],
            "authorized": "04.08.2026",
            "approved": "04.08.2026",
        }
        record["assoc_users"] = {
            **(record.get("assoc_users") if isinstance(record.get("assoc_users"), dict) else {}),
            "applicant_login": "Manager",
            "planning_login": "Researcher",
            "staff_logins": ["Keeper", "Vet", "Veti"],
        }
        record["animals_config"] = {
            "approved_count": profile["approved_count"],
            "departed_with_sev_no_legal": 0,
            "roles": [
                {"role": role, "count": profile["roles"][role]}
                for role in role_order
            ],
        }
        record["arrive"] = _project_arrive_fields(profile)
        record.setdefault("created_at", profile["start_date"] + " 09:00")
        record.setdefault("created_by", "Dr. Manager Plansdottir")
        record["modified_at"] = "04.08.2026 14:30"
        record["modified_by"] = "Dr. Manager Plansdottir"
        _ensure_project_history(project_history, name, core, profile["start_date"])


def domain_records(core: dict[str, Any], key_map: dict[str, str],
                   scenario: dict[str, Any]) -> dict[tuple[str, str], Any]:
    records: dict[tuple[str, str], Any] = {}
    legacy_heritage = {}
    # Keep the archived identity set only for dangling-reference validation;
    # the legacy records themselves are never inserted into the package.
    scenario["retired_heritage_dummy_identities"] = sorted(
        heritage_dummy_identity_values(legacy_heritage)
    )
    legacy = {
        ("projects", "catalog"): load_input("projects-catalog"),
        ("projects", "history"): load_input("projects-history"),
        ("housing", "cage"): load_input("housing-cage"),
        ("housing", "inspections"): load_input("housing-inspections"),
        ("medical", "history"): load_input("medical-history"),
        ("reports", "animal-reports"): load_input("reports-animal-reports"),
        ("samples", "organs"): load_input("samples-organs"),
        ("samples", "other"): load_input("samples-other"),
    }
    for key, value in legacy.items():
        records[key] = prune_removed_animal_references(
            rewrite_references(value, key_map)
        )
    # Embryo Track reference curves are static seed material only.  They are
    # copied into the backend-owned record so runtime reads never fall back to
    # the plugin JSON after initialization.
    records[("embryo-track", "cranimetry-reference")] = load_input("craniometry")
    project_catalog = records[("projects", "catalog")]
    project_catalog = _normalise_project_payload(project_catalog, history=False)
    records[("projects", "catalog")] = project_catalog
    project_catalog.setdefault("projects", {})["Ringbearer"] = {
        "status": "active",
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
            "unit": PROJECT_DATA_PROFILES["Ringbearer"]["unit_id"],
        },
        "animals_config": {
            "approved_count": 4,
            "departed_with_sev_no_legal": 0,
            "roles": [{"role": "experimental_animal", "count": 4}],
        },
        "arrive": {},
        "created_at": "22.07.2026 10:00",
        "created_by": "Dr. Manager Plansdottir",
        "modified_at": "22.07.2026 10:00",
        "modified_by": "Dr. Manager Plansdottir",
    }
    project_history = _normalise_project_payload(
        records[("projects", "history")], history=True
    )
    records[("projects", "history")] = project_history
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
    _enrich_project_catalog(project_catalog, project_history, core)
    # Shire provenance survives transfer into Ringbearer, without assigning
    # either animal to two current projects or erasing the study's procedures.
    shire_history = project_history["projects"]["Shire"]["animals"]
    for entry in shire_history:
        birthday = parse_record_date(core["animals"].get(entry["ipid"], {}).get("birth_date"))
        if birthday:
            entry["date_entered"] = max(date(2019, 1, 1), birthday).strftime("%d.%m.%Y")
    for ipid in scenario["mouse"]["experimental"]:
        animal = core["animals"][ipid]
        former = {
            "project": "Shire", "ipid": ipid, "name": animal["name"],
            "date_entered": animal["birth_date"], "date_left": "22.07.2026",
            "status": "former", "last_severity": None,
            "had_in_experiment": False, "previous_in_experiment": False,
            "previous_project_snapshot": [], "previous_experimental_snapshot": [],
        }
        shire_history.append(former)
        next(entry for entry in project_history["projects"]["Ringbearer"]["animals"]
             if entry["ipid"] == ipid)["previous_project_snapshot"] = [copy.deepcopy(former)]
    records[("seed", "shire-colony")] = copy.deepcopy(scenario["shire"])
    records[("housing", "cage")] = complete_housing(
        records[("housing", "cage")], core["animals"]
    )
    records[("heritage", "graph")] = complete_heritage()
    medical = _remove_obsolete_project_medical_entries(
        records[("medical", "history")]
    )
    medical, reports = add_resolved_histories(
        medical,
        records[("reports", "animal-reports")],
        core,
    )
    records[("medical", "history")] = medical
    records[("reports", "animal-reports")] = reports
    records[("reproduction", "flow")] = build_flow(scenario, core)
    records[("security", "users")] = seed_users()
    records[("security", "organization-units")] = {
        "schema_version": 1,
        "units": [dict(item) for item in CANONICAL_ORGANIZATION_UNITS],
    }
    records[("configuration", "global-settings")] = {"language": "en"}
    records[("configuration", "disabled-plugins")] = []
    records[("configuration", "animal-roles")] = seeded_animal_role_configuration()
    records[("seed", "metadata")] = {
        "version": "0.2.1",
        "fictional": True,
        "created_at": SEED_CREATED,
        "scenario_ids": [
            "denethor-family", "otof-success", "otof-poor-yield",
            "otof-low-development", "surrogate-nonpregnant", "surrogate-abortions",
            "mild-resolved-medical", "all-species-weights", "four-level-housing",
            "mus-musculus-ringbearer-colony", "mouse-house-group-housing",
            "shire-five-generation-breeding-colony",
        ],
    }
    # Archived authoring snapshots contain a handful of obsolete medical and
    # report keys that no longer have a Core animal.  A canonical backend seed
    # must not retain plugin-local ghosts: those records cannot be opened,
    # housed, reported on, or resolved through an immutable IPID.
    known_animals = {
        **core.get("animals", {}),
        **core.get("archived_animals", {}),
    }
    # Medical records are Core-owned and must remain keyed only by real
    # animals.  The shipped Heritage graph is intentionally dummy-free;
    # explicitly authored runtime dummies are not seed data.
    for domain_key, container_key in ((("medical", "history"), "animals"),):
        payload = records.get(domain_key, {})
        container = payload.get(container_key, {}) if isinstance(payload, dict) else {}
        if isinstance(container, dict):
            payload[container_key] = {
                ipid: value
                for ipid, value in container.items()
                if ipid in known_animals
            }
    heritage_payload = records.get(("heritage", "graph"), {})
    if isinstance(heritage_payload, dict):
        heritage_animals = heritage_payload.get("animals", {})
        heritage_payload["animals"] = {
            ipid: value
            for ipid, value in heritage_animals.items()
            if ipid in known_animals
        } if isinstance(heritage_animals, dict) else {}
    report_payload = records.get(("reports", "animal-reports"), {})
    if isinstance(report_payload, dict):
        records[("reports", "animal-reports")] = {
            ipid: value
            for ipid, value in report_payload.items()
            if ipid in known_animals
        }
    return records
