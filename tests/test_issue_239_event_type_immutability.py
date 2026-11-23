"""Cross-role immutable animal event types and historical plot coverage (#239)."""

from __future__ import annotations

import copy
import os
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from Plugins.core.backend import ConflictError, ProgTrackBackend
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths

ROOT = Path(__file__).resolve().parents[1]


def _runtime_paths(root: Path) -> RuntimePaths:
    base = root / "runtime"
    paths = RuntimePaths(
        application_root=ROOT,
        profile=BackendProfile.STANDALONE_SQLITE,
        data_root=base / "data",
        config_root=base / "config",
        cache_root=base / "cache",
        state_root=base / "state",
        database_path=base / "data" / "database" / "progtrack.sqlite3",
        managed_root=base / "managed",
        managed_documents=base / "managed" / "documents",
        managed_config_assets=base / "managed" / "config-assets",
        logs=base / "state" / "logs",
        runtime=base / "state" / "runtime",
        exports=base / "data" / "exports",
        preferences=base / "config" / "preferences",
        profile_file=base / "config" / "backend.json",
    )
    paths.create_mutable_roots()
    return paths


def _record(events: list[dict]) -> dict:
    return {
        "Test Animal | Callithrix jacchus | 01.01.2020 | Issue239": {
            "name": "Test Animal",
            "species": "Callithrix jacchus",
            "birth_date": "01.01.2020",
            "origin": "Issue239",
            "rolle": "sperm_donor",
            "events": copy.deepcopy(events),
        }
    }


def _open_backend(directory: str) -> ProgTrackBackend:
    return ProgTrackBackend(
        _runtime_paths(Path(directory)),
        acquire_process_lock=False,
        bootstrap_seed=False,
    )


def test_backend_assigns_stable_event_ids_and_rejects_retyping_atomically() -> None:
    with tempfile.TemporaryDirectory() as directory:
        backend = _open_backend(directory)
        try:
            initial = _record([{
                "event_type": "oocyte_retrieval",
                "datum": "2024-12-01T00:00:00",
                "course_id": "COURSE-1",
                "result": "complete",
                "custom_event_snapshot": {"label": "Historical retrieval"},
            }])
            backend.save_core_data({"animals": initial, "archived_animals": {}})
            first_snapshot = backend.load_core_data()
            animal_key = next(iter(first_snapshot["animals"]))
            original = first_snapshot["animals"][animal_key]["events"][0]
            event_id = original["event_id"]
            assert event_id
            assert original["event_type"] == "oocyte_retrieval"
            assert original["recorded_role"] == "sperm_donor"

            # Date/payload and role may change while the saved event type and
            # record identity, original role, and historical label remain fixed.
            updated = copy.deepcopy(first_snapshot)
            updated["animals"][animal_key]["rolle"] = "offspring"
            updated["animals"][animal_key]["events"][0]["datum"] = (
                "2024-12-02T00:00:00"
            )
            updated["animals"][animal_key]["events"][0]["result"] = "reviewed"
            updated["animals"][animal_key]["events"].append({
                "event_type": "hearing_test",
                "datum": "2025-01-02T00:00:00",
            })
            backend.save_core_data(updated)
            after_edit = backend.load_core_data()["animals"][animal_key]
            saved_event = next(
                event for event in after_edit["events"]
                if event["event_id"] == event_id
            )
            assert saved_event["event_id"] == event_id
            assert saved_event["event_type"] == "oocyte_retrieval"
            assert saved_event["recorded_role"] == "sperm_donor"
            assert saved_event["result"] == "reviewed"
            assert saved_event["custom_event_snapshot"] == {
                "label": "Historical retrieval"
            }
            assert after_edit["rolle"] == "offspring"
            new_event = next(
                event for event in after_edit["events"]
                if event["event_type"] == "hearing_test"
            )
            assert new_event["recorded_role"] == "offspring"

            wrong_origin = copy.deepcopy(backend.load_core_data())
            wrong_origin["animals"][animal_key]["events"][0]["recorded_role"] = "experimental"
            with pytest.raises(ConflictError, match="recording role is immutable"):
                backend.save_core_data(wrong_origin)

            tampered = copy.deepcopy(backend.load_core_data())
            original_tampered = next(
                event for event in tampered["animals"][animal_key]["events"]
                if event["event_id"] == event_id
            )
            original_tampered["event_type"] = "hearing_test"
            tampered["animals"][animal_key]["rolle"] = "experimental"
            with pytest.raises(ConflictError, match="type is immutable"):
                backend.save_core_data(tampered)

            # The attempted type and role change share one transaction: neither
            # the animal row nor the event can be partially committed.
            after_rejection = backend.load_core_data()["animals"][animal_key]
            assert after_rejection["rolle"] == "offspring"
            after_original = next(
                event for event in after_rejection["events"]
                if event["event_id"] == event_id
            )
            assert after_original["event_id"] == event_id
            assert after_original["event_type"] == "oocyte_retrieval"
            assert after_original["recorded_role"] == "sperm_donor"
        finally:
            backend.close()


