"""Regression coverage for the PdG Unified Prog table's real scroll lifecycle."""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
import copy
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PyQt6.QtGui import QWheelEvent  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import (  # noqa: E402
    QApplication,
    QAbstractItemView,
    QDialog,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from Plugins.PdG_converter.plugin import PdGCapability  # noqa: E402


_SPEC = importlib.util.spec_from_file_location(
    "progtrack_issue_300_main", ROOT / "ProgTrack.v.0.3.0.py"
)
_MAIN = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_MAIN)


class UnifiedProgScrollRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def _settle(self):
        for _ in range(4):
            self.qt.processEvents()

    @contextmanager
    def _female_308(self, *, allowed=False, rows=25, computed=True, capability=True,
                    steroid=True, read_only=False, model=True, localized=False, actor=None):
        # Reuse the existing full Core editor host rather than testing a hand-
        # assembled permission map that could miss the actual female wiring.
        from tests.test_phase023_core_repair_regressions import CoreRepairTests
        harness = CoreRepairTests()
        harness.qt = self.qt
        harness.setUp()
        try:
            app = harness.host()
            app.has_pdg_plugin = capability
            app._is_steroid_track_active = lambda: steroid
            app._master_can = lambda key: allowed if key == 'core.edit_animal_research_data' else True
            if actor is not None:
                from Plugins.Master_Track.permissions import can
                role, jobs = actor
                app._master_can = lambda key: can(role, jobs, [], [], key)
            app._role_block_enabled = lambda _role, block, *_args: computed if block == 'computed_values' else True
            app.animals['Subject'] = harness.record('donor')
            app.animals['Subject']['pdg'] = [dict(datum=datetime(2025, 1, 1) + timedelta(days=i),
                wert=float(i + 1), probennummer=f'test-{i + 1}') for i in range(rows)]
            if localized:
                app.messages.update({'pdg_converter.tab.unified_prog': 'Berechnete Werte',
                                     'pdg_converter.button.refresh': 'Neu berechnen'})
            plugin = SimpleNamespace(app=app, converter=SimpleNamespace(predict=lambda values, _params: values),
                get_parameters=lambda _name: {'model_type': 'linear', 'n_pairs': 3, 'mse': 0.0} if model else None)
            app.pdg_cap = PdGCapability(plugin)
            app._save_persistence = Mock()
            before = copy.deepcopy(app.animals)
            captured = []
            with patch.object(QDialog, 'exec', lambda dialog: captured.append(dialog) or 0):
                app._dlg_female_animal('Subject', read_only=read_only)
            self.assertEqual(len(captured), 1)
            dialog = captured[0]
            harness.hosts.append(dialog)
            pages = app.pdg_cap._pdg_tabs.get('Subject', (None, None))
            raw, unified = pages
            tabs = unified.parentWidget() if unified is not None else None
            if unified is not None:
                tabs = next(t for t in dialog.findChildren(QTabWidget) if t.indexOf(unified) >= 0
                            or raw is not None and t.indexOf(raw) >= 0)
                if tabs.indexOf(unified) >= 0:
                    tabs.setCurrentWidget(unified)
            dialog.show()
            self._settle()
            yield app, dialog, raw, unified, tabs, before
        finally:
            harness.tearDown()

    def test_308_actual_female_permission_map_keeps_calculated_navigation_enabled(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed), self._female_308(allowed=allowed) as fixture:
                app, _dialog, raw, unified, _tabs, before = fixture
                table = unified.findChild(QTableWidget)
                for widget in (unified, table, table.viewport(), table.verticalScrollBar()):
                    self.assertTrue(widget.isEnabled())
                self.assertTrue(unified.findChild(QPushButton).isEnabled())
                inputs = raw.findChildren(QLineEdit) + raw.findChildren(QPushButton)
                self.assertTrue(inputs)
                self.assertTrue(all(w.isEnabled() == allowed for w in inputs))
                self.assertEqual(app.animals, before)
                app._save_persistence.assert_not_called()

    def test_308_calculated_cells_are_noneditable_for_all_actors_and_after_refresh(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed), self._female_308(allowed=allowed, localized=True) as fixture:
                app, _dialog, _raw, unified, _tabs, before = fixture
                table = unified.findChild(QTableWidget)
                self.assertEqual(table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
                for _attempt in range(2):
                    self.assertTrue(all(not table.item(row, col).flags() & Qt.ItemFlag.ItemIsEditable
                        for row in range(table.rowCount()) for col in range(table.columnCount())))
                    original = table.item(0, 0).text()
                    table.setCurrentCell(0, 0)
                    QTest.keyClick(table, Qt.Key.Key_F2)
                    QTest.keyClicks(table, 'not-editable')
                    self.assertEqual(table.item(0, 0).text(), original)
                    self.assertFalse(table.findChildren(QLineEdit))
                    unified.findChild(QPushButton).click()
                    self._settle()
                self.assertEqual(app.animals, before)
                app._save_persistence.assert_not_called()

    def test_308_denied_real_input_navigation_refresh_resize_and_no_writes(self):
        with self._female_308(localized=True) as fixture:
            app, dialog, _raw, unified, tabs, before = fixture
            table = unified.findChild(QTableWidget)
            bar = table.verticalScrollBar()
            self.assertEqual(table.rowCount(), 25)
            self.assertGreater(bar.maximum(), 0)
            initial_target = table.property('_progtrack_viewport_target')
            self.assertEqual(table.height(), initial_target)
            before_records = copy.deepcopy(app.backend.records.data)
            with patch.object(app.backend.records, 'put', side_effect=AssertionError('Viewing must not write')):
                table.setCurrentCell(0, 0)
                QTest.keyClick(table, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
                self._settle()
                self.assertEqual(table.currentRow(), 24)
                self.assertEqual(bar.value(), bar.maximum())
                QTest.keyClick(table, Qt.Key.Key_Home, Qt.KeyboardModifier.ControlModifier)
                self._settle()
                self.assertEqual(bar.value(), bar.minimum())
                QTest.keyClick(table, Qt.Key.Key_PageDown)
                self._settle()
                self.assertGreater(bar.value(), bar.minimum())
                bar.setValue(bar.minimum())
                point = table.viewport().rect().center()
                event = QWheelEvent(QPointF(point), QPointF(table.viewport().mapToGlobal(point)),
                    QPoint(), QPoint(0, -120), Qt.MouseButton.NoButton,
                    Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
                QApplication.sendEvent(table.viewport(), event)
                self._settle()
                self.assertGreater(bar.value(), bar.minimum())
                bar.setValue(bar.minimum())
                QTest.mouseClick(bar, Qt.MouseButton.LeftButton,
                    pos=QPoint(max(1, bar.width() // 2), max(1, bar.height() - 2)))
                self._settle()
                self.assertGreater(bar.value(), bar.minimum())
                tabs.setCurrentIndex(0)
                tabs.setCurrentWidget(unified)
                dialog.resize(dialog.width() + 40, dialog.height() + 40)
                unified.findChild(QPushButton).click()
                self._settle()
                self.assertTrue(unified.isEnabled())
                self.assertTrue(bar.isEnabled())
                self.assertGreater(bar.maximum(), 0)
                self.assertEqual(table.property('_progtrack_viewport_target'), initial_target)
                self.assertEqual(table.height(), initial_target)
            self.assertEqual(app.backend.records.data, before_records)
            self.assertEqual(app.animals, before)
            app._save_persistence.assert_not_called()

    def test_308_permission_separation_preserves_the_same_existing_viewport_metrics(self):
        metrics = []
        for allowed in (True, False):
            with self._female_308(allowed=allowed) as fixture:
                _app, _dialog, _raw, unified, _tabs, _before = fixture
                table = unified.findChild(QTableWidget)
                metrics.append((table.height(), table.viewport().height(),
                    table.verticalScrollBar().maximum(),
                    tuple(table.rowHeight(row) for row in range(table.rowCount()))))
        self.assertEqual(metrics[0], metrics[1])

    def test_308_refresh_empty_short_overflow_and_unfitted_values_remain_view_only(self):
        for allowed in (False, True):
            with self.subTest(allowed=allowed), self._female_308(allowed=allowed, model=False) as fixture:
                app, _dialog, _raw, unified, _tabs, before = fixture
                original = copy.deepcopy(app.animals['Subject']['pdg'])
                table = unified.findChild(QTableWidget)
                for count in (0, 3, 7, 25):
                    with self.subTest(rows=count):
                        app.animals['Subject']['pdg'] = copy.deepcopy(original[:count])
                        expected = copy.deepcopy(app.animals)
                        unified.findChild(QPushButton).click()
                        self._settle()
                        self.assertEqual(table.rowCount(), count)
                        self.assertEqual(table.verticalScrollBar().maximum() > 0, count > 5)
                        self.assertTrue(table.isEnabled())
                        self.assertEqual(table.editTriggers(), QAbstractItemView.EditTrigger.NoEditTriggers)
                        for row in range(count):
                            self.assertEqual(table.item(row, 2).text(), '-')
                            self.assertTrue(all(not table.item(row, col).flags() & Qt.ItemFlag.ItemIsEditable
                                                for col in range(3)))
                        self.assertEqual(app.animals, expected)
                self.assertEqual(app.animals, before)
                app._save_persistence.assert_not_called()

    def test_308_visibility_still_follows_role_block_and_plugin_capability(self):
        for computed, capability, steroid in ((False, True, True), (True, False, True), (True, True, False)):
            with self.subTest(computed=computed, capability=capability, steroid=steroid):
                with self._female_308(computed=computed, capability=capability, steroid=steroid) as fixture:
                    app, _dialog, _raw, unified, tabs, before = fixture
                    if unified is not None:
                        self.assertLess(tabs.indexOf(unified), 0)
                    else:
                        self.assertFalse(app.pdg_cap._pdg_tabs)
                    self.assertEqual(app.animals, before)
                    app._save_persistence.assert_not_called()

    def test_308_overall_read_only_freeze_preserves_only_declared_view_action(self):
        with self._female_308(read_only=True, allowed=False, localized=True) as fixture:
            app, dialog, raw, unified, _tabs, before = fixture
            table = unified.findChild(QTableWidget)
            refresh = unified.findChild(QPushButton)
            self.assertTrue(refresh.isEnabled())
            self.assertTrue(table.isEnabled())
            self.assertTrue(all(not widget.isEnabled() for widget in raw.findChildren(QLineEdit)))
            save = next(button for button in dialog.findChildren(QPushButton)
                        if button.text() == app.messages['button.save'])
            self.assertFalse(save.isEnabled())
            refresh.click()
            self._settle()
            self.assertGreater(table.verticalScrollBar().maximum(), 0)
            self.assertEqual(app.animals, before)
            app._save_persistence.assert_not_called()

    def test_308_real_vet_and_lord_permission_bundles_preserve_view_and_raw_write_gate(self):
        for actor, expected in ((('user', ['vet']), False), (('lord', []), True)):
            with self.subTest(actor=actor), self._female_308(actor=actor) as fixture:
                app, _dialog, raw, unified, _tabs, before = fixture
                self.assertTrue(app._master_can('core.edit_animal_core'))
                self.assertEqual(app._master_can('core.edit_animal_research_data'), expected)
                self.assertTrue(unified.isEnabled())
                self.assertTrue(unified.findChild(QPushButton).isEnabled())
                self.assertTrue(unified.findChild(QTableWidget).verticalScrollBar().isEnabled())
                self.assertTrue(all(field.isEnabled() == expected for field in raw.findChildren(QLineEdit)))
                self.assertEqual(app.animals, before)
                app._save_persistence.assert_not_called()

    @staticmethod
    def _visible_row_count(table: QTableWidget) -> int:
        header = table.verticalHeader()
        viewport_height = table.viewport().height()
        return sum(
            1
            for row in range(table.rowCount())
            if (
                (position := header.sectionViewportPosition(row)) < viewport_height
                and position + header.sectionSize(row) > 0
            )
        )

    def test_plugin_table_caps_measured_rows_and_keeps_overflow_scrollable(self):
        main = _MAIN.ProgTrackApp.__new__(_MAIN.ProgTrackApp)
        dialog = QDialog()
        tabs = QTabWidget(dialog)
        base = QWidget()
        base.setMinimumSize(240, 120)
        tabs.addTab(base, "Base")
        dialog.setLayout(QVBoxLayout())
        dialog.layout().addWidget(tabs)

        records = [
            {"datum": datetime(2025, 1, 1) + timedelta(days=day), "wert": float(day + 1)}
            for day in range(7)
        ]
        animal = {"name": "Scroll Witness", "pdg": records}
        fake_plugin = SimpleNamespace(
            app=SimpleNamespace(
                messages={},
                animals={"Scroll Witness": animal},
            ),
            converter=SimpleNamespace(predict=lambda values, _params: values),
            get_parameters=lambda _name: None,
        )
        capability = PdGCapability(fake_plugin)

        hooks = SimpleNamespace(messages={})
        hooks._build_editable_list = lambda *_args, **_kwargs: (QScrollArea(), [])
        hooks._cap_table_viewport = lambda table, count: (
            _MAIN.ProgTrackApp._cap_table_viewport(main, table, count)
        )

        _pdg_tab, unified_tab = capability.extend_animal_dialog(
            tabs,
            animal,
            "Scroll Witness",
            hooks,
            add_unified_prog=True,
        )
        _MAIN.ProgTrackApp._register_animal_dialog_tabs(main, dialog, tabs)
        dialog.show()
        tabs.setCurrentWidget(unified_tab)
        self._settle()

        table = unified_tab.findChild(QTableWidget)
        self.assertIsNotNone(table)
        self.assertEqual(table.rowCount(), 7)
        bar = table.verticalScrollBar()
        self.assertGreater(
            bar.maximum(), 0,
            "seven short rows must overflow the five-row cap and remain scrollable",
        )
        self.assertTrue(bar.isVisible(), "overflow must expose the actual scrollbar")
        self.assertTrue(bar.isEnabled(), "the visible scrollbar must be interactive")
        self.assertLessEqual(self._visible_row_count(table), 5)

        # Exercise the scrollbar's actual track input, not only setValue().
        # Starting at the top, a click in the lower track must advance the
        # visible rows; the native Retest 2O still verifies thumb dragging.
        bar.setValue(bar.minimum())
        QTest.mouseClick(
            bar,
            Qt.MouseButton.LeftButton,
            pos=QPoint(max(1, bar.width() // 2), max(1, bar.height() - 2)),
        )
        self._settle()
        self.assertGreater(
            bar.value(), bar.minimum(),
            "clicking the scrollbar track must move the table",
        )
        self.assertLess(
            table.verticalHeader().sectionViewportPosition(0), 0,
            "track input must move the first row out of its initial viewport position",
        )

        bar.setValue(bar.minimum())
        self._settle()
        self.assertEqual(table.verticalHeader().sectionViewportPosition(0), 0)
        bar.setValue(bar.maximum())
        self._settle()
        last_position = table.verticalHeader().sectionViewportPosition(table.rowCount() - 1)
        self.assertGreaterEqual(last_position, 0)
        self.assertLess(
            last_position,
            table.viewport().height(),
            "scrolling to the end must bring the last row into the viewport",
        )

        # A refresh to a shorter history removes the range; restoring overflow
        # must restore it, including after the role-driven tab hide/show path.
        animal["pdg"] = records[:3]
        unified_tab._refresh_table()
        self._settle()
        self.assertEqual(table.rowCount(), 3)
        self.assertEqual(
            bar.maximum(), 0,
            f"short table still scrolls: table={table.height()}; viewport="
            f"{table.viewport().height()}; header={table.horizontalHeader().height()}; "
            f"hscroll={table.horizontalScrollBar().isVisible()}/"
            f"{table.horizontalScrollBar().sizeHint().height()}; "
            f"rows={[table.rowHeight(row) for row in range(table.rowCount())]}",
        )

        animal["pdg"] = records
        unified_tab._refresh_table()
        capability.set_unified_prog_visible(tabs, unified_tab, False)
        self._settle()
        capability.set_unified_prog_visible(tabs, unified_tab, True)
        tabs.setCurrentWidget(unified_tab)
        self._settle()
        self.assertEqual(table.rowCount(), 7)
        self.assertGreater(bar.maximum(), 0)
        self.assertLessEqual(self._visible_row_count(table), 5)

        dialog.close()
        dialog.deleteLater()
        self._settle()


if __name__ == "__main__":
    unittest.main()
