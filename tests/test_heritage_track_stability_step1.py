from __future__ import annotations

import copy
import importlib
import time
import unittest
import pytest

from Plugins.Heritage_Track.heritage_store import HeritageStore
from Plugins.Heritage_Track.inbreeding import InbreedingCalculator
from Plugins.Heritage_Track.pedigree_router import PedigreeRouter
from Plugins.Heritage_Track.display_strategies import SelectedAnimalsStrategy
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine
from Plugins.core.backend.errors import ConflictError


class _MemoryRecords:
    def __init__(self):
        self.values = {}
        self.revisions = {}
        self.put_count = 0

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def get_with_revision(self, namespace, record_id, default=None):
        key = (namespace, record_id)
        return copy.deepcopy(self.values.get(key, default)), self.revisions.get(key, 0)

    def put(self, namespace, record_id, payload, *, expected_revision=None):
        key = (namespace, record_id)
        current = self.revisions.get(key, 0)
        if expected_revision is not None and expected_revision != current:
            raise ConflictError(
                f"stale revision {expected_revision}; current revision is {current}"
            )
        self.put_count += 1
        self.values[key] = copy.deepcopy(payload)
        self.revisions[key] = current + 1
        return current + 1


class _Backend:
    def __init__(self):
        self.records = _MemoryRecords()


