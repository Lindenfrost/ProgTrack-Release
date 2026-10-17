"""Authored experiment admissions projected into the existing history contracts."""
from __future__ import annotations

import copy
import hashlib
from datetime import date
from typing import Any

from .common import parse_record_date
from .config import CANONICAL_USERS, load_input


def enrollment_rows(core: dict[str, Any]):
    plan = load_input("experiment-enrollments")
    if plan.get("schema") != "progtrack-seed-enrollments/1":
        raise ValueError("Invalid seed experiment-enrollment schema")
    animals = {**core["animals"], **core["archived_animals"]}
    expected = {ipid for ipid, record in animals.items() if record.get("in_experiment")}
    entries = plan.get("animals")
    if not isinstance(entries, dict) or set(entries) != expected:
        raise ValueError("Seed enrollment plan must cover exactly all currently experimental animals")
    users = {user["username"]: user for user in CANONICAL_USERS}
    actors = tuple(users[plan[key]]["display_name"] for key in ("project_actor", "experiment_actor"))
    for ipid, spec in sorted(entries.items()):
        record = animals[ipid]
        entered = date.fromisoformat(spec["project_entered"])
        started = date.fromisoformat(spec["experiment_started"])
        born = parse_record_date(record.get("birth_date"))
        if not born or not born <= entered <= started or spec["project"] != record.get("project"):
            raise ValueError(f"Invalid seed enrollment chronology/project: {ipid}")
        evidence = [parse_record_date(item.get("datum") or item.get("date"))
                    for kind in ("daten", "pdg", "sperm", "events")
                    for item in record.get(kind, []) if isinstance(item, dict)]
        if any(day and day < started for day in evidence):
            raise ValueError(f"Seed experiment starts after scientific monitoring/procedure: {ipid}")
        yield ipid, record, spec, entered, started, actors


def _event_id(ipid, kind):
    return hashlib.sha256(f"progtrack-seed-enrollment:{ipid}:{kind}".encode()).hexdigest()[:32]


def apply_experiment_enrollments(core: dict[str, Any]) -> None:
    """Set Core dates before project-period attribution; never change identity/events."""
    for ipid, record, spec, entered, started, actors in enrollment_rows(core):
        entered_iso, started_iso = entered.isoformat(), started.isoformat()
        edits = record.setdefault("edits", {})
        old_day = parse_record_date(record.get("project_entry_date"))
        if old_day and old_day != entered and old_day.isoformat() in edits:
            edits[old_day.isoformat()].pop("project_note", None)
            if not edits[old_day.isoformat()]:
                del edits[old_day.isoformat()]
        record["project_entry_date"] = entered.strftime("%d.%m.%Y")
        # Ringbearers leave their breeding project on their own transfer date,
        # not at the old blanket 2026 fixture-authoring date.
        if spec["project"] == "Ringbearer":
            for previous in record.get("project_history", []):
                if previous.get("project") == "Shire":
                    previous["leave_date"] = record["project_entry_date"]
        starts = [event for event in record.get("lifecycle_events", [])
                  if event.get("event_type") == "experiment_started"]
        if spec.get("preserve_history"):
            if not starts or parse_record_date(starts[-1].get("date")) != started:
                raise ValueError(f"Authored current experiment history disagrees: {ipid}")
        else:
            event_id = _event_id(ipid, "experiment_started")
            lifecycle = record.setdefault("lifecycle_events", [])
            lifecycle[:] = [event for event in lifecycle if event.get("event_id") != event_id]
            lifecycle.append({"event_id": event_id, "event_type": "experiment_started",
                              "date": started.strftime("%d.%m.%Y"), "actor": actors[1],
                              "role": "researcher", "reason": "", "recipient": ""})
        edits.setdefault(entered_iso, {}).setdefault("project_note", f"Assigned to project: {spec['project']}")
        edits.setdefault(started_iso, {}).setdefault("experiment_note", f"Experiment started: admitted by {actors[1]}")


def _medical_entry(ipid, record, kind, day, project, actor):
    return {"id": _event_id(ipid, "medical:" + kind), "date": day.isoformat(),
            "entry_type": kind, "status_type": "other", "issue_id": "",
            "condition_key": "", "condition_label_snapshot": project if kind == "project_assigned" else "",
            "condition_path_keys": [], "condition_path_label_snapshot": [],
            "condition_language": "en", "condition_depth": 0,
            "note": f"Assigned to project: {project}" if kind == "project_assigned" else f"{record['name']} admitted to {project}",
            "signature": actor, "linked_document_ids": []}


