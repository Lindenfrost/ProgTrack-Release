from __future__ import annotations

import copy
import os
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from Plugins.core.authorization import AuthorizationService, CanonicalUnitService, OrganizationUnit
from Plugins.core.backend import ProgTrackBackend, ConflictError
from Plugins.core.backend.repositories import DomainRecordRepository
from Plugins.core.runtime_paths import resolve_runtime_paths
from Plugins.core.institution_branding import _OrganizationalUnitsEditor
from Plugins.Master_Track.auth import UserDB
from Plugins.Master_Track.plugin import MasterTrackPlugin
from Plugins.Master_Track.dialogs import (
    _organization_unit_combo, _validated_organization_unit, _organization_unit_display,
    _CreateUserSubDialog, _EditUserDialog, AuditLogsDialog,
)
from Plugins.Master_Track.permissions import can as permission_can, get_permission_label
from PyQt6.QtWidgets import QApplication, QMessageBox


class _Records:
    def __init__(self):
        self.values = {}
        self.revisions = {}

    def get(self, namespace, record_id, default=None):
        value = self.values.get((namespace, record_id), default)
        return copy.deepcopy(value)

    def get_with_revision(self, namespace, record_id, default=None):
        return self.get(namespace, record_id, default), self.revisions.get((namespace, record_id), 0)

    def put(self, namespace, record_id, payload, expected_revision=None):
        key = (namespace, record_id)
        current = self.revisions.get(key, 0)
        if expected_revision is not None and expected_revision != current:
            raise RuntimeError("stale")
        self.values[key] = copy.deepcopy(payload)
        self.revisions[key] = current + 1
        return current + 1

    def put_many(self, records, expected_revisions=None):
        expected_revisions = expected_revisions or {}
        for namespace, record_id, _payload in records:
            key = (namespace, record_id)
            if expected_revisions.get(key) is not None and expected_revisions[key] != self.revisions.get(key, 0):
                raise RuntimeError("stale")
        result = {}
        for namespace, record_id, payload in records:
            result[(namespace, record_id)] = self.put(
                namespace, record_id, payload,
                expected_revision=expected_revisions.get((namespace, record_id)),
            )
        return result

    def put_many_audited(self, records, *, expected_revisions, audit_events):
        old_values, old_revisions = copy.deepcopy(self.values), dict(self.revisions)
        old_events = copy.deepcopy(self.audit.events)
        try:
            result = self.put_many(records, expected_revisions)
            for event in audit_events:
                self.audit.append(**event)
            return result
        except Exception:
            self.values, self.revisions, self.audit.events = old_values, old_revisions, old_events
            raise


class _Audit:
    def __init__(self):
        self.events = []

    def append(self, **event):
        self.events.append(event)


class _Backend:
    def __init__(self):
        self.records = _Records()
        self.audit = _Audit()
        self.records.audit = self.audit


class _Policy:
    def __init__(self, allowed):
        self.allowed = allowed

    def can(self, action, *, write=False):
        return self.allowed and action == "core.manage_institution_settings"


