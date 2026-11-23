from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
MAIN_PATH = ROOT / "ProgTrack.v.0.3.0.py"


def load_main_module():
    spec = importlib.util.spec_from_file_location("progtrack_codehealth_main", MAIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class CodeHealthTargetedFixesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.main = load_main_module()

    def test_institution_branding_compatibility_entry_uses_current_master_context(self):
        app = self.main.ProgTrackApp.__new__(self.main.ProgTrackApp)
        self.main.QtWidgets.QMainWindow.__init__(app)
        self.addCleanup(app.deleteLater)
        app.master_track = SimpleNamespace(
            current_role="lord",
            current_username="Admin",
            is_logged_in=True,
            get_assigned_jobs=lambda: [],
            can=lambda permission: permission == "core.manage_institution_settings",
        )
        app._disabled_plugins = set()
        app.backend = SimpleNamespace(branding=object())
        app.messages = {}

        captured = {}

        class FakeDialog:
            def __init__(self, branding, actor, **kwargs):
                captured["branding"] = branding
                captured["actor"] = actor
                captured["authorized"] = kwargs["authorized"]

            def exec(self):
                captured["executed"] = True
                return 1

        with patch(
            "Plugins.core.institution_branding.InstitutionBrandingDialog",
            FakeDialog,
        ):
            self.main.ProgTrackApp._show_institution_branding(app)

        self.assertEqual(captured["actor"], "Admin")
        self.assertTrue(captured["authorized"])
        self.assertTrue(captured["executed"])

    def test_institution_branding_compatibility_entry_denies_non_manager(self):
        app = self.main.ProgTrackApp.__new__(self.main.ProgTrackApp)
        self.main.QtWidgets.QMainWindow.__init__(app)
        self.addCleanup(app.deleteLater)
        app.master_track = SimpleNamespace(
            current_role="user",
            current_username="Researcher",
            is_logged_in=True,
            get_assigned_jobs=lambda: ["researcher"],
            can=lambda _permission: False,
        )
        app._disabled_plugins = set()
        app.messages = {}
        with patch.object(self.main.QMessageBox, "warning") as warning:
            self.main.ProgTrackApp._show_institution_branding(app)
        warning.assert_called_once()

    def test_targeted_codehealth_cleanups_are_present(self):
        main_source = MAIN_PATH.read_text(encoding="utf-8")
        self.assertNotIn("assert val >= 0", main_source)
        self.assertIn('mt = getattr(self, "master_track", None)', main_source)

        reports = (ROOT / "Plugins" / "Animal_Reports" / "animal_reports.py").read_text(
            encoding="utf-8"
        )
        pdg = (ROOT / "Plugins" / "PdG_converter" / "ui_hooks.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("print(", reports)
        self.assertNotIn("print(", pdg)
        self.assertIn("logger.error", pdg)

    def test_backend_events_are_canonical_and_report_index_uses_them(self):
        from datetime import datetime
        from Plugins.Animal_Reports.report_index import AnimalReportIndex

        record = {
            "events": [
                {"event_type": "surgery", "datum": datetime(2026, 1, 2)},
                {"event_type": "pgf", "datum": datetime(2026, 1, 3)},
                {"event_type": "embryo_transfer", "datum": datetime(2026, 1, 4)},
            ],
            "sperm": [],
        }
        index = AnimalReportIndex(record, lambda value: value.strip().casefold(), date_format="%d.%m.%Y")
        self.assertEqual(index.donor_recovery_dates, [datetime(2026, 1, 2).date()])
        self.assertEqual(index.embryo_transfer_dates, [datetime(2026, 1, 4).date()])
        self.assertEqual(index.event_counts_through(datetime(2026, 1, 4).date())["pgf"], (1, 1))
        self.assertFalse(any(key in record for key in ("op", "pgf", "embryo")))

    def test_changed_modules_compile(self):
        paths = [
            MAIN_PATH,
            ROOT / "Plugins" / "Animal_Reports" / "animal_reports.py",
            ROOT / "Plugins" / "PdG_converter" / "ui_hooks.py",
            ROOT / "Plugins" / "Embryo_Track" / "embryo_track.py",
            ROOT / "Plugins" / "Medi_Track" / "medi_track_widget.py",
            ROOT / "Plugins" / "core" / "backend" / "interchange.py",
            ROOT / "Plugins" / "core" / "backend" / "postgresql_admin.py",
            ROOT / "Plugins" / "Sample_Track" / "sample_track_widget.py",
        ]
        for path in paths:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


if __name__ == "__main__":
    unittest.main()
