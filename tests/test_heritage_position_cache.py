"""Focused regression tests for Heritage Track issue #161."""

from __future__ import annotations

import copy
import unittest

from Plugins.Heritage_Track.heritage_store import HeritageStore


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

    def test_cache_write_preserves_queued_derived_changes(self):
        backend = _Backend({"animals": {"A": {}}})
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
