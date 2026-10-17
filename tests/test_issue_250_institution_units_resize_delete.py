from __future__ import annotations

import copy
import importlib.util
import os
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from Plugins.core.authorization import CanonicalUnitService
from Plugins.core.authorization import AuthorizationService
from Plugins.core.backend import ProgTrackBackend, ConflictError
from Plugins.core.runtime_paths import resolve_runtime_paths
from Plugins.Master_Track.auth import UserDB
from Plugins.core.institution_branding import (
    InstitutionBrandingDialog,
    InstitutionBrandingService,
)


class _Records:
    def __init__(self):
        self.values = {}
        self.revisions = {}

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def get_with_revision(self, namespace, record_id, default=None):
        key = (namespace, record_id)
        return self.get(namespace, record_id, default), self.revisions.get(key, 0)

    def put(self, namespace, record_id, payload, expected_revision=None):
        key = (namespace, record_id)
        current = self.revisions.get(key, 0)
        if expected_revision is not None and expected_revision != current:
            raise RuntimeError("stale record revision")
        self.values[key] = copy.deepcopy(payload)
        self.revisions[key] = current + 1
        return current + 1


    def put_many(self, records, expected_revisions=None):
        records = list(records)
        expected_revisions = expected_revisions or {}
        for ns, key, _value in records:
            if expected_revisions.get((ns, key)) != self.revisions.get((ns, key), 0):
                raise RuntimeError("stale record revision")
        return {(ns, key): self.put(ns, key, value, expected_revision=expected_revisions.get((ns, key)))
                for ns, key, value in records}

    def put_many_audited(self, records, *, expected_revisions, audit_events):
        old_values, old_revisions = copy.deepcopy(self.values), dict(self.revisions)
        old_events = copy.deepcopy(self.audit.entries)
        try:
            result = self.put_many(records, expected_revisions)
            for event in audit_events:
                self.audit.append(**event)
            return result
        except Exception:
            self.values, self.revisions, self.audit.entries = old_values, old_revisions, old_events
            raise


class _Audit:
    def __init__(self):
        self.entries = []

    def append(self, **entry):
        self.entries.append(entry)


