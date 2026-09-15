"""Focused regression tests for Heritage Track archived-row selection."""

from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QListWidget, QListWidgetItem

from Plugins.Heritage_Track.display_strategies import SelectedAnimalsStrategy
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackWidget


ROOT = Path(__file__).resolve().parents[1]


class _Item:
    def __init__(self, value: str, selected: bool = True):
        self.value = value
        self._selected = selected

    def data(self, role):
        self.assert_role(role)
        return self.value

    @staticmethod
    def assert_role(role):
        assert role == Qt.ItemDataRole.UserRole

    def isSelected(self):
        return self._selected


class _List:
    def __init__(self, values, selected=None):
        selected = set(values) if selected is None else set(selected)
        self._items = [_Item(value, value in selected) for value in values]

    def selectedItems(self):
        return [item for item in self._items if item.isSelected()]

    def count(self):
        return len(self._items)

    def item(self, index):
        return self._items[index]


class _Button:
    def __init__(self):
        self.enabled = None

    def setEnabled(self, value):
        self.enabled = bool(value)


class ArchivedSelectionBoundaryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])
        spec = importlib.util.spec_from_file_location(
            "progtrack_issue_174", ROOT / "ProgTrack.v.0.2.3.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def test_archived_sentinel_normalizes_only_existing_archived_key(self):
        fake = type("FakeApp", (), {})()
        fake.archived = {"Old": {"id": "old-id"}}
        normalize = self.module.ProgTrackApp._normalize_sidebar_selection_value
        self.assertEqual(normalize(fake, "__archived__Old"), "Old")
        self.assertEqual(normalize(fake, "Active"), "Active")
        self.assertEqual(normalize(fake, "__archived__Missing"), "")
        self.assertEqual(normalize(fake, "__archived__"), "")

    def test_on_select_promotes_archived_row_without_leaking_sentinel(self):
        fake = type("FakeApp", (), {})()
        fake.lst = _List(["Active", "__archived__Old"])
        fake.animals = {"Active": {}}
        fake.archived = {"Old": {"id": "old-id"}}
        fake.selected_animals = []
        fake._plot_selection_order = []
        fake._edit_selection_order = []
        fake.has_heritage_plugin = False
        fake._plot_selected = lambda: None
        fake._normalize_sidebar_selection_value = (
            lambda value: self.module.ProgTrackApp._normalize_sidebar_selection_value(
                fake, value
            )
        )
        fake._sidebar_projection_keys = (
            lambda: self.module.ProgTrackApp._sidebar_projection_keys(fake)
        )

        self.module.ProgTrackApp._on_select(fake)

        self.assertEqual(fake.selected_animals, ["Active", "Old"])
        self.assertEqual(fake._selected_archived, ["Old"])
        self.assertNotIn("__archived__Old", fake.selected_animals)

    def test_on_select_keeps_hidden_graph_selection_but_allows_visible_deselect(self):
        fake = type("FakeApp", (), {})()
        fake.lst = _List(["Visible"], selected=["Visible"])
        fake.animals = {"Visible": {}, "Hidden": {}}
        fake.archived = {}
        fake.selected_animals = ["Visible", "Hidden"]
        fake._plot_selection_order = ["Visible", "Hidden"]
        fake._edit_selection_order = []
        fake._selected_heritage_only = []
        fake.has_heritage_plugin = False
        fake._plot_selected = lambda: None
        fake._normalize_sidebar_selection_value = (
            lambda value: self.module.ProgTrackApp._normalize_sidebar_selection_value(
                fake, value
            )
        )
        fake._sidebar_projection_keys = (
            lambda: self.module.ProgTrackApp._sidebar_projection_keys(fake)
        )

        self.module.ProgTrackApp._on_select(fake)
        self.assertEqual(fake.selected_animals, ["Visible", "Hidden"])

        fake.lst = _List(["Visible", "Hidden"], selected=[])
        self.module.ProgTrackApp._on_select(fake)
        self.assertEqual(fake.selected_animals, [])

    def test_graph_click_adds_filtered_out_active_animal_without_sidebar_row(self):
        app = type("FakeApp", (), {})()
        app.lst = QListWidget()
        visible = QListWidgetItem("Visible")
        visible.setData(Qt.ItemDataRole.UserRole, "Visible")
        app.lst.addItem(visible)
        visible.setSelected(True)
        app.animals = {"Visible": {}, "Filtered": {}}
        app.archived = {}
        app.selected_animals = ["Visible"]
        app._plot_selection_order = ["Visible"]
        app._edit_selection_order = []
        app._selected_heritage_only = []
        app.has_heritage_plugin = False
        app._plot_selected = lambda: None
        app._normalize_sidebar_selection_value = (
            lambda value: self.module.ProgTrackApp._normalize_sidebar_selection_value(
                app, value
            )
        )
        app._sidebar_projection_keys = (
            lambda: self.module.ProgTrackApp._sidebar_projection_keys(app)
        )
        app._on_select = lambda: self.module.ProgTrackApp._on_select(app)
        app._extend_animal_selection_from_graph = (
            lambda key: self.module.ProgTrackApp._extend_animal_selection_from_graph(
                app, key
            )
        )

        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.app = app
        widget.plugin = SimpleNamespace(is_heritage_only=lambda _key: False)
        widget._ghost_nodes = set()
        widget.temp_positions = {}
        widget._force_relayout = False
        widget.refresh_graph = lambda keep_view=True: None

        HeritageTrackWidget._add_animal_to_selection(widget, "Filtered")

        self.assertEqual(app.selected_animals, ["Visible", "Filtered"])
        self.assertEqual(app.lst.count(), 1)
        self.assertEqual(
            [item.text() for item in app.lst.selectedItems()],
            ["Visible"],
        )

    def test_graph_click_matches_visible_archived_row_sentinel(self):
        app = type("FakeApp", (), {})()
        app.lst = QListWidget()
        archived_item = QListWidgetItem("Old")
        archived_item.setData(Qt.ItemDataRole.UserRole, "__archived__Old")
        app.lst.addItem(archived_item)
        app.animals = {"Visible": {}}
        app.archived = {"Old": {"id": "old-id"}}
        app.selected_animals = ["Visible"]
        app._plot_selection_order = ["Visible"]
        app._edit_selection_order = []
        app._selected_heritage_only = []
        app.has_heritage_plugin = False
        app._plot_selected = lambda: None
        app._normalize_sidebar_selection_value = (
            lambda value: self.module.ProgTrackApp._normalize_sidebar_selection_value(
                app, value
            )
        )
        app._sidebar_projection_keys = (
            lambda: self.module.ProgTrackApp._sidebar_projection_keys(app)
        )
        app._on_select = lambda: self.module.ProgTrackApp._on_select(app)
        app._extend_animal_selection_from_graph = (
            lambda key: self.module.ProgTrackApp._extend_animal_selection_from_graph(
                app, key
            )
        )

        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.app = app
        widget.plugin = SimpleNamespace(is_heritage_only=lambda _key: False)
        widget._ghost_nodes = set()
        widget.temp_positions = {}
        widget._force_relayout = False
        widget.refresh_graph = lambda keep_view=True: None

        HeritageTrackWidget._add_animal_to_selection(widget, "Old")

        self.assertEqual(app.selected_animals, ["Visible", "Old"])
        self.assertEqual(app._selected_archived, ["Old"])
        self.assertEqual(
            archived_item.data(Qt.ItemDataRole.UserRole), "__archived__Old"
        )

    def test_delayed_commit_rejects_node_from_replaced_frame(self):
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget._pending_selection = "StaleAnimal"
        widget._pending_selection_timer = object()
        widget._render_cache_entry = SimpleNamespace(
            display_nodes=frozenset({"CurrentAnimal"}),
            positions={"CurrentAnimal": (1.0, 2.0)},
            node_metadata={"CurrentAnimal": {"kind": "animal"}},
        )
        promoted = []
        widget._add_animal_to_selection = promoted.append

        HeritageTrackWidget._commit_pending_selection(widget)

        self.assertEqual(promoted, [])
        self.assertIsNone(widget._pending_selection)
        self.assertIsNone(widget._pending_selection_timer)

    def test_archived_selection_is_restored_only_when_exclusion_is_off(self):
        engine = PedigreeEngine(
            {"Old": {}, "Child": {}},
            lambda name, _record: {"egg_donor": "Old"} if name == "Child" else {},
        )
        engine.build()
        strategy = SelectedAnimalsStrategy()

        self.assertEqual(
            strategy.compute(
                engine,
                ["Old"],
                exclude_archived=False,
                archived_set={"Old"},
            ),
            {"Old"},
        )
        self.assertEqual(
            strategy.compute(
                engine,
                ["Old"],
                exclude_archived=True,
                archived_set={"Old"},
            ),
            set(),
        )
        self.assertEqual(
            strategy.compute(
                engine,
                ["Child"],
                exclude_archived=True,
                archived_set={"Old"},
            ),
            {"Child"},
        )


if __name__ == "__main__":
    unittest.main()
