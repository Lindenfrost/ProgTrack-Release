"""Current 0.2.x backend contracts.

These tests deliberately use the same SQLite backend facade as the running
application.  They do not read or write the retired JSON stores.
"""

from __future__ import annotations

import tempfile
import json
import copy
import os
import socket
import time
import unittest
from contextlib import contextmanager, closing
from unittest.mock import patch
from pathlib import Path
from typing import Any

from Plugins.core.backend import (
    ConflictError,
    LockConflictError,
    ProgTrackBackend,
    StandaloneLockError,
)
from Plugins.core.backend.adapters import StandaloneProcessLock
from Plugins.core.backend.errors import BackendConfigurationError
from Plugins.core.animal_roles import AnimalRoleRegistry
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths


ROOT = Path(__file__).resolve().parents[1]


def runtime_paths(root: Path, *, application_root: Path | None = None) -> RuntimePaths:
    base = root / "runtime"
    paths = RuntimePaths(
        application_root=application_root or ROOT,
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


def _latency_snapshot() -> dict[str, Any]:
    def animal(ipid: str, name: str) -> dict[str, Any]:
        return {
            "ipid": ipid,
            "name": name,
            "species": "Callithrix jacchus",
            "origin": "Latency test",
            "birth_date": "01.01.2020",
            "rolle": "Offspring",
            "gewicht": [{"datum": "01.01.2026", "wert": 10.0}],
            "events": [{
                "event_id": f"event-{ipid}",
                "event_type": "Blood draw",
                "datum": "02.01.2026",
                "recorded_role": "Offspring",
            }],
        }

    return {
        "animals": {"animal-a": animal("animal-a", "Animal A"),
                    "animal-b": animal("animal-b", "Animal B")},
        "archived_animals": {},
        "settings": {"language": "en", "show_weight": True},
    }


class CurrentBackendRuntimeTests(unittest.TestCase):
    def test_2s_306_real_sqlite_lock_wait_is_bounded_and_preserves_atomic_save(self):
        """Distinguish a real disposable SQLite busy wait from a GUI timer delay."""
        import sqlite3
        import threading
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            backend.save_core_data(_latency_snapshot())
            before = backend.load_core_data()
            changed = copy.deepcopy(before)
            changed['animals']['animal-a']['gewicht'][0]['wert'] = 12.75
            locked = threading.Event()
            errors = []
            def competing_writer():
                try:
                    with closing(sqlite3.connect(backend.paths.database_path)) as connection:
                        connection.execute('BEGIN IMMEDIATE')
                        locked.set()
                        time.sleep(0.2)
                except BaseException as exc:
                    errors.append(exc)
                    locked.set()
            worker = threading.Thread(target=competing_writer)
            worker.start()
            self.assertTrue(locked.wait(5))
            start = time.perf_counter()
            receipt = backend.save_core_data(changed, clinical_context=self._321_actor())
            elapsed = time.perf_counter() - start
            worker.join(5)
            self.assertFalse(worker.is_alive())
            self.assertEqual(errors, [])
            actual = backend.load_core_data()
            self.assertEqual(actual['animals']['animal-a']['gewicht'][0]['wert'], 12.75)
            self.assertEqual(actual['animals']['animal-b'], before['animals']['animal-b'])
            self.assertIn(('animals', 'animal-a'), receipt.acknowledged_records)
            with backend.adapter.transaction() as connection:
                self.assertEqual(connection.execute('PRAGMA synchronous').fetchone()[0], 2)
                self.assertEqual(connection.execute('PRAGMA busy_timeout').fetchone()[0], 15_000)
            print('ISSUE_306_REAL_CONTENTION ' + json.dumps(dict(seconds=elapsed, held_seconds=.2)), flush=True)

    @staticmethod
    def _321_actor():
        return {'actor_login': 'tester', 'signature': 'Test Researcher', 'actor_role': 'researcher',
                'permissions': {key: True for key in ('core.create_animals', 'core.edit_animal_core',
                                'core.edit_animal_measurements', 'core.archive_animals', 'core.delete_animals')}}

    def test_321_same_day_retired_definition_commit_failure_and_hard_deletion(self):
        from Plugins.core.backend.clinical_mirroring import NAMESPACE, POLICY
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            backend.save_core_data(_latency_snapshot())
            changed = backend.load_core_data()
            animal = changed['animals']['animal-a']
            for suffix in ('one', 'two'):
                source = dict(event_id='surgery-' + suffix, event_type='surgery',
                    datum='2026-01-01', recorded_role='offspring', project_period_id='period-321',
                    source_plan_id='plan-321')
                if suffix == 'two':
                    source['date'] = source.pop('datum')
                animal['events'].append(source)
            transaction = backend.adapter.transaction
            @contextmanager
            def fail_commit(*args, **kwargs):
                with transaction(*args, **kwargs) as connection:
                    yield connection
                    if kwargs.get('write'):
                        raise RuntimeError('commit failed')
            before = backend.load_core_data()
            with patch.object(backend.adapter, 'transaction', fail_commit):
                with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                    backend.save_core_data(changed, clinical_context=self._321_actor())
            self.assertEqual(backend.load_core_data(), before)
            self.assertIsNone(backend.records.get(NAMESPACE, 'animal-a'))
            backend.save_core_data(changed, clinical_context=self._321_actor())
            ledger = backend.records.get(NAMESPACE, 'animal-a')
            originals = copy.deepcopy(ledger['entries'])
            self.assertEqual(len(originals), 2, 'same date/type is not an occurrence identity')
            self.assertEqual(originals[0]['source_payload']['source_plan_id'], 'plan-321')
            self.assertEqual(originals[0]['source_payload']['project_period_id'], 'period-321')
            changed = backend.load_core_data()
            event = next(e for e in changed['animals']['animal-a']['events'] if e['event_id'] == 'surgery-one')
            event[POLICY]['mirror'] = False
            with self.assertRaisesRegex(ConflictError, 'immutable'):
                backend.save_core_data(changed, clinical_context=self._321_actor())
            changed = backend.load_core_data()
            changed['archived_animals']['animal-a'] = changed['animals'].pop('animal-a')
            changed['archived_animals']['animal-a']['death_date'] = '01.02.2026'
            backend.save_core_data(changed, clinical_context=self._321_actor())
            self.assertEqual(backend.records.get(NAMESPACE, 'animal-a')['entries'], originals,
                             'death archive must not invent a live departure or a second death')
            denied = self._321_actor()
            denied['permissions']['core.delete_animals'] = False
            from Plugins.core.backend.errors import ValidationError
            with self.assertRaisesRegex(ValidationError, 'not authorized'):
                backend.delete_archived_animal_atomically('animal-a', changed['archived_animals']['animal-a'],
                                                          clinical_context=denied)
            self.assertTrue(backend.delete_archived_animal_atomically(
                'animal-a', changed['archived_animals']['animal-a'], clinical_context=self._321_actor()))
            final = backend.records.get(NAMESPACE, 'animal-a')['entries']
            self.assertEqual(final[:2], originals)
            self.assertEqual([e['entry_type'] for e in final[2:]], ['core_deletion', 'core_deletion'])
            self.assertNotIn('animal-a', backend.load_core_data()['archived_animals'])

    def test_321_custom_kind_style_policy_independent_of_counts_and_retirement(self):
        from Plugins.core.backend.clinical_mirroring import NAMESPACE
        from Plugins.core.role_block_presets import RoleBlockPresetRegistry, normalize_event_definition
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            backend.save_core_data(_latency_snapshot())
            definitions = [normalize_event_definition(dict(name=f'Test {kind} {mode} {mirror}',
                event_type=f'test_{kind}_{mode}_{int(mirror)}', kind=kind, render_mode=mode,
                default_maximum=5, mirror_in_medical_history=mirror))
                for kind in ('counting', 'limiting') for mode in ('line', 'symbol') for mirror in (False, True)]
            registry = RoleBlockPresetRegistry(backend)
            registry.save_presets([], event_definitions=definitions)
            changed = backend.load_core_data()
            for definition in definitions:
                changed['animals']['animal-a']['events'].append(dict(event_id=definition['event_type'],
                    event_type=definition['event_type'], datum='2026-01-01', recorded_role='custom_role'))
            backend.save_core_data(changed, clinical_context=self._321_actor())
            originals = copy.deepcopy(backend.records.get(NAMESPACE, 'animal-a')['entries'])
            expected = {d['event_type'] for d in definitions if d['mirror_in_medical_history']}
            self.assertEqual({e['status_type'] for e in originals}, expected)
            # Actual registry retirement/detachment cannot cancel annotations.
            registry.save_presets([], event_definitions=[])
            changed = backend.load_core_data()
            for event in changed['animals']['animal-a']['events']:
                if event['event_id'] in expected:
                    event['datum'] = '2026-01-02'
            actor = self._321_actor()
            actor.update(actor_login='corrector', signature='Corrector', actor_role='custom_editor')
            backend.save_core_data(changed, clinical_context=actor)
            final = backend.records.get(NAMESPACE, 'animal-a')['entries']
            self.assertEqual(final[:4], originals)
            self.assertEqual(len(final), 8)
            self.assertTrue(all(e['actor_login'] == 'corrector' for e in final[4:]))
            changed = backend.load_core_data()
            # All 8 sources count independently of clinical policy; the historical blood event remains too.
            self.assertEqual(len(changed['animals']['animal-a']['events']), 9)

    def test_321_builtin_matrix_atomic_receipt_and_no_render_backfill(self):
        from Plugins.core.backend.clinical_mirroring import BUILTIN_TYPES, CLINICAL_TYPES, NAMESPACE, POLICY
        from Plugins.Medi_Track.medi_track_widget import MediStore
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            backend.save_core_data(_latency_snapshot())  # Historical bootstrap: not a recording command.
            snapshot = backend.load_core_data()
            store = MediStore(backend)
            self.assertEqual(store.get_entries('animal-a'), [])
            store.add_entry('animal-a', {'id': 'manual', 'entry_type': 'note', 'note': 'Independent'})
            for index, token in enumerate(sorted(BUILTIN_TYPES)):
                snapshot['animals']['animal-a']['events'].append(
                    {'event_id': '321-' + token, 'event_type': token, 'datum': '2026-01-01',
                     'recorded_role': 'Offspring', 'unknown': {'flag': False, 'ordinal': index}})
            receipt = backend.save_core_data(snapshot, clinical_context=self._321_actor())
            accepted = receipt.acknowledged_records['animals', 'animal-a']
            self.assertEqual(accepted, backend.load_core_data()['animals']['animal-a'])
            self.assertTrue(all(POLICY in e for e in accepted['events'] if e['event_id'].startswith('321-')))
            entries = store.get_entries('animal-a')  # Same pre-existing store: no stale medical cache.
            self.assertEqual({e['status_type'] for e in entries if e['id'] != 'manual'}, CLINICAL_TYPES)
            self.assertEqual(len(entries), len(CLINICAL_TYPES) + 1)
            self.assertTrue(all(e['signature'] == 'Test Researcher' for e in entries if e['id'] != 'manual'))
            saved = backend.records.get(NAMESPACE, 'animal-a')
            raw_results = backend.load_core_data()
            for field in ('daten', 'gewicht', 'pdg', 'sperm'):
                raw_results['animals']['animal-a'].setdefault(field, []).append(
                    dict(datum='2026-02-01', wert=12.5, mot_pct=60))
            backend.save_core_data(raw_results, clinical_context=self._321_actor())
            self.assertEqual(backend.records.get(NAMESPACE, 'animal-a'), saved)
            backend.save_core_data(backend.load_core_data(), clinical_context=self._321_actor())
            self.assertEqual(backend.records.get(NAMESPACE, 'animal-a'), saved)
            self.assertEqual(store.get_entries('animal-a'), entries)

    def test_321_future_only_policy_timeline_retained_annotations_and_stale_revision(self):
        from Plugins.core.backend.clinical_mirroring import NAMESPACE
        from Plugins.core.role_block_presets import normalize_event_definition
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            bootstrap = _latency_snapshot()
            bootstrap['animals']['animal-a']['events'].append(
                dict(event_id='historical321', event_type='custom_321', datum='2019-01-01'))
            backend.save_core_data(bootstrap)
            def flag(value, revision):
                definition = normalize_event_definition(dict(name='Test Clinical', event_type='custom_321',
                    kind='counting', mirror_in_medical_history=value, revision=revision))
                backend.records.put('configuration', 'role-block-presets', {'event_definitions': [definition]})
            def save(mutate):
                snapshot = backend.load_core_data()
                mutate(snapshot['animals']['animal-a']['events'])
                return backend.save_core_data(snapshot, clinical_context=self._321_actor())
            def add(identity):
                return lambda events: events.append(dict(event_id=identity, event_type='custom_321', datum='2020-01-01'))
            flag(False, 1)
            save(add('A'))
            flag(True, 2)
            save(lambda events: next(e for e in events if e['event_id'] == 'historical321').update(datum='2019-01-02'))
            save(lambda events: next(e for e in events if e['event_id'] == 'A').update(datum='2020-01-02'))
            self.assertEqual(backend.records.get(NAMESPACE, 'animal-a')['entries'], [])
            save(add('B'))
            stale = backend.load_core_data()
            original = copy.deepcopy(backend.records.get(NAMESPACE, 'animal-a')['entries'][0])
            flag(False, 3)
            save(lambda events: next(e for e in events if e['event_id'] == 'B').update(datum='2020-01-03'))
            next(e for e in stale['animals']['animal-a']['events'] if e['event_id'] == 'B')['datum'] = '2020-01-04'
            with self.assertRaisesRegex(ConflictError, 'Stale clinical source'):
                backend.save_core_data(stale, clinical_context=self._321_actor())
            save(lambda events: events.__setitem__(slice(None), [e for e in events if e['event_id'] != 'B']))
            save(add('C'))
            state = backend.records.get(NAMESPACE, 'animal-a')
            self.assertEqual([e['entry_type'] for e in state['entries']], ['core_event', 'core_correction', 'core_deletion'])
            self.assertEqual(state['entries'][0], original)
            self.assertTrue(all(e['original_entry_id'] == original['id'] for e in state['entries'][1:]))
            self.assertFalse(state['sources']['A']['policy']['mirror'])
            self.assertEqual(state['sources']['B']['policy']['definition_revision'], 2)
            backend.close()
            reopened = self.open_backend(directory)
            reopened.save_core_data(reopened.load_core_data(), clinical_context=self._321_actor())
            self.assertEqual(reopened.records.get(NAMESPACE, 'animal-a'), state)
            with self.assertRaisesRegex(ConflictError, 'deleted clinical source'):
                snapshot = reopened.load_core_data()
                snapshot['animals']['animal-a']['events'].append(original['source_payload'])
                reopened.save_core_data(snapshot, clinical_context=self._321_actor())

    def test_321_destination_failure_permissions_and_newborn_departure(self):
        from Plugins.core.backend.clinical_mirroring import ClinicalMirrorTransaction, NAMESPACE
        from Plugins.core.backend.errors import ValidationError
        from Plugins.core.lifecycle_events import apply_departure
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            baseline = _latency_snapshot()
            backend.save_core_data(baseline)
            before = backend.load_core_data()
            changed = copy.deepcopy(before)
            changed['animals']['animal-a']['events'].append(dict(event_id='new321', event_type='surgery', datum='2026-01-01'))
            with patch.object(ClinicalMirrorTransaction, 'flush', side_effect=RuntimeError('clinical write failed')):
                with self.assertRaisesRegex(RuntimeError, 'clinical write failed'):
                    backend.save_core_data(changed, clinical_context=self._321_actor())
            self.assertEqual(backend.load_core_data(), before)
            self.assertIsNone(backend.records.get(NAMESPACE, 'animal-a'))
            denied = self._321_actor()
            denied['permissions']['core.edit_animal_measurements'] = False
            with self.assertRaisesRegex(ValidationError, 'not authorized'):
                backend.save_core_data(changed, clinical_context=denied)
            self.assertEqual(backend.load_core_data(), before)
            child = copy.deepcopy(before['animals']['animal-b'])
            child.update(ipid='test-321-newborn', name='test-321-newborn', rolle='experimental_offspring', events=[])
            changed = copy.deepcopy(before)
            changed['animals']['test-321-newborn'] = child
            backend.save_core_data(changed, clinical_context=self._321_actor())
            entries = backend.records.get(NAMESPACE, 'test-321-newborn')['entries']
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]['status_type'], 'own_birth')
            self.assertEqual(entries[0]['date'], '2020-01-01')
            changed = backend.load_core_data()
            record = changed['animals'].pop('test-321-newborn')
            apply_departure(record, departure_date='01.02.2026', reason='handover', recipient='Test recipient', actor='tester')
            changed['archived_animals']['test-321-newborn'] = record
            backend.save_core_data(changed, clinical_context=self._321_actor())
            backend.save_core_data(backend.load_core_data(), clinical_context=self._321_actor())
            entries = backend.records.get(NAMESPACE, 'test-321-newborn')['entries']
            self.assertEqual([e['status_type'] for e in entries], ['own_birth', 'departure'])
            self.assertEqual(entries[-1]['source_payload']['recipient'], 'Test recipient')

    def test_316_receipt_canonical_ack_without_writes_or_extra_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            snapshot = _latency_snapshot()
            event = snapshot['animals']['animal-a']['events'][0]
            event.pop('recorded_role')
            event.pop('event_id')
            receipt = backend.save_core_data(snapshot)
            accepted = receipt.acknowledged_records['animals', 'animal-a']
            self.assertTrue(accepted['events'][0]['event_id'])
            self.assertEqual(accepted['events'][0]['recorded_role'], 'Offspring')
            self.assertNotIn('event_id', event, 'caller is not mutated inside a transaction')
            self.assertEqual(accepted, backend.load_core_data()['animals']['animal-a'])
            submitted = backend.load_core_data()
            submitted['animals']['animal-a']['events'][0].pop('recorded_role')
            submitted['animals']['animal-a']['events'][0]['event_type'] = 'blood DRAW'
            statements = []
            connect = backend.adapter._connect
            def traced_connect():
                connection = connect()
                connection.set_trace_callback(statements.append)
                return connection
            with patch.object(backend.adapter, '_connect', side_effect=traced_connect), \
                 patch.object(backend.animals, 'load_snapshot', side_effect=AssertionError('extra reload')):
                ack = backend.save_core_data(submitted)
            self.assertEqual(ack.acknowledged_records['animals', 'animal-a'], accepted)
            self.assertEqual(ack.audit_before, {'animals': {}, 'archived_animals': {}})
            self.assertEqual(ack.audit_after, ack.audit_before)
            self.assertFalse(any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))
                                 for s in statements), statements)
            self.assertEqual(sum(s.lstrip().upper().startswith('SELECT') for s in statements), 4)
            submitted['animals']['animal-a']['events'][0]['datum'] = '2026-12-01'
            self.assertEqual(ack.acknowledged_records['animals', 'animal-a'], accepted,
                             'receipt evidence must be detached from subsequent edits')

    def test_316_receipt_actual_multi_record_delta_and_commit_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            backend.save_core_data(_latency_snapshot())
            snapshot = backend.load_core_data()
            snapshot['archived_animals']['animal-b'] = snapshot['animals'].pop('animal-b')
            backend.save_core_data(snapshot)
            before = backend.load_core_data()
            changed = copy.deepcopy(before)
            changed['animals']['animal-a']['gewicht'][0]['wert'] = 12.5
            changed['archived_animals']['animal-b']['events'][0]['unknown'] = {'zero': 0, 'flag': False}
            receipt = backend.save_core_data(changed)
            after = backend.load_core_data()
            for section, ipid in [('animals', 'animal-a'), ('archived_animals', 'animal-b')]:
                self.assertEqual(receipt.audit_before[section][ipid], before[section][ipid])
                self.assertEqual(receipt.audit_after[section][ipid], after[section][ipid])
                self.assertEqual(receipt.acknowledged_records[section, ipid], after[section][ipid])
            transaction = backend.adapter.transaction
            @contextmanager
            def fail_commit(*args, **kwargs):
                with transaction(*args, **kwargs) as connection:
                    yield connection
                    if kwargs.get('write'):
                        raise RuntimeError('commit failed')
            rejected = copy.deepcopy(after)
            rejected['animals']['animal-a']['genotype'] = 'must rollback'
            with patch.object(backend.adapter, 'transaction', fail_commit):
                with self.assertRaisesRegex(RuntimeError, 'commit failed'):
                    backend.save_core_data(rejected)
            self.assertEqual(backend.load_core_data(), after)
            connect = backend.adapter._connect
            class CommitFailure:
                def __init__(self):
                    self.connection = connect()
                def __getattr__(self, name):
                    return getattr(self.connection, name)
                def commit(self):
                    raise RuntimeError('sqlite commit failed')
            with patch.object(backend.adapter, '_connect', side_effect=CommitFailure):
                with self.assertRaisesRegex(RuntimeError, 'sqlite commit failed'):
                    backend.save_core_data(rejected)
            self.assertEqual(backend.load_core_data(), after)

    def test_315_null_stored_role_never_becomes_literal_none(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            backend.save_core_data(_latency_snapshot())
            snapshot = backend.load_core_data()
            event = snapshot['animals']['animal-a']['events'][0]
            event['recorded_role'] = None
            with backend.adapter.transaction(write=True) as connection:
                connection.execute('UPDATE animal_events SET payload_json=? WHERE event_id=?',
                                   (json.dumps(event), event['event_id']))
            receipt = backend.save_core_data(snapshot)
            accepted = receipt.acknowledged_records['animals', 'animal-a']['events'][0]
            self.assertEqual(accepted['recorded_role'], 'Offspring')
            self.assertNotEqual(accepted['recorded_role'], 'None')

    def open_backend(self, directory: str):
        backend = ProgTrackBackend(
            runtime_paths(Path(directory)), acquire_process_lock=False
        )
        self.addCleanup(backend.close)
        return backend

    def test_seed_is_loaded_into_the_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            snapshot = backend.load_core_data()
            self.assertGreater(len(snapshot.get("animals", {})), 0)
            self.assertNotEqual(snapshot.get("animals"), {})
            self.assertTrue(backend.paths.database_path.is_file())

    def test_stale_or_incomplete_standalone_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            lock_path = paths.runtime / "standalone.lock"
            lock_path.write_text("", encoding="utf-8")
            stale_time = time.time() - StandaloneProcessLock._INCOMPLETE_LOCK_GRACE_SECONDS - 1
            os.utime(lock_path, (stale_time, stale_time))

            lock = StandaloneProcessLock(lock_path)
            lock.acquire()
            self.addCleanup(lock.release)
            owner = json.loads(lock_path.read_text(encoding="utf-8"))
            self.assertEqual(owner["pid"], os.getpid())

    def test_dead_pid_standalone_lock_is_recovered(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            lock_path = paths.runtime / "standalone.lock"
            lock_path.write_text(
                json.dumps({
                    "pid": 2147483647,
                    "host": socket.gethostname(),
                    "started_at": "2020-01-01T00:00:00+00:00",
                }),
                encoding="utf-8",
            )
            lock = StandaloneProcessLock(lock_path)
            lock.acquire()
            self.addCleanup(lock.release)
            self.assertEqual(json.loads(lock_path.read_text())["pid"], os.getpid())

    def test_active_standalone_lock_still_blocks_second_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            first = StandaloneProcessLock(paths.runtime / "standalone.lock")
            first.acquire()
            self.addCleanup(first.release)
            with self.assertRaises(StandaloneLockError) as caught:
                StandaloneProcessLock(paths.runtime / "standalone.lock").acquire()
            self.assertEqual(
                Path(caught.exception.lock_path), paths.runtime / "standalone.lock"
            )
            self.assertEqual(caught.exception.owner["pid"], os.getpid())

    def test_records_use_optimistic_revisions_and_entity_leases(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            revision = backend.records.put("tests", "record", {"value": 1})
            self.assertEqual(revision, 1)
            backend.records.put("tests", "record", {"value": 2}, expected_revision=1)
            payload, revision = backend.records.get_with_revision(
                "tests", "record", default=None
            )
            self.assertEqual(payload, {"value": 2})
            self.assertEqual(revision, 2)
            with self.assertRaises(ConflictError):
                backend.records.put("tests", "record", {"value": 3}, expected_revision=1)

            missing, missing_revision = backend.records.get_with_revision(
                "tests", "missing", default={"value": "default"}
            )
            self.assertEqual(missing, {"value": "default"})
            self.assertEqual(missing_revision, 0)

            lease = backend.leases.acquire(
                "animal", "test-animal", owner_login="first", owner_display="First"
            )
            with self.assertRaises(LockConflictError):
                backend.leases.acquire(
                    "animal", "test-animal", owner_login="second", owner_display="Second"
                )
            self.assertTrue(backend.leases.heartbeat(lease["token"]))
            self.assertTrue(
                backend.leases.force_release_as_lord(
                    "animal", "test-animal", actor_role="lord",
                    reason="current backend test", audit=backend.audit,
                    actor_login="test-lord",
                )
            )
            force_events = [
                event
                for event in backend.audit.list_events()
                if event["action"] == "force_release"
            ]
            self.assertTrue(force_events)
            self.assertEqual(force_events[0]["actor_login"], "test-lord")
            self.assertEqual(force_events[0]["payload"]["reason"], "current backend test")

    def test_core_parentage_write_rejects_concurrent_snapshot_change(self):
        """Parentage validation and replacement share one backend revision check."""
        with tempfile.TemporaryDirectory() as directory:
            paths = runtime_paths(Path(directory))
            first = ProgTrackBackend(
                paths, acquire_process_lock=False, bootstrap_seed=False
            )
            second = ProgTrackBackend(
                paths, acquire_process_lock=False, bootstrap_seed=False
            )
            self.addCleanup(first.close)
            self.addCleanup(second.close)
            initial = {
                "animals": {
                    "child": {
                        "ipid": "child", "name": "Child",
                        "species": "Callithrix jacchus", "origin": "Test", "sex": "female",
                        "birth_date": "01.01.2020",
                        "eizellspenderin": "", "samenspender": "",
                    },
                    "mother": {
                        "ipid": "mother", "name": "Mother",
                        "species": "Callithrix jacchus", "origin": "Test", "sex": "female",
                        "birth_date": "01.01.2010",
                    },
                    "father": {
                        "ipid": "father", "name": "Father",
                        "species": "Callithrix jacchus", "origin": "Test", "sex": "male",
                        "birth_date": "01.01.2010",
                    },
                },
                "archived_animals": {},
            }
            first.save_core_data(initial)
            validated = first.load_core_data()
            token = first.animals.parentage_revision_for_snapshot(validated)

            concurrent = second.load_core_data()
            concurrent["animals"]["father"]["samenspender"] = "mother"
            second.save_core_data(concurrent)

            stale_draft = copy.deepcopy(validated)
            stale_draft["animals"]["child"]["eizellspenderin"] = "mother"
            with self.assertRaises(ConflictError):
                first.save_core_data(
                    stale_draft,
                    expected_parentage_revision=token,
                )
            current = first.load_core_data()
            self.assertEqual(current["animals"]["father"]["samenspender"], "mother")
            self.assertEqual(current["animals"]["child"].get("eizellspenderin", ""), "")

    def test_identical_core_save_does_not_rewrite_or_bump_revisions(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            snapshot = _latency_snapshot()
            backend.save_core_data(snapshot)

            def state():
                with backend.adapter.transaction() as connection:
                    animals = [tuple(row) for row in connection.execute(
                        "SELECT ipid,revision,updated_at FROM animals ORDER BY ipid"
                    ).fetchall()]
                    measurements = [tuple(row) for row in connection.execute(
                        "SELECT measurement_id,animal_ipid,created_at FROM measurements "
                        "ORDER BY measurement_id"
                    ).fetchall()]
                    events = [tuple(row) for row in connection.execute(
                        "SELECT event_id,occurred_at,payload_json,created_at FROM animal_events "
                        "ORDER BY event_id"
                    ).fetchall()]
                    settings = tuple(connection.execute(
                        "SELECT revision,updated_at FROM installation WHERE key='core.settings'"
                    ).fetchone())
                return animals, measurements, events, settings

            before = state()
            statements: list[str] = []
            connect = backend.adapter._connect

            def traced_connect():
                connection = connect()
                connection.set_trace_callback(statements.append)
                return connection

            backend.adapter._connect = traced_connect
            backend.save_core_data(snapshot)
            backend.adapter._connect = connect

            self.assertEqual(state(), before)
            self.assertFalse(any(
                statement.lstrip().upper().startswith((
                    "INSERT INTO ANIMALS", "UPDATE ANIMALS", "DELETE FROM ANIMALS",
                    "INSERT INTO MEASUREMENTS", "UPDATE MEASUREMENTS",
                    "DELETE FROM MEASUREMENTS", "INSERT INTO ANIMAL_EVENTS",
                    "UPDATE ANIMAL_EVENTS", "DELETE FROM ANIMAL_EVENTS",
                    "INSERT INTO INSTALLATION", "UPDATE INSTALLATION",
                ))
                for statement in statements
            ), statements)
            event_reads = [
                statement for statement in statements
                if statement.lstrip().upper().startswith("SELECT")
                and "FROM ANIMAL_EVENTS" in statement.upper()
            ]
            self.assertEqual(len(event_reads), 1, statements)

    def test_316_read_order_and_distinct_same_day_measurements_reuse_row_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            snapshot = _latency_snapshot()
            ipid, record = next(iter(snapshot['animals'].items()))
            record['gewicht'] = [
                {'datum': '2026-01-02', 'wert': 27, 'meta': {'flag': False}},
                {'datum': '2026-01-01', 'wert': 26, 'sample_id': 'sample-316', 'replicate': 1},
                {'datum': '2026-01-01', 'wert': 26, 'sample_id': 'sample-316'},
            ]
            backend.save_core_data(snapshot)

            def rows():
                with backend.adapter.transaction() as connection:
                    return [tuple(r) for r in connection.execute(
                        'SELECT measurement_id,value_json,source_id,created_at FROM measurements '
                        'WHERE animal_ipid=? AND kind=? ORDER BY measurement_id', (ipid, 'weight'))]

            original = rows()
            loaded = backend.load_core_data()
            backend.save_core_data(loaded)
            self.assertEqual(rows(), original, 'reader sort order must not replace row IDs')
            loaded['animals'][ipid]['gewicht'].reverse()
            backend.save_core_data(loaded)
            self.assertEqual(rows(), original)
            loaded = backend.load_core_data()
            duplicate = copy.deepcopy(next(r for r in loaded['animals'][ipid]['gewicht'] if r['wert'] == 26))
            duplicate['replicate'] = 2
            loaded['animals'][ipid]['gewicht'].append(duplicate)
            backend.save_core_data(loaded)
            appended = rows()
            self.assertEqual(len(appended), 4)
            self.assertTrue(set(original).issubset(set(appended)))
            loaded = backend.load_core_data()
            backend.save_core_data(loaded)
            self.assertEqual(rows(), appended)
            loaded['animals'][ipid]['gewicht'].pop()
            backend.save_core_data(loaded)
            self.assertEqual(len(rows()), 3)
            self.assertTrue(set(rows()).issubset(set(appended)))

    def test_core_save_only_rewrites_changed_animal_measurements(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            backend.save_core_data(_latency_snapshot())

            def revisions():
                with backend.adapter.transaction() as connection:
                    return {
                        str(row["ipid"]): int(row["revision"])
                        for row in connection.execute(
                            "SELECT ipid,revision FROM animals"
                        ).fetchall()
                    }

            def untouched_child_rows():
                with backend.adapter.transaction() as connection:
                    measurements = [tuple(row) for row in connection.execute(
                        "SELECT measurement_id,created_at FROM measurements "
                        "WHERE animal_ipid='animal-b' ORDER BY measurement_id"
                    ).fetchall()]
                    events = [tuple(row) for row in connection.execute(
                        "SELECT event_id,occurred_at,payload_json,created_at FROM animal_events "
                        "WHERE animal_ipid='animal-b' ORDER BY event_id"
                    ).fetchall()]
                return measurements, events

            before_revisions = revisions()
            before_untouched = untouched_child_rows()
            changed = backend.load_core_data()
            changed["animals"]["animal-a"]["gewicht"][0]["wert"] = 10.5
            backend.save_core_data(changed)

            after_revisions = revisions()
            self.assertEqual(after_revisions["animal-a"], before_revisions["animal-a"] + 1)
            self.assertEqual(after_revisions["animal-b"], before_revisions["animal-b"])
            self.assertEqual(untouched_child_rows(), before_untouched)
            reloaded = backend.load_core_data()
            self.assertEqual(
                reloaded["animals"]["animal-a"]["gewicht"][0]["wert"], 10.5
            )

    def test_master_idle_resets_reuse_bounded_qt_timers(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication, QWidget
        from Plugins.Master_Track.plugin import MasterTrackPlugin

        application = QApplication.instance() or QApplication([])
        parent = QWidget()
        plugin = object.__new__(MasterTrackPlugin)
        plugin.app = parent
        plugin._current_username = "timer-test"
        plugin._effective_timeout_minutes = 30
        plugin._idle_timer = None
        plugin._warning_timer = None
        plugin._idle_warned = False
        plugin._idle_warning_countdown = None
        plugin._idle_warning_dialog = None

        for _ in range(1000):
            plugin.reset_idle_timer()
        timers = parent.findChildren(QTimer)
        self.assertEqual(len(timers), 2)
        timer_ids = {id(timer) for timer in timers}
        self.assertTrue(all(timer.isActive() for timer in timers))

        plugin._stop_idle_timer()
        plugin._current_username = None
        self.assertEqual(len(parent.findChildren(QTimer)), 2)
        self.assertFalse(any(timer.isActive() for timer in timers))

        plugin._current_username = "timer-test"
        plugin.reset_idle_timer()
        self.assertEqual({id(timer) for timer in parent.findChildren(QTimer)}, timer_ids)
        self.assertTrue(all(timer.isActive() for timer in timers))
        plugin._stop_idle_timer()
        parent.close()
        application.processEvents()

    def test_master_logout_can_skip_a_session_already_saved_by_the_host(self):
        from Plugins.Master_Track.permissions import ROLE_GUEST
        from Plugins.Master_Track.plugin import MasterTrackPlugin

        plugin = object.__new__(MasterTrackPlugin)
        plugin._current_username = "timer-test"
        plugin._current_role = "user"
        plugin._idle_timer = None
        plugin._warning_timer = None
        plugin._idle_warned = False
        plugin._idle_warning_countdown = None
        plugin._idle_warning_dialog = None
        saved = []
        audited = []
        plugin.save_session = lambda: saved.append(True)
        plugin.audit = lambda action, username: audited.append((action, username))

        plugin.logout(save_session=False)

        self.assertEqual(saved, [])
        self.assertEqual(audited, [("logout", "timer-test")])
        self.assertIsNone(plugin.current_username)
        self.assertEqual(plugin.current_role, ROLE_GUEST)

    def test_interchange_preserves_records_and_managed_documents(self):
        with tempfile.TemporaryDirectory() as directory:
            source_root = Path(directory) / "source"
            target_root = Path(directory) / "target"
            source = ProgTrackBackend(
                runtime_paths(source_root), acquire_process_lock=False
            )
            source.records.put("tests", "roundtrip", {"value": "ok"})
            document = source_root / "source.pdf"
            document.write_bytes(b"%PDF-1.4\ncurrent backend test\n")
            managed = source.documents.add(
                document, owner_type="project", owner_id="test-project", actor="tester"
            )
            package = Path(directory) / "roundtrip.ptdb"
            source.interchange.export_package(package, package_id="current-test")
            self.assertTrue(source.interchange.preview(package).valid)
            source.close()

            target = ProgTrackBackend(
                runtime_paths(target_root, application_root=target_root / "empty-app"),
                acquire_process_lock=False,
            )
            self.addCleanup(target.close)
            preview = target.interchange.import_package(package)
            self.assertTrue(preview.valid)
            self.assertEqual(target.records.get("tests", "roundtrip"), {"value": "ok"})
            imported = target.documents.list_active()
            self.assertEqual([row["document_id"] for row in imported], [managed["document_id"]])
            self.assertEqual(target.documents.payload_path(imported[0]).read_bytes(), document.read_bytes())

    def test_role_configuration_is_backend_owned(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = self.open_backend(directory)
            registry = AnimalRoleRegistry(backend)
            self.assertTrue(registry.roles())
            custom = registry.make_custom_role("Current test role", "role_partner")
            registry.save_roles(registry.roles() + [custom])
            reloaded = AnimalRoleRegistry(backend)
            self.assertIn("Current test role", [role["label"] for role in reloaded.roles()])

    def test_retired_dynamic_json_stores_are_not_runtime_authorities(self):
        retired = (
            "progtrack_daten.json",
            "Plugins/Heritage_Track/heritage_animals.json",
            "Plugins/Heritage_Track/heritage_settings.json",
            "Plugins/Medi_Track/medi_history.json",
            "Plugins/Animal_Reports/animal_report_data.json",
            "Plugins/Cage__Track/cage.json",
            "Plugins/Projects_Track/projects_history.json",
        )
        for relative in retired:
            self.assertFalse((ROOT / relative).exists(), relative)


if __name__ == "__main__":
    unittest.main()
