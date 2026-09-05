"""Focused regression tests for Heritage Track issue #159."""

from __future__ import annotations

import copy
import math
import unittest

from Plugins.Heritage_Track.display_context import (
    RenderCacheEntry,
    RenderCacheKey,
)
from Plugins.Heritage_Track.heritage_store import HeritageStore
from Plugins.Heritage_Track.layout_pipeline import LayoutPipeline
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine
from Plugins.Heritage_Track.pedigree_router import (
    GeometryValidationError,
    PedigreeRouter,
    RoutePlan,
)


class _MemoryRecords:
    def __init__(self, initial=None):
        self.values = copy.deepcopy(initial or {})
        self.put_count = 0
        self.fail_put = False

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def put(self, namespace, record_id, payload, **_kwargs):
        if self.fail_put:
            raise OSError("simulated backend failure")
        self.put_count += 1
        self.values[(namespace, record_id)] = copy.deepcopy(payload)
        return self.put_count


class _Backend:
    def __init__(self, graph=None):
        self.records = _MemoryRecords(
            {("heritage", "graph"): graph} if graph is not None else None
        )


def _empty_plan(**overrides):
    values = {
        "animal_positions": {"A": (0.0, 0.0)},
        "family_positions": {},
        "family_members": {},
        "routes": {},
    }
    values.update(overrides)
    return RoutePlan(**values)


class HeritageNonFiniteStoreTest(unittest.TestCase):
    def test_load_filters_all_nonfinite_and_comma_decimal_values_without_write(self):
        graph = {
            "node_positions": {
                "finite": ["1.5", "-2"],
                "nan": [float("nan"), 0.0],
                "positive_inf": [float("inf"), 0.0],
                "negative_inf": [0.0, float("-inf")],
                "comma": ["1,5", 0.0],
                "malformed": ["not-a-number", 0.0],
            }
        }
        backend = _Backend(graph)
        store = HeritageStore("", backend)

        self.assertEqual(store.load()["node_positions"], {"finite": {"x": 1.5, "y": -2.0}})
        self.assertEqual(store.get_node_positions(), {"finite": (1.5, -2.0)})
        self.assertEqual(
            set(store.get_invalid_node_positions()),
            {"nan", "positive_inf", "negative_inf", "comma", "malformed"},
        )
        self.assertEqual(backend.records.put_count, 0)

    def test_unrelated_save_preserves_invalid_until_explicit_cleanup(self):
        graph = {"node_positions": {"bad": [float("inf"), 0.0]}}
        backend = _Backend(graph)
        store = HeritageStore("", backend)
        store.load()
        store.set_settings({"show_grid": True})

        persisted = backend.records.values[("heritage", "graph")]
        self.assertIn("bad", persisted["node_positions"])
        self.assertEqual(set(store.get_invalid_node_positions()), {"bad"})

        removed = store.cleanup_invalid_node_positions()
        self.assertEqual(removed, 1)
        self.assertEqual(backend.records.values[("heritage", "graph")]["node_positions"], {})
        self.assertEqual(store.get_invalid_node_positions(), {})

    def test_malformed_position_container_is_preserved_until_cleanup(self):
        graph = {"node_positions": ["not", "a", "mapping"]}
        backend = _Backend(graph)
        store = HeritageStore("", backend)
        store.load()
        store.set_settings({"show_grid": True})
        self.assertEqual(
            backend.records.values[("heritage", "graph")]["node_positions"],
            ["not", "a", "mapping"],
        )
        self.assertEqual(store.cleanup_invalid_node_positions(), 1)
        self.assertEqual(backend.records.values[("heritage", "graph")]["node_positions"], {})

    def test_cleanup_failure_restores_view_and_reports_to_caller(self):
        backend = _Backend({"node_positions": {"bad": [float("nan"), 0.0]}})
        store = HeritageStore("", backend)
        store.load()
        backend.records.fail_put = True

        with self.assertRaises(OSError):
            store.cleanup_invalid_node_positions()
        self.assertEqual(set(store.get_invalid_node_positions()), {"bad"})
        self.assertEqual(backend.records.put_count, 0)

    def test_cleanup_without_invalid_coordinates_preserves_local_view(self):
        backend = _Backend({"node_positions": {"good": [1.0, 2.0]}})
        store = HeritageStore("", backend)
        view = store.load()
        # Simulate an unsaved local draft held by an active widget.  A no-op
        # cleanup must not replace that object with a normalized backend copy.
        view["settings"]["show_grid"] = True
        before_cache = store._genotype_colors_cache

        self.assertEqual(store.cleanup_invalid_node_positions(), 0)
        self.assertIs(store._data, view)
        self.assertTrue(store._data["settings"]["show_grid"])
        self.assertEqual(store.get_invalid_node_positions(), {})
        self.assertIs(store._genotype_colors_cache, before_cache)
        self.assertEqual(backend.records.put_count, 0)

    def test_position_batch_rejects_atomically(self):
        backend = _Backend()
        store = HeritageStore("", backend)
        store.set_node_position("A", (1.0, 2.0))
        writes = backend.records.put_count

        with self.assertRaises(ValueError):
            store.set_node_positions_batch(
                {"A": (3.0, 4.0), "B": (float("inf"), 5.0)}
            )
        self.assertEqual(store.get_node_positions(), {"A": (1.0, 2.0)})
        self.assertEqual(backend.records.put_count, writes)


