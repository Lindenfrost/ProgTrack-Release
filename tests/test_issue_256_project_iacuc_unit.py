from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QComboBox

from Plugins.Projects_Track.project_track_tab import ProjectTrackTab
from Plugins.core.authorization import CanonicalUnitService, OrganizationUnit
from Plugins.core.backend import ProgTrackBackend
from Plugins.core.runtime_paths import resolve_runtime_paths


ROOT = Path(__file__).resolve().parents[1]


class _Records:
    def __init__(self, values=None):
        self.values = copy.deepcopy(values or {})
        self.revisions = {key: 1 for key in self.values}
        self.put_counts = {}

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def get_with_revision(self, namespace, record_id, default=None):
        key = (namespace, record_id)
        return self.get(namespace, record_id, default), self.revisions.get(key, 0)

    def put(self, namespace, record_id, value, expected_revision=None):
        key = (namespace, record_id)
        current = self.revisions.get(key, 0)
        if expected_revision is not None and expected_revision != current:
            raise RuntimeError("stale record revision")
        self.values[key] = copy.deepcopy(value)
        self.revisions[key] = current + 1
        self.put_counts[key] = self.put_counts.get(key, 0) + 1
        return self.revisions[key]

    def put_many(self, records, *, expected_revisions):
        records = list(records)
        for namespace, record_id, _value in records:
            if self.revisions.get((namespace, record_id), 0) != expected_revisions[(namespace, record_id)]:
                raise RuntimeError("stale record revision")
        return {(ns, key): self.put(ns, key, value, expected_revision=expected_revisions[(ns, key)])
                for ns, key, value in records}


class _Documents:
    @staticmethod
    def list_for_owner(_owner_type, _owner_id):
        return []


class _Backend:
    def __init__(self, records):
        self.records = records
        self.documents = _Documents()


class _App:
    def __init__(self, backend, *, can_manage=True):
        self.backend = backend
        self.animals = {}
        self.archived_animals = {}
        self._can_manage = can_manage

    def _master_can(self, permission):
        return self._can_manage if permission == "project.manage" else True


class _History:
    @staticmethod
    def all_projects():
        return []

    @staticmethod
    def is_archived(_name):
        return False

    @staticmethod
    def get_animals(_name):
        return []


def _messages(language="en"):
    return json.loads(
        (ROOT / "lang" / f"messages_{language}.json").read_text(encoding="utf-8")
    )


def _unit(unit_id, display_name, *, active=True, archived=False):
    return OrganizationUnit(
        unit_id=unit_id,
        display_name=display_name,
        active=active,
        archived=archived,
    )


