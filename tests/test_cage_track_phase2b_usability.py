import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtWidgets import QApplication

from Plugins.Cage__Track.cage_store import CageStore, UNASSIGNED_CAGE_ID
from Plugins.Cage__Track.cage_track_widget import (
    CageTrackWidget,
    InspectionDialog,
    load_inspection_sort_preference,
    resolve_cage_selection_scope,
    save_inspection_sort_preference,
    sort_inspection_records,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeRecords:
    def __init__(self):
        self.values = {}

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def put(self, namespace, record_id, payload, **_kwargs):
        self.values[(namespace, record_id)] = copy.deepcopy(payload)
        return 1


class FakeBackend:
    def __init__(self):
        self.records = FakeRecords()


class InspectionSortingTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "record_id": "new",
                "unit_name": "Unit 10",
                "date": "02.01.2026",
                "date_sort": "2026-01-02",
                "cages": "Cage 2",
                "user": "Zoë",
            },
            {
                "record_id": "old",
                "unit_name": "Unit 2",
                "date": "31.12.2025",
                "date_sort": "2025-12-31",
                "cages": "Cage 1",
                "user": "Alice",
            },
            {
                "record_id": "missing",
                "unit_name": "",
                "date": "unknown",
                "date_sort": "",
                "cages": "",
                "user": "",
            },
        ]

    def test_date_sort_is_chronological_and_missing_values_stay_last(self):
        descending = sort_inspection_records(
            self.records, {"column": "date", "direction": "descending"}
        )
        ascending = sort_inspection_records(
            self.records, {"column": "date", "direction": "ascending"}
        )
        self.assertEqual([row["record_id"] for row in descending], ["new", "old", "missing"])
        self.assertEqual([row["record_id"] for row in ascending], ["old", "new", "missing"])

    def test_each_text_column_sorts_without_mutating_source_records(self):
        original = copy.deepcopy(self.records)
        for column in ("unit", "cages", "inspector"):
            result = sort_inspection_records(
                self.records, {"column": column, "direction": "ascending"}
            )
            self.assertEqual(result[-1]["record_id"], "missing")
            if column == "unit":
                self.assertEqual(
                    [row["record_id"] for row in result],
                    ["old", "new", "missing"],
                )
        self.assertEqual(self.records, original)

    def test_backend_preferences_are_per_user_and_guests_are_not_persisted(self):
        backend = FakeBackend()
        self.assertTrue(save_inspection_sort_preference(
            backend, "alice", {"column": "unit", "direction": "ascending"}
        ))
        self.assertTrue(save_inspection_sort_preference(
            backend, "bob", {"column": "inspector", "direction": "descending"}
        ))
        self.assertFalse(save_inspection_sort_preference(
            backend, None, {"column": "cages", "direction": "ascending"}
        ))
        self.assertEqual(
            load_inspection_sort_preference(backend, "alice"),
            {"column": "unit", "direction": "ascending"},
        )
        self.assertEqual(
            load_inspection_sort_preference(backend, "bob"),
            {"column": "inspector", "direction": "descending"},
        )
        self.assertEqual(
            load_inspection_sort_preference(backend, None),
            {"column": "date", "direction": "descending"},
        )
        self.assertEqual(len(backend.records.values), 2)

    def test_offscreen_header_click_updates_table_indicator_and_callback(self):
        app = QApplication.instance() or QApplication([])
        changes = []
        dialog = InspectionDialog(
            None,
            {
                "cage_track.inspection.sort_ascending": "Ascending",
                "cage_track.inspection.sort_descending": "Descending",
            },
            self.records,
            lambda: None,
            initial_sort={"column": "date", "direction": "descending"},
            on_sort_changed=changes.append,
        )
        self.assertEqual(dialog._table.item(0, 0).text(), "Unit 10")
        dialog._on_header_clicked(1)
        self.assertEqual(dialog._table.item(0, 0).text(), "Unit 2")
        self.assertEqual(changes[-1], {"column": "date", "direction": "ascending"})
        dialog._on_header_clicked(0)
        self.assertEqual(changes[-1], {"column": "unit", "direction": "ascending"})
        self.assertIn("Ascending", dialog._table.accessibleDescription())
        self.assertTrue(dialog._table.horizontalHeaderItem(0).toolTip())
        dialog.close()
        app.processEvents()

    def test_sort_feedback_is_localized_in_every_shipped_language(self):
        required = {
            "cage_track.inspection.sort_hint",
            "cage_track.inspection.sort_ascending",
            "cage_track.inspection.sort_descending",
        }
        for language in ("en", "de", "it", "ru"):
            messages = json.loads(
                (ROOT / "lang" / f"messages_{language}.json").read_text(
                    encoding="utf-8-sig"
                )
            )
            self.assertTrue(required.issubset(messages), language)
            self.assertTrue(all(str(messages[key]).strip() for key in required))


