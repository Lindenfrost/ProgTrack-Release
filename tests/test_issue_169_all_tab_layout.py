"""Focused geometry checks for the All-tab animal-list row."""

from __future__ import annotations

import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QSizePolicy, QHBoxLayout, QWidget


ROOT = Path(__file__).resolve().parents[1]


class AllTabNameColumnLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_long_name_yields_width_to_status_controls(self):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(4, 2, 4, 2)
        name = QLabel("A very long animal identity that must not hide status")
        name.setToolTip("full identity")
        name.setMinimumWidth(0)
        name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(name, 1)
        status = QLabel("+")
        status.setFixedWidth(18)
        layout.addWidget(status, 0)
        row.resize(130, 30)
        row.show()
        self.qt_app.processEvents()
        try:
            self.assertGreaterEqual(status.geometry().right(), 0)
            self.assertLessEqual(status.geometry().right(), row.width())
            self.assertLessEqual(name.geometry().right(), status.geometry().left())
            self.assertEqual(name.toolTip(), "full identity")
        finally:
            row.close()
            row.deleteLater()
            self.qt_app.processEvents()

    def test_refresh_list_keeps_compact_policy_scoped_to_all_tab(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        self.assertIn("if show_all_animals_tab:", source)
        start = source.index("            name_label = (", source.index("def _refresh_list"))
        end = source.index("            for semantic_id", start)
        active_builder = source[start:end]
        self.assertIn("_BoundedNameLabel", active_builder)
        self.assertNotIn("QSizePolicy.Policy.Ignored", active_builder)
        self.assertIn("h.addWidget(name_label, 1)", active_builder)
        self.assertIn("h.addWidget(name_label)\n                h.addStretch()", active_builder)


if __name__ == "__main__":
    unittest.main()