class HeritageStoreStabilityStep1Test(unittest.TestCase):
    def test_read_does_not_create_backend_record(self):
        backend = _Backend()
        store = HeritageStore('', backend)
        store.load()
        self.assertEqual(backend.records.put_count, 0)

    def test_core_sync_hook_is_read_only(self):
        backend = _Backend()
        store = HeritageStore('', backend)
        store.load()
        changed = store.sync_from_animals(
            {'Child': {'eizellspenderin': 'Mother', 'samenspender': 'Father', 'sex': 'female'}},
            persist=False,
        )
        self.assertFalse(changed)
        self.assertFalse(store.has_pending_changes())
        self.assertEqual(backend.records.put_count, 0)

    def test_normalized_backend_record_is_not_rewritten_when_unchanged(self):
        backend = _Backend()
        first = HeritageStore('', backend)
        first.set_settings({'show_grid': True})
        writes = backend.records.put_count
        second = HeritageStore('', backend)
        second.load()
        self.assertEqual(backend.records.put_count, writes)

    def test_f_batch_skips_equal_values(self):
        backend = _Backend()
        store = HeritageStore('', backend)
        store.set_inbreeding_f_batch({'Child': 0.25})
        writes = backend.records.put_count
        self.assertFalse(store.set_inbreeding_f_batch({'Child': 0.25}))
        self.assertEqual(backend.records.put_count, writes)

    def test_memory_records_enforces_create_and_update_revisions(self):
        records = _MemoryRecords()
        self.assertEqual(records.put("ns", "id", {"v": 1}, expected_revision=0), 1)
        self.assertEqual(records.put("ns", "id", {"v": 2}, expected_revision=1), 2)
        with self.assertRaises(ConflictError):
            records.put("ns", "id", {"v": 3}, expected_revision=1)
        self.assertEqual(records.get_with_revision("ns", "id"), ({"v": 2}, 2))
        with self.assertRaises(ConflictError):
            records.put("other", "id", {"v": 1}, expected_revision=1)

    def test_memory_records_preserves_unrelated_records_and_returns_copies(self):
        records = _MemoryRecords()
        records.put("ns", "one", {"nested": {"v": 1}}, expected_revision=0)
        records.put("ns", "two", {"v": 2}, expected_revision=0)
        value, revision = records.get_with_revision("ns", "one")
        value["nested"]["v"] = 99
        self.assertEqual(records.get("ns", "one")["nested"]["v"], 1)
        records.put("ns", "one", value, expected_revision=revision)
        self.assertEqual(records.get_with_revision("ns", "two"), ({"v": 2}, 1))

    def test_empty_display_strategy_does_not_fall_back_to_all_nodes(self):
        engine = PedigreeEngine({'A': {}, 'B': {}}, lambda _name, _record: {})
        engine.build()
        self.assertEqual(SelectedAnimalsStrategy().compute(engine, []), set())

    def test_cyclic_pedigree_is_explicitly_diagnosed(self):
        calculator = InbreedingCalculator({
            'A': ('B', None),
            'B': ('A', None),
            'Self': ('Self', None),
        })
        self.assertEqual(calculator.cycle_nodes, {'A', 'B', 'Self'})
        self.assertEqual(calculator.self_inbreeding_F('A'), 0.0)

    def test_remaining_plugin_surface_imports_after_legacy_cleanup(self):
        module = importlib.import_module('Plugins.Heritage_Track')
        for name in (
            'HeritageTrackPlugin',
            'LayoutPipeline',
            'PedigreeRouter',
            'RoutePlan',
            'SelectedAnimalsStrategy',
        ):
            self.assertTrue(hasattr(module, name), name)
        for removed in (
            'ForceDirectedLayout',
            'EdgeBundler',
            'FamilyPositionOptimizer',
            'AdaptiveSpacer',
            'ViewportManager',
            'AllAnimalsStrategy',
            'ScopeGhostStrategy',
        ):
            self.assertFalse(hasattr(module, removed), removed)

    @pytest.mark.non_blocking_benchmark
    def test_synthetic_1000_node_router_budget(self):
        from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine
        from Plugins.Heritage_Track.pedigree_router import PedigreeRouter

        count = 1000
        animals = {f'N{index}': {} for index in range(count)}
        parent_map = {
            f'N{index}': {'egg_donor': f'N{index - 1}'}
            for index in range(1, count)
        }
        engine = PedigreeEngine(
            animals,
            lambda node, _record: parent_map.get(node, {}),
        )
        engine.build()
        # Keep the 1000-node stress graph without bypassing the UI's
        # three-generation ancestor budget: each explicit lower selection
        # extends the next local three-step window.
        selected = [f'N{index}' for index in range(count - 1, -1, -3)]
        nodes = engine.get_display_nodes(selected, max_generations=3)
        families = {
            f'family::{index}': {
                'mother': f'N{index - 1}',
                'father': '',
                'children': [f'N{index}'],
            }
            for index in range(1, count)
        }
        positions = {name: (0.0, float(count - index)) for index, name in enumerate(nodes)}
        started = time.perf_counter()
        plan = PedigreeRouter().plan(
            positions,
            families,
            focus_nodes={'N999'},
            show_inbreeding=False,
        )
        elapsed = time.perf_counter() - started
        self.assertEqual(plan.unresolved, [])
        self.assertEqual(len(plan.routes), count - 1)
        if elapsed >= 20.0:
            pytest.xfail(
                f'non-blocking benchmark: router took {elapsed:.3f}s '
                'against the 20.000s reference target; routing output is valid'
            )

    def test_route_plan_revision_invalidates_structural_gaps(self):
        router = PedigreeRouter()
        plan = router.plan(
            {'Parent': (0.0, 1.0), 'Child': (0.0, 0.0)},
            {'family::parent': {'mother': 'Parent', 'father': '', 'children': ['Child']}},
        )
        initial_revision = plan.geometry_revision
        initial_gap_revision = plan.gap_geometry_revision
        self.assertEqual(initial_gap_revision, initial_revision)
        plan.animal_positions['Child'] = (1.0, 0.0)
        plan.mark_geometry_changed()
        self.assertGreater(plan.geometry_revision, initial_revision)
        self.assertFalse(plan.line_crossings_ready)
        self.assertEqual(plan.gap_geometry_revision, -1)
        router.recompute_line_gaps(plan)
        self.assertEqual(plan.gap_geometry_revision, plan.geometry_revision)
        self.assertGreater(plan.pixel_gap_revision, 0)


if __name__ == '__main__':
    unittest.main()
