# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Module: shared animal status display helpers.

from __future__ import annotations

from typing import Any, Mapping


PREGNANT_STATUS_SYMBOL = "☉"
POSSIBLY_PREGNANT_STATUS_SYMBOL = "☉?"
OFFSPRING_STATUS_SYMBOL = "Oo"
SICK_STATUS_SYMBOL = "+"
ABNORMAL_STATUS_SYMBOL = "!"
DECEASED_STATUS_SYMBOL = "✝"


def has_death_date(record: Mapping[str, Any]) -> bool:
    """Return True when an animal record carries a non-empty death date."""
    return bool(str(record.get("death_date") or record.get("sterbedatum") or "").strip())


def deceased_status_text(messages: Mapping[str, str] | None = None) -> str:
    messages = messages or {}
    return messages.get("status.deceased", "Deceased")


def compact_death_status(record: Mapping[str, Any]) -> str:
    genotype = str(record.get("genotype") or "").strip()
    return f"{genotype} {DECEASED_STATUS_SYMBOL}" if genotype else DECEASED_STATUS_SYMBOL


def compact_status_with_death_priority(record: Mapping[str, Any], fallback_status: str = "") -> str:
    """Return the compact status, making death override transient markers."""
    if has_death_date(record):
        return compact_death_status(record)
    return fallback_status


def status_summary_with_death_priority(
    record: Mapping[str, Any],
    messages: Mapping[str, str] | None = None,
    projects_track_active: bool = False,
    include_special: bool = True,
) -> str:
    """Return human-readable current status with death as highest priority."""
    messages = messages or {}
    if has_death_date(record):
        return deceased_status_text(messages)

    parts: list[str] = []
    if record.get("sick", False):
        parts.append(messages.get("status.sick", "Sick"))
    if record.get("abnormal_current", False):
        parts.append(messages.get("status.abnormal", "Abnormal"))
    if record.get("in_experiment", False) and projects_track_active:
        parts.append(messages.get("status.in_experiment", "In Experiment"))

    status = ", ".join(parts) if parts else messages.get("status.normal", "Normal")
    special = str(record.get("special_status") or "").strip()
    if include_special and special:
        status += " — " + special
    return status
