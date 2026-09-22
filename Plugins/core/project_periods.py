"""Canonical project-membership periods and per-animal scoped usage.

Project display names are not identities.  This module gives every assignment
period a stable record-local ID and provides one small, dependency-free
boundary for event/measurement counters.  Existing records without period
metadata are attributed deterministically from ``project_history``; records
with no project context retain the legacy unscoped behavior for compatibility
with non-experimental animals.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Any, Iterable, Mapping


UNASSIGNED_PERIOD_ID = "unassigned"


def _day(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    return None


def _period_id(project: Any, start: Any, end: Any, ordinal: int) -> str:
    payload = "|".join((str(project or "").strip(), str(start or ""), str(end or ""), str(ordinal)))
    return "project-period-" + hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def make_project_period_id(
    project: Any,
    start: Any = "",
    end: Any = "",
    ordinal: int = 0,
) -> str:
    """Create the stable identity for a newly recorded membership period."""
    return _period_id(project, start, end, ordinal)


def has_project_context(record: Mapping[str, Any]) -> bool:
    return bool(
        str(record.get("project") or "").strip()
        or record.get("project_history")
        or str(record.get("project_period_id") or "").strip()
    )


def ensure_project_periods(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize project periods and attribute unscoped records once.

    The function is intentionally idempotent.  It never changes an existing
    event's period assignment, which protects historical data after a later
    project change.
    """
    if not isinstance(record, dict):
        return []
    history = record.get("project_history")
    if not isinstance(history, list):
        history = []
    current_project = str(record.get("project") or "").strip()
    if not current_project and not history and not str(
        record.get("project_period_id") or ""
    ).strip():
        return []

    periods: list[dict[str, Any]] = []
    for index, entry in enumerate(history):
        if not isinstance(entry, dict):
            continue
        project = str(entry.get("project") or "").strip()
        if not project:
            continue
        start = str(entry.get("entry_date") or "").strip()
        end = str(entry.get("leave_date") or "").strip()
        period_id = str(entry.get("period_id") or "").strip()
        if not period_id:
            period_id = _period_id(project, start, end, index)
            entry["period_id"] = period_id
        period = {
            "period_id": period_id,
            "project": project,
            "entry_date": start,
            "leave_date": end,
            "severity": str(entry.get("severity") or ""),
            "current": False,
            "_start": _day(start),
            "_end": _day(end),
        }
        periods.append(period)

    if current_project:
        current_start = str(record.get("project_entry_date") or "").strip()
        current_id = str(record.get("project_period_id") or "").strip()
        if not current_id:
            current_id = _period_id(current_project, current_start, "", len(history))
            record["project_period_id"] = current_id
        periods.append({
            "period_id": current_id,
            "project": current_project,
            "entry_date": current_start,
            "leave_date": "",
            "severity": str(record.get("project_severity") or ""),
            "current": True,
            "_start": _day(current_start),
            "_end": None,
        })

    if not has_project_context(record):
        return periods

    # Current/open periods win same-day boundaries over periods that ended on
    # that date.  Otherwise retain the newest applicable assignment.
    def resolve(value: Any) -> dict[str, Any] | None:
        when = _day(value)
        if when is None:
            return None
        active = [
            item for item in periods
            if (item["_start"] is None or item["_start"] <= when)
            and (item["_end"] is None or when < item["_end"])
        ]
        if active:
            return sorted(active, key=lambda item: (item["_start"] or date.min, item["current"]))[-1]
        ended_on_day = [item for item in periods if item["_end"] == when]
        return ended_on_day[-1] if ended_on_day else None

    for collection_name in ("events", "daten", "sperm", "gewicht"):
        values = record.get(collection_name)
        if not isinstance(values, list):
            continue
        for item in values:
            if not isinstance(item, dict):
                continue
            existing = str(item.get("project_period_id") or "").strip()
            period = next((candidate for candidate in periods if candidate["period_id"] == existing), None)
            if period is None:
                period = resolve(item.get("datum") or item.get("date"))
            if period is None:
                item.setdefault("project_period_id", UNASSIGNED_PERIOD_ID)
                item.setdefault("project_period_project", "")
                continue
            item["project_period_id"] = period["period_id"]
            item.setdefault("project_period_project", period["project"])
            item.setdefault("project_period_start", period["entry_date"])
            item.setdefault("project_period_end", period["leave_date"])
    return periods


def current_period_id(record: Mapping[str, Any]) -> str:
    if not has_project_context(record):
        return ""
    current = str(record.get("project_period_id") or "").strip()
    if current:
        return current
    project = str(record.get("project") or "").strip()
    if not project:
        return ""
    return _period_id(project, record.get("project_entry_date", ""), "", len(record.get("project_history") or []))


def current_period_records(record: dict[str, Any], collection: str) -> list[dict[str, Any]]:
    """Return only records charged to the active project period."""
    values = record.get(collection) if isinstance(record, dict) else []
    if not isinstance(values, list):
        return []
    if not has_project_context(record):
        return [item for item in values if isinstance(item, dict)]
    ensure_project_periods(record)
    active_id = current_period_id(record)
    if not active_id:
        return []
    return [
        item for item in values
        if isinstance(item, dict) and str(item.get("project_period_id") or "") == active_id
    ]


def current_event_counts(
    record: dict[str, Any],
    event_type: str | None = None,
    *,
    recorded_role: str | None = None,
) -> dict[str, int]:
    result: dict[str, int] = {}
    wanted = str(event_type or "").strip().casefold()
    role = str(recorded_role or "").strip().casefold()
    for event in current_period_records(record, "events"):
        typ = str(event.get("event_type") or "").strip().casefold()
        if not typ or (wanted and typ != wanted):
            continue
        if role and str(event.get("recorded_role") or "").strip().casefold() != role:
            continue
        result[typ] = result.get(typ, 0) + 1
    return result


def event_counts_by_period(record: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Return immutable event usage grouped by membership-period ID."""
    if not isinstance(record, dict) or not has_project_context(record):
        return {}
    ensure_project_periods(record)
    result: dict[str, dict[str, int]] = {}
    for event in record.get("events", []) or []:
        if not isinstance(event, dict):
            continue
        period_id = str(event.get("project_period_id") or UNASSIGNED_PERIOD_ID)
        event_type = str(event.get("event_type") or "").strip().casefold()
        if not event_type:
            continue
        period_counts = result.setdefault(period_id, {})
        period_counts[event_type] = period_counts.get(event_type, 0) + 1
    return result


def event_period_id(record: dict[str, Any], event: Mapping[str, Any]) -> str:
    ensure_project_periods(record)
    return str(event.get("project_period_id") or UNASSIGNED_PERIOD_ID)
