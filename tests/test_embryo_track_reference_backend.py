
from __future__ import annotations
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from PyQt6.QtWidgets import QApplication, QWidget

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Plugins.Embryo_Track.embryo_track import EmbryoTrackerWidget
from Plugins.core.backend.facade import ProgTrackBackend
from Plugins.core.runtime_paths import resolve_runtime_paths


class EmbryoReferenceBackendTests(unittest.TestCase):
    def test_reference_edit_permission_matrix(self):
        cases = [
            ("lord", [], True),
            ("user", ["researcher"], True),
            ("user", ["Researcher"], True),
            ("master", ["researcher"], False),
            ("user", ["manager", "keeper"], False),
            ("guest", [], False),
            ("", [], False),
        ]
        for role, jobs, expected in cases:
            mt = SimpleNamespace(
                current_role=role,
                is_logged_in=True,
                get_assigned_jobs=lambda jobs=jobs: jobs,
            )
            shell = SimpleNamespace(parent=lambda mt=mt: SimpleNamespace(master_track=mt))
            self.assertEqual(
                EmbryoTrackerWidget._reference_data_edit_allowed(shell),
                expected,
                (role, jobs),
            )

    def test_seed_contains_backend_owned_reference_record(self):
        package = ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb"
        with zipfile.ZipFile(package) as archive:
            rows = [
                json.loads(line)
                for line in archive.read("records/domain.jsonl").decode("utf-8").splitlines()
                if line.strip()
            ]
        records = {(row["namespace"], row["record_id"]): row["payload"] for row in rows}
        reference = records[("embryo-track", "cranimetry-reference")]
        self.assertEqual({len(reference[key]) for key in ("1_embryo", "2_embryo", "3_embryo")}, {23, 8, 11})

    def test_reference_record_round_trips_through_backend(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "app"
            paths = resolve_runtime_paths(app, environ={"PROGTRACK_PORTABLE": "1"})
            backend = ProgTrackBackend(paths, acquire_process_lock=False, bootstrap_seed=False)
            payload = json.loads((ROOT / "Plugins" / "Embryo_Track" / "cranimetry_reference.json").read_text(encoding="utf-8"))
            backend.records.put("embryo-track", "cranimetry-reference", payload)
            stored = backend.records.get("embryo-track", "cranimetry-reference", default=None)
            backend.close()
        self.assertEqual(stored, payload)

    def test_empty_backend_bootstraps_once_but_empty_record_stays_empty(self):
        payload = {"1_embryo": [1], "2_embryo": [2], "3_embryo": [3]}

        class Store:
            def __init__(self, value):
                self.value = value
                self.saved = None
            def load(self, default):
                return self.value
            def save(self, value):
                self.saved = value
                return 1

        def run(value):
            shell = SimpleNamespace(
                _reference_store=Store(value),
                reference_data={},
                prediction_models={},
                _load_packaged_reference_data=lambda: payload,
                _build_prediction_models=lambda: setattr(
                    shell, "prediction_models", {2: {"col_model": object()}}
                ),
            )
            shell._has_baseline_model = EmbryoTrackerWidget._has_baseline_model
            EmbryoTrackerWidget._load_reference_data(shell)
            return shell

        bootstrapped = run(None)
        self.assertEqual(bootstrapped.reference_data, payload)
        self.assertEqual(bootstrapped._reference_store.saved, payload)

        existing_empty = run({})
        self.assertEqual(existing_empty.reference_data, {})
        self.assertIsNone(existing_empty._reference_store.saved)

    def test_import_path_is_guarded_and_replacement_is_confirmed(self):
        source = (ROOT / "Plugins" / "Embryo_Track" / "embryo_track.py").read_text(encoding="utf-8")
        self.assertIn("if not self._reference_data_edit_allowed():", source)
        self.assertIn("self._has_baseline_model(self.prediction_models.get(2, {}))", source)
        self.assertIn("QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No", source)
        self.assertIn("The previous dataset remains active.", source)

    def test_reference_button_matches_role_and_job(self):
        app = QApplication.instance() or QApplication([])
        payload = json.loads(
            (ROOT / "Plugins" / "Embryo_Track" / "cranimetry_reference.json").read_text(
                encoding="utf-8"
            )
        )

        class Records:
            def get(self, namespace, record_id, default=None):
                return payload if (namespace, record_id) == (
                    "embryo-track", "cranimetry-reference"
                ) else default

            def put(self, namespace, record_id, value):
                return 1

        class Backend:
            records = Records()

        original_builder = EmbryoTrackerWidget._build_prediction_models
        EmbryoTrackerWidget._build_prediction_models = lambda self: setattr(
            self, "prediction_models", {2: {"col_model": object()}}
        )
        try:
            for role, jobs, expected in (
                ("lord", [], True),
                ("user", ["researcher"], True),
                ("master", [], False),
                ("user", ["manager"], False),
            ):
                parent = QWidget()
                parent.backend = Backend()
                parent.master_track = SimpleNamespace(
                    current_role=role,
                    is_logged_in=True,
                    get_assigned_jobs=lambda jobs=jobs: jobs,
                )
                dialog = EmbryoTrackerWidget({}, parent)
                self.assertEqual(dialog.load_excel_button.isEnabled(), expected, (role, jobs))
                dialog.close()
                parent.close()
            app.processEvents()
        finally:
            EmbryoTrackerWidget._build_prediction_models = original_builder



if __name__ == "__main__":
    unittest.main()
