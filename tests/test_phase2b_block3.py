from __future__ import annotations

import copy
import importlib.util
import json
import os
import unittest
from datetime import date
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "ProgTrack.v.0.2.3.py"
SURGERY = ROOT / "Plugins" / "Surgery_Planner"


class Records:
    def __init__(self):
        self.data = {}

    def get(self, namespace, key, default=None):
        return copy.deepcopy(self.data.get((namespace, key), default))

    def put(self, namespace, key, value, **kwargs):
        self.data[(namespace, key)] = copy.deepcopy(value)
        return 1


class Backend:
    def __init__(self):
        self.records = Records()


class Master:
    def __init__(self, logged_in=False, session=None):
        self.is_logged_in = logged_in
        self._session = copy.deepcopy(session or {})

    def load_session(self):
        return copy.deepcopy(self._session)

    def save_session(self, extra=None):
        if self.is_logged_in and extra:
            self._session.update(copy.deepcopy(extra))


class Block3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("progtrack_block3", MAIN)
        cls.main = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.main)

    def test_style_normalizer_merges_defaults_rejects_known_invalid_values_and_preserves_extensions(self):
        host = self.main.ProgTrackApp.__new__(self.main.ProgTrackApp)
        normalized = host._normalize_style_settings({
            "prog_color": "not-a-colour",
            "weight_marker": "not-a-marker",
            "custom_events": {"future.event": {"color": "#123456"}},
        })
        self.assertEqual(normalized["prog_color"], "#DC143C")
        self.assertEqual(normalized["weight_marker"], "^")
        self.assertEqual(normalized["custom_events"]["future.event"]["color"], "#123456")
        self.assertIn("blood_color", normalized)

    def test_standalone_style_uses_default_user_key_and_round_trips(self):
        host = self.main.ProgTrackApp.__new__(self.main.ProgTrackApp)
        host.backend = Backend()
        host.master_track = None
        host._save_user_style_settings({"prog_color": "#123456"})
        self.assertIn(("preferences", "style:default_user"), host.backend.records.data)
        loaded = host._load_user_style_settings()
        self.assertEqual(loaded["prog_color"], "#123456")

    def test_master_sessions_are_isolated_and_guest_does_not_leak_outgoing_style(self):
        host = self.main.ProgTrackApp.__new__(self.main.ProgTrackApp)
        host.backend = Backend()
        host.master_track = Master(True, {"style_settings": {"prog_color": "#111111"}})
        self.assertEqual(host._load_user_style_settings()["prog_color"], "#111111")
        host.master_track = Master(True, {"style_settings": {"prog_color": "#222222"}})
        self.assertEqual(host._load_user_style_settings()["prog_color"], "#222222")
        host.master_track = Master(False)
        self.assertEqual(host._load_user_style_settings()["prog_color"], "#DC143C")
        host._save_user_style_settings({"prog_color": "#333333"})
        self.assertNotIn(("preferences", "style:default_user"), host.backend.records.data)

    def test_master_fixture_session_save_merges_copies_and_ignores_guest(self):
        original = {"language": "de", "style_settings": {"prog_color": "#111111"}}
        master = Master(True, original)
        other = Master(True, original)
        extra = {"style_settings": {"prog_color": "#222222"}}
        master.save_session(extra)
        extra["style_settings"]["prog_color"] = "#333333"
        loaded = master.load_session()
        self.assertEqual(loaded["language"], "de")
        self.assertEqual(loaded["style_settings"]["prog_color"], "#222222")
        loaded["style_settings"].clear()
        self.assertEqual(master.load_session()["style_settings"]["prog_color"], "#222222")
        self.assertEqual(other.load_session(), original)
        guest = Master(False, original)
        guest.save_session(extra)
        self.assertEqual(guest.load_session(), original)

    def test_style_transitions_are_wired_to_login_and_logout(self):
        source = MAIN.read_text(encoding="utf-8")
        login = source.index("    def _do_master_login(self):")
        logout = source.index("    def _do_master_logout(self):")
        self.assertIn("self._reload_user_style_settings()", source[login:login + 5000])
        self.assertIn("self._reload_user_style_settings()", source[logout:logout + 4000])

    def test_surgery_manifest_imports_with_real_plugin_manager(self):
        from Plugins.core.plugin_manager import PluginManager
        manager = PluginManager(ROOT / "Plugins", app_version="0.2.1")
        diagnostic = manager.validate_manifest(SURGERY, import_entry_point=True)
        self.assertTrue(diagnostic.valid, diagnostic.errors)
        self.assertEqual(diagnostic.manifest["dependencies"], ["PyQt6", "matplotlib", "numpy"])

    def test_surgery_engine_is_deterministic_and_read_only(self):
        from Plugins.Surgery_Planner.surgery_engine import PlannerSnapshot, generate_preview_candidates
        animals = [
            {"name": "S2", "rolle": "amme", "Embryo_max": 1},
            {"name": "D1", "rolle": "spenderin", "OP_max": 1},
        ]
        settings = {"surgery_weekdays": [0], "transfer_weekdays": [0]}
        blocked_day = date(2026, 8, 17)
        snap_a = PlannerSnapshot.from_inputs(
            animals=animals,
            settings=settings,
            horizon_start=date(2026, 8, 17),
            horizon_end=date(2026, 8, 31),
            blocked_days=(blocked_day,),
        )
        snap_b = PlannerSnapshot.from_inputs(
            animals=list(reversed(animals)),
            settings=settings,
            horizon_start=date(2026, 8, 17),
            horizon_end=date(2026, 8, 31),
            blocked_days=(blocked_day,),
        )
        self.assertEqual(snap_a, snap_b)
        self.assertEqual(snap_a.blocked_days, (blocked_day,))
        candidates = generate_preview_candidates(snap_a)
        self.assertEqual(candidates, generate_preview_candidates(snap_b))
        self.assertTrue(all(candidate.scheduled_date != blocked_day for candidate in candidates))
        self.assertTrue(all(item.candidate_id for item in generate_preview_candidates(snap_a)))
        unblocked = PlannerSnapshot.from_inputs(
            animals=animals,
            settings=settings,
            horizon_start=date(2026, 8, 17),
            horizon_end=date(2026, 8, 31),
        )
        self.assertNotEqual(snap_a.revision, unblocked.revision)

    def test_surgery_generate_uses_declared_snapshot_factory_contract(self):
        from unittest.mock import Mock, patch

        from PyQt6.QtWidgets import QApplication, QDialog

        from Plugins.Surgery_Planner import surgery_planner
        from Plugins.Surgery_Planner.surgery_engine import PlannerSnapshot

        app = QApplication.instance() or QApplication([])
        self.__class__._test_qapplication = app
        widget = surgery_planner.GanttWidget.__new__(surgery_planner.GanttWidget)
        QDialog.__init__(widget)
        self.addCleanup(widget.close)

        widget.ax = Mock()
        widget._update_xaxis = Mock()
        widget.animals = [{"name": "Planner probe", "rolle": "sperm donor"}]
        widget.settings = {
            "recovery_op": 60,
            "recovery_transfer": 30,
            "cycle_active_days": 60,
            "cycle_break_days": 30,
            "transfer_offset_days": 6,
            "donors_per_surgery": 2,
            "surrogates_per_transfer": 2,
        }
        for control, setting in (
            ("recovery_op", "recovery_op"),
            ("recovery_transfer", "recovery_transfer"),
            ("cycle_active_days", "cycle_active_days"),
            ("cycle_break_days", "cycle_break_days"),
            ("transfer_offset", "transfer_offset_days"),
            ("donors_per_surgery", "donors_per_surgery"),
            ("surrogates_per_transfer", "surrogates_per_transfer"),
        ):
            value = Mock()
            value.value.return_value = widget.settings[setting]
            setattr(widget, control, value)
        monday = Mock()
        monday.text.return_value = "Monday"
        for control in ("surgery_days", "transfer_days"):
            day_list = Mock()
            day_list.selectedItems.return_value = [monday]
            setattr(widget, control, day_list)
        widget._parse_weekdays = Mock(return_value=[0])
        widget.horizon_start = Mock()
        widget.horizon_start.text.return_value = date(2026, 8, 17).strftime(
            surgery_planner.DATE_FORMAT
        )
        widget.horizon_end = Mock()
        widget.horizon_end.text.return_value = date(2026, 8, 31).strftime(
            surgery_planner.DATE_FORMAT
        )
        widget.block_days = [Mock(date=date(2026, 8, 24))]
        # A pre-existing entry must stay on the separate path; it is not an
        # input to PlannerSnapshot.from_inputs.
        widget.planned = [Mock(fixed=True)]
        widget.messages = {}

        original_factory = PlannerSnapshot.from_inputs
        captured = {}

        class _StopAfterSnapshot(RuntimeError):
            pass

        def checked_factory(
            cls, *, animals, settings, horizon_start, horizon_end, blocked_days=()
        ):
            captured["snapshot"] = original_factory(
                animals=animals,
                settings=settings,
                horizon_start=horizon_start,
                horizon_end=horizon_end,
                blocked_days=blocked_days,
            )
            raise _StopAfterSnapshot("snapshot call reached successfully")

        with (
            patch.object(
                PlannerSnapshot, "from_inputs", classmethod(checked_factory)
            ),
            patch.object(surgery_planner.QMessageBox, "critical") as critical,
        ):
            widget._generate()

        snapshot = captured["snapshot"]
        self.assertEqual(snapshot.horizon_start, date(2026, 8, 17))
        self.assertEqual(snapshot.horizon_end, date(2026, 8, 31))
        self.assertEqual(snapshot.blocked_days, (date(2026, 8, 24),))
        self.assertEqual(critical.call_count, 1)
        self.assertIn("snapshot call reached successfully", critical.call_args.args[2])

    def test_surgery_has_no_process_global_qt5_or_backend_state(self):
        source = (SURGERY / "surgery_planner.py").read_text(encoding="utf-8")
        self.assertNotIn("Qt5Agg", source)
        self.assertNotIn("backend_qt5", source)
        self.assertNotIn("_SURGERY_BACKEND", source)
        self.assertIn("backend_qtagg", source)
        self.assertIn("self.backend", source)


if __name__ == "__main__":
    unittest.main()
