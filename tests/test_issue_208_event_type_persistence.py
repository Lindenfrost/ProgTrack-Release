"""Regression coverage for canonical reproductive event persistence (#208)."""

from __future__ import annotations

import copy
import importlib.util
import json
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "progtrack_issue_208", ROOT / "ProgTrack.v.0.3.0.py"
)
MAIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MAIN)


def _bare_loader_app(payload: dict) -> object:
    return SimpleNamespace(
        _read_json=lambda: copy.deepcopy(payload),
        _refresh_list=lambda **_kwargs: None,
        _show_message=lambda *_args, **_kwargs: None,
        _custom_event_definition_for_type=lambda _event_type: None,
        _custom_event_snapshot=lambda event_type: {"id": event_type},
        _animal_role_value=lambda record, default=None: str(
            record.get("rolle") or default or "unknown"
        ),
    )


def _event_record() -> dict:
    return {
        "animals": {
            "Generic donor | Callitrix jacchus | 08.02.2018 | Test": {
                "rolle": "sperm_donor",
                "events": [
                    {
                        "event_type": "sperm_donation",
                        "datum": "2024-11-20",
                        "sample_id": "SP-GENERIC-001",
                        "project": "Study-A",
                    },
                    {
                        "event_type": "oocyte_retrieval",
                        "datum": "2025-03-15",
                        "course_id": "FSH-GENERIC-001",
                        "result": "complete",
                        "offspring": "generic offspring reference",
                    },
                ],
                "sperm": [
                    {
                        "datum": "2024-11-20",
                        "sample_id": "SP-GENERIC-001",
                        "motility": 88.0,
                        "progressive": 81.0,
                        "count": 1240000000.0,
                    },
                    {
                        "datum": "2024-11-21",
                        "probennummer": "LEGACY-001",
                        "motility": 80.0,
                        "progressive": 70.0,
                        "count": 100.0,
                    },
                ],
            }
        },
        "archived_animals": {},
        "settings": {},
    }


def test_canonical_seed_event_types_are_registered() -> None:
    assert "sperm_donation" in MAIN.EVENT_TYPES
    assert "oocyte_retrieval" in MAIN.EVENT_TYPES


def test_loader_keeps_canonical_event_payloads() -> None:
    app = _bare_loader_app(_event_record())

    MAIN.ProgTrackApp._load_persistence(app)

    record = next(iter(app.animals.values()))
    events = record["events"]
    assert [event["event_type"] for event in events] == [
        "sperm_donation",
        "oocyte_retrieval",
    ]
    assert all(isinstance(event["datum"], datetime) for event in events)
    assert events[0]["sample_id"] == "SP-GENERIC-001"
    assert events[0]["project"] == "Study-A"
    assert events[1]["course_id"] == "FSH-GENERIC-001"
    assert events[1]["result"] == "complete"
    assert events[1]["offspring"] == "generic offspring reference"
    assert "typ" not in events[1]
    assert "date" not in events[1]

    sperm = record["sperm"]
    assert sperm[0]["sample_id"] == "SP-GENERIC-001"
    assert sperm[1]["probennummer"] == "LEGACY-001"


