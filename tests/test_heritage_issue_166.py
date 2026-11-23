"""Focused regression coverage for Heritage Track issue #166.

The repository double below enforces the same compare-and-swap contract as
the production domain-record repository.  It deliberately refuses writes
without an expected revision so a stale full-record save cannot hide in the
test suite.
"""

from __future__ import annotations

import copy
import unittest

from Plugins.Heritage_Track.heritage_store import HeritageStore
from Plugins.core.backend.errors import ConflictError


class _RevisionRecords:
    def __init__(self, initial=None):
        self.values = {}
        self.revisions = {}
        self.put_calls = []
        if initial is not None:
            self.values[("heritage", "graph")] = copy.deepcopy(initial)
            self.revisions[("heritage", "graph")] = 1

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def get_with_revision(self, namespace, record_id, default=None):
        key = (namespace, record_id)
        return copy.deepcopy(self.values.get(key, default)), self.revisions.get(key, 0)

    def put(self, namespace, record_id, payload, *, expected_revision=None):
        key = (namespace, record_id)
        current = self.revisions.get(key, 0)
        self.put_calls.append((key, expected_revision, copy.deepcopy(payload)))
        if expected_revision != current:
            raise ConflictError(
                f"stale revision {expected_revision}; current revision is {current}"
            )
        self.values[key] = copy.deepcopy(payload)
        self.revisions[key] = current + 1
        return current + 1


class _Backend:
    def __init__(self, initial=None):
        self.records = _RevisionRecords(initial)


def _graph():
    return {
        "pedigree_revision": "initial",
        "animals": {
            "Child": {
                "heritage_only": True,
                "dummy_kind": "direct",
                "persistence_kind": "direct_dummy",
                "unit_id": "unit-a",
                "egg_donor": "Egg",
                "sperm_donor": "",
                "surrogate_mother": "",
                "surrogate_father": "",
            },
            "Egg": {
                "heritage_only": True,
                "dummy_kind": "direct",
                "persistence_kind": "direct_dummy",
                "unit_id": "unit-a",
            },
        },
    }


class HeritageIssue166Tests(unittest.TestCase):
    def test_deferred_f_patch_preserves_concurrent_parentage_and_revision(self):
        backend = _Backend(_graph())
        first = HeritageStore("", backend)
        second = HeritageStore("", backend)
        first.load()
        second.load()

        first.set_inbreeding_cache_batch(
            {
                "Child": {
                    "value": 0.25,
                    "pedigree_revision": "initial",
                    "lineage_fingerprint": "fp",
                    "status": "valid",
                }
            },
            persist=False,
        )
        second.set_parentage("Child", {"egg_donor": "Egg", "sperm_donor": "Sperm"})
        second.atomic_update(
            lambda data: data.update({"pedigree_revision": "session-B"})
        )

        first.flush_pending()
        saved = backend.records.get("heritage", "graph")
        self.assertEqual(saved["pedigree_revision"], "session-B")
        self.assertEqual(saved["animals"]["Child"]["sperm_donor"], "Sperm")
        self.assertEqual(saved["animals"]["Child"]["inbreeding_f_cache"]["value"], 0.25)

    def test_overlapping_pending_field_is_rejected_and_remains_pending(self):
        backend = _Backend(_graph())
        first = HeritageStore("", backend)
        second = HeritageStore("", backend)
        first.load()
        second.load()

        first.set_inbreeding_cache_batch(
            {
                "Child": {
                    "value": 0.25,
                    "pedigree_revision": "initial",
                    "lineage_fingerprint": "fp-a",
                    "status": "valid",
                }
            },
            persist=False,
        )
        second.set_inbreeding_cache_batch(
            {
                "Child": {
                    "value": 0.5,
                    "pedigree_revision": "initial",
                    "lineage_fingerprint": "fp-b",
                    "status": "valid",
                }
            }
        )
        with self.assertRaises(ConflictError):
            first.flush_pending()
        self.assertTrue(first.has_pending_changes())
        self.assertEqual(
            backend.records.get("heritage", "graph")["animals"]["Child"]["inbreeding_f_cache"]["value"],
            0.5,
        )

    def test_one_hundred_interleavings_keep_authoritative_and_derived_fields(self):
        for index in range(100):
            backend = _Backend(_graph())
            first = HeritageStore("", backend)
            second = HeritageStore("", backend)
            first.load()
            second.load()
            first.set_inbreeding_cache_batch(
                {
                    "Child": {
                        "value": index / 100.0,
                        "pedigree_revision": "initial",
                        "lineage_fingerprint": f"fp-{index}",
                        "status": "valid",
                    }
                },
                persist=False,
            )
            second.set_parentage(
                "Child",
                {"egg_donor": "Egg", "sperm_donor": f"Sperm-{index}"},
            )
            first.flush_pending()
            saved = backend.records.get("heritage", "graph")
            self.assertEqual(saved["animals"]["Child"]["sperm_donor"], f"Sperm-{index}")
            self.assertEqual(
                saved["animals"]["Child"]["inbreeding_f_cache"]["lineage_fingerprint"],
                f"fp-{index}",
            )

    def test_position_patch_does_not_replace_concurrent_animal_edit(self):
        backend = _Backend(_graph())
        first = HeritageStore("", backend)
        second = HeritageStore("", backend)
        first.load()
        second.load()
        first.set_node_position("Child", (2, 3))
        second.set_parentage("Child", {"egg_donor": "Egg", "sperm_donor": "Sperm"})
        first.set_node_position("Egg", (4, 5))
        saved = backend.records.get("heritage", "graph")
        self.assertEqual(saved["animals"]["Child"]["sperm_donor"], "Sperm")
        self.assertEqual(saved["node_positions"]["Egg"], {"x": 4.0, "y": 5.0})

    def test_settings_patch_does_not_replace_concurrent_dummy_edit(self):
        backend = _Backend(_graph())
        first = HeritageStore("", backend)
        second = HeritageStore("", backend)
        first.load()
        second.load()
        first.set_settings({"show_grid": True})
        second.set_heritage_only("Child", True)
        first.set_settings({"show_legend": False})
        saved = backend.records.get("heritage", "graph")
        self.assertTrue(saved["animals"]["Child"]["heritage_only"])
        self.assertFalse(saved["settings"]["show_legend"])

    def test_same_client_atomic_mutation_preserves_queued_derived_patch(self):
        backend = _Backend(_graph())
        store = HeritageStore("", backend)
        store.load()
        store.set_inbreeding_cache_batch(
            {
                "Child": {
                    "value": 0.25,
                    "pedigree_revision": "initial",
                    "lineage_fingerprint": "fp-same-client",
                    "status": "valid",
                }
            },
            persist=False,
        )
        store.atomic_update(
            lambda data: data.setdefault("settings", {}).update({"show_grid": True})
        )
        saved = backend.records.get("heritage", "graph")
        self.assertTrue(saved["settings"]["show_grid"])
        self.assertEqual(
            saved["animals"]["Child"]["inbreeding_f_cache"]["value"],
            0.25,
        )
        self.assertFalse(store.has_pending_changes())


if __name__ == "__main__":
    unittest.main()
