"""Regression tests for Master Track audit recording and export."""

from __future__ import annotations

import json
import copy
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from datetime import date

from pypdf import PdfReader

from Plugins.Master_Track.dialogs import AuditLogsDialog
from Plugins.core.backend.repositories import AuditRepository
from Plugins.core.audit_comparison import (
    core_records_equal, event_collections_equal, preserve_unset_optional_fields,
)


def test_316_preparation_equivalence_preserves_presence_types_unknowns_and_ordinal():
    before = {'events': _315_events(), 'unknown': {'flag': False, 'value': None}}
    reordered = copy.deepcopy(before)
    reordered['events'].reverse()
    assert core_records_equal(before, reordered)
    for path, value in [('flag', 0), ('value', ''), ('extra', None)]:
        candidate = copy.deepcopy(reordered)
        candidate['unknown'][path] = value
        assert not core_records_equal(before, candidate)
    candidate = copy.deepcopy(reordered)
    candidate['events'][0]['ordinal'] = 0
    assert not core_records_equal(before, candidate)
    assert not core_records_equal({'events': []}, {})
    for events in ([{'event_type': 'measurement'}], [{'event_id': 'x'}, {'event_id': 'x'}]):
        assert not core_records_equal({'events': events}, {'events': copy.deepcopy(events)})
    assert not core_records_equal(None, {})


def test_315_optional_blanks_preserve_absent_null_empty_without_touching_original():
    for original in ({'name': 'test-1'}, {'name': 'test-1', 'death_date': None,
                                         'severity': None},
                     {'name': 'test-1', 'death_date': '', 'severity': ''}):
        before = copy.deepcopy(original)
        candidate = dict(original, death_date='', severity='', gewicht=[{'wert': 27.1}])
        preserve_unset_optional_fields(candidate, original)
        for field in ('death_date', 'severity'):
            assert (field in candidate) == (field in original)
            assert candidate.get(field) == original.get(field)
        assert candidate['gewicht'] == [{'wert': 27.1}]
        assert original == before


def test_315_preservation_is_field_specific_not_falsy_normalization():
    for old, new in (('01.01.2026', ''), ('SV1', ''), ('', 'SV1'),
                     (None, 0), (None, False), (False, ''), (0, ''),
                     (' ', ''), ('unknown', '')):
        for field in ('death_date', 'severity'):
            candidate = {field: new, 'other': ''}
            preserve_unset_optional_fields(candidate, {'name': 'test-1', field: old,
                                                       'other': None})
            assert candidate[field] == new and type(candidate[field]) is type(new)
            assert candidate['other'] == ''
    created = {'death_date': '', 'severity': ''}
    preserve_unset_optional_fields(created, {})
    assert created == {'death_date': '', 'severity': ''}


def _315_events():
    ids = ['3e17023df9cf4b6a9b96bafa46e7fe41', '6b5c9745d05e4c1295dc0f368c3545a2',
           'bc8c0d3a3c964ef8ae072f2c80122b22', 'f9360da83a0d44e79f4967a066f7d8a9',
           '4073b96da86340228fd5e5f976898bcc', 'd41acca6bf5f4375bf6a195106ecbd4f']
    dates = ['2025-01-04', '2025-01-05', '2025-01-05',
             '2025-10-05', '2026-10-20', '2026-10-20']
    return [dict(event_id=key, datum=day + 'T00:00:00',
                 event_type='test_event_2p' if i == 3 else 'test_limit_2p',
                 recorded_role='custom.new_role',
                 custom_event_snapshot={'label': 'Test-Event 2P' if i == 3 else 'Test-Limit 2P',
                                        'revision': 1, 'marker': 'o' if i == 3 else '^'})
            for i, (key, day) in enumerate(zip(ids, dates))]


def test_315_reported_six_id_reordering_is_equivalent_and_inputs_unchanged():
    before = _315_events()
    after = [copy.deepcopy(before[i]) for i in (0, 3, 2, 1, 5, 4)]
    originals = copy.deepcopy((before, after))
    assert event_collections_equal(before, after)
    assert (before, after) == originals
    assert event_collections_equal([], [])


