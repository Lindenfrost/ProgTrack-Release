"""Targeted regression tests for issue #127's render-cache contract."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from copy import deepcopy

from Plugins.Heritage_Track.display_context import (
    DisplayContext,
    FrozenRoutePlan,
    RenderCacheEntry,
    RenderCacheKey,
    RenderCacheRegistry,
)
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine
from Plugins.Heritage_Track.pedigree_router import RoutePlan
from Plugins.Heritage_Track.pedigree_router import PedigreeRouter
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackWidget
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackPlugin


def _entry(*dependencies: str) -> RenderCacheEntry:
    engine = PedigreeEngine({"A": {}}, lambda _name, _record: {})
    engine.build()
    plan = RoutePlan(
        animal_positions={"A": (0.0, 0.0)},
        family_positions={},
        family_members={},
        routes={},
    )
    key = RenderCacheKey.create("user", ["A"], "selected", "focused:partner_normalized")
    return RenderCacheEntry(
        cache_key=key,
        core_projection_revision="core",
        pedigree_f_revision="f",
        engine_resolution_revision="engine",
        logical_layout_revision="layout",
        dependencies=set(dependencies),
        record_index={"A": {"name": "A"}},
        canonical_selection=("A",),
        selection_type="selected",
        display_mode="focused:partner_normalized",
        effective_parent_map={},
        display_nodes={"A"},
        ghost_nodes=set(),
        levels={"A": 0},
        family_nodes={},
        family_members={},
        positions={"A": (0.0, 0.0)},
        locked_positions={},
        route_plan=plan,
        obstacles={"A": "A"},
        bounds=((-1.0, 1.0), (-1.0, 1.0)),
        f_values={"A": 0.0},
        f_status={"A": "calculated"},
    )


class HeritageRenderCacheTest(unittest.TestCase):
    def test_display_context_freezes_nested_collections(self):
        engine = PedigreeEngine({"A": {}}, lambda _name, _record: {})
        engine.build()
        context = DisplayContext(
            engine=engine,
            display_nodes={"A"},
            levels={"A": 0},
            family_nodes={"family": {"children": ["A"]}},
        )
        with self.assertRaises(TypeError):
            context.levels["A"] = 1
        with self.assertRaises(TypeError):
            context.family_nodes["family"]["children"] = ()
        self.assertEqual(context.get_visible_nodes(), {"A"})

    def test_entry_and_route_plan_are_deeply_immutable(self):
        source = _entry("A", "Parent")
        entry = source
        self.assertIsInstance(entry.route_plan, FrozenRoutePlan)
        with self.assertRaises(TypeError):
            entry.positions["A"] = (1.0, 1.0)
        with self.assertRaises(TypeError):
            entry.route_plan.animal_positions["A"] = (1.0, 1.0)
        self.assertEqual(entry.revision_tuple, ("core", "f", "engine", "layout"))
        self.assertEqual(entry.schema_version, "heritage-render-cache.v1")
        self.assertTrue(entry.valid)

        mutable = entry.route_plan.to_mutable()
        mutable.animal_positions["A"] = (99.0, 99.0)
        self.assertEqual(entry.route_plan.animal_positions["A"], (0.0, 0.0))

    def test_cache_key_canonicalizes_selection_and_keeps_user_scope(self):
        first = RenderCacheKey.create("alice", ["B", " A ", "B"], "selected", "focused")
        second = RenderCacheKey.create("alice", ["A", "B"], "selected", "focused")
        other_user = RenderCacheKey.create("bob", ["A", "B"], "selected", "focused")
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_user)

    def test_registry_invalidates_all_frames_referencing_changed_dependency(self):
        registry = RenderCacheRegistry()
        first = _entry("A", "Parent")
        second = RenderCacheEntry(
            **{
                **first.__dict__,
                "cache_key": RenderCacheKey.create("user", ["B"], "selected", "focused"),
                "canonical_selection": ("B",),
                "dependencies": frozenset({"B", "Parent"}),
            }
        )
        registry.put(first)
        registry.put(second)
        self.assertEqual(len(registry), 2)
        self.assertEqual(registry.invalidate({"Parent"}), 2)
        self.assertEqual(len(registry), 0)

    def test_registry_rejects_stale_revision_before_consumption(self):
        registry = RenderCacheRegistry()
        entry = _entry("A")
        registry.put(entry)
        self.assertIsNone(
            registry.get_valid(
                entry.cache_key,
                revisions=("changed", "f", "engine", "layout"),
            )
        )
        self.assertEqual(len(registry), 0)

    def test_non_finite_geometry_is_rejected_before_paint(self):
        plan = RoutePlan(
            animal_positions={"A": (float("nan"), 0.0)},
            family_positions={},
            family_members={},
            routes={},
        )
        diagnostics = HeritageTrackWidget._render_geometry_fatal_diagnostics(
            plan, plan.animal_positions
        )
        self.assertTrue(diagnostics)
        self.assertIn("non-finite", diagnostics[0])

    def test_entry_freezes_paint_metadata_and_source_token(self):
        entry = RenderCacheEntry(
            **{
                **_entry("A").__dict__,
                "source_revision": "source-1",
                "node_metadata": {"A": {"shape": "o", "detail_text": "F: 0.0000"}},
            }
        )
        self.assertEqual(entry.source_revision, "source-1")
        with self.assertRaises(TypeError):
            entry.node_metadata["A"]["shape"] = "^"

    def test_render_inbreeding_does_not_queue_backend_writes(self):
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        writes = []

        class Store:
            def get_inbreeding_cache(self, node, *, snapshot=None):
                return None

            def set_inbreeding_cache_batch(self, updates, *, persist=False):
                writes.append((updates, persist))
                return True

        class Engine:
            animals = {"A": {"name": "A"}}
            heritage_entries = {}

            def get_genetic_parent_map(self):
                return {"A": (None, None)}

        widget.plugin = SimpleNamespace(store=Store())
        widget._render_store_animals = {}
        widget._render_transaction_active = True
        widget._cycle_nodes = set()
        widget._malformed_f_nodes = set()
        values, status = widget._compute_inbreeding_state(
            Engine(), {"A"}, show_f=True
        )
        self.assertEqual(values["A"], 0.0)
        self.assertEqual(status["A"], "calculated")
        self.assertEqual(writes, [])

    def test_render_ownership_classification_uses_captured_snapshot(self):
        calls = []

        class Backend:
            def load_core_data(self):
                calls.append("backend")
                return {"animals": {"Core": {"name": "Core"}}}

        plugin = HeritageTrackPlugin.__new__(HeritageTrackPlugin)
        plugin.app = SimpleNamespace(backend=Backend(), animals={}, archived={})
        plugin._active_core_snapshot = {"Core": {"name": "Core"}}
        plugin._active_store_snapshot = {
            "animals": {
                "Dummy": {
                    "name": "Dummy",
                    "heritage_only": True,
                    "dummy_kind": "direct",
                    "persistence_kind": "direct_dummy",
                }
            }
        }
        plugin._temporary_dummies = {}
        plugin._store_snapshot_entries = HeritageTrackPlugin._store_snapshot_entries.__get__(plugin)
        plugin._current_core_records = HeritageTrackPlugin._current_core_records.__get__(plugin)
        self.assertTrue(plugin.is_heritage_only("Dummy", fresh=False))
        self.assertFalse(plugin.is_heritage_only("Core", fresh=False))
        self.assertEqual(calls, [])
        self.assertFalse(plugin.is_heritage_only("Core", fresh=True))
        self.assertEqual(calls, ["backend"])

    def test_render_source_revision_ignores_position_bookkeeping_but_tracks_depth(self):
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.settings = {
            "exclude_archived": False,
            "animal_label_detail": "inbreeding_f",
            "vertical_layout_mode": "partner_normalized",
            "show_grid": False,
            "show_legend": True,
        }
        widget.layout_mode = "focused"
        widget.selected_nodes = set()
        widget.plugin = SimpleNamespace(_temporary_dummies={})
        widget._max_generations = 3
        widget._render_revision = HeritageTrackWidget._render_revision
        core = {"child": {"species": "Callithrix jacchus"}}
        store = {
            "animals": {},
            "position_cache": {"alice": {"selection": {"positions": {"child": {"x": 1}}}}},
            "node_positions": {"child": [1, 2]},
            "updated_at": "old",
        }
        first = widget._render_source_revision(
            core_snapshot=core,
            raw_store=store,
            backend_revision=1,
            selected_animals=["child"],
            chronological_mode=False,
        )
        changed_positions = deepcopy(store)
        changed_positions["position_cache"]["alice"]["selection"]["positions"]["child"]["x"] = 99
        changed_positions["node_positions"]["child"] = [99, 99]
        changed_positions["updated_at"] = "new"
        second = widget._render_source_revision(
            core_snapshot=core,
            raw_store=changed_positions,
            backend_revision=999,
            selected_animals=["child"],
            chronological_mode=False,
        )
        self.assertEqual(first, second)
        widget._max_generations = 2
        self.assertNotEqual(
            first,
            widget._render_source_revision(
                core_snapshot=core,
                raw_store=store,
                backend_revision=1,
                selected_animals=["child"],
                chronological_mode=False,
            ),
        )

    def test_position_cache_identity_changes_with_generation_depth(self):
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.settings = {
            "vertical_layout_mode": "chronological",
            "show_heritage_only": True,
            "exclude_archived": False,
        }
        widget.layout_mode = "focused"
        widget._canonical_selection_ids = ("child",)
        widget._canonicalize_selection = lambda values, **_kwargs: tuple(values)
        widget._max_generations = 1
        first = widget._position_cache_key(["child"])
        widget._max_generations = 3
        second = widget._position_cache_key(["child"])
        self.assertNotEqual(first, second)

    def test_final_widget_collision_boundary_uses_router_obstacles(self):
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget._pedigree_router = PedigreeRouter()
        overlapping = widget._render_node_collision_diagnostics(
            {"A": (0.0, 0.0), "B": (0.0, 0.0)},
            {"A": "A", "B": "B"},
            show_inbreeding=False,
        )
        self.assertEqual(overlapping, ["A/B: animal markers or labels overlap"])
        clear = widget._render_node_collision_diagnostics(
            {"A": (0.0, 0.0), "B": (2.0, 0.0)},
            {"A": "A", "B": "B"},
            show_inbreeding=False,
        )
        self.assertEqual(clear, [])

    def test_artist_measurement_ignores_suppressed_annotation_at_axes_origin(self):
        """A clipped Annotation must not become Bbox.unit() at pixel origin."""
        from matplotlib.backends.backend_agg import FigureCanvasAgg, RendererAgg
        from matplotlib.figure import Figure

        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        figure = Figure(figsize=(2, 2), dpi=100)
        canvas = FigureCanvasAgg(figure)
        widget.ax = figure.add_axes([0.0, 0.0, 1.0, 1.0])
        widget.ax.set_xlim(0.0, 1.0)
        widget.ax.set_ylim(0.0, 1.0)
        suppressed = widget.ax.annotate(
            "suppressed", xy=(-1.0, -1.0), annotation_clip=True,
        )
        first = widget.ax.annotate("same", xy=(0.5, 0.5), annotation_clip=True)
        second = widget.ax.annotate("same", xy=(0.5, 0.5), annotation_clip=True)
        widget.node_meta = {
            "suppressed": {"label_artist": suppressed},
            "first": {"label_artist": first},
            "second": {"label_artist": second},
        }
        canvas.draw()
        renderer = canvas.get_renderer()
        automatic_fatal = widget._render_artist_fatal_diagnostics(
            renderer, check_viewport=True, check_collisions=True,
        )
        self.assertTrue(any("suppressed/label_artist" in item for item in automatic_fatal))
        self.assertTrue(any("first/label_artist/second/label_artist" in item for item in automatic_fatal))
        manual_fatal = widget._render_artist_fatal_diagnostics(
            renderer, check_viewport=False, check_collisions=True,
        )
        self.assertFalse(any("suppressed/label_artist" in item for item in manual_fatal))
        self.assertTrue(any("first/label_artist/second/label_artist" in item for item in manual_fatal))


if __name__ == "__main__":
    unittest.main()
