import copy
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from Plugins.Cage__Track.cage_store import CageStore
from Plugins.Cage__Track.cage_track_widget import CageTrackWidget, MovementHistoryDialog


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


def build_store():
    store = CageStore("", FakeBackend())
    building = store.create_building("Mouse Research and Breeding Building")
    unit = store.create_unit(building["id"], "Experimental Mouse Unit")
    room = store.create_room(unit["id"], "Long-term Housing Room")
    cage = store.create_cage(room["id"], "Experimental Cohort Cage")
    store.create_occupant("Frodo", cage_id=cage["id"])
    store.create_occupant("Sam", cage_id=cage["id"])
    store.set_ui_state({
        "expanded_buildings": [building["id"]],
        "expanded_units": [unit["id"]],
        "expanded_rooms": [room["id"]],
        "show_unassigned": True,
    })
    return store, building, unit, room, cage


class CageZoomLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_zoom_out_scales_every_hierarchy_label_and_preserves_viewport(self):
        store, *_ = build_store()
        animals = {
            "Frodo": {"name": "Frodo", "rolle": "experimental", "sex": "male"},
            "Sam": {"name": "Sam", "rolle": "experimental", "sex": "male"},
        }
        plugin = SimpleNamespace(
            store=store,
            messages={},
            app=SimpleNamespace(animals=animals, archived={}),
        )
        widget = CageTrackWidget(plugin)
        widget.resize(920, 620)
        widget.show()
        self.app.processEvents()
        widget.refresh_view(sync_animals=True)
        widget.canvas.draw()

        self.assertGreaterEqual(len(widget._zoom_scaled_text_artists), 6)
        initial_sizes = [artist.get_fontsize() for artist, _ in widget._zoom_scaled_text_artists]
        initial_width = abs(widget.ax.get_xlim()[1] - widget.ax.get_xlim()[0])
        initial_height = abs(widget.ax.get_ylim()[1] - widget.ax.get_ylim()[0])
        initial_bbox = widget.ax.get_window_extent()
        initial_scale = min(
            initial_bbox.width / initial_width, initial_bbox.height / initial_height, 1.0)
        center_x = sum(widget.ax.get_xlim()) / 2.0
        center_y = sum(widget.ax.get_ylim()) / 2.0

        for _ in range(7):
            widget._on_scroll(SimpleNamespace(
                inaxes=widget.ax,
                button="down",
                xdata=center_x,
                ydata=center_y,
            ))
        widget.canvas.draw()

        final_sizes = [artist.get_fontsize() for artist, _ in widget._zoom_scaled_text_artists]
        final_width = abs(widget.ax.get_xlim()[1] - widget.ax.get_xlim()[0])
        final_height = abs(widget.ax.get_ylim()[1] - widget.ax.get_ylim()[0])
        final_bbox = widget.ax.get_window_extent()
        final_scale = min(
            final_bbox.width / final_width, final_bbox.height / final_height, 1.0)
        self.assertGreater(final_width, initial_width * 1.8)
        self.assertGreater(final_height, initial_height * 1.8)
        self.assertTrue(all(after < before for before, after in zip(initial_sizes, final_sizes)))
        for before, after in zip(initial_sizes, final_sizes):
            self.assertAlmostEqual(after / before, final_scale / initial_scale, places=6)
        self.assertLess(max(final_sizes), max(initial_sizes) * 0.75)
        self.assertTrue(all(
            artist.get_clip_path() is not None or artist.get_clip_box() is not None
            for artist, _ in widget._zoom_scaled_text_artists
        ))

        widget.close()
        self.app.processEvents()


class MovementHistorySizingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_empty_history_is_compact_but_populated_history_keeps_full_layout(self):
        store, *_ = build_store()
        data = store.load_data()
        data["occupants"]["NoHistory"] = {
            "occupant_id": "NoHistory",
            "ipid": "NoHistory",
            "name": "NoHistory",
            "type": "real",
            "cage_id": None,
            "moved_at": "2026-08-10T10:00:00",
        }
        data["movement_history"]["NoHistory"] = []
        store.save_data()
        engine = SimpleNamespace(resolve_cage_path=lambda cage_id, unassigned: unassigned)

        empty = MovementHistoryDialog(None, {}, "NoHistory", engine, store)
        full = MovementHistoryDialog(None, {}, "Frodo", engine, store)
        empty.show()
        full.show()
        self.app.processEvents()

        self.assertIsNone(empty._table)
        self.assertIsNotNone(full._table)
        self.assertLess(empty.height(), full.height() - 100)
        self.assertLessEqual(empty.height(), 240)
        self.assertGreaterEqual(full.height(), 380)

        empty.close()
        full.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