def test_writer_round_trips_event_metadata_and_both_sperm_id_aliases() -> None:
    from tests.disposable_seed_backend import open_disposable_seed_backend

    temporary, backend = open_disposable_seed_backend()
    try:
        app = SimpleNamespace()
        app.read_only_mode = False
        app._pending_core_parentage_revision = None
        app._save_trace = lambda *_args, **_kwargs: None
        app._normalize_species_value = MAIN.ProgTrackApp._normalize_species_value
        app._serialize_core_record = lambda name, record: MAIN.ProgTrackApp._serialize_core_record(
            app, name, record
        )
        app._acknowledge_core_save = lambda receipt, snapshot: MAIN.ProgTrackApp._acknowledge_core_save(
            app, receipt, snapshot
        )
        app._master_can = lambda _permission: True
        app.master_track = SimpleNamespace(
            current_username="seed-backend-test",
            current_role="master",
            current_display_name="Seed backend test",
        )
        app._disabled_plugins = set()
        app.authorization = None
        app.backend = backend

        key = "Generic donor | Callitrix jacchus | 08.02.2018 | Test"
        data = {
            "animals": {
                key: {
                    "rolle": "sperm_donor",
                    "species": "Callitrix jacchus",
                    "birth_date": datetime(2018, 2, 8),
                    "origin": "Test",
                    "events": [{
                        "event_type": "sperm_donation",
                        "datum": datetime(2024, 11, 20),
                        "sample_id": "SP-GENERIC-001",
                        "project": "Study-A",
                    }],
                    "sperm": [
                        {
                            "datum": datetime(2024, 11, 20),
                            "sample_id": "SP-GENERIC-001",
                            "motility": 88.0,
                            "progressive": 81.0,
                            "count": 1240000000.0,
                        },
                        {
                            "datum": datetime(2024, 11, 21),
                            "probennummer": "LEGACY-001",
                            "motility": 80.0,
                            "progressive": 70.0,
                            "count": 100.0,
                        },
                    ],
                }
            },
            "archived_animals": {},
        }

        app._read_json = lambda: copy.deepcopy(data)
        with patch.object(MAIN, "RUNTIME_PATHS", backend.paths):
            MAIN.ProgTrackApp._write_json(app, data, audit_after_save=False)

        stored = backend.load_core_data()["animals"][key]
        assert len(stored["events"]) == 1
        saved_event = stored["events"][0]
        assert saved_event["event_id"]
        assert {
            field: saved_event[field]
            for field in (
                "event_id", "event_type", "datum", "sample_id", "project"
            )
        } == {
            "event_id": saved_event["event_id"],
            "event_type": "sperm_donation",
            "datum": "2024-11-20T00:00:00",
            "sample_id": "SP-GENERIC-001",
            "project": "Study-A",
        }
        assert saved_event["recorded_role"] == "sperm_donor"
        assert saved_event["medical_history_policy"]["mirror"] is False
        assert saved_event["clinical_source_revision"] >= 1
        assert stored["sperm"][0]["sample_id"] == "SP-GENERIC-001"
        assert stored["sperm"][1]["probennummer"] == "LEGACY-001"
    finally:
        backend.close()
        temporary.cleanup()


def test_editor_and_localized_labels_expose_added_builtins() -> None:
    source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
    assert "'oocyte_retrieval'" in source
    assert "'pregnancy_verification'" in source
    assert "_progtrack_source_event" in source
    assert "event.oocyte_retrieval" in source
    assert "plot.event.sperm_donation" in source

    for language in ("en", "de", "it", "ru"):
        messages = json.loads(
            (ROOT / "lang" / f"messages_{language}.json").read_text(
                encoding="utf-8"
            )
        )
        assert messages["event.oocyte_retrieval"]
        assert messages["event.sperm_donation"]
        assert messages["event.pregnancy_verification"]
        assert messages["plot.event.oocyte_retrieval"]
        assert messages["plot.event.sperm_donation"]


def test_sperm_donor_choices_keep_sperm_donation_without_retyping() -> None:
    """Existing donation rows stay selected and unrelated types stay hidden."""
    from PyQt6.QtWidgets import QApplication, QComboBox

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    qt = QApplication.instance() or QApplication([])

    app = MAIN.ProgTrackApp.__new__(MAIN.ProgTrackApp)
    app.messages = {
        "button.new_item": "New {title}",
        "form.placeholder.date": "DD.MM.YYYY",
    }
    app._active_custom_event_definitions_for_role = lambda *_args, **_kwargs: []
    app._custom_event_types_for_role = lambda _role: set()
    app._cap_history_viewport = lambda *_args, **_kwargs: None

    role_cb = QComboBox()
    role_cb.addItem("Sperm donor", MAIN.Role.SAMENSP.value)
    _scroll, widgets = app._build_repro_event_list(
        "Events",
        [{
            "datum": datetime(2024, 11, 20),
            "event_type": "sperm_donation",
            "sample_id": "SP-GENERIC-001",
        }],
        lambda event: (
            event["datum"].strftime(MAIN.DATE_FORMAT),
            event["event_type"],
            event,
        ),
        lambda _widgets: (datetime.now().strftime(MAIN.DATE_FORMAT), "surgery"),
        role_cb,
    )

    assert len(widgets) == 1
    assert widgets[0][1].currentData() == "sperm_donation"
    offered_types = {
        widgets[0][1].itemData(index)
        for index in range(widgets[0][1].count())
        if widgets[0][1].itemData(index)
    }
    assert "sperm_donation" in offered_types
    assert "oocyte_retrieval" not in offered_types
    _scroll.close()
    role_cb.deleteLater()
    qt.processEvents()