def test_315_actual_event_changes_and_unknown_fields_and_order_remain_visible():
    before = _315_events()
    changes = [lambda e: e.pop(), lambda e: e.append(dict(e[0], event_id='new')),
               lambda e: e[1].update(datum='2025-01-06'),
               lambda e: e[1].update(event_type='different'),
               lambda e: e[1].update(recorded_role='different'),
               lambda e: e[1]['custom_event_snapshot'].update(revision=2),
               lambda e: e[1].update(ordinal=2),
               lambda e: e[1].update(unknown_retained={'nested': ['a', 'b']})]
    for change in changes:
        after = copy.deepcopy(before)
        change(after)
        assert not event_collections_equal(before, after)
    before[0]['unknown_retained'] = [1, 2]
    after = copy.deepcopy(before)
    after[0]['unknown_retained'].reverse()
    assert not event_collections_equal(before, after), 'Nested payload order is meaningful'
    before[0]['confirmed'] = False
    after = copy.deepcopy(before)
    after[0]['confirmed'] = 0
    assert not event_collections_equal(before, after), 'Boolean is not numeric zero'


def test_315_invalid_identities_never_establish_event_equivalence():
    for events in (None, {}, [None], [{}], [{'event_id': ''}], [{'event_id': ' x'}],
                   [{'event_id': 'x'}, {'event_id': 'x'}]):
        assert not event_collections_equal(events, copy.deepcopy(events))



def _dialog_stub() -> AuditLogsDialog:
    dialog = AuditLogsDialog.__new__(AuditLogsDialog)
    dialog.messages = {}
    dialog._cage_name_map = {}
    return dialog


def test_old_and_new_values_are_lossless() -> None:
    dialog = _dialog_stub()
    previous = {"field": "old", "entries": list(range(120))}
    current = {"field": "new", "entries": list(range(240))}

    old_text, new_text = dialog._derive_old_new_display(
        "experimental_limits",
        json.dumps(previous),
        json.dumps(current),
    )

    assert json.dumps(previous, ensure_ascii=False, sort_keys=True) == old_text
    assert json.dumps(current, ensure_ascii=False, sort_keys=True) == new_text
    assert "119" in old_text
    assert "239" in new_text


def test_create_action_has_dedicated_display_name() -> None:
    dialog = _dialog_stub()
    assert dialog._format_change_type("create", "<record>", {}) == "Create animal"


def test_backend_actions_have_localized_viewer_labels() -> None:
    dialog = _dialog_stub()
    actions = {
        "database_created": "Create database",
        "database_selected": "Select database",
        "database_archived": "Archive database",
        "database_unarchived": "Unarchive database",
        "database_deleted": "Delete database",
        "database_backup": "Database backup",
        "database_restore": "Database restore",
        "database_interchange_import": "Import database interchange",
        "institution_branding_update": "Update institution branding",
        "force_release": "Force-release lock",
    }
    for action, expected in actions.items():
        assert dialog._format_change_type(action, "", {}) == expected


def test_backend_events_are_adapted_to_master_track_rows() -> None:
    dialog = _dialog_stub()

    class _AuditStore:
        def list_events(self):
            return [
                {
                    "event_id": "evt-1",
                    "occurred_at": "2026-08-28T12:00:00+00:00",
                    "actor_login": "Admin",
                    "category": "configuration",
                    "action": "database_backup",
                    "entity_type": "database",
                    "entity_id": "progtrack",
                    "payload": {"database": "progtrack", "path": "backup.zip"},
                }
            ]

    dialog.parentWidget = lambda: SimpleNamespace(backend=SimpleNamespace(audit=_AuditStore()))
    rows = dialog._backend_audit_rows()

    assert len(rows) == 1
    assert rows[0]["animal"] == "progtrack"
    assert rows[0]["change"] == "Database backup"
    assert '"path": "backup.zip"' in rows[0]["new"]
    assert rows[0]["user"] == "Admin"


