"""Focused regression tests for Heritage Track issue #161."""

from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from Plugins.Heritage_Track.heritage_store import HeritageStore
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackWidget


class _Records:
    def __init__(self, graph=None):
        self.values = {("heritage", "graph"): copy.deepcopy(graph)} if graph is not None else {}
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
        self.records = _Records(graph)


class HeritagePositionCacheTest(unittest.TestCase):
    def test_cache_is_scoped_by_user_and_selection_and_keeps_legacy_map_separate(self):
        backend = _Backend({"node_positions": {"legacy": [99, 99]}})
        store = HeritageStore("", backend)
        store.set_position_cache_entry("alice", "selection-a", {"A": (1, 2)}, "rev-1", ["A"])
        store.set_position_cache_entry("alice", "selection-b", {"B": (3, 4)}, "rev-1", ["B"])
        store.set_position_cache_entry("bob", "selection-a", {"A": (8, 9)}, "rev-1", ["A"])

        self.assertEqual(
            store.get_position_cache_entry("alice", "selection-a", pedigree_revision="rev-1", dependency_ids=["A"])["positions"]["A"],
            {"x": 1.0, "y": 2.0},
        )
        self.assertIsNone(store.get_position_cache_entry("bob", "selection-b"))
        self.assertEqual(store.get_node_positions()["legacy"], (99.0, 99.0))

    def test_revision_and_dependency_mismatch_are_read_only_cache_misses(self):
        backend = _Backend()
        store = HeritageStore("", backend)
        store.set_position_cache_entry("alice", "key", {"A": (1, 2)}, "rev-1", ["A", "Parent"])
        writes = backend.records.put_count

        self.assertIsNone(store.get_position_cache_entry("alice", "key", pedigree_revision="rev-2"))
        self.assertIsNone(store.get_position_cache_entry("alice", "key", dependency_ids=["A"]))
        self.assertEqual(backend.records.put_count, writes)

    def test_dependency_revision_ignores_disjoint_component_changes(self):
        backend = _Backend()
        store = HeritageStore("", backend)
        parent_map = {
            "A": {"egg_donor": "P", "sperm_donor": ""},
            "P": {"egg_donor": "", "sperm_donor": ""},
        }
        records = {
            "A": {"name": "A", "species": "Callithrix jacchus"},
            "P": {"name": "P", "species": "Callithrix jacchus"},
        }
        revision_a = store.build_position_dependency_revision(
            ["A", "P"], parent_map, records
        )
        changed_disjoint = dict(records)
        changed_disjoint["B"] = {"name": "B", "species": "Mus musculus"}
        revision_a_after_disjoint_edit = store.build_position_dependency_revision(
            ["A", "P"], parent_map, changed_disjoint
        )
        self.assertEqual(revision_a, revision_a_after_disjoint_edit)

        changed_dependency = dict(records)
        changed_dependency["P"] = {
            "name": "P",
            "species": "Callithrix jacchus",
            "genotype": "WT/WT",
        }
        revision_after_dependency_edit = store.build_position_dependency_revision(
            ["A", "P"], parent_map, changed_dependency
        )
        self.assertNotEqual(revision_a, revision_after_dependency_edit)

    def test_position_entry_uses_dependency_revision_not_aggregate_revision(self):
        backend = _Backend()
        store = HeritageStore("", backend)
        scoped_revision = store.build_position_dependency_revision(
            ["A"], {"A": {"egg_donor": "", "sperm_donor": ""}}, {"A": {"name": "A"}}
        )
        store.set_position_cache_entry(
            "alice", "key", {"A": (1, 2)}, scoped_revision, ["A"]
        )
        # The aggregate pedigree token may advance for an unrelated graph
        # write; callers validate the scoped token instead.
        latest = copy.deepcopy(backend.records.values[("heritage", "graph")])
        latest["pedigree_revision"] = "aggregate-rev-2"
        latest["animals"] = {"B": {"name": "B"}}
        backend.records.put("heritage", "graph", latest)
        self.assertIsNotNone(
            store.get_position_cache_entry(
                "alice", "key", pedigree_revision=scoped_revision, dependency_ids=["A"]
            )
        )

    def test_selection_key_is_stable_across_backend_revisions(self):
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget._canonical_selection_ids = ()
        widget._canonicalize_selection = lambda values: tuple(sorted(values))
        widget.layout_mode = "focused"
        widget.settings = {
            "vertical_layout_mode": "partner_normalized",
            "show_heritage_only": True,
            "exclude_archived": False,
        }
        widget._max_generations = 4
        widget.plugin = SimpleNamespace(
            _active_backend_revision=1,
            _active_core_projection_revision="core-a",
        )
        first = HeritageTrackWidget._position_cache_key(widget, ["B", "A"])
        widget.plugin._active_backend_revision = 27
        widget.plugin._active_core_projection_revision = "core-b"
        second = HeritageTrackWidget._position_cache_key(widget, ["A", "B"])
        self.assertEqual(first, second)

    def test_nonfinite_cache_position_is_rejected_before_backend_write(self):
        backend = _Backend()
        store = HeritageStore("", backend)
        with self.assertRaises(ValueError):
            store.set_position_cache_entry("alice", "key", {"A": (float("inf"), 0)}, "rev", ["A"])
        self.assertEqual(backend.records.put_count, 0)

    def test_failed_replace_keeps_previous_entry(self):
        backend = _Backend()
        store = HeritageStore("", backend)
        store.set_position_cache_entry("alice", "key", {"A": (1, 2)}, "rev-1", ["A"])
        backend.records.fail_put = True
        with self.assertRaises(OSError):
            store.set_position_cache_entry("alice", "key", {"A": (7, 8)}, "rev-2", ["A"])
        backend.records.fail_put = False
        self.assertEqual(
            store.get_position_cache_entry("alice", "key")["positions"]["A"],
            {"x": 1.0, "y": 2.0},
        )

    def test_concurrent_remove_noop_does_not_advance_backend_revision(self):
        backend = _Backend()
        first = HeritageStore("", backend)
        first.set_position_cache_entry("alice", "key", {"A": (1, 2)}, "rev-1", ["A"])
        second = HeritageStore("", backend)
        # Load the entry into the first session, then remove it from the
        # second session.  The first removal is now a stale no-op and must not
        # produce an extra backend write or revision bump.
        self.assertIsNotNone(first.get_position_cache_entry("alice", "key"))
        self.assertTrue(second.remove_position_cache_entry("alice", "key"))
        writes_after_second = backend.records.put_count
        self.assertFalse(first.remove_position_cache_entry("alice", "key"))
        self.assertEqual(backend.records.put_count, writes_after_second)
        self.assertIsNone(first.get_position_cache_entry("alice", "key"))

    def test_cache_write_preserves_queued_derived_changes(self):
        backend = _Backend({"animals": {"A": {
            "heritage_only": True,
            "dummy_kind": "direct",
            "persistence_kind": "direct_dummy",
            "unit_id": "unit-a",
        }}})
        store = HeritageStore("", backend)
        store.set_node_position("A", (1, 2))
        store.set_inbreeding_cache_batch(
            {
                "A": {
                    "value": 0.25,
                    "pedigree_revision": "rev",
                    "lineage_fingerprint": "fp",
                    "status": "valid",
                }
            },
            persist=False,
        )
        store.set_position_cache_entry("alice", "key", {"A": (3, 4)}, "rev", ["A"])
        persisted = backend.records.values[("heritage", "graph")]
        self.assertEqual(persisted["animals"]["A"]["inbreeding_f_cache"]["value"], 0.25)
        self.assertEqual(persisted["position_cache"]["alice"]["key"]["positions"]["A"]["x"], 3.0)

    def test_dependency_invalidation_is_bounded_and_does_not_write_when_unaffected(self):
        backend = _Backend()
        store = HeritageStore("", backend)
        store.set_position_cache_entry("alice", "a", {"A": (1, 2)}, "rev", ["A"])
        store.set_position_cache_entry("alice", "b", {"B": (3, 4)}, "rev", ["B"])
        writes = backend.records.put_count
        self.assertEqual(store.invalidate_position_cache_dependencies(["unrelated"]), 0)
        self.assertEqual(backend.records.put_count, writes)
        self.assertEqual(store.invalidate_position_cache_dependencies(["A"]), 1)
        self.assertIsNone(store.get_position_cache_entry("alice", "a"))
        self.assertIsNotNone(store.get_position_cache_entry("alice", "b"))

    def test_cache_is_limited_to_one_thousand_entries(self):
        existing = {
            f"key-{index:04d}": {
                "pedigree_revision": "rev",
                "dependency_revision": "rev",
                "dependency_ids": ["A"],
                "positions": {"A": {"x": index, "y": index}},
                "selection_type": "selected",
                "updated_at": f"2026-01-01T00:00:{index % 60:02d}.{index // 60:03d}Z",
            }
            for index in range(1000)
        }
        backend = _Backend({"position_cache": {"alice": existing}})
        store = HeritageStore("", backend)
        store.set_position_cache_entry("alice", "key-1000", {"A": (1000, 1000)}, "rev", ["A"])
        entries = backend.records.values[("heritage", "graph")]["position_cache"]["alice"]
        self.assertEqual(len(entries), store.POSITION_CACHE_LIMIT)
        self.assertNotIn("key-0000", entries)
        self.assertIn("key-1000", entries)


if __name__ == "__main__":
    unittest.main()