def test_egg_cell_donor_editor_offers_oocyte_retrieval_not_sperm_donation() -> None:
    from PyQt6.QtWidgets import QApplication, QComboBox

    qt = QApplication.instance() or QApplication([])
    app = MAIN.ProgTrackApp.__new__(MAIN.ProgTrackApp)
    app.messages = {
        "button.new_item": "New {title}",
        "form.placeholder.date": "DD.MM.YYYY",
        "event.surgery": "Surgery",
        "event.oocyte_retrieval": "Oocyte retrieval",
        "event.sperm_donation": "Sperm donation",
    }
    app._active_custom_event_definitions_for_role = lambda *_args, **_kwargs: []
    app._custom_event_types_for_role = lambda _role: set()
    app._cap_history_viewport = lambda *_args, **_kwargs: None

    role_cb = QComboBox()
    role_cb.addItem("Egg cell donor", MAIN.Role.SPENDER.value)
    scroll, widgets = app._build_repro_event_list(
        "Events",
        [{"datum": datetime(2024, 12, 1), "event_type": "surgery"}],
        lambda event: (event["datum"].strftime(MAIN.DATE_FORMAT), event["event_type"], event),
        lambda _widgets: (datetime.now().strftime(MAIN.DATE_FORMAT), "surgery"),
        role_cb,
    )

    event_combo = widgets[0][1]
    offered_types = {
        event_combo.itemData(index)
        for index in range(event_combo.count())
        if event_combo.itemData(index)
    }
    assert "oocyte_retrieval" in offered_types
    assert "sperm_donation" not in offered_types
    scroll.close()
    role_cb.deleteLater()
    qt.processEvents()


def test_unsupported_historical_event_stays_visible_and_preserves_payload() -> None:
    from PyQt6.QtWidgets import QApplication, QComboBox

    qt = QApplication.instance() or QApplication([])
    combo = QComboBox()
    combo.addItem("Surgery", "surgery")
    source_event = {
        "event_type": "oocyte_retrieval",
        "datum": datetime(2024, 12, 1, 13, 45),
        "course_id": "COURSE-001",
        "result": "complete",
        "date": "legacy alias must be normalized",
    }
    combo._progtrack_source_event = copy.deepcopy(source_event)

    is_normal_choice = MAIN.restore_event_combo_selection(
        combo, "oocyte_retrieval", "Oocyte retrieval", persisted=True
    )
    assert not is_normal_choice
    assert combo.currentData() == "oocyte_retrieval"
    assert combo.isEnabled() is False
    historical_index = combo.currentIndex()
    assert combo.model().item(historical_index).isEnabled() is False
    assert combo.model().item(combo.findData("surgery")).isEnabled() is True

    unchanged = MAIN.event_payload_from_editor_row(
        combo, str(combo.currentData()), datetime(2024, 12, 1)
    )
    assert unchanged == {
        "event_id": unchanged["event_id"],
        "event_type": "oocyte_retrieval",
        "datum": datetime(2024, 12, 1, 13, 45),
        "course_id": "COURSE-001",
        "result": "complete",
    }

    combo.setCurrentIndex(combo.findData("surgery"))
    with pytest.raises(ValueError, match="type cannot be changed"):
        MAIN.event_payload_from_editor_row(
            combo, str(combo.currentData()), datetime(2024, 12, 2)
        )
    combo.deleteLater()
    qt.processEvents()