def test_same_day_events_keep_distinct_ids_through_reorder_and_reload() -> None:
    with tempfile.TemporaryDirectory() as directory:
        backend = _open_backend(directory)
        try:
            events = [
                {"event_type": "surgery", "datum": "2025-01-01", "record": "one"},
                {"event_type": "surgery", "datum": "2025-01-01", "record": "two"},
            ]
            backend.save_core_data({
                "animals": _record(events), "archived_animals": {}
            })
            snapshot = backend.load_core_data()
            key = next(iter(snapshot["animals"]))
            first_ids = {
                event["record"]: event["event_id"]
                for event in snapshot["animals"][key]["events"]
            }
            assert len(set(first_ids.values())) == 2

            reordered = copy.deepcopy(snapshot)
            reordered["animals"][key]["events"].reverse()
            reordered["animals"][key]["events"][0]["datum"] = "2025-01-02"
            reordered["animals"][key]["events"][0]["notes"] = "edited"
            backend.save_core_data(reordered)
            persisted = backend.load_core_data()["animals"][key]["events"]
            second_ids = {event["record"]: event["event_id"] for event in persisted}
            assert second_ids == first_ids
            assert all(event["event_type"] == "surgery" for event in persisted)
        finally:
            backend.close()


def test_interchange_round_trip_preserves_event_identity_and_type() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = _open_backend(str(root / "source"))
        target = None
        try:
            source.save_core_data({
                "animals": _record([{
                    "event_type": "oocyte_retrieval",
                    "datum": "2024-12-01",
                    "course_id": "IMPORT-239",
                }]),
                "archived_animals": {},
            })
            source_snapshot = source.load_core_data()
            source_key = next(iter(source_snapshot["animals"]))
            original_event = source_snapshot["animals"][source_key]["events"][0]
            package = root / "event-roundtrip.ptdb"
            source.interchange.export_package(package, package_id="issue-239")

            target = _open_backend(str(root / "target"))
            assert target.interchange.preview(package).valid
            target.interchange.import_package(package)
            imported = target.load_core_data()["animals"][source_key]["events"][0]
            assert imported["event_id"] == original_event["event_id"]
            assert imported["event_type"] == "oocyte_retrieval"
            assert imported["course_id"] == "IMPORT-239"
        finally:
            source.close()
            if target is not None:
                target.close()