def project_experiment_enrollments(core: dict[str, Any], records: dict[tuple[str, str], Any]) -> None:
    """Align existing Project/Medi histories with Core; Reports reads Core edits."""
    projects = records[("projects", "history")]["projects"]
    medical = records[("medical", "history")].setdefault("animals", {})
    for ipid, record, spec, entered, started, actors in enrollment_rows(core):
        project = spec["project"]
        current = [entry for entry in projects[project]["animals"] if entry.get("ipid") == ipid
                   and not entry.get("date_left") and entry.get("status") == "active"]
        if len(current) != 1:
            raise ValueError(f"Expected one current project-history entry: {ipid}")
        current[0]["date_entered"] = record["project_entry_date"]
        current[0]["had_in_experiment"] = True
        if project == "Ringbearer":
            for entry in projects["Shire"]["animals"]:
                if entry.get("ipid") == ipid and entry.get("status") == "former":
                    entry["date_left"] = record["project_entry_date"]
                    current[0]["previous_project_snapshot"] = [copy.deepcopy(entry)]
        history = medical.setdefault(ipid, {"animal_id": record.get("id", ""), "documents": [], "entries": []})
        entries = history.setdefault("entries", [])
        # The old OTOF -> OTOF- spelling correction is not a real project exit.
        if project == "OTOF-":
            entries[:] = [entry for entry in entries if not (
                entry.get("entry_type") == "project_left"
                and entry.get("condition_label_snapshot", "").split(" (")[0] == "OTOF")]
        for kind, day, actor in (("project_assigned", entered, actors[0]),
                                  ("experiment_started", started, actors[1])):
            matches = [entry for entry in entries if entry.get("entry_type") == kind
                       and (kind != "project_assigned" or entry.get("condition_label_snapshot", "").split(" (")[0] == project)]
            if matches:
                target = max(matches, key=lambda entry: parse_record_date(entry.get("date")) or date.min)
                if not spec.get("preserve_history"):
                    target["date"] = day.isoformat()
            else:
                entries.append(_medical_entry(ipid, record, kind, day, project, actor))
        entries.sort(key=lambda entry: entry["date"])


def enrollment_errors(core: dict[str, Any], records: dict[tuple[str, str], Any]) -> list[str]:
    """Fail compilation when dates, cross-plugin projections or periods diverge."""
    errors = []
    try:
        rows = list(enrollment_rows(core))
    except (ValueError, KeyError, TypeError) as exc:
        return [f"experiment enrollment: {exc}"]
    projects = records[("projects", "history")]["projects"]
    medical = records[("medical", "history")].get("animals", {})
    for ipid, record, spec, entered, started, _actors in rows:
        if parse_record_date(record.get("project_entry_date")) != entered:
            errors.append(f"experiment enrollment Core project date: {ipid}")
        starts = [parse_record_date(event.get("date")) for event in record.get("lifecycle_events", [])
                  if event.get("event_type") == "experiment_started"]
        if not starts or starts[-1] != started:
            errors.append(f"experiment enrollment Core start date: {ipid}")
        active = [entry for entry in projects.get(spec["project"], {}).get("animals", [])
                  if entry.get("ipid") == ipid and not entry.get("date_left") and entry.get("status") == "active"]
        if len(active) != 1 or parse_record_date(active[0].get("date_entered")) != entered:
            errors.append(f"experiment enrollment Project Track date: {ipid}")
        for kind, day in (("project_assigned", entered), ("experiment_started", started)):
            matches = [parse_record_date(entry.get("date")) for entry in medical.get(ipid, {}).get("entries", [])
                       if entry.get("entry_type") == kind and (kind != "project_assigned"
                           or entry.get("condition_label_snapshot", "").split(" (")[0] == spec["project"])]
            if day not in matches or any(item and item > day for item in matches):
                errors.append(f"experiment enrollment Medi Track {kind}: {ipid}")
        edits = record.get("edits", {})
        if not edits.get(entered.isoformat(), {}).get("project_note") or not edits.get(started.isoformat(), {}).get("experiment_note"):
            errors.append(f"experiment enrollment Reports notes: {ipid}")
        for previous in record.get("project_history", []):
            left = parse_record_date(previous.get("leave_date"))
            if left and left > entered:
                errors.append(f"experiment enrollment overlapping former project: {ipid}")
    return errors