class HeritageNonFiniteGeometryTest(unittest.TestCase):
    def test_layout_and_router_reject_nonfinite_inputs(self):
        engine = PedigreeEngine({"A": {}}, lambda _name, _record: {})
        engine.build()
        with self.assertRaises(GeometryValidationError):
            LayoutPipeline().compute_positions(
                {"A"}, {"A": 0}, engine, locked_positions={"A": (float("nan"), 0.0)}
            )
        with self.assertRaises(GeometryValidationError):
            PedigreeRouter().plan({"A": (0.0, float("-inf"))}, {})

    def test_router_rejects_nonfinite_gap_on_recompute(self):
        router = PedigreeRouter()
        plan = router.plan(
            {"Parent": (0.0, 1.0), "Child": (0.0, 0.0)},
            {"family": {"mother": "Parent", "father": "", "children": ["Child"]}},
        )
        plan.line_crossings_ready = True
        plan.line_crossing_gaps[("family", "Child", 0)] = [(float("nan"), 0.0)]
        with self.assertRaises(GeometryValidationError):
            router.recompute_line_gaps(plan, recompute_crossings=False)

    def test_render_cache_rejects_family_bounds_and_gap_nonfinite_geometry(self):
        plan = _empty_plan(
            family_positions={"family": (float("inf"), 0.0)},
            crossing_gaps={("family", "A", 0): [(0.0, float("nan"))]},
        )
        entry = RenderCacheEntry(
            cache_key=RenderCacheKey.create("user", ["A"], "selected", "focused"),
            core_projection_revision="core",
            pedigree_f_revision="f",
            engine_resolution_revision="engine",
            logical_layout_revision="layout",
            dependencies=frozenset({"A"}),
            record_index={"A": {}},
            canonical_selection=("A",),
            selection_type="selected",
            display_mode="focused",
            effective_parent_map={},
            display_nodes=frozenset({"A"}),
            ghost_nodes=frozenset(),
            levels={"A": 0},
            family_nodes={},
            family_members={},
            positions={"A": (0.0, 0.0)},
            locked_positions={},
            route_plan=plan,
            obstacles={},
            bounds=((float("-inf"), 1.0), (-1.0, 1.0)),
        )
        self.assertFalse(entry.valid)
        self.assertTrue(any("non-finite" in item for item in entry.fatal_diagnostics))
        self.assertTrue(any(math.isnan(point[1]) for point in plan.all_points() if len(point) == 2))


if __name__ == "__main__":
    unittest.main()
