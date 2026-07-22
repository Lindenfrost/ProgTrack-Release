"""Structured lifecycle-event helpers for Phase 1."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Iterable, Mapping, Optional
from uuid import uuid4


DEATH_RELATED_EXIT_REASONS = {
    "§4 Abs. 3 TierSchG – Organentnahme",
    "§7 Abs. 2 TierSchG – Abbruchkriterien erfüllt",
    "§7 Abs. 2 TierSchG – im Versuch verstorben",
    "§7 Abs. 2 TierSchG – tierärztliche Indikation",
    "Totgeburt",
}


def lifecycle_event(
    event_type: str,
    *,
    event_date: str,
    reason: str = "",
    actor: str = "",
    role: str = "",
    recipient: str = "",
) -> Dict[str, Any]:
    return {
        "event_id": uuid4().hex,
        "event_type": str(event_type),
        "date": str(event_date),
        "reason": str(reason),
        "actor": str(actor),
        "role": str(role),
        "recipient": str(recipient),
    }


def add_lifecycle_event(record: Dict[str, Any], event: Mapping[str, Any]) -> None:
    events = record.setdefault("lifecycle_events", [])
    events.append(dict(event))


def ever_in_experiment(
    record: Mapping[str, Any],
    history_entries: Iterable[Mapping[str, Any]] = (),
) -> bool:
    """Return whether at least one experiment period has been completed.

    The user-facing filter is deliberately labelled ``was in experiment``.  A
    first, still-running experiment therefore does *not* qualify.  Older data
    may only contain the corresponding MediTrack entry, so callers can provide
    those entries without having to migrate the sample database.
    """
    lifecycle_entries = record.get("lifecycle_events") or []
    return any(
        isinstance(event, Mapping)
        and (event.get("event_type") or event.get("entry_type")) == "experiment_ended"
        for event in (*lifecycle_entries, *history_entries)
    )


def apply_experiment_exit(
    record: Dict[str, Any],
    *,
    exit_date: str,
    reason: str,
    actor: str = "",
    role: str = "",
) -> Dict[str, Any]:
    if not exit_date or not reason:
        raise ValueError("Experiment exit requires date and reason")
    event = lifecycle_event(
        "experiment_ended",
        event_date=exit_date,
        reason=reason,
        actor=actor,
        role=role,
    )
    add_lifecycle_event(record, event)
    record["in_experiment"] = False
    record["experiment_exit_date"] = exit_date
    record["experiment_exit_reason"] = reason
    if reason in DEATH_RELATED_EXIT_REASONS:
        record["death_date"] = exit_date
        record["death_cause"] = reason
        record["project"] = ""
    return event


def apply_departure(
    record: Dict[str, Any],
    *,
    departure_date: Optional[str] = None,
    reason: str,
    recipient: str = "",
    actor: str = "",
    role: str = "",
) -> Dict[str, Any]:
    if not reason:
        raise ValueError("Departure requires a reason")
    when = departure_date or date.today().strftime("%d.%m.%Y")
    event = lifecycle_event(
        "departure",
        event_date=when,
        reason=reason,
        recipient=recipient,
        actor=actor,
        role=role,
    )
    add_lifecycle_event(record, event)
    record["departure_date"] = when
    record["departure_reason"] = reason
    record["handover_recipient"] = recipient
    return event