def test_saved_event_type_control_is_disabled_but_new_row_stays_editable() -> None:
    from PyQt6.QtWidgets import QApplication, QComboBox, QPushButton

    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "progtrack_issue_239_ui", ROOT / "ProgTrack.v.0.3.0.py"
    )
    main = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(main)
    app = QApplication.instance() or QApplication([])
    editor = main.ProgTrackApp.__new__(main.ProgTrackApp)
    editor.messages = {
        "button.new_item": "New {title}",
        "form.placeholder.date": "DD.MM.YYYY",
        "table.header.date": "Date",
        "table.header.event_type": "Event type",
        "table.header.delete": "Delete",
        "event.surgery": "Surgery",
        "event.pregnancy": "Pregnancy",
        "event.oocyte_retrieval": "Oocyte retrieval",
    }
    editor._active_custom_event_definitions_for_role = lambda *_args, **_kwargs: []
    editor._cap_history_viewport = lambda *_args, **_kwargs: None
    role_combo = QComboBox()
    role_combo.addItem("Offspring", "offspring")

    scroll, rows = editor._build_repro_event_list(
        "Events",
        [{
            "datum": datetime(2024, 12, 1),
            "event_type": "oocyte_retrieval",
            "event_id": "persisted-239",
            "course_id": "COURSE-1",
        }],
        lambda event: (
            event["datum"].strftime(main.DATE_FORMAT), event["event_type"], event
        ),
        lambda _widgets: ("01.12.2024", "surgery"),
        role_combo,
    )
    saved_combo = rows[0][1]
    assert saved_combo.currentData() == "oocyte_retrieval"
    assert saved_combo.isEnabled() is False
    assert saved_combo._progtrack_source_event["event_id"] == "persisted-239"

    # The add-row factory is the same path used by the New Event button.
    add_button = next(
        button for button in scroll.findChildren(QPushButton)
        if button.text() == "New Events"
    )
    add_button.click()
    assert rows[-1][1].currentData() == "surgery"
    assert rows[-1][1].isEnabled() is True

    retired_custom = QComboBox()
    retired_custom.addItem("Current custom choice", "current_custom")
    retired_custom._progtrack_source_event = {
        "event_id": "retired-custom-239",
        "event_type": "phase2e_custom_event",
        "custom_event_snapshot": {"label": "Phase 2E custom event"},
    }
    assert main.restore_event_combo_selection(
        retired_custom,
        "phase2e_custom_event",
        "Phase 2E custom event",
        persisted=True,
    ) is False
    assert retired_custom.currentText() == "Phase 2E custom event"
    assert retired_custom.isEnabled() is False

    scroll.close()
    retired_custom.deleteLater()
    role_combo.deleteLater()
    app.processEvents()


def test_plot_event_projection_ignores_current_role_and_keeps_same_day_records() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "progtrack_issue_239_plot", ROOT / "ProgTrack.v.0.3.0.py"
    )
    main = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(main)

    events = [
        {
            "event_id": "event-a",
            "event_type": "oocyte_retrieval",
            "datum": datetime(2024, 12, 1),
        },
        {
            "event_id": "event-b",
            "event_type": "oocyte_retrieval",
            "datum": datetime(2024, 12, 1),
        },
        # A duplicate copy of one normalized record should not inflate plots.
        {
            "event_id": "event-a",
            "event_type": "oocyte_retrieval",
            "datum": datetime(2024, 12, 1),
        },
        {
            "event_id": "event-c",
            "event_type": "retired_custom_measurement",
            "datum": datetime(2024, 12, 2),
            "custom_event_snapshot": {
                "label": "Historical measurement",
                "render_mode": "symbol",
                "color": "#123456",
                "marker": "D",
            },
        },
    ]
    for role in ("sperm_donor", "offspring", "custom_facility_role"):
        entries = main.plot_event_entries({"rolle": role, "events": events})
        assert [entry[0] for entry in entries] == [
            "oocyte_retrieval",
            "oocyte_retrieval",
            "retired_custom_measurement",
        ]
        assert entries[0][2]["event_id"] == "event-a"
        assert entries[1][2]["event_id"] == "event-b"

    source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
    assert "evs = plot_event_entries(a)" in source

    assert main.historical_event_label({}, "retired_custom_measurement", events[-1]) == (
        "Historical measurement"
    )


