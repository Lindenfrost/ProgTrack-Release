# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure deterministic scheduling primitives for Surgery Planner."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
import hashlib
import json


@dataclass(frozen=True)
class PlannerSettings:
    recovery_op: int = 60
    recovery_transfer: int = 30
    transfer_offset_days: int = 6
    donors_per_surgery: int = 2
    surrogates_per_transfer: int = 2
    surgery_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    transfer_weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class PlannerAnimal:
    ipid: str
    role: str
    op_max: int = 0
    transfer_max: int = 0
    performed_ops: int = 0
    performed_transfers: int = 0


@dataclass(frozen=True)
class PlannerSnapshot:
    animals: tuple[PlannerAnimal, ...]
    settings: PlannerSettings
    horizon_start: date
    horizon_end: date
    blocked_days: tuple[date, ...] = ()
    revision: str = "1"

    @classmethod
    def from_inputs(cls, animals, settings, horizon_start, horizon_end, blocked_days=()):
        def integer(value, default=0):
            try:
                return max(int(value), 0)
            except (TypeError, ValueError):
                return default

        def weekdays(value):
            if isinstance(value, set):
                value = sorted(value)
            if not isinstance(value, (list, tuple)):
                return (0, 1, 2, 3, 4)
            return tuple(sorted({int(v) for v in value if str(v).lstrip("-").isdigit() and 0 <= int(v) <= 6}))

        rows = []
        for raw in animals or ():
            if not isinstance(raw, dict):
                continue
            ipid = str(raw.get("ipid") or raw.get("name") or "").strip()
            if not ipid:
                continue
            canonical_events = [
                event for event in raw.get("events", ()) or ()
                if isinstance(event, dict)
            ]
            performed_ops = sum(1 for event in canonical_events if event.get("typ") == "surgery")
            performed_transfers = sum(
                1 for event in canonical_events if event.get("typ") == "embryo_transfer"
            )
            rows.append(PlannerAnimal(
                ipid, str(raw.get("rolle") or raw.get("role") or "").strip(),
                integer(raw.get("OP_max", raw.get("max_op", 0))),
                integer(raw.get("Embryo_max", raw.get("max_embryo", 0))),
                performed_ops,
                performed_transfers,
            ))
        rows.sort(key=lambda item: item.ipid.casefold())
        cfg = PlannerSettings(
            integer(settings.get("recovery_op", 60)),
            integer(settings.get("recovery_transfer", 30)),
            integer(settings.get("transfer_offset_days", 6)),
            integer(settings.get("donors_per_surgery", 2)),
            integer(settings.get("surrogates_per_transfer", 2)),
            weekdays(settings.get("surgery_weekdays")),
            weekdays(settings.get("transfer_weekdays")),
        )
        blocked = tuple(sorted({d for d in blocked_days if isinstance(d, date)}))
        payload = {
            "animals": [r.__dict__ for r in rows],
            "settings": cfg.__dict__,
            "start": horizon_start.isoformat(),
            "end": horizon_end.isoformat(),
            "blocked": [d.isoformat() for d in blocked],
        }
        revision = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]
        return cls(tuple(rows), cfg, horizon_start, horizon_end, blocked, revision)


@dataclass(frozen=True)
class ScheduleCandidate:
    candidate_id: str
    ipid: str
    event_type: str
    scheduled_date: date
    source_revision: str


def stable_schedule_id(ipid, event_type, scheduled_date, ordinal=0):
    raw = "|".join((str(ipid), str(event_type), scheduled_date.isoformat(), str(int(ordinal))))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def generate_preview_candidates(snapshot):
    result, blocked = [], set(snapshot.blocked_days)
    for animal in snapshot.animals:
        role = animal.role.casefold()
        if "spender" in role or "donor" in role:
            event, remaining, days = "op", max(animal.op_max - animal.performed_ops, 0), snapshot.settings.surgery_weekdays
        elif "amme" in role or "surrogate" in role:
            event, remaining, days = "embryoübertragung", max(animal.transfer_max - animal.performed_transfers, 0), snapshot.settings.transfer_weekdays
        else:
            continue
        current, ordinal = snapshot.horizon_start, 0
        while current <= snapshot.horizon_end and ordinal < remaining:
            if current.weekday() in days and current not in blocked:
                result.append(ScheduleCandidate(stable_schedule_id(animal.ipid, event, current, ordinal), animal.ipid, event, current, snapshot.revision))
                ordinal += 1
            current += timedelta(days=1)
    return tuple(sorted(result, key=lambda item: (item.scheduled_date, item.event_type, item.ipid.casefold(), item.candidate_id)))
