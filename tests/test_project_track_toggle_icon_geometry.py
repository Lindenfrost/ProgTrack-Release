import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import QApplication, QPushButton

from Plugins.core import ui_icons


class ProjectTrackToggleIconGeometryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_apply_icon_is_idempotent_after_toggle_refresh(self):
        button = QPushButton()
        pixmap = QPixmap(16, 16)
        pixmap.fill()
        with mock.patch.object(ui_icons, "icon", return_value=QIcon(pixmap)):
            self.assertTrue(ui_icons.apply_icon(button, "toggle.expand"))
            first = button.iconSize()
            self.assertTrue(ui_icons.apply_icon(button, "toggle.collapse"))
            second = button.iconSize()
            self.assertTrue(ui_icons.apply_icon(button, "toggle.expand"))
            third = button.iconSize()
        self.assertEqual(first, second)
        self.assertEqual(second, third)
        self.assertGreaterEqual(first.width(), 24)
        self.assertGreaterEqual(first.height(), 24)

    def test_collapsible_section_keeps_toggle_button_geometry(self):
        from Plugins.Projects_Track import project_track_tab
        with mock.patch.object(ui_icons, "icon", return_value=QIcon(QPixmap(16, 16))):
            section = project_track_tab.CollapsibleSection("Area", collapsed=True)
            first = section._btn.iconSize()
            section._btn.click()
            second = section._btn.iconSize()
            section._btn.click()
            third = section._btn.iconSize()
        self.assertEqual(first, second)
        self.assertEqual(second, third)


if __name__ == "__main__":
    unittest.main()
