"""Focused project-period attribution and counter checks for issue #247."""

from __future__ import annotations

from datetime import date, datetime

from Plugins.Animal_Reports.report_index import AnimalReportIndex
from Plugins.core.project_periods import (
    current_event_counts,
    current_period_id,
    event_counts_by_period,
    ensure_project_periods,
)


def _event(day: str, event_type: str = "surgery") -> dict:
    return {"event_type": event_type, "datum": datetime.fromisoformat(day)}


def _record() -> dict:
    return {
        "project": "Project B",
        "project_entry_date": "01.02.2025",
        "project_history": [
            {
                "project": "Project A",
                "entry_date": "01.01.2025",
                "leave_date": "01.02.2025",
            }
        ],
        "events": [
            _event("2025-01-05T09:00:00"),
            _event("2025-01-20T09:00:00"),
            _event("2025-02-02T09:00:00"),
        ],
    }


def test_current_counter_excludes_former_project_events_and_preserves_ids() -> None:
    record = _record()
    periods = ensure_project_periods(record)
    current_id = current_period_id(record)
    former_id = next(item["period_id"] for item in periods if not item["current"])

    assert current_event_counts(record) == {"surgery": 1}
    assert record["events"][0]["project_period_id"] == former_id
    assert record["events"][1]["project_period_id"] == former_id
    assert record["events"][2]["project_period_id"] == current_id
    assert event_counts_by_period(record)[former_id]["surgery"] == 2
    assert event_counts_by_period(record)[current_id]["surgery"] == 1


def test_report_index_can_render_current_and_former_period_counts() -> None:
    record = _record()
    ensure_project_periods(record)
    former_id = record["events"][0]["project_period_id"]
    index = AnimalReportIndex(
        record,
        lambda event_type: event_type,
        date_format="%d.%m.%Y",
    )

    assert index.event_counts_through(date(2025, 2, 28))["surgery"] == (1, 1)
    assert index.event_counts_through(
        date(2025, 2, 28), project_period_id=former_id
    )["surgery"] == (2, 2)
    assert index.event_period_id_for_occurrence(
        date(2025, 1, 5), "surgery"
    ) == former_id


def test_unscoped_records_keep_legacy_lifetime_counter_behavior() -> None:
    record = {"events": [_event("2025-01-01T09:00:00")]}
    index = AnimalReportIndex(
        record,
        lambda event_type: event_type,
        date_format="%d.%m.%Y",
    )
    assert index.event_counts_through(date(2025, 2, 28))["surgery"] == (1, 1)