def test_audit_repository_reads_sqlite_and_postgresql_style_rows() -> None:
    class _Cursor:
        def __init__(self, rows):
            self.rows = rows

        def fetchall(self):
            return self.rows

    class _Connection:
        def __init__(self, rows):
            self.rows = rows

        def execute(self, _sql, _params=()):
            return _Cursor(self.rows)

    class _Adapter:
        dialect = "sqlite"

        def __init__(self, rows):
            self.rows = rows

        @contextmanager
        def transaction(self, *, write=False):
            del write
            yield _Connection(self.rows)

    payload = json.dumps({"database": "progtrack"})
    rows = [
        (
            "evt-1",
            "2026-08-28T12:00:00+00:00",
            "Admin",
            "configuration",
            "database_created",
            "database",
            "progtrack",
            "corr-1",
            payload,
        )
    ]
    events = AuditRepository(_Adapter(rows)).list_events()
    assert events[0]["action"] == "database_created"
    assert events[0]["payload"] == {"database": "progtrack"}


def test_long_old_new_values_remain_in_exported_pdf(tmp_path: Path) -> None:
    old_value = "old-value-marker-" + ("0123456789" * 900)
    new_value = "new-value-marker-" + ("abcdefghij" * 900)
    output = tmp_path / "audit.pdf"

    AuditLogsDialog._export_rows_to_pdf(
        SimpleNamespace(messages={}),
        str(output),
        [{
            "date": "28.08.2026T12:00:00",
            "animal": "Yellowfang",
            "change": "Edit: Experimental limits",
            "old": old_value,
            "new": new_value,
            "user": "admin",
        }],
        date(2026, 8, 28),
        date(2026, 8, 28),
    )

    reader = PdfReader(str(output))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "old-value-marker-" in text
    assert "new-value-marker-" in text
    assert text.count("0123456789") >= 100
    assert text.count("abcdefghij") >= 100


def test_permission_checks_do_not_infer_mutations() -> None:
    source = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.2.3.py"
    text = source.read_text(encoding="utf-8")
    start = text.index("    def _master_can(self, action: str)")
    end = text.index("    def _master_can_for_unit", start)
    permission_check = text[start:end]
    assert "_audit_data_operation(action)" not in permission_check
    assert "_infer_audit_source" not in text
    assert "import inspect" not in text


def test_snapshot_diff_has_no_implicit_record_lifecycle_events() -> None:
    source = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.2.3.py"
    text = source.read_text(encoding="utf-8")
    start = text.index("    def _audit_data_snapshot_diff(")
    end = text.index("    def _init_menu_bar", start)
    snapshot_diff = text[start:end]
    assert 'self._master_audit("data_create"' not in snapshot_diff
    assert 'self._master_audit("data_delete"' not in snapshot_diff


def test_animal_lifecycle_handlers_emit_explicit_events() -> None:
    source = Path(__file__).resolve().parents[1] / "ProgTrack.v.0.2.3.py"
    text = source.read_text(encoding="utf-8")
    for function_name in (
        "_dlg_partner",
        "_dlg_samenspender",
        "_dlg_offspring",
        "_dlg_zuchttier",
        "_dlg_versuchstier",
        "_dlg_basic_animal_role",
        "_dlg_female_animal",
    ):
        start = text.index(f"    def {function_name}(")
        next_def = text.find("\n    def ", start + 1)
        block = text[start:] if next_def < 0 else text[start:next_def]
        assert "_audit_animal_created" in block, function_name

    assert 'self._master_audit("archive"' in text
    assert 'self._master_audit("restore"' in text
    assert 'self._master_audit("delete"' in text


def test_details_parser_preserves_semicolons_inside_old_new_json() -> None:
    dialog = _dialog_stub()
    line = (
        '[2026-08-28T12:00:00] [admin] [data_edit] [ProgTrack] '
        'animal=Yellowfang; parameter=note; '
        'previous={"text":"old\\u003b parameter=not-a-key"}; '
        'new={"text":"new\\u003b parameter=not-a-key"}'
    )
    parsed = dialog._parse_line(line)
    assert parsed is not None
    assert "old; parameter=not-a-key" in parsed["old"]
    assert "new; parameter=not-a-key" in parsed["new"]
