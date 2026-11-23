from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt6.QtGui import QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication, QMainWindow

from Plugins.Projects_Track.project_track_tab import (
    CollapsibleSection,
    ProjectTrackTab,
)
from tests.test_phase2b_sidebar_project_refinements import (
    _History,
    _ProjectApp,
    _load_main_module,
    _messages,
)


OUTPUT = ROOT.parent / "Issue Tracker" / "Tests" / "_phase2b_ui_evidence_2026-08-10"


def render_main_sidebar(app: QApplication) -> Path:
    module = _load_main_module()

    class Harness(module.ProgTrackApp):
        def __init__(self):
            QMainWindow.__init__(self)
            self.messages = _messages("en")
            self.has_projects_plugin = False
            self.projects_plugin = None
            self._disabled_plugins = set()
            self.has_pdg_plugin = False

        def _rebuild_category_tabs(self):
            for label in ("F", "M", "O", "P", "B", "E", "All"):
                self.category_tab.addTab(label)

        @staticmethod
        def _is_steroid_track_active():
            return False

        def _refresh_list(self):
            return None

    harness = Harness()
    layout = harness._build_sidebar()
    widget = harness._make_main_sidebar_widget(layout)
    for animal in ("Arwen", "Eldarion", "Frodo", "Sam", "Unknown sex"):
        harness.lst.addItem(animal)
    widget.resize(200, 820)
    widget.show()
    app.processEvents()
    path = OUTPUT / "main_animal_sidebar_sex_filters.png"
    widget.grab().save(str(path))
    widget.close()
    widget.deleteLater()
    harness.deleteLater()
    app.processEvents()
    return path


def render_project_track(app: QApplication) -> Path:
    tab = ProjectTrackTab(_ProjectApp(), _messages("en"), _History())
    tab._project_data["projects"]["Demo Project"] = {
        "status": "active",
        "iacuc": {
            "short_title": "Demo Project",
            "protocol_id": "IACUC-2026-0042",
            "internal_number": "PT-42",
            "authorization_nr": "AZ 42/2026",
            "unit": "Animal Facility",
            "purpose": "Method validation",
            "authorized": "2026-01-15",
            "approved": "2026-02-01",
        },
    }
    tab._build_detail("Demo Project")
    for section in tab.findChildren(CollapsibleSection):
        title = getattr(section, "_title", "")
        if title == _messages("en")["project.section.iacuc"]:
            section._btn.setChecked(True)
        elif title == _messages("en")["project.section.summary"]:
            section._btn.setChecked(False)
    tab.resize(1280, 720)
    tab.show()
    app.processEvents()
    path = OUTPUT / "project_track_actions_and_iacuc_widths.png"
    tab.grab().save(str(path))
    tab.close()
    tab.deleteLater()
    app.processEvents()
    return path


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    font_id = QFontDatabase.addApplicationFont(r"C:\Windows\Fonts\segoeui.ttf")
    families = QFontDatabase.applicationFontFamilies(font_id)
    if families:
        app.setFont(QFont(families[0], 9))

    paths = (render_main_sidebar(app), render_project_track(app))
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