class Issue256ProjectIacucUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def make_tab(self, units=(), saved_unit="", *, can_manage=True):
        project_record = {
            "status": "active",
            "summary": {"species": ""},
            "iacuc": {"unit": saved_unit},
            "assoc_users": {},
            "animals_config": {},
            "arrive": {},
        }
        records = _Records({
            ("projects", "catalog"): {
                "version": 1,
                "projects": {"Demo": project_record},
            },
            ("security", "organization-units"): {
                "schema_version": 1,
                "units": [unit.as_record() for unit in units],
            },
        })
        backend = _Backend(records)
        app = _App(backend, can_manage=can_manage)
        tab = ProjectTrackTab(app, _messages(), _History())
        self.addCleanup(tab.close)
        self.addCleanup(tab.deleteLater)
        self.addCleanup(self.qt.processEvents)
        return tab, app, records

    def test_selector_uses_active_canonical_ids_and_disambiguates_duplicate_names(self):
        tab, _app, _records = self.make_tab(
            (
                _unit("unit-b", "Research"),
                _unit("unit-a", "Research"),
                _unit("unit-archived", "Old", archived=True),
                _unit("unit-inactive", "Inactive", active=False),
            ),
            saved_unit="unit-a",
        )

        combo = tab._iacuc_unit_combo
        self.assertIsInstance(tab._iacuc_fields["unit"], QComboBox)
        self.assertEqual(combo.itemData(0), "")
        self.assertEqual(combo.currentData(), "unit-a")
        self.assertEqual(combo.findData("unit-archived"), -1)
        self.assertEqual(combo.findData("unit-inactive"), -1)
        self.assertEqual(combo.itemText(combo.findData("unit-a")), "Research (unit-a)")
        self.assertEqual(combo.itemText(combo.findData("unit-b")), "Research (unit-b)")

    def test_empty_catalog_has_only_unassigned_and_preserves_unknown_current_value(self):
        tab, _app, _records = self.make_tab(saved_unit="old-free-text")
        combo = tab._iacuc_unit_combo
        self.assertEqual(combo.count(), 2)
        self.assertEqual(combo.itemData(0), "")
        self.assertEqual(combo.currentData(), "old-free-text")
        stale_index = combo.findData("old-free-text")
        self.assertGreaterEqual(stale_index, 0)
        self.assertFalse(combo.model().item(stale_index).isEnabled())

    def test_new_assignment_persists_stable_unit_id(self):
        tab, app, records = self.make_tab((_unit("org-tts", "TTS"),))
        tab._iacuc_unit_combo.setCurrentIndex(tab._iacuc_unit_combo.findData("org-tts"))

        tab._save_detail("Demo")

        saved = records.get("projects", "catalog")["projects"]["Demo"]["iacuc"]
        self.assertEqual(saved["unit"], "org-tts")
        self.assertEqual(records.put_counts.get(("projects", "catalog")), 1)
        reopened = ProjectTrackTab(app, _messages(), _History())
        self.addCleanup(reopened.close)
        self.addCleanup(reopened.deleteLater)
        self.assertEqual(reopened._iacuc_unit_combo.currentData(), "org-tts")

    def test_existing_stale_assignment_is_not_silently_erased_on_unrelated_save(self):
        tab, _app, records = self.make_tab(saved_unit="legacy project unit")
        tab._iacuc_fields["protocol_id"].setText("updated protocol")

        with patch("Plugins.Projects_Track.project_track_tab.QMessageBox.warning") as warning:
            tab._save_detail("Demo")

        saved = records.get("projects", "catalog")["projects"]["Demo"]["iacuc"]
        self.assertEqual(saved["unit"], "legacy project unit")
        self.assertEqual(saved["protocol_id"], "updated protocol")
        warning.assert_not_called()

    def test_backend_switch_refreshes_from_new_catalog_even_at_same_revision(self):
        tab, app, _records = self.make_tab((_unit("old-backend", "Old Unit"),))
        other_records = _Records({
            ("security", "organization-units"): {
                "schema_version": 1,
                "units": [_unit("new-backend", "New Unit").as_record()],
            },
        })
        app.backend = _Backend(other_records)

        tab._refresh_iacuc_unit_catalog()

        self.assertEqual(tab._iacuc_unit_combo.findData("old-backend"), -1)
        self.assertGreaterEqual(tab._iacuc_unit_combo.findData("new-backend"), 0)

    def test_display_name_refresh_keeps_canonical_id(self):
        tab, app, _records = self.make_tab((_unit("unit-a", "Old name"),), saved_unit="unit-a")
        service = CanonicalUnitService(app.backend)
        service.update("unit-a", "New name", authorized=True)

        tab._refresh_iacuc_unit_catalog()

        self.assertEqual(tab._iacuc_unit_combo.currentData(), "unit-a")
        self.assertEqual(tab._iacuc_unit_combo.currentText(), "New name")

    def test_catalogue_change_rejects_stale_new_selection_before_any_project_save(self):
        tab, app, records = self.make_tab((_unit("unit-a", "Research"),))
        tab._iacuc_unit_combo.setCurrentIndex(tab._iacuc_unit_combo.findData("unit-a"))
        service = CanonicalUnitService(app.backend)
        service.set_archived("unit-a", True, authorized=True)
        before = records.get("projects", "catalog")

        with patch("Plugins.Projects_Track.project_track_tab.QMessageBox.warning") as warning:
            tab._save_detail("Demo")

        self.assertEqual(records.get("projects", "catalog"), before)
        self.assertEqual(records.put_counts.get(("projects", "catalog"), 0), 0)
        self.assertEqual(tab._iacuc_unit_combo.currentData(), "unit-a")
        stale_index = tab._iacuc_unit_combo.findData("unit-a")
        self.assertFalse(tab._iacuc_unit_combo.model().item(stale_index).isEnabled())
        warning.assert_called_once()

    def test_catalogue_revision_refreshes_choices_when_selector_is_refreshed(self):
        tab, app, _records = self.make_tab((_unit("unit-a", "Research"),))
        service = CanonicalUnitService(app.backend)
        service.create("unit-b", "New Unit", authorized=True)

        tab._iacuc_unit_combo.showPopup()
        tab._iacuc_unit_combo.hidePopup()
        self.qt.processEvents()

        self.assertGreaterEqual(tab._iacuc_unit_combo.findData("unit-b"), 0)
        self.assertEqual(tab._iacuc_unit_combo.currentData(), "")

    def test_existing_read_only_permission_gate_applies_to_new_selector(self):
        tab, _app, _records = self.make_tab(
            (_unit("unit-a", "Research"),), can_manage=False
        )
        self.assertFalse(tab._iacuc_unit_combo.isEnabled())

    def test_new_messages_are_present_in_all_supported_locales(self):
        required = {
            "project.iacuc.unit.unassigned",
            "project.iacuc.unit.unavailable",
            "project.iacuc.unit.invalid",
            "project.iacuc.unit.archived",
            "project.iacuc.unit.inactive",
        }
        for language in ("en", "de", "it", "ru"):
            with self.subTest(language=language):
                messages = _messages(language)
                self.assertTrue(required.issubset(messages))

    def test_known_archived_inactive_current_labels_use_names_and_localized_status(self):
        statuses = {'en': ('Archived', 'Inactive'), 'de': ('Archiviert', 'Inaktiv'),
                    'it': ('Archiviata', 'Inattiva'), 'ru': ('Архивировано', 'Неактивно')}
        for language, labels in statuses.items():
            for archived, status in ((True, labels[0]), (False, labels[1])):
                with self.subTest(language=language, archived=archived):
                    tab, _app, records = self.make_tab(
                        (_unit('test_unit_256', 'Test-Unit 256 Renamed', active=False, archived=archived),),
                        saved_unit='test_unit_256')
                    before = records.get('projects', 'catalog')
                    tab.update_language(_messages(language))
                    combo = tab._iacuc_unit_combo
                    self.assertEqual(combo.currentText(), f'Test-Unit 256 Renamed ({status})')
                    self.assertEqual(combo.currentData(), 'test_unit_256')
                    self.assertFalse(combo.model().item(combo.currentIndex()).isEnabled())
                    self.assertFalse(tab._iacuc_unit_selection_dirty)
                    self.assertEqual(records.get('projects', 'catalog'), before)
                    self.assertEqual(records.put_counts.get(('projects', 'catalog'), 0), 0)

    def test_duplicate_name_disambiguation_includes_retained_unassignable_current(self):
        tab, _app, _records = self.make_tab((
            _unit('test_unit_256', 'Research', active=False, archived=True),
            _unit('active_unit_256', 'Research'),
            _unit('other_archived_256', 'Research', active=False, archived=True),
        ), saved_unit='test_unit_256')
        combo = tab._iacuc_unit_combo
        self.assertEqual(combo.currentText(), 'Research (test_unit_256) (Archived)')
        active = combo.findData('active_unit_256')
        self.assertEqual(combo.itemText(active), 'Research (active_unit_256)')
        self.assertTrue(combo.model().item(active).isEnabled())
        self.assertEqual(combo.findData('other_archived_256'), -1)

    def test_archive_after_rename_reopen_unrelated_save_and_restore_preserve_id(self):
        tab, app, records = self.make_tab((_unit('test_unit_256', 'Original'),), saved_unit='test_unit_256')
        service = CanonicalUnitService(app.backend)
        service.update('test_unit_256', 'Test-Unit 256 Renamed', authorized=True)
        service.set_archived('test_unit_256', True, authorized=True)
        before = records.get('projects', 'catalog')
        tab._refresh_iacuc_unit_catalog()
        self.assertEqual(tab._iacuc_unit_combo.currentText(), 'Test-Unit 256 Renamed (Archived)')
        self.assertFalse(tab._iacuc_unit_selection_dirty)
        reopened = ProjectTrackTab(app, _messages(), _History())
        self.addCleanup(reopened.close)
        self.addCleanup(reopened.deleteLater)
        self.assertEqual(reopened._iacuc_unit_combo.currentText(), 'Test-Unit 256 Renamed (Archived)')
        self.assertEqual(records.get('projects', 'catalog'), before)
        self.assertEqual(records.put_counts.get(('projects', 'catalog'), 0), 0)
        reopened._iacuc_fields['protocol_id'].setText('test-256')
        reopened._save_detail('Demo')
        saved = records.get('projects', 'catalog')['projects']['Demo']['iacuc']
        self.assertEqual(saved['unit'], 'test_unit_256')
        self.assertEqual(saved['protocol_id'], 'test-256')
        service.set_archived('test_unit_256', False, authorized=True)
        reopened._refresh_iacuc_unit_catalog()
        combo = reopened._iacuc_unit_combo
        self.assertEqual(combo.currentText(), 'Test-Unit 256 Renamed')
        self.assertEqual(combo.currentData(), 'test_unit_256')
        self.assertTrue(combo.model().item(combo.currentIndex()).isEnabled())

    def test_deleted_current_uses_unavailable_id_not_a_fabricated_name(self):
        tab, app, records = self.make_tab((_unit('test_unit_256', 'Original'),), saved_unit='test_unit_256')
        CanonicalUnitService(app.backend).delete('test_unit_256', authorized=True)
        before = records.get('projects', 'catalog')
        tab._refresh_iacuc_unit_catalog()
        combo = tab._iacuc_unit_combo
        self.assertEqual(combo.currentText(), 'Unavailable: test_unit_256')
        self.assertEqual(combo.currentData(), 'test_unit_256')
        self.assertFalse(combo.model().item(combo.currentIndex()).isEnabled())
        self.assertFalse(tab._iacuc_unit_selection_dirty)
        self.assertEqual(records.get('projects', 'catalog'), before)

    def test_refresh_only_disambiguates_visible_rows_and_preserves_signal_state(self):
        tab, _app, records = self.make_tab((
            _unit('test_unit_256', 'Research'),
            _unit('hidden_old_256', 'Research', archived=True),
        ), saved_unit='test_unit_256')
        self.assertEqual(tab._iacuc_unit_combo.currentText(), 'Research')
        combo = tab._iacuc_unit_combo
        seen = []
        combo.currentIndexChanged.connect(seen.append)
        units = CanonicalUnitService(tab._app.backend).load()
        for blocked in (True, False):
            with self.subTest(blocked=blocked):
                combo.blockSignals(blocked)
                tab._populate_iacuc_unit_combo(combo, 'test_unit_256', units)
                self.assertEqual(combo.signalsBlocked(), blocked)
                self.assertEqual(combo.currentData(), 'test_unit_256')
        self.assertEqual(seen, [])
        self.assertFalse(tab._iacuc_unit_selection_dirty)
        self.assertEqual(records.put_counts.get(('projects', 'catalog'), 0), 0)

    def test_keyboard_navigation_cannot_reselect_retained_archived_assignment(self):
        tab, _app, records = self.make_tab((
            _unit('test_unit_256', 'Original', archived=True, active=False),
            _unit('active_unit_256', 'Active'),
        ), saved_unit='test_unit_256')
        combo = tab._iacuc_unit_combo
        self.assertEqual(combo.currentData(), 'test_unit_256')
        QTest.keyClick(combo, Qt.Key.Key_Down)
        self.assertEqual(combo.currentData(), 'active_unit_256')
        QTest.keyClick(combo, Qt.Key.Key_Up)
        self.assertEqual(combo.currentData(), '')
        QTest.keyClick(combo, Qt.Key.Key_Down)
        self.assertEqual(combo.currentData(), 'active_unit_256')
        self.assertEqual(records.put_counts.get(('projects', 'catalog'), 0), 0)

    def test_real_sqlite_reopen_keeps_archived_name_without_project_write(self):
        _template, _app, records = self.make_tab(
            (_unit('test_unit_256', 'Original'),), saved_unit='test_unit_256')
        with tempfile.TemporaryDirectory() as directory:
            paths = resolve_runtime_paths(Path(directory), environ={'PROGTRACK_PORTABLE': '1'})
            backend = ProgTrackBackend(paths, acquire_process_lock=False, bootstrap_seed=False)
            self.addCleanup(backend.close)
            backend.records.put('projects', 'catalog', records.get('projects', 'catalog'))
            backend.records.put('security', 'organization-units', records.get('security', 'organization-units'))
            before = backend.records.get_with_revision('projects', 'catalog')
            service = CanonicalUnitService(backend)
            service.update('test_unit_256', 'Test-Unit 256 Renamed', authorized=True)
            service.set_archived('test_unit_256', True, authorized=True)
            backend.close()
            reopened = ProgTrackBackend(paths, acquire_process_lock=False, bootstrap_seed=False)
            try:
                tab = ProjectTrackTab(_App(reopened), _messages(), _History())
                try:
                    self.assertEqual(tab._iacuc_unit_combo.currentText(), 'Test-Unit 256 Renamed (Archived)')
                    self.assertEqual(tab._iacuc_unit_combo.currentData(), 'test_unit_256')
                    self.assertFalse(tab._iacuc_unit_selection_dirty)
                    self.assertEqual(reopened.records.get_with_revision('projects', 'catalog'), before)
                finally:
                    tab.close()
                    tab.deleteLater()
                    self.qt.processEvents()
            finally:
                reopened.close()


if __name__ == "__main__":
    unittest.main()