class Issue250InstitutionUnitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        # Load the real application adapter without depending on locally
        # ignored test modules. Mocking question() hid the original failure.
        source = Path(__file__).resolve().parents[1] / 'ProgTrack.v.0.2.3.py'
        spec = importlib.util.spec_from_file_location('progtrack_issue250', source)
        cls.main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.main)

    def _dialog(self):
        backend = SimpleNamespace(records=_Records(), audit=_Audit())
        backend.records.audit = backend.audit
        units = CanonicalUnitService(backend)
        return backend, units, InstitutionBrandingDialog(
            InstitutionBrandingService(backend),
            "phase2e-manager",
            authorized=True,
            units_service=units,
            units_authorized=True,
        )

    def test_245_main_institution_entrypoint_uses_live_policy_not_role_strings(self):
        policy = AuthorizationService(trusted_local=True)
        host = SimpleNamespace(authorization=policy)
        self.assertTrue(self.main.ProgTrackApp._can_manage_institution_settings(host))
        host.authorization = SimpleNamespace(can=lambda *_args, **_kwargs: False)
        host.master_track = SimpleNamespace(is_logged_in=True, current_role='lord',
            current_username='Admin', get_assigned_jobs=lambda: ['manager'], can=lambda _action: True)
        self.assertFalse(self.main.ProgTrackApp._can_manage_institution_settings(host))
        host.authorization = None
        del host.master_track.can
        self.assertFalse(self.main.ProgTrackApp._can_manage_institution_settings(host))
        host.authorization = policy
        host.backend = SimpleNamespace(branding=object())
        host.messages = {}
        host._can_manage_institution_settings = lambda: True
        with patch('Plugins.core.institution_branding.InstitutionBrandingDialog') as dialog:
            self.main.ProgTrackApp._show_institution_branding(host)
        self.assertIs(dialog.call_args.kwargs['units_service'].authorization, policy)
        self.assertIs(dialog.call_args.kwargs['authorization'], policy)

    def _click_real_confirmation(self, trigger, reply, *, expected_default=None):
        seen, errors = [], []
        click = QTimer()
        click.setSingleShot(True)
        timeout = QTimer()
        timeout.setSingleShot(True)
        def drive():
            box = self.qt_app.activeModalWidget()
            if not isinstance(box, QMessageBox):
                errors.append('No real modal QMessageBox')
                return
            seen.append((box.standardButtons(), box.defaultButton()))
            button = box.button(reply) if reply not in ('escape', 'close', 'default') else None
            if reply == 'escape':
                QTest.keyClick(box, Qt.Key.Key_Escape)
            elif reply == 'close':
                box.close()
            elif reply == 'default':
                QTest.keyClick(box, Qt.Key.Key_Return)
            elif button is None:
                errors.append(f'Requested standard button {reply} absent; actual={box.standardButtons()}')
                box.reject()
            else:
                if expected_default is not None and box.defaultButton() is not box.button(expected_default):
                    errors.append('Wrong default button')
                QTest.mouseClick(button, Qt.MouseButton.LeftButton)
        def abort():
            errors.append('Confirmation timed out')
            box = self.qt_app.activeModalWidget()
            if isinstance(box, QMessageBox):
                box.reject()
        click.timeout.connect(drive)
        timeout.timeout.connect(abort)
        click.start(0)
        timeout.start(3000)
        try:
            result = trigger()
        finally:
            click.stop()
            timeout.stop()
        self.assertEqual(errors, [])
        self.assertEqual(len(seen), 1)
        return result, seen[0][0]

    def test_real_qt_yes_no_escape_delete_sqlite_users_audit_and_reopen(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = resolve_runtime_paths(root, environ={'PROGTRACK_PORTABLE': '1'})
            backend = ProgTrackBackend(paths,
                                       acquire_process_lock=False, bootstrap_seed=False)
            self.addCleanup(backend.close)
            units = CanonicalUnitService(backend)
            units.create('test_unit_250', 'Test-Unit 250', actor='manager', authorized=True)
            units.create('keep_unit', 'Keep', actor='manager', authorized=True)
            backend.records.put('security', 'users', [
                {'username': 'test_unit_user', 'role': 'user', 'jobs': [],
                 'unit_id': 'test_unit_250', 'unit': 'Test-Unit 250'},
                {'username': 'other_user', 'role': 'user', 'jobs': [],
                 'unit_id': 'keep_unit', 'unit': 'Keep'},
            ])
            user_db = UserDB('unused', backend)
            user_db.load()
            dialog = InstitutionBrandingDialog(InstitutionBrandingService(backend), 'manager',
                authorized=True, units_service=units, units_authorized=True, user_db=user_db)
            self.addCleanup(dialog.close)
            dialog.show()
            editor = dialog.units_editor
            editor.toggle.setChecked(True)
            row = next(i for i in range(editor.table.rowCount())
                       if editor.table.item(i, 0).text() == 'test_unit_250')
            editor.table.selectRow(row)
            self.qt_app.processEvents()
            snapshot = backend.records.get('security', 'users')
            audit_before = backend.audit.list_events()
            for reply in (QMessageBox.StandardButton.No, 'escape', 'close', 'default'):
                self._click_real_confirmation(editor.delete_button.click, reply,
                                              expected_default=QMessageBox.StandardButton.No)
                self.assertIn('test_unit_250', units.load())
                self.assertEqual(backend.records.get('security', 'users'), snapshot)
                self.assertEqual(backend.audit.list_events(), audit_before)
                self.assertEqual(editor._selected_id, 'test_unit_250')
            self._click_real_confirmation(editor.delete_button.click, QMessageBox.StandardButton.Yes,
                                          expected_default=QMessageBox.StandardButton.No)
            self.assertNotIn('test_unit_250', units.load())
            self.assertEqual([editor.table.item(i, 0).text() for i in range(editor.table.rowCount())], ['keep_unit'])
            self.assertEqual(editor._selected_id, '')
            self.assertEqual(editor.id_edit.text(), '')
            self.assertEqual(user_db.get_user('test_unit_user')['unit_id'], '')
            self.assertEqual(user_db.get_user('test_unit_user')['unit'], '')
            self.assertEqual(user_db.get_user('other_user')['unit_id'], 'keep_unit')
            events = [e for e in backend.audit.list_events() if e['action'] == 'organization_unit_delete']
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]['actor_login'], 'manager')
            self.assertEqual(events[0]['entity_id'], 'test_unit_250')
            self.assertTrue(events[0]['event_id'])
            self.assertEqual(events[0]['correlation_id'], events[0]['event_id'])
            self.assertTrue(events[0]['payload']['users_unassigned'])
            self.assertNotIn('password', json.dumps(events[0]['payload']).casefold())
            dialog.close()
            backend.close()
            reopened = ProgTrackBackend(paths,
                                        acquire_process_lock=False, bootstrap_seed=False)
            self.addCleanup(reopened.close)
            self.assertNotIn('test_unit_250', CanonicalUnitService(reopened).load())
            self.assertEqual(reopened.records.get('security', 'users')[0]['unit_id'], '')

    def test_shared_message_adapter_preserves_qt_defaults_and_default_button_argument(self):
        result, buttons = self._click_real_confirmation(
            lambda: QMessageBox.question(None, 'Test', 'Question'), QMessageBox.StandardButton.Yes)
        self.assertEqual(buttons, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        self.assertEqual(result, QMessageBox.StandardButton.Yes)
        for method in ('warning', 'information', 'critical'):
            with self.subTest(method=method):
                result, buttons = self._click_real_confirmation(
                    lambda: getattr(QMessageBox, method)(None, 'Test', 'Message'), QMessageBox.StandardButton.Ok)
                self.assertEqual(buttons, QMessageBox.StandardButton.Ok)
                self.assertEqual(result, QMessageBox.StandardButton.Ok)
        result, buttons = self._click_real_confirmation(
            lambda: QMessageBox.question(None, 'Test', 'Question',
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No),
            'escape', expected_default=QMessageBox.StandardButton.No)
        self.assertEqual(result, QMessageBox.StandardButton.No)

    def test_expanded_units_table_gets_resize_space_but_preview_stays_bounded(self):
        _backend, units, dialog = self._dialog()
        editor = dialog.units_editor
        self.assertIsNotNone(editor)
        for index in range(20):
            units.create(
                f"phase2e_unit_{index:02d}",
                f"Phase 2E Unit {index:02d}",
                actor="manager",
                authorized=True,
            )
        editor.refresh()
        dialog.resize(700, 560)
        dialog.show()
        editor.toggle.setChecked(True)
        self.qt_app.processEvents()
        compact_table_height = editor.table.height()
        self.assertGreater(editor.table.verticalScrollBar().maximum(), 0)
        editor.table.verticalScrollBar().setValue(editor.table.verticalScrollBar().maximum())
        self.qt_app.processEvents()
        self.assertGreater(editor.table.verticalScrollBar().value(), 0)
        editor.table.verticalScrollBar().setValue(0)
        preview_height = dialog.preview.height()

        dialog.resize(700, 760)
        self.qt_app.processEvents()
        tall_table_height = editor.table.height()
        self.assertGreater(tall_table_height, compact_table_height)
        self.assertEqual(dialog.preview.height(), preview_height)
        self.assertEqual(preview_height, dialog.preview.sizeHint().height())
        for widget in (
            editor.toggle,
            editor.table,
            editor.id_edit,
            editor.name_edit,
            editor.new_button,
            editor.save_button,
            editor.archive_button,
            editor.delete_button,
        ):
            top_left = widget.mapTo(dialog, QPoint(0, 0))
            self.assertGreaterEqual(top_left.x(), 0)
            self.assertGreaterEqual(top_left.y(), 0)
            self.assertLessEqual(top_left.x() + widget.width(), dialog.width())
            self.assertLessEqual(top_left.y() + widget.height(), dialog.height())
        table_bottom = editor.table.mapTo(dialog, QPoint(0, editor.table.height())).y()
        form_top = editor.id_edit.mapTo(dialog, QPoint(0, 0)).y()
        self.assertLessEqual(table_bottom, form_top)

        dialog.resize(700, 360)
        self.qt_app.processEvents()
        self.assertGreaterEqual(dialog.height(), dialog.minimumHeight())
        self.assertLess(editor.table.height(), tall_table_height)
        for widget in (
            editor.toggle,
            editor.table,
            editor.id_edit,
            editor.name_edit,
            editor.new_button,
            editor.save_button,
            editor.archive_button,
            editor.delete_button,
        ):
            top_left = widget.mapTo(dialog, QPoint(0, 0))
            self.assertGreaterEqual(top_left.y(), 0)
            self.assertLessEqual(top_left.y() + widget.height(), dialog.height())
        dialog.resize(700, 560)
        self.qt_app.processEvents()
        editor.toggle.setChecked(False)
        self.qt_app.processEvents()
        editor.toggle.setChecked(True)
        self.qt_app.processEvents()
        self.assertEqual(dialog.findChildren(type(editor.table)).count(editor.table), 1)
        self.assertEqual(dialog.preview.height(), preview_height)
        dialog.close()

    def test_expanding_grows_dialog_for_three_rows_and_collapse_restores_height(self):
        _backend, units, dialog = self._dialog()
        for index in range(3):
            units.create(
                f"phase2e_resize_{index:02d}",
                f"Phase 2E Resize Unit {index:02d}",
                actor="manager",
                authorized=True,
            )
        editor = dialog.units_editor
        self.assertTrue(editor.refresh())

        dialog.resize(700, 360)
        dialog.show()
        self.qt_app.processEvents()
        collapsed_height = dialog.height()
        collapsed_minimum_height = dialog.minimumHeight()

        editor.toggle.setChecked(True)
        self.qt_app.processEvents()
        expanded_height = dialog.height()
        self.assertGreater(expanded_height, collapsed_height)
        self.assertGreaterEqual(dialog.minimumHeight(), expanded_height)
        required_table_height = (
            editor.table.horizontalHeader().height()
            + sum(editor.table.rowHeight(row) for row in range(3))
            + 2 * editor.table.frameWidth()
        )
        self.assertGreaterEqual(editor.table.height(), required_table_height)
        for widget in (
            editor.table,
            editor.id_edit,
            editor.name_edit,
            editor.new_button,
            editor.save_button,
            editor.archive_button,
            editor.delete_button,
        ):
            self.assertTrue(widget.isVisibleTo(dialog))
            top_left = widget.mapTo(dialog, QPoint(0, 0))
            self.assertGreaterEqual(top_left.y(), 0)
            self.assertLessEqual(top_left.y() + widget.height(), dialog.height())

        editor.toggle.setChecked(False)
        self.qt_app.processEvents()
        self.assertEqual(dialog.height(), collapsed_height)
        self.assertEqual(dialog.minimumHeight(), collapsed_minimum_height)
        editor.toggle.setChecked(True)
        self.qt_app.processEvents()
        self.assertEqual(dialog.height(), expanded_height)
        editor.toggle.setChecked(False)
        self.qt_app.processEvents()
        self.assertEqual(dialog.height(), collapsed_height)
        self.assertEqual(dialog.minimumHeight(), collapsed_minimum_height)
        dialog.close()

    def test_confirmed_delete_removes_row_and_survives_fresh_catalogue_load(self):
        backend, units, dialog = self._dialog()
        units.create("phase2e_unit_250", "Phase 2E Unit 250", actor="manager", authorized=True)
        self.assertTrue(dialog.units_editor.refresh())
        dialog.units_editor.table.selectRow(0)
        self.assertEqual(dialog.units_editor._selected_id, "phase2e_unit_250")

        with patch(
            "Plugins.core.institution_branding.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            dialog.units_editor._delete()

        self.assertEqual(dialog.units_editor.table.rowCount(), 0)
        self.assertEqual(units.load(), {})
        self.assertEqual(CanonicalUnitService(backend).load(), {})
        dialog.close()

    def test_cancelled_or_noop_delete_does_not_remove_the_unit_or_claim_success(self):
        _backend, units, dialog = self._dialog()
        units.create("phase2e_unit_250", "Phase 2E Unit 250", actor="manager", authorized=True)
        dialog.units_editor.refresh()
        dialog.units_editor.table.selectRow(0)

        with patch(
            "Plugins.core.institution_branding.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            dialog.units_editor._delete()
        self.assertEqual(dialog.units_editor.table.rowCount(), 1)
        self.assertIn("phase2e_unit_250", units.load())

        with (
            patch(
                "Plugins.core.institution_branding.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch.object(units, "delete", return_value=False),
            patch("Plugins.core.institution_branding.QMessageBox.warning") as warning,
        ):
            dialog.units_editor._delete()
        self.assertEqual(dialog.units_editor.table.rowCount(), 1)
        self.assertIn("phase2e_unit_250", units.load())
        warning.assert_called_once()
        dialog.close()

    def test_failed_refresh_preserves_displayed_rows_and_reports_failure(self):
        _backend, units, dialog = self._dialog()
        units.create("phase2e_unit_250", "Phase 2E Unit 250", actor="manager", authorized=True)
        dialog.units_editor.refresh()
        self.assertEqual(dialog.units_editor.table.rowCount(), 1)

        with (
            patch.object(units, "load_with_revision", side_effect=OSError("backend unavailable")),
            patch("Plugins.core.institution_branding.QMessageBox.warning") as warning,
        ):
            self.assertFalse(dialog.units_editor.refresh())

        self.assertEqual(dialog.units_editor.table.rowCount(), 1)
        warning.assert_called_once()
        dialog.close()

    def test_successful_delete_does_not_resurrect_from_stale_refresh(self):
        backend, units, dialog = self._dialog()
        self.addCleanup(dialog.close)
        units.create('test_unit_250', 'Test-Unit 250', actor='manager', authorized=True)
        editor = dialog.units_editor
        editor.refresh()
        editor.table.selectRow(0)
        stale = units.load()
        original_refresh = editor.refresh
        def stale_refresh(**kwargs):
            with patch.object(units, 'load_with_revision', return_value=(stale, 999)):
                return original_refresh(**kwargs)
        with (patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes),
              patch.object(QMessageBox, 'warning') as warning,
              patch.object(editor, 'refresh', side_effect=stale_refresh)):
            editor._delete()
        self.assertEqual(CanonicalUnitService(backend).load(), {})
        self.assertEqual(editor.table.rowCount(), 0)
        self.assertEqual(editor._selected_id, '')
        warning.assert_called_once()
        self.assertIn('test_unit_250', warning.call_args.args[2])
        self.assertTrue(editor.refresh())
        self.assertEqual(editor.table.rowCount(), 0)

    def test_refresh_updates_selected_fields_and_clears_vanished_selection(self):
        _backend, units, dialog = self._dialog()
        self.addCleanup(dialog.close)
        units.create('test_unit_250', 'Original', actor='manager', authorized=True)
        editor = dialog.units_editor
        editor.refresh()
        editor.table.selectRow(0)
        units.update('test_unit_250', 'Renamed', actor='manager', authorized=True)
        units.set_archived('test_unit_250', True, actor='manager', authorized=True)
        with patch.object(units, 'get', side_effect=AssertionError('refresh must use one snapshot')):
            self.assertTrue(editor.refresh())
        self.assertEqual(editor.name_edit.text(), 'Renamed')
        self.assertEqual(editor.archive_button.text(), 'Restore')
        units.delete('test_unit_250', actor='manager', authorized=True)
        self.assertTrue(editor.refresh())
        self.assertEqual(editor._selected_id, '')
        self.assertEqual(editor.id_edit.text(), '')
        self.assertEqual(editor.name_edit.text(), '')
        self.assertFalse(editor.table.selectionModel().selectedRows())

    def test_service_errors_preserve_catalogue_selection_and_assignments(self):
        for error in (PermissionError('denied'), OSError('unavailable'), ConflictError('stale revision')):
            with self.subTest(error=type(error).__name__):
                backend, units, dialog = self._dialog()
                self.addCleanup(dialog.close)
                units.create('test_unit_250', 'Original', actor='manager', authorized=True)
                backend.records.put('security', 'users', [{'username': 'tester', 'unit_id': 'test_unit_250'}])
                editor = dialog.units_editor
                editor.refresh()
                editor.table.selectRow(0)
                snapshot = copy.deepcopy(backend.records.values)
                audit = copy.deepcopy(backend.audit.entries)
                with (patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes),
                      patch.object(units, 'delete', side_effect=error),
                      patch.object(QMessageBox, 'warning') as warning):
                    editor._delete()
                self.assertEqual(backend.records.values, snapshot)
                self.assertEqual(backend.audit.entries, audit)
                self.assertEqual(editor.table.rowCount(), 1)
                self.assertEqual(editor._selected_id, 'test_unit_250')
                self.assertEqual(editor.name_edit.text(), 'Original')
                warning.assert_called_once()

    def test_post_commit_refresh_errors_preserve_confirmed_deletion_and_report_user_error(self):
        for catalogue_fails in (False, True):
            with self.subTest(catalogue_fails=catalogue_fails):
                backend, units, dialog = self._dialog()
                self.addCleanup(dialog.close)
                units.create('test_unit_250', 'Original', actor='manager', authorized=True)
                editor = dialog.units_editor
                editor.refresh()
                editor.table.selectRow(0)
                editor.user_db = SimpleNamespace(load=lambda: (_ for _ in ()).throw(OSError('user reload failed')))
                original_refresh = editor.refresh
                def refresh_after_commit(**kwargs):
                    if catalogue_fails:
                        with patch.object(units, 'load_with_revision', side_effect=OSError('catalogue unavailable')):
                            return original_refresh(**kwargs)
                    return original_refresh(**kwargs)
                with (patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes),
                      patch.object(editor, 'refresh', side_effect=refresh_after_commit),
                      patch.object(QMessageBox, 'warning') as warning):
                    editor._delete()
                self.assertEqual(CanonicalUnitService(backend).load(), {})
                self.assertEqual(editor.table.rowCount(), 0)
                self.assertEqual(editor._selected_id, '')
                self.assertEqual(warning.call_count, 2 if catalogue_fails else 1)
                self.assertTrue(any('user reload failed' in c.args[2] for c in warning.call_args_list))

    def test_sqlite_revision_conflict_is_atomic_for_unit_and_user_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = resolve_runtime_paths(root, environ={'PROGTRACK_PORTABLE': '1'})
            backend = ProgTrackBackend(paths,
                                       acquire_process_lock=False, bootstrap_seed=False)
            self.addCleanup(backend.close)
            units = CanonicalUnitService(backend)
            units.create('test_unit_250', 'Original', actor='manager', authorized=True)
            users = [{'username': 'tester', 'unit_id': 'test_unit_250', 'unit': 'Original'}]
            backend.records.put('security', 'users', users)
            dialog = InstitutionBrandingDialog(InstitutionBrandingService(backend), 'manager',
                authorized=True, units_service=units, units_authorized=True)
            self.addCleanup(dialog.close)
            editor = dialog.units_editor
            editor.table.selectRow(0)
            original_put_many = backend.records.put_many
            def racing_write(records, **kwargs):
                backend.records.put('security', 'users', users + [{'username': 'concurrent', 'unit_id': ''}])
                return original_put_many(records, **kwargs)
            with (patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes),
                  patch.object(backend.records, 'put_many', side_effect=racing_write),
                  patch.object(QMessageBox, 'warning') as warning):
                editor._delete()
            self.assertIn('test_unit_250', units.load())
            saved_users = backend.records.get('security', 'users')
            self.assertEqual(saved_users[0]['unit_id'], 'test_unit_250')
            self.assertEqual(saved_users[1]['username'], 'concurrent')
            self.assertFalse(any(e['action'] == 'organization_unit_delete' for e in backend.audit.list_events()))
            self.assertEqual(editor.table.rowCount(), 1)
            warning.assert_called_once()

    def test_already_deleted_noop_clears_stale_editor_without_false_success(self):
        backend, units, dialog = self._dialog()
        self.addCleanup(dialog.close)
        units.create('test_unit_250', 'Original', actor='manager', authorized=True)
        editor = dialog.units_editor
        editor.refresh()
        editor.table.selectRow(0)
        units.delete('test_unit_250', actor='other-manager', authorized=True)
        audit = copy.deepcopy(backend.audit.entries)
        with (patch.object(QMessageBox, 'question', return_value=QMessageBox.StandardButton.Yes),
              patch.object(QMessageBox, 'warning') as warning):
            editor._delete()
        warning.assert_called_once()
        self.assertEqual(backend.audit.entries, audit)
        self.assertEqual(editor.table.rowCount(), 0)
        self.assertEqual(editor._selected_id, '')
        self.assertEqual(editor.id_edit.text(), '')


if __name__ == "__main__":
    unittest.main()