class Issue245ServiceTest(unittest.TestCase):
    def backend(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        paths = resolve_runtime_paths(Path(directory.name), environ={'PROGTRACK_PORTABLE': '1'})
        backend = ProgTrackBackend(paths, acquire_process_lock=False, bootstrap_seed=False)
        self.addCleanup(backend.close)
        return backend

    def manager(self, backend, *, role='user', jobs=None, granted=None, revoked=None):
        backend.records.put('security', 'users', [dict(
            username='manager', role=role, jobs=jobs if jobs is not None else ['manager'],
            unit_id='test_unit_245', unit='Original',
            permissions={'granted': granted or [], 'revoked': revoked or []},
            password_hash='DO_NOT_AUDIT_HASH', password_salt='DO_NOT_AUDIT_SALT',
        )])
        mt = MasterTrackPlugin.__new__(MasterTrackPlugin)
        mt._current_username, mt._current_role = 'manager', role
        mt.user_db = UserDB('unused', backend)
        return mt, AuthorizationService(mt, backend=backend)

    def test_all_mutators_including_bulk_and_seed_require_live_permission(self):
        backend = self.backend()
        service = CanonicalUnitService(backend, _Policy(False))
        operations = [lambda: service.create('test_unit_245', 'Name', authorized=True),
            lambda: service.update('test_unit_245', 'Changed', authorized=True),
            lambda: service.set_archived('test_unit_245', True, authorized=True),
            lambda: service.delete('test_unit_245', authorized=True),
            lambda: service.save([OrganizationUnit('test_unit_245', 'Name')], authorized=True),
            lambda: service.ensure_seed([OrganizationUnit('test_unit_245', 'Name')], authorized=True)]
        for operation in operations:
            with self.subTest(operation=operations.index(operation)), self.assertRaises(PermissionError):
                operation()
        self.assertEqual(service.load(), {})
        self.assertEqual(backend.audit.list_events(), [])
        with self.assertRaises(PermissionError):
            CanonicalUnitService(backend).save([])

    def test_canonical_permission_roles_jobs_grants_revocations_and_locales(self):
        permission = 'core.manage_institution_settings'
        for role, jobs, grants, revokes, expected in [
            ('lord', [], [], [], True), ('master', [], [], [], True),
            ('user', ['manager'], [], [], True), ('user', ['researcher'], [], [], False),
            ('user', [], [permission], [], True), ('user', ['manager'], [], [permission], False),
            ('guest', [], [], [], False),
        ]:
            with self.subTest(role=role, jobs=jobs, grants=grants, revokes=revokes):
                self.assertEqual(permission_can(role, jobs, grants, revokes, permission), expected)
        for locale in ('en', 'de', 'it', 'ru'):
            with self.subTest(locale=locale):
                self.assertNotEqual(get_permission_label(permission, locale), permission)
                messages = json.loads((Path(__file__).resolve().parents[1] / 'lang' / f'messages_{locale}.json').read_text('utf-8'))
                for key in ('units.catalogue_unavailable', 'units.assignment_changed', 'units.user_refresh_failed'):
                    self.assertTrue(messages[key])
                editor = _OrganizationalUnitsEditor(CanonicalUnitService(self.backend()), '', authorized=False, messages=messages)
                self.addCleanup(editor.close)
                self.assertEqual(editor._mutation_error(ValueError('Invalid organizational unit ID.')), messages['units.invalid_id'])
                self.assertEqual(editor._mutation_error(PermissionError('denied')), messages['units.permission_denied'])
                self.assertEqual(editor._mutation_error(ConflictError('stale')), messages['units.assignment_changed'])
                dialog = AuditLogsDialog.__new__(AuditLogsDialog)
                dialog.messages = messages
                for action in ('organization_unit_create', 'organization_unit_update',
                    'organization_unit_archive', 'organization_unit_restore',
                    'organization_unit_delete', 'organization_units_replace'):
                    self.assertNotEqual(dialog._format_change_type(action, '', {}), action)

    def test_real_sqlite_crud_has_atomic_revision_actor_and_assignment_evidence(self):
        backend = self.backend()
        mt, policy = self.manager(backend)
        service = CanonicalUnitService(backend, policy)
        service.create('test_unit_245', 'Original', actor='forged-login')
        mt.user_db.load()
        service.update('test_unit_245', 'Renamed', actor='forged-login')
        self.assertEqual(mt.user_db.get_user('manager')['unit'], 'Renamed')
        self.assertEqual(mt.current_unit_id, 'test_unit_245')
        self.assertTrue(policy.same_unit('test_unit_245'))
        service.set_archived('test_unit_245', True)
        self.assertFalse(policy.same_unit('test_unit_245'))
        service.set_archived('test_unit_245', False)
        self.assertTrue(policy.same_unit('test_unit_245'))
        service.delete('test_unit_245')
        self.assertEqual(mt.current_unit_id, '')
        self.assertEqual(policy.current_unit_id, '')
        self.assertFalse(policy.same_unit('test_unit_245'))
        events = list(reversed(backend.audit.list_events()))
        self.assertEqual([e['action'] for e in events], [
            'organization_unit_create', 'organization_unit_update', 'organization_unit_archive',
            'organization_unit_restore', 'organization_unit_delete'])
        self.assertEqual([e['payload']['catalogue_revision'] for e in events], [1, 2, 3, 4, 5])
        for event in events:
            self.assertEqual(event['actor_login'], 'manager')
            self.assertEqual(event['entity_id'], 'test_unit_245')
            self.assertEqual(event['event_id'], event['correlation_id'])
        self.assertEqual(events[-1]['payload']['users_affected'], ['manager'])
        self.assertEqual(events[-1]['payload']['users_affected_count'], 1)
        self.assertNotIn('DO_NOT_AUDIT', json.dumps(events))

    def test_audit_insert_failure_rolls_back_every_unit_mutation_and_users(self):
        backend = self.backend()
        service = CanonicalUnitService(backend)
        service.create('test_unit_245', 'Original', authorized=True)
        backend.records.put('security', 'users', [dict(username='test_user_245', role='user',
            unit_id='test_unit_245', unit='Original', jobs=[], permissions={})])
        before = backend.records.list_all()
        revisions = [backend.records.get_with_revision('security', key)[1] for key in ('organization-units', 'users')]
        with backend.adapter.transaction(write=True) as connection:
            connection.execute("CREATE TRIGGER fail_unit_audit BEFORE INSERT ON audit_events BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END")
        operations = [lambda: service.create('other_unit_245', 'Other', authorized=True, actor='manager'),
            lambda: service.update('test_unit_245', 'Renamed', authorized=True, actor='manager'),
            lambda: service.set_archived('test_unit_245', True, authorized=True, actor='manager'),
            lambda: service.delete('test_unit_245', authorized=True, actor='manager'),
            lambda: service.save([], authorized=True, actor='manager')]
        for index, operation in enumerate(operations):
            with self.subTest(operation=index), self.assertRaisesRegex(Exception, 'audit unavailable'):
                operation()
            self.assertEqual(backend.records.list_all(), before)
            self.assertEqual([backend.records.get_with_revision('security', key)[1] for key in ('organization-units', 'users')], revisions)
            self.assertEqual(backend.audit.list_events(), [])

    def test_partial_record_write_failure_rolls_back_delete_and_rename(self):
        backend = self.backend()
        service = CanonicalUnitService(backend)
        service.create('test_unit_245', 'Original', authorized=True)
        backend.records.put('security', 'users', [dict(username='test_user_245', unit_id='test_unit_245', unit='Original')])
        before = backend.records.list_all()
        with backend.adapter.transaction(write=True) as connection:
            connection.execute("CREATE TRIGGER fail_unit_users BEFORE UPDATE ON domain_records WHEN NEW.namespace='security' AND NEW.record_id='users' BEGIN SELECT RAISE(ABORT, 'users write failed'); END")
        for operation in ('rename', 'delete'):
            with self.subTest(operation=operation), self.assertRaisesRegex(Exception, 'users write failed'):
                if operation == 'rename':
                    service.update('test_unit_245', 'Renamed', authorized=True, actor='manager')
                else:
                    service.delete('test_unit_245', authorized=True, actor='manager')
            self.assertEqual(backend.records.list_all(), before)
            self.assertEqual(backend.audit.list_events(), [])

    def test_missing_atomic_backend_and_corrupt_users_fail_closed(self):
        backend = self.backend()
        service = CanonicalUnitService(backend)
        service.create('test_unit_245', 'Original', authorized=True)
        backend.records.put('security', 'users', {'broken': 'not a list'})
        with self.assertRaisesRegex(ValueError, 'Invalid user catalogue'):
            service.delete('test_unit_245', authorized=True)
        self.assertIn('test_unit_245', service.load())
        with patch.object(backend.records, 'put_many_audited', None):
            with self.assertRaisesRegex(RuntimeError, 'atomic audited'):
                service.create('other_unit_245', 'Other', authorized=True, actor='manager')
        self.assertNotIn('other_unit_245', service.load())

    def test_trusted_local_does_not_invent_actor_audit(self):
        backend = self.backend()
        policy = AuthorizationService(backend=backend, trusted_local=True)
        service = CanonicalUnitService(backend, policy)
        service.create('test_unit_245', 'Local', actor='installation-admin')
        service.delete('test_unit_245', actor='installation-admin')
        self.assertEqual(backend.audit.list_events(), [])

    def test_bulk_replacement_has_same_permission_cas_unassignment_and_audit(self):
        backend = self.backend()
        service = CanonicalUnitService(backend)
        service.create('test_unit_245', 'Original', authorized=True)
        backend.records.put('security', 'users', [dict(username='test_user_245', unit_id='test_unit_245', unit='Original')])
        service.save([], expected_revision=1, authorized=True, actor='manager')
        self.assertEqual(backend.records.get('security', 'users')[0]['unit_id'], '')
        self.assertEqual(backend.audit.list_events()[0]['action'], 'organization_units_replace')
        with self.assertRaises(ConflictError):
            service.save([OrganizationUnit('test_unit_245', 'Old')], expected_revision=1, authorized=True, actor='manager')
        self.assertEqual(service.load(), {})

    def test_stale_editor_save_archive_delete_cannot_overwrite_remote_change(self):
        backend = self.backend()
        service = CanonicalUnitService(backend)
        service.create('test_unit_245', 'Original', authorized=True)
        for operation in ('rename', 'archive', 'delete'):
            with self.subTest(operation=operation):
                editor = _OrganizationalUnitsEditor(service, 'manager', authorized=True, messages={})
                self.addCleanup(editor.close)
                editor.table.selectRow(0)
                expected = editor._catalogue_revision
                service.update('test_unit_245', f'Remote-{expected}', authorized=True, actor='remote')
                before, events = backend.records.list_all(), backend.audit.list_events()
                with patch.object(QMessageBox, 'warning') as warning, patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes):
                    if operation == 'rename':
                        editor.name_edit.setText('Stale draft')
                        editor._save()
                    elif operation == 'archive':
                        editor._toggle_archive()
                    else:
                        editor._delete()
                warning.assert_called_once()
                self.assertEqual(backend.records.list_all(), before)
                self.assertEqual(backend.audit.list_events(), events)

    def test_revoked_permission_is_rechecked_by_existing_editor(self):
        backend = self.backend()
        mt, policy = self.manager(backend)
        service = CanonicalUnitService(backend, policy)
        service.create('test_unit_245', 'Original')
        editor = _OrganizationalUnitsEditor(service, 'manager', authorized=True, messages={}, user_db=mt.user_db)
        self.addCleanup(editor.close)
        editor.table.selectRow(0)
        users = backend.records.get('security', 'users')
        users[0]['permissions']['revoked'] = ['core.manage_institution_settings']
        backend.records.put('security', 'users', users)
        editor.name_edit.setText('Unauthorized change')
        with patch.object(QMessageBox, 'warning') as warning:
            editor._save()
        warning.assert_called_once()
        self.assertEqual(service.get('test_unit_245').display_name, 'Original')

    def test_denied_fields_remain_protected_after_new_selection_and_refresh(self):
        backend = self.backend()
        service = CanonicalUnitService(backend)
        service.create('test_unit_245', 'Original', authorized=True)
        editor = _OrganizationalUnitsEditor(service, '', authorized=False, messages={})
        self.addCleanup(editor.close)
        for operation in (lambda: editor.table.selectRow(0), editor._new, editor.refresh):
            operation()
            for field in (editor.id_edit, editor.name_edit):
                self.assertTrue(field.isReadOnly())
                self.assertIn('background: #f0f0f0', field.styleSheet())
        self.assertTrue(editor.table.isEnabled())

    def test_catalogue_failure_preserves_assignment_and_blocks_save(self):
        backend = self.backend()
        db = UserDB('unused', backend)
        with patch.object(CanonicalUnitService, 'load', side_effect=OSError('offline')):
            combo = _organization_unit_combo(db, 'test_unit_245', {'units.catalogue_unavailable': 'Unavailable'})
        self.assertEqual(combo.currentData(), 'test_unit_245')
        self.assertFalse(combo.isEnabled())
        with self.assertRaisesRegex(ValueError, 'Unavailable'):
            _validated_organization_unit(db, combo, {'units.catalogue_unavailable': 'Unavailable'})

    def test_already_open_user_selectors_reject_deleted_and_newly_archived_units(self):
        backend = self.backend()
        service, db = CanonicalUnitService(backend), UserDB('unused', backend)
        service.create('test_unit_245', 'Original', authorized=True)
        combo = _organization_unit_combo(db)
        combo.setCurrentIndex(combo.findData('test_unit_245'))
        service.set_archived('test_unit_245', True, authorized=True)
        with self.assertRaises(ValueError):
            _validated_organization_unit(db, combo, {})
        self.assertEqual(_validated_organization_unit(db, combo, {}, current_id='test_unit_245'), ('test_unit_245', 'Original'))
        service.delete('test_unit_245', authorized=True)
        with self.assertRaises(ValueError):
            _validated_organization_unit(db, combo, {}, current_id='test_unit_245')

    def test_independent_client_user_context_and_selectors_revalidate_revisions(self):
        backend = self.backend()
        service = CanonicalUnitService(backend)
        service.create('test_unit_245', 'Original', authorized=True)
        other = ProgTrackBackend(backend.paths, acquire_process_lock=False, bootstrap_seed=False)
        self.addCleanup(other.close)
        mt, policy = self.manager(other)
        self.assertTrue(policy.same_unit('test_unit_245'))
        service.update('test_unit_245', 'Remote name', authorized=True)
        user = mt.user_db.get_user('manager')
        self.assertEqual(user['unit'], 'Remote name')
        self.assertEqual(_organization_unit_display(mt.user_db, user, {}), 'Remote name')
        self.assertEqual(_organization_unit_combo(mt.user_db, 'test_unit_245').currentText(), 'Remote name')
        service.delete('test_unit_245', authorized=True)
        self.assertEqual(mt.current_unit_id, '')
        self.assertFalse(policy.same_unit('test_unit_245'))
        self.assertEqual(_organization_unit_combo(mt.user_db).findData('test_unit_245'), -1)

    def test_actual_create_and_edit_dialogs_reject_stale_unit_before_user_writes(self):
        backend = self.backend()
        service, db = CanonicalUnitService(backend), UserDB('unused', backend)
        service.create('test_unit_245', 'Original', authorized=True)
        backend.records.put('security', 'users', [dict(username='test_user_245', role='user',
            unit_id='test_unit_245', unit='Original', jobs=[], permissions={'granted': [], 'revoked': []})])
        create = _CreateUserSubDialog(None, {}, db)
        edit = _EditUserDialog(None, {}, db, 'test_user_245')
        self.addCleanup(create.close)
        self.addCleanup(edit.close)
        create.uname.setText('test_new_245')
        create.pw.setText('123456')
        create.f_unit.setCurrentIndex(create.f_unit.findData('test_unit_245'))
        edit._p_display.setText('Must not save')
        service.delete('test_unit_245', authorized=True)
        before = backend.records.list_all()
        create._accept()
        edit._save()
        self.assertTrue(create.error_label.text())
        self.assertTrue(edit.error_label.text())
        self.assertEqual(create.result(), 0)
        self.assertEqual(edit.result(), 0)
        self.assertEqual(backend.records.list_all(), before)
        self.assertEqual(db.get_user('test_user_245')['unit_id'], '')
        self.assertIsNone(db.get_user('test_new_245'))

    def test_actual_edit_dialog_catalogue_outage_does_not_clear_valid_assignment(self):
        backend = self.backend()
        service, db = CanonicalUnitService(backend), UserDB('unused', backend)
        service.create('test_unit_245', 'Original', authorized=True)
        backend.records.put('security', 'users', [dict(username='test_user_245', role='user',
            unit_id='test_unit_245', unit='Original', jobs=[], permissions={'granted': [], 'revoked': []})])
        before = backend.records.list_all()
        with patch.object(CanonicalUnitService, 'load', side_effect=OSError('offline')):
            dialog = _EditUserDialog(None, {}, db, 'test_user_245')
        self.addCleanup(dialog.close)
        self.assertFalse(dialog._p_unit.isEnabled())
        self.assertEqual(dialog._p_unit.currentData(), 'test_unit_245')
        dialog._p_display.setText('Must not save')
        dialog._save()
        self.assertIn('unavailable', dialog.error_label.text())
        self.assertEqual(backend.records.list_all(), before)

    def test_user_selector_localization_and_invalid_id_never_inferred_from_label(self):
        backend = self.backend()
        service, db = CanonicalUnitService(backend), UserDB('unused', backend)
        service.create('test_unit_245', 'Original', authorized=True)
        service.set_archived('test_unit_245', True, authorized=True)
        messages = {'cage_track.unassigned': 'Localized Unassigned', 'units.archived': 'Localized Archived'}
        combo = _organization_unit_combo(db, 'test_unit_245', messages)
        self.assertEqual(combo.itemText(0), 'Localized Unassigned')
        self.assertEqual(combo.currentText(), 'Original (Localized Archived)')
        self.assertEqual(combo.currentData(), 'test_unit_245')
        self.assertEqual(_organization_unit_combo(db, messages=messages).findData('test_unit_245'), -1)
        self.assertEqual(_organization_unit_combo(db, 'Original', messages).currentData(), '')
        self.assertEqual(_organization_unit_display(db, {'unit_id': 'missing', 'unit': 'Original'}, messages), 'Localized Unassigned')

    def test_failure_after_committed_rename_reports_refresh_without_false_save_failure(self):
        backend = self.backend()
        service = CanonicalUnitService(backend)
        service.create('test_unit_245', 'Original', authorized=True)
        editor = _OrganizationalUnitsEditor(service, 'manager', authorized=True, messages={},
            user_db=SimpleNamespace(load=lambda: (_ for _ in ()).throw(OSError('user reload failed'))))
        self.addCleanup(editor.close)
        editor.table.selectRow(0)
        editor.name_edit.setText('Renamed')
        with patch.object(QMessageBox, 'warning') as warning:
            editor._save()
        warning.assert_called_once()
        self.assertIn('change was saved', warning.call_args.args[2])
        self.assertEqual(service.get('test_unit_245').display_name, 'Renamed')
        self.assertEqual(editor.table.item(0, 1).text(), 'Renamed')

    def test_postgresql_repository_uses_atomic_revision_cas_for_single_and_batch_writes(self):
        # SQL protocol coverage only; not a live PostgreSQL integration PASS.
        statements = []
        class Connection:
            def execute(self, sql, params=()):
                statements.append((sql, params))
                self.sql = sql
                return self
            def fetchone(self):
                return (2,) if self.sql.lstrip().upper().startswith("UPDATE") else (1,)
        class Adapter:
            dialect = 'postgresql'
            @contextmanager
            def transaction(self, *, write=False):
                self.write = write
                yield Connection()
        repository = DomainRecordRepository(Adapter())
        self.assertEqual(repository.put('security', 'users', [], expected_revision=1), 2)
        sql, params = statements[0]
        normalized = ' '.join(sql.upper().split())
        self.assertTrue(normalized.startswith('UPDATE DOMAIN_RECORDS SET'))
        self.assertIn('REVISION=REVISION+1', normalized)
        self.assertIn('WHERE NAMESPACE=%S AND RECORD_ID=%S AND REVISION=%S RETURNING REVISION', normalized)
        self.assertEqual(params[-1], 1)
        statements.clear()
        repository.put_many([('security', 'users', []), ('security', 'organization-units', {})],
            expected_revisions={('security', 'users'): 1, ('security', 'organization-units'): 1})
        self.assertEqual(len(statements), 2)
        self.assertTrue(all('UPDATE domain_records' in sql for sql, _params in statements))
        self.assertEqual([params[-3:] for _sql, params in statements], [
            ('security', 'organization-units', 1), ('security', 'users', 1)])

    def test_backend_mutation_requires_canonical_permission(self):
        backend = _Backend()
        denied = CanonicalUnitService(backend, _Policy(False))
        with self.assertRaises(PermissionError):
            denied.create("blocked", "Blocked")
        allowed = CanonicalUnitService(backend, _Policy(True))
        allowed.create("allowed", "Allowed")

    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_crud_audit_and_delete_unassigns_users(self):
        backend = _Backend()
        service = CanonicalUnitService(backend)
        service.create("phase2e", "Phase 2E", actor="manager", authorized=True)
        service.update("phase2e", "Phase 2E renamed", actor="manager", authorized=True)
        service.set_archived("phase2e", True, actor="manager", authorized=True)
        service.set_archived("phase2e", False, actor="manager", authorized=True)
        backend.records.put("security", "users", [
            {"username": "tester", "unit_id": "phase2e", "unit": "Phase 2E renamed"},
        ])
        self.assertTrue(service.delete("phase2e", actor="manager", authorized=True))
        self.assertEqual(backend.records.get("security", "users")[0]["unit_id"], "")
        self.assertEqual(len(backend.audit.events), 5)

    def test_user_selector_reads_canonical_catalog_and_keeps_current_archived_unit(self):
        backend = _Backend()
        service = CanonicalUnitService(backend)
        service.create("active", "Active", actor="manager", authorized=True)
        service.create("old", "Old", actor="manager", authorized=True)
        service.set_archived("old", True, actor="manager", authorized=True)
        user_db = UserDB("unused", backend)
        combo = _organization_unit_combo(user_db, "old")
        self.assertEqual(combo.findData("active") >= 0, True)
        self.assertEqual(combo.findData("old") >= 0, True)
        self.assertEqual(combo.currentData(), "old")


if __name__ == "__main__":
    unittest.main()