def test_role_statistics_count_only_events_originating_in_current_role() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "progtrack_issue_239_role_counts", ROOT / "ProgTrack.v.0.3.0.py"
    )
    main = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(main)
    app = main.ProgTrackApp.__new__(main.ProgTrackApp)
    app.messages = {"stats.surgery": "Surgery", "stats.measurement": "Measurement"}
    app._animal_role_value = lambda record, default=None: record.get("rolle", default or "")
    custom_definition = {
        "id": "custom-event:biopsy",
        "stable_id": "custom-event-biopsy-v1",
        "event_type": "biopsy",
        "block_id": "experimental-block:biopsy",
        "kind": "limiting",
        "name": "Biopsy",
        "max_field": "max_biopsy",
        "default_maximum": 1,
    }
    app._custom_event_definitions = lambda include_retired=True: [custom_definition]
    # A current quota requires an explicitly attached role block (#247).
    app._role_dialog_blocks = lambda *_a, **_k: ['experimental-block:biopsy']
    app._custom_experimental_block_definitions = lambda include_retired=True: [{
        "id": "experimental-block:biopsy",
        "event_ids": ["biopsy", "custom-event-biopsy-v1"],
        "event_type": "biopsy",
        "kind": "limiting",
        "name": "Biopsy",
        "max_field": "max_biopsy",
        "default_maximum": 1,
    }]
    app._normalize_report_event_type = lambda value: str(value or "").strip().casefold()

    animal = {
        "rolle": "experimental_animal",
        "events": [
            {"event_type": "surgery", "recorded_role": "sperm_donor"},
            {"event_type": "surgery", "recorded_role": "experimental_animal"},
            {"event_type": "measurement", "recorded_role": "sperm_donor"},
            {"event_type": "biopsy", "recorded_role": "sperm_donor", "event_id": "old-biopsy"},
            {"event_type": "biopsy", "recorded_role": "experimental_animal", "event_id": "new-biopsy"},
        ],
        "max_op": 3,
        "max_measurements": 4,
        "max_biopsy": 1,
    }
    assert app._get_event_statistics(animal) == (
        "Surgery: 1/3, Measurement: 0/4, Biopsy: 1/1"
    )
    assert app._get_event_statistics_localized(
        animal, {"stats.surgery": "Operation", "stats.measurement": "Reading"}
    ) == "Operation: 1/3, Reading: 0/4, Biopsy: 1/1"
    assert app._validate_custom_event_limits_for_record(animal) == []

    previous = {
        "rolle": "sperm_donor",
        "events": [
            {"event_type": "biopsy", "recorded_role": "sperm_donor", "event_id": "old-biopsy"}
        ],
    }
    candidate = {
        "rolle": "experimental_animal",
        "events": previous["events"] + [
            {"event_type": "biopsy", "recorded_role": "experimental_animal", "event_id": "new-biopsy"}
        ],
        "max_biopsy": 1,
    }
    assert app._event_save_limit_overruns(previous, candidate) == []


def test_same_type_event_keeps_historical_role_and_label_payload() -> None:
    import importlib.util
    from PyQt6.QtWidgets import QApplication, QComboBox

    spec = importlib.util.spec_from_file_location(
        "progtrack_issue_239_payload", ROOT / "ProgTrack.v.0.3.0.py"
    )
    main = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(main)
    app = QApplication.instance() or QApplication([])
    combo = QComboBox()
    combo.addItem("Shared event", "shared_event")
    combo._progtrack_source_event = {
        "event_id": "same-role-type-event",
        "event_type": "shared_event",
        "recorded_role": "old_role",
        "custom_event_snapshot": {"label": "Original label"},
    }

    retained = main.event_payload_from_editor_row(
        combo,
        "shared_event",
        datetime(2025, 3, 4),
        recording_role="new_role",
    )
    assert retained["recorded_role"] == "old_role"
    assert retained["custom_event_snapshot"] == {"label": "Original label"}
    assert retained["event_type"] == "shared_event"
    combo.deleteLater()
    app.processEvents()
