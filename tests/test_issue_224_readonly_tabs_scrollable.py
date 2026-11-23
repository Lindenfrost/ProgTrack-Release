import importlib.util
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


_SPEC = importlib.util.spec_from_file_location(
    "progtrack_main_issue_224", ROOT / "ProgTrack.v.0.3.0.py"
)
_MAIN = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MAIN)


class ReadOnlyDataTabScrollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def make_app(self, permissions):
        app = _MAIN.ProgTrackApp.__new__(_MAIN.ProgTrackApp)
        app._master_can = lambda permission: permissions.get(permission, False)
        return app

    def make_data_page(self, editor_type):
        host = QDialog()
        host.setLayout(QVBoxLayout())
        page = QWidget()
        page.setLayout(QVBoxLayout())
        scroll = QScrollArea(page)
        scroll.setWidgetResizable(False)
        scroll.setFixedSize(360, 180)

        content = QWidget()
        content.setMinimumSize(320, 900)
        content.setLayout(QVBoxLayout())
        if editor_type == "measurement":
            editor = QLineEdit()
            editor.setText("read-only weight")
            mutation = QPushButton("Add weight")
        else:
            editor = QComboBox()
            editor.addItems(["measurement A", "measurement B"])
            mutation = QPushButton("Add sperm measurement")
        content.layout().addWidget(editor)
        content.layout().addWidget(mutation)
        scroll.setWidget(content)
        page.layout().addWidget(scroll)
        host.layout().addWidget(page)
        host.resize(400, 220)
        host.show()
        self.qt.processEvents()
        return host, page, scroll, editor, mutation

    def test_denied_measurement_tab_keeps_scrollbar_enabled_and_moving(self):
        app = self.make_app({"core.edit_animal_measurements": False})
        host, page, scroll, editor, mutation = self.make_data_page("measurement")
        try:
            app._apply_dialog_field_permissions({
                "core.edit_animal_measurements": [page],
            })
            bar = scroll.verticalScrollBar()
            self.assertTrue(page.isEnabled())
            self.assertTrue(scroll.isEnabled())
            self.assertTrue(scroll.viewport().isEnabled())
            self.assertTrue(bar.isEnabled())
            self.assertFalse(editor.isEnabled())
            self.assertFalse(mutation.isEnabled())
            self.assertGreater(bar.maximum(), 0)
            bar.setValue(bar.maximum())
            self.qt.processEvents()
            self.assertEqual(bar.value(), bar.maximum())
        finally:
            host.close()

    def test_denied_research_tab_keeps_scrollbar_enabled_and_mixed_permission_isolated(self):
        permissions = {
            "core.edit_animal_measurements": True,
            "core.edit_animal_research_data": False,
        }
        app = self.make_app(permissions)
        measurement_host, measurement_page, measurement_scroll, measurement_editor, _ = (
            self.make_data_page("measurement")
        )
        research_host, research_page, research_scroll, research_editor, research_mutation = (
            self.make_data_page("research")
        )
        try:
            app._apply_dialog_field_permissions({
                "core.edit_animal_measurements": [measurement_page],
                "core.edit_animal_research_data": [research_page],
            })
            self.assertTrue(measurement_editor.isEnabled())
            self.assertTrue(measurement_scroll.isEnabled())
            self.assertTrue(measurement_scroll.verticalScrollBar().isEnabled())
            self.assertFalse(research_editor.isEnabled())
            self.assertFalse(research_mutation.isEnabled())
            self.assertTrue(research_page.isEnabled())
            self.assertTrue(research_scroll.isEnabled())
            self.assertTrue(research_scroll.viewport().isEnabled())
            self.assertTrue(research_scroll.verticalScrollBar().isEnabled())
            self.assertGreater(research_scroll.verticalScrollBar().maximum(), 0)
        finally:
            measurement_host.close()
            research_host.close()

    def test_non_tab_permission_targets_keep_container_disable_behavior(self):
        app = self.make_app({"core.edit_animal_identity": False})
        field = QLineEdit()
        app._apply_dialog_field_permissions({"core.edit_animal_identity": [field]})
        self.assertFalse(field.isEnabled())


if __name__ == "__main__":
    unittest.main()
