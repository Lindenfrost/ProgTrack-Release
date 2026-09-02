"""Regression tests for issue #155 Core-first Heritage resolution."""

from __future__ import annotations

import copy
import unittest

from Plugins.Heritage_Track.engine_cache import PedigreeEngineCache
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackPlugin
from Plugins.Heritage_Track.heritage_store import HeritageStore


class _MemoryRecords:
    def __init__(self, initial=None):
        self.values = copy.deepcopy(initial or {})
        self.put_count = 0

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def put(self, namespace, record_id, payload, **_kwargs):
        self.put_count += 1
        self.values[(namespace, record_id)] = copy.deepcopy(payload)
        return self.put_count


class _Backend:
    def __init__(self, graph=None):
        self.records = _MemoryRecords(
            {("heritage", "graph"): graph} if graph is not None else None
        )


class _App:
    def __init__(self, graph=None):
        self.backend = _Backend(graph)
        self.messages = {}
        self.animals = {
            "Child": {
                "name": "Child",
                "species": "Callithrix jacchus",
                "sex": "female",
                "genotype": "",
                "birth_date": "01.01.2020",
                "eizellspenderin": "",
                "samenspender": "",
                "ziehmutter": "",
                "ziehvater": "",
                "rolle": "offspring",
            },
            "Mother": {
                "name": "Mother",
                "species": "Callithrix jacchus",
                "sex": "female",
            },
        }
        self.archived = {}


def _stale_graph():
    return {
        "version": "1.0.0",
        "animals": {
            "Child": {
                "name": "Child",
                "species": "Macaca mulatta",
                "sex": "male",
                "genotype": "WT/WT",
                "node_fill_color": "#stale",
                "egg_donor": "OldMother",
                "sperm_donor": "OldFather",
                "surrogate_mother": "OldSurrogate",
                "surrogate_father": "OldFather",
                "heritage_only": False,
            }
        },
    }


class HeritageCoreProjectionTest(unittest.TestCase):
    def test_core_values_override_stale_projection_including_clears(self):
        app = _App(_stale_graph())
        plugin = HeritageTrackPlugin(app)
        record = app.animals["Child"]

        self.assertEqual(
            plugin.get_parentage("Child", record),
            {
                "egg_donor": "",
                "sperm_donor": "",
                "surrogate_mother": "",
                "surrogate_father": "",
            },
        )
        self.assertEqual(plugin.store.get_species("Child", record, core_authoritative=True), "Callithrix jacchus")
        self.assertEqual(plugin.get_effective_sex("Child", record), "female")
        self.assertEqual(
            plugin.store.get_node_visual(
                "Child",
                fallback_record=record,
                core_authoritative=True,
            ),
            {"genotype": "", "node_fill_color": ""},
        )

    def test_sync_from_record_replaces_stale_projection_without_writing_when_requested(self):
        app = _App(_stale_graph())
        store = HeritageStore(".", app.backend)
        store.load()
        before_writes = app.backend.records.put_count
        changed = store.sync_from_record("Child", app.animals["Child"], persist=False)

        self.assertTrue(changed)
        projected = store.get_all_entries()["Child"]
        self.assertEqual(projected["species"], "Callithrix jacchus")
        self.assertEqual(projected["sex"], "female")
        self.assertEqual(projected["genotype"], "")
        self.assertEqual(projected["egg_donor"], "")
        self.assertEqual(projected["surrogate_mother"], "")
        self.assertEqual(app.backend.records.put_count, before_writes)

    def test_read_only_engine_build_excludes_stale_core_store_copy_and_writes_nothing(self):
        app = _App(_stale_graph())
        plugin = HeritageTrackPlugin(app)
        app.backend.records.put_count = 0

        engine = plugin.build_engine(sync=False)

        self.assertEqual(app.backend.records.put_count, 0)
        self.assertEqual(engine.child_to_parents["Child"]["egg_donor"], "")
        self.assertEqual(engine.child_to_parents["Child"]["surrogate_mother"], "")


class PedigreeEngineResolutionCacheTest(unittest.TestCase):
    def test_resolution_revision_changes_for_effective_identity_inputs(self):
        cache = PedigreeEngineCache()
        animals = {
            "A": {
                "name": "A",
                "species": "Callithrix jacchus",
                "sex": "female",
                "birth_date": "01.01.2020",
                "eizellspenderin": "",
                "samenspender": "",
                "ziehmutter": "",
                "ziehvater": "",
            }
        }

        def lookup(_name, record):
            return {
                "egg_donor": record.get("eizellspenderin", ""),
                "sperm_donor": record.get("samenspender", ""),
                "surrogate_mother": record.get("ziehmutter", ""),
                "surrogate_father": record.get("ziehvater", ""),
            }

        first = cache.get_engine(animals, lookup, {})
        self.assertIs(first, cache.get_engine(copy.deepcopy(animals), lookup, {}))
        self.assertEqual(first.resolution_revision, cache._cache_key)

        changed = copy.deepcopy(animals)
        changed["A"]["species"] = "Macaca mulatta"
        second = cache.get_engine(changed, lookup, {})
        self.assertIsNot(first, second)
        self.assertNotEqual(first.resolution_revision, second.resolution_revision)


if __name__ == "__main__":
    unittest.main()
