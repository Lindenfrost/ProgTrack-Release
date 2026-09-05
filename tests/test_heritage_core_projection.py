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


class _RevisionRecords(_MemoryRecords):
    """Tiny shared repository with the optimistic revision API."""

    def __init__(self, initial=None):
        super().__init__(initial)
        self.revision = 1 if self.values else 0

    def get_with_revision(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default)), self.revision

    def put(self, namespace, record_id, payload, expected_revision=None, **_kwargs):
        if expected_revision is not None and int(expected_revision) != self.revision:
            raise RuntimeError("stale revision")
        self.put_count += 1
        self.revision += 1
        self.values[(namespace, record_id)] = copy.deepcopy(payload)
        return self.revision


class _RevisionBackend:
    def __init__(self, graph):
        self.records = _RevisionRecords({("heritage", "graph"): graph})


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

    def test_core_sync_hook_never_materializes_or_rewrites_a_shadow(self):
        app = _App(_stale_graph())
        store = HeritageStore(".", app.backend)
        store.load()
        before_writes = app.backend.records.put_count
        changed = store.sync_from_record("Child", app.animals["Child"], persist=False)

        self.assertFalse(changed)
        self.assertEqual(store.get_all_entries()["Child"]["species"], "Macaca mulatta")
        self.assertEqual(store.get_all_entries()["Child"]["egg_donor"], "OldMother")
        self.assertEqual(app.backend.records.put_count, before_writes)

    def test_legacy_store_mutators_cannot_materialize_core_shadow(self):
        app = _App({"version": "1.0.0", "animals": {}})
        store = HeritageStore(".", app.backend)
        store.load()

        store.set_parentage("core-ipid-1", {"egg_donor": "Mother"})
        store.set_heritage_only("core-ipid-1", True)
        store.set_species("core-ipid-1", "Callithrix jacchus")
        store.set_identity_fields("core-ipid-1", display_name="Core")
        store.set_manual_sex("core-ipid-1", "female")
        store.set_node_visual("core-ipid-1", "WT/WT", "#ffeeaa")
        store.set_inbreeding_f_batch({"core-ipid-1": 0.25})

        self.assertEqual(app.backend.records.put_count, 0)
        self.assertNotIn("core-ipid-1", store.get_all_entries())

    def test_legacy_store_mutators_cannot_change_unowned_projection(self):
        app = _App(_stale_graph())
        store = HeritageStore(".", app.backend)
        before = copy.deepcopy(store.load()["animals"]["Child"])

        store.set_parentage("Child", {"egg_donor": "Mother"})
        store.set_heritage_only("Child", True)
        store.set_species("Child", "Callithrix jacchus")
        store.set_identity_fields("Child", display_name="Changed")
        store.set_manual_sex("Child", "female")
        store.set_node_visual("Child", "WT/WT", "#ffeeaa")
        store.set_inbreeding_cache_batch(
            {
                "Child": {
                    "value": 0.25,
                    "pedigree_revision": "rev",
                    "lineage_fingerprint": "fp",
                    "status": "valid",
                }
            }
        )

        self.assertEqual(store.get_all_entries()["Child"], before)
        self.assertEqual(app.backend.records.put_count, 0)

    def test_malformed_core_loader_falls_back_without_opening_ownership_boundary(self):
        app = _App(_stale_graph())
        app.backend.load_core_data = lambda: {"animals": "not-a-map"}
        plugin = HeritageTrackPlugin(app)

        self.assertTrue(plugin._is_core_animal("Child", fresh=True))
        self.assertEqual(plugin._core_record("Child")["name"], "Child")

    def test_read_only_engine_build_excludes_stale_core_store_copy_and_writes_nothing(self):
        app = _App(_stale_graph())
        plugin = HeritageTrackPlugin(app)
        app.backend.records.put_count = 0

        engine = plugin.build_engine(sync=False)

        self.assertEqual(app.backend.records.put_count, 0)
        self.assertEqual(engine.child_to_parents["Child"]["egg_donor"], "")
        self.assertEqual(engine.child_to_parents["Child"]["surrogate_mother"], "")

    def test_second_session_revision_rebuilds_first_session_without_reopen(self):
        graph = {
            "version": "1.0.0",
            "animals": {
                "Dummy": {
                    "ipid": "dummy-ipid",
                    "name": "Dummy",
                    "species": "Callithrix jacchus",
                    "heritage_only": True,
                    "egg_donor": "",
                }
            },
        }
        app = _App(graph)
        app.backend = _RevisionBackend(graph)
        app.animals["Child"]["eizellspenderin"] = "Dummy"
        first = HeritageTrackPlugin(app)
        first_engine = first.build_engine()
        self.assertEqual(first_engine.heritage_entries["Dummy"]["species"], "Callithrix jacchus")

        second = HeritageTrackPlugin(app)
        def mutate(data):
            data["animals"]["Dummy"].update(
                species="Macaca mulatta", egg_donor="NewGrand"
            )
            data["animals"]["NewGrand"] = {
                "name": "NewGrand",
                "species": "Macaca mulatta",
                "heritage_only": True,
            }

        second.store.atomic_update(mutate)

        rebuilt = first.build_engine()
        self.assertIsNot(first_engine, rebuilt)
        self.assertEqual(rebuilt.heritage_entries["Dummy"]["species"], "Macaca mulatta")
        self.assertEqual(rebuilt.child_to_parents["Dummy"]["egg_donor"], "NewGrand")
        self.assertEqual(first.get_parentage("Dummy")["egg_donor"], "NewGrand")
        self.assertIn("NewGrand", first.store.get_all_entries())

    def test_backend_revision_invalidates_engine_even_when_effective_graph_is_unchanged(self):
        graph = {"version": "1.0.0", "animals": {}}
        app = _App(graph)
        app.backend = _RevisionBackend(graph)
        plugin = HeritageTrackPlugin(app)
        first = plugin.build_engine()
        second = HeritageTrackPlugin(app)
        second.store.atomic_update(lambda data: data.setdefault("settings", {}).update(show_grid=True))
        rebuilt = plugin.build_engine()
        self.assertIsNot(first, rebuilt)
        self.assertEqual(first.resolution_revision, rebuilt.resolution_revision)


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
