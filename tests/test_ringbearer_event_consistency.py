import json
import unittest
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb"
APP = ROOT / "ProgTrack.v.0.3.0.py"
RINGBEARER_SURGERY = {
    "Frodo": "2024-09-15",
    "Sam": "2024-09-16",
    "Merry": "2024-09-17",
    "Pippin": "2024-09-18",
}


class RingbearerEventConsistencyTests(unittest.TestCase):
    @staticmethod
    def _seed_named_animals(package):
        domains = [
            json.loads(line)
            for line in package.read("records/domain.jsonl").decode().splitlines()
            if line.strip()
        ]
        return next(
            row["payload"]["named_animals"]
            for row in domains
            if row.get("namespace") == "seed" and row.get("record_id") == "shire-colony"
        )

    def test_seed_contains_only_ringbearer_surgeries_for_mouse_events(self):
        with zipfile.ZipFile(SEED) as package:
            rows = [
                json.loads(line)
                for line in package.read("records/animals.jsonl").decode().splitlines()
                if line.strip()
            ]
            named_animals = self._seed_named_animals(package)
        mice = [
            row["payload"] for row in rows
            if row.get("payload", {}).get("species") == "Mus musculus"
        ]
        for record in mice:
            events = record.get("events", []) or []
            self.assertFalse(
                any(str(event.get("event_type", "")).casefold() == "birth" for event in events),
                record["name"],
            )
        for name, expected_date in RINGBEARER_SURGERY.items():
            record = next(
                item for item in mice if item["ipid"] == named_animals[name]
            )
            self.assertEqual(
                [{"event_type": event.get("event_type"), "datum": event.get("datum")} for event in record["events"]],
                [{"event_type": "surgery", "datum": expected_date}],
            )
            last_weight = max(item["datum"] for item in record["gewicht"])
            self.assertGreaterEqual(last_weight[:10], expected_date)

    def test_active_backend_has_the_same_canonical_mouse_events(self):
        from tests.disposable_seed_backend import open_disposable_seed_backend

        temporary, backend = open_disposable_seed_backend()
        try:
            snapshot = backend.load_core_data()
            all_animals = {
                **snapshot["animals"],
                **snapshot["archived_animals"],
            }
            mice = {
                ipid: record
                for ipid, record in all_animals.items()
                if record.get("species") == "Mus musculus"
            }
            with zipfile.ZipFile(SEED) as package:
                seed_mouse_count = sum(
                    1
                    for line in package.read("records/animals.jsonl").decode().splitlines()
                    if line.strip()
                    and json.loads(line)["payload"].get("species") == "Mus musculus"
                )
                named_animals = self._seed_named_animals(package)
            self.assertEqual(len(mice), seed_mouse_count)
            for ipid, record in mice.items():
                self.assertFalse(
                    any(
                        str(event.get("event_type", "")).casefold() == "birth"
                        for event in record.get("events", [])
                    ),
                    ipid,
                )
            for name, expected_date in RINGBEARER_SURGERY.items():
                ipid = named_animals[name]
                record = mice[ipid]
                events = sorted(
                    (
                        event.get("event_type"),
                        str(event.get("datum", ""))[:10],
                    )
                    for event in record.get("events", [])
                )
                self.assertEqual(events, [("surgery", expected_date)])
                last_weight = max(
                    str(item.get("datum", ""))[:10]
                    for item in record.get("gewicht", [])
                )
                self.assertGreaterEqual(last_weight, expected_date)
        finally:
            backend.close()
            temporary.cleanup()

    def test_plot_keeps_experimental_all_event_path_and_deduplicates(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location("progtrack_ringbearer_plot", APP)
        main = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(main)
        source = APP.read_text(encoding="utf-8")
        events = [
            {"event_id": "surgery-1", "event_type": "surgery", "datum": "2024-09-15"},
            {"event_id": "birth-1", "event_type": "birth", "datum": "2024-09-16"},
            {"event_id": "surgery-1", "event_type": "surgery", "datum": "2024-09-15"},
        ]
        projected = main.plot_event_entries(
            {"rolle": "experimental_animal", "events": events}
        )
        self.assertEqual(
            [(kind, event_date) for kind, event_date, _event in projected],
            [("surgery", "2024-09-15"), ("birth", "2024-09-16")],
        )
        self.assertIn("evs = plot_event_entries(a)", source)
        self.assertIn("appearance = self._custom_event_appearance(event_type)", source)
        self.assertIn("appearance = appearances.get(typ) or self._custom_event_appearance(typ)", source)
        self.assertNotIn("'surgery': getattr(self, 'op_color'", source)


if __name__ == "__main__":
    unittest.main()