class CageSelectionTests(unittest.TestCase):
    def test_majority_and_configured_order_tie_break_are_deterministic(self):
        addresses = {
            "A": {"building_id": "b1", "unit_id": "u1", "room_id": "r1", "cage_id": "c1"},
            "B": {"building_id": "b1", "unit_id": "u1", "room_id": "r2", "cage_id": "c2"},
            "C": {"building_id": "b2", "unit_id": "u2", "room_id": "r3", "cage_id": "c3"},
            "D": {"building_id": "b2", "unit_id": "u2", "room_id": "r3", "cage_id": "c3"},
        }
        order = {"b1": (5, "b1"), "b2": (1, "b2")}
        majority = resolve_cage_selection_scope(["A", "B", "C"], addresses, order)
        self.assertEqual(majority["building_id"], "b1")
        self.assertEqual(majority["animal_ids"], ["A", "B"])
        tied = resolve_cage_selection_scope(["A", "C"], addresses, order)
        self.assertEqual(tied["building_id"], "b2")
        self.assertEqual(tied["animal_ids"], ["C"])

    def test_unassigned_selection_is_highlighted_without_a_building(self):
        scope = resolve_cage_selection_scope(
            ["Unknown"],
            {"Unknown": {"building_id": None, "unit_id": None, "room_id": None, "cage_id": None}},
            {},
        )
        self.assertIsNone(scope["building_id"])
        self.assertEqual(scope["animal_ids"], ["Unknown"])
        self.assertTrue(scope["show_unassigned"])

    def test_widget_selection_projection_does_not_write_housing_ui_state(self):
        addresses = {
            "A": {"building_id": "b1", "unit_id": "u1", "room_id": "r1", "cage_id": "c1"},
            "B": {"building_id": "b1", "unit_id": "u1", "room_id": "r2", "cage_id": "c2"},
            "C": {"building_id": "b2", "unit_id": "u2", "room_id": "r3", "cage_id": "c3"},
        }

        class FakeStore:
            def __init__(self):
                self.occupants = {name: {"cage_id": path["cage_id"]} for name, path in addresses.items()}
                self.ui_writes = 0

            def get_occupant(self, name):
                return self.occupants.get(name)

            def get_address_for_dialog(self, name):
                return addresses[name]

            def get_all_buildings(self):
                return [{"id": "b1", "order": 0}, {"id": "b2", "order": 1}]

            def set_ui_state(self, _state):
                self.ui_writes += 1

        fake = SimpleNamespace(
            store=FakeStore(),
            refresh_calls=0,
            refresh_view=lambda sync_animals=False: setattr(
                fake, "refresh_calls", fake.refresh_calls + 1
            ),
        )
        CageTrackWidget.on_animal_selected(fake, ["C", "A", "B"])
        self.assertEqual(fake._selection_scope_building, "b1")
        self.assertEqual(fake._highlight_occupants, {"A", "B"})
        self.assertEqual(fake._selection_expanded_units, {"u1"})
        self.assertEqual(fake._selection_expanded_rooms, {"r1", "r2"})
        self.assertEqual(fake.store.ui_writes, 0)
        self.assertEqual(fake.refresh_calls, 1)
        CageTrackWidget.on_animal_selected(fake, [])
        self.assertFalse(fake._highlight_occupants)
        self.assertIsNone(fake._selection_scope_building)
        self.assertEqual(fake.store.ui_writes, 0)
        self.assertEqual(fake.refresh_calls, 2)

    def test_empty_canvas_double_click_uses_shared_clear_callback(self):
        axis = object()
        fake = SimpleNamespace(
            ax=axis,
            _hit_test=lambda _x, _y: None,
            clear_calls=0,
            _clear_shared_animal_selection=lambda: setattr(
                fake, "clear_calls", fake.clear_calls + 1
            ),
        )
        event = SimpleNamespace(
            inaxes=axis,
            xdata=10.0,
            ydata=20.0,
            button=1,
            key=None,
            dblclick=True,
        )
        CageTrackWidget._on_canvas_press(fake, event)
        self.assertEqual(fake.clear_calls, 1)

    def test_offscreen_render_expands_scope_and_emphasizes_all_selected_names(self):
        app = QApplication.instance() or QApplication([])
        backend = FakeBackend()
        store = CageStore("", backend)
        b1 = store.create_building("Mouse House")
        u1 = store.create_unit(b1["id"], "Unit A")
        r1 = store.create_room(u1["id"], "Room 1")
        c1 = store.create_cage(r1["id"], "Cage 1")
        r2 = store.create_room(u1["id"], "Room 2")
        c2 = store.create_cage(r2["id"], "Cage 2")
        b2 = store.create_building("Primate House")
        u2 = store.create_unit(b2["id"], "Unit B")
        r3 = store.create_room(u2["id"], "Room 3")
        c3 = store.create_cage(r3["id"], "Cage 3")
        store.create_occupant("Frodo", cage_id=c1["id"])
        store.create_occupant("Sam", cage_id=c2["id"])
        store.create_occupant("Arwen", cage_id=c3["id"])
        animals = {
            "Frodo": {"name": "Frodo", "rolle": "experimental", "sex": "male"},
            "Sam": {"name": "Sam", "rolle": "experimental", "sex": "male"},
            "Arwen": {"name": "Arwen", "rolle": "breeding animal", "sex": "female"},
        }
        host = SimpleNamespace(animals=animals, archived={})
        plugin = SimpleNamespace(store=store, messages={}, app=host)
        widget = CageTrackWidget(plugin)
        app.processEvents()
        widget.on_animal_selected(["Arwen", "Frodo", "Sam"])
        app.processEvents()
        self.assertEqual(widget._selection_scope_building, b1["id"])
        self.assertEqual(widget._highlight_occupants, {"Frodo", "Sam"})
        selected_text = {
            text.get_text(): text
            for text in widget.ax.texts
            if text.get_text() in {"Frodo", "Sam"}
        }
        self.assertEqual(set(selected_text), {"Frodo", "Sam"})
        self.assertTrue(all(text.get_fontweight() == "bold" for text in selected_text.values()))
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / "cage_multiselect.png"
            widget.figure.savefig(screenshot, dpi=120)
            self.assertGreater(screenshot.stat().st_size, 10_000)
            image = Image.open(screenshot).convert("RGB")
            self.assertGreater(len(image.getcolors(maxcolors=1_000_000) or []), 10)
        widget.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
