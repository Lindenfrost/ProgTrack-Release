from __future__ import annotations

import copy
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QPoint
from PyQt6.QtWidgets import QApplication, QMessageBox

from Plugins.core.authorization import CanonicalUnitService
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


class _Audit:
    def __init__(self):
        self.entries = []

    def append(self, **entry):
        self.entries.append(entry)


class Issue250InstitutionUnitsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def _dialog(self):
        backend = SimpleNamespace(records=_Records(), audit=_Audit())
        units = CanonicalUnitService(backend)
        return backend, units, InstitutionBrandingDialog(
            InstitutionBrandingService(backend),
            "phase2e-manager",
            authorized=True,
            units_service=units,
            units_authorized=True,
        )

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
            patch.object(units, "load", side_effect=OSError("backend unavailable")),
            patch("Plugins.core.institution_branding.QMessageBox.warning") as warning,
        ):
            self.assertFalse(dialog.units_editor.refresh())

        self.assertEqual(dialog.units_editor.table.rowCount(), 1)
        warning.assert_called_once()
        dialog.close()


if __name__ == "__main__":
    unittest.main()
