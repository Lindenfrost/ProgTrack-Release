"""Focused regression tests for the Unified Prog. date display in #301."""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QScrollArea, QTabWidget, QTableWidget

from Plugins.PdG_converter.plugin import (
    PdGCapability,
    _format_unified_prog_date,
)


class UnifiedProgDateFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def test_formatter_handles_supported_inputs_without_timezone_shift(self):
        cases = (
            (date(2025, 2, 1), "01. 02. 2025"),
            (datetime(2025, 10, 3, 23, 30, tzinfo=timezone(timedelta(hours=14))),
             "03. 10. 2025"),
            ("2025-02-01", "01. 02. 2025"),
            ("2025-10-03T23:30:00-05:00", "03. 10. 2025"),
            ("not-a-date", "not-a-date"),
            ("2025-02-30", "2025-02-30"),
            (None, ""),
            ("", ""),
        )
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(_format_unified_prog_date(value), expected)

    def test_table_is_display_only_keeps_order_and_leaves_raw_pdg_format(self):
        records = [
            {"datum": datetime(2025, 2, 10), "wert": 2.0, "probennummer": "B"},
            {"datum": datetime(2025, 1, 5), "wert": 1.0, "probennummer": "A"},
        ]
        before = [dict(record) for record in records]
        raw_pdg_rows = []

        def build_editable_list(_title, source_rows, formatter, _default, **_kwargs):
            raw_pdg_rows.extend(formatter(row) for row in source_rows)
            return QScrollArea(), []

        animal = {"name": "Date Witness", "pdg": records}
        fake_plugin = SimpleNamespace(
            app=SimpleNamespace(messages={}, animals={"Date Witness": animal}),
            converter=SimpleNamespace(predict=lambda values, _params: values),
            get_parameters=lambda _name: None,
        )
        parent = SimpleNamespace(
            messages={},
            _build_editable_list=build_editable_list,
            _cap_table_viewport=lambda _table, _count: None,
        )
        tabs = QTabWidget()
        capability = PdGCapability(fake_plugin)

        pdg_tab, unified_tab = capability.extend_animal_dialog(
            tabs, animal, "Date Witness", parent, add_unified_prog=True
        )
        self.qt.processEvents()

        table = unified_tab.findChild(QTableWidget)
        self.assertIsNotNone(table)
        self.assertEqual(
            [table.item(row, 0).text() for row in range(table.rowCount())],
            ["05. 01. 2025", "10. 02. 2025"],
        )
        self.assertEqual(records, before)
        self.assertEqual(
            [row[0] for row in raw_pdg_rows],
            ["05.01.2025", "10.02.2025"],
        )
        self.assertIsNotNone(pdg_tab)

        tabs.deleteLater()
        self.qt.processEvents()


if __name__ == "__main__":
    unittest.main()
