"""Focused regression tests for Heritage Track issue #161."""

from __future__ import annotations

import copy
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMainWindow, QMessageBox

from Plugins.Heritage_Track.heritage_store import HeritageStore
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackPlugin, HeritageTrackWidget
from Plugins.Heritage_Track.pedigree_router import GeometryValidationError
from Plugins.core.backend.errors import ConflictError


class _Records:
    def __init__(self, graph=None):
        self.values = {("heritage", "graph"): copy.deepcopy(graph)} if graph is not None else {}
        self.put_count = 0
        self.fail_put = False
        self.revision = 1 if graph is not None else 0

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def get_with_revision(self, namespace, record_id, default=None):
        return self.get(namespace, record_id, default), self.revision

    def put(self, namespace, record_id, payload, *, expected_revision=None):
        if self.fail_put:
            raise OSError("simulated backend failure")
        if expected_revision is not None and expected_revision != self.revision:
            raise ConflictError("stale graph revision")
        self.put_count += 1
        self.revision += 1
        self.values[(namespace, record_id)] = copy.deepcopy(payload)
        return self.revision


class _Backend:
    def __init__(self, graph=None):
        self.records = _Records(graph)


class HeritagePositionCacheTest(unittest.TestCase):
    def test_retired_family_display_preference_is_ignored(self):
        backend = _Backend({"collapsed_families": ["legacy-family"]})
        store = HeritageStore("", backend)

        self.assertNotIn("collapsed_families", store.load())

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


class HeritagePositionWidgetTest(unittest.TestCase):
    """Exercise real release/render/store integration, not a fake painter."""

    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    def setUp(self):
        self.app = QMainWindow()
        self.app.backend = _Backend()
        self.app.messages = {}
        self.app.master_track = None
        self.app.projects_plugin = None
        self.app.archived = {}
        self.app._selected_heritage_only = []
        self.app.selected_animals = ["C"]
        self.app.animals = {
            name: {"name": name, "birth_date": birth, "sex": sex,
                   "species": "Callithrix jacchus"}
            for name, birth, sex in (
                ("M", "2000-01-01", "female"),
                ("F", "2001-01-01", "male"),
                ("C", "2020-01-01", "female"),
                ("Other", "2010-01-01", "male"),
            )
        }
        self.app.animals["C"].update(eizellspenderin="M", samenspender="F")
        self.plugin = HeritageTrackPlugin(self.app)
        self.widget = HeritageTrackWidget(self.plugin)
        self.widget.settings.update(animal_label_detail="nothing", show_legend=False)
        self.widget.refresh_graph()
        self.assertIsNotNone(self.widget._render_cache_entry)

    def tearDown(self):
        self.widget.close()
        self.app.close()

    def saved(self, key=None):
        return self.plugin.store.get_position_cache_entry(
            "guest", key or self.widget._active_position_cache_key)

    def drag(self, node="C", dx=5.0, dy=0.0):
        w = self.widget
        before = w.node_positions[node]
        w.drag_active = w.is_dragging = True
        w.drag_node = node
        w.drag_group_nodes = set()
        w.temp_positions = {node: (before[0] + dx, before[1] + dy)}
        w._on_mouse_release(SimpleNamespace(button=1))
        return w._snap_to_grid(before[0] + dx, before[1] + dy)

    def refresh_button(self):
        with patch.object(QMessageBox, "exec", return_value=QMessageBox.StandardButton.Yes), \
                patch.object(self.widget, "_show_coefficients_dialog"):
            self.widget._on_refresh_clicked()

    def test_drag_replaces_render_entry_and_survives_ordinary_update(self):
        expected = self.drag()
        self.assertEqual(self.widget.node_positions["C"], expected)
        self.assertEqual(self.widget._render_cache_entry.positions["C"], expected)
        self.widget.refresh_graph()
        self.assertEqual(self.widget.node_positions["C"], expected)

    def test_failed_refresh_keeps_registry_map_axes_and_does_not_arm_next_update(self):
        old_entry = self.widget._render_cache_entry
        old_map = self.saved()
        old_axes = self.widget.ax
        old_limits = (old_axes.get_xlim(), old_axes.get_ylim())
        self.app.backend.records.fail_put = True
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
            self.refresh_button()
        self.app.backend.records.fail_put = False
        self.assertEqual(self.saved(), old_map)
        self.assertIs(self.plugin.get_render_entry(old_entry.cache_key), old_entry)
        self.assertIs(self.widget.ax, old_axes)
        self.assertEqual((old_axes.get_xlim(), old_axes.get_ylim()), old_limits)
        self.assertFalse(self.widget._force_relayout)
        self.widget.refresh_graph()
        self.assertEqual(self.saved(), old_map)

    def test_paint_failure_does_not_replace_durable_positions(self):
        old_map = self.saved()
        old_entry = self.widget._render_cache_entry
        with patch.object(self.widget, "_paint_cached_render_entry", side_effect=RuntimeError("paint failed")):
            self.refresh_button()
        self.assertEqual(self.saved(), old_map)
        self.assertIs(self.widget._render_cache_entry, old_entry)

    def test_selection_returns_restore_complete_context_for_overlapping_and_disjoint_maps(self):
        for other_selection in (["Other"], ["C", "M"]):
            with self.subTest(selection=other_selection):
                self.app.selected_animals = ["C"]
                self.widget.refresh_graph()
                a_key = self.widget._active_position_cache_key
                a_point = self.drag()
                self.app.selected_animals = other_selection
                self.assertTrue(self.widget.refresh_graph())
                b_key = self.widget._active_position_cache_key
                b_saved = self.saved()
                self.app.selected_animals = ["C"]
                self.assertTrue(self.widget.refresh_graph())
                self.assertEqual(self.widget._active_position_cache_key, a_key)
                self.assertEqual(self.widget.node_positions["C"], a_point)
                self.drag(dx=2)
                self.assertEqual(self.saved(b_key), b_saved)

    def test_depth_and_mode_returns_restore_their_own_maps(self):
        w = self.widget
        w._max_generations = 1
        self.assertTrue(w.refresh_graph())
        first_point = self.drag()
        first_key = w._active_position_cache_key
        first_render_key = w._render_cache_entry.cache_key
        w._max_generations = 4
        self.assertTrue(w.refresh_graph())
        second_key = w._active_position_cache_key
        self.assertNotEqual(second_key, first_key)
        self.assertNotEqual(w._render_cache_entry.cache_key, first_render_key)
        second_map = self.saved()
        w._max_generations = 1
        self.assertTrue(w.refresh_graph())
        self.assertEqual(w.node_positions["C"], first_point)
        w.settings["vertical_layout_mode"] = "chronological"
        self.assertTrue(w.refresh_graph())
        self.drag(dx=3, dy=20)
        self.assertEqual(w.node_positions["C"][1], 2020.0)
        chrono_point = w.node_positions["C"]
        w.settings["vertical_layout_mode"] = "partner_normalized"
        self.assertTrue(w.refresh_graph())
        self.assertEqual(w.node_positions["C"], first_point)
        w.settings["vertical_layout_mode"] = "chronological"
        self.assertTrue(w.refresh_graph())
        self.assertEqual(w.node_positions["C"], chrono_point)
        self.assertEqual(self.saved(second_key), second_map)

    def test_reordered_selection_language_grid_and_view_changes_preserve_map_without_writes(self):
        self.app.selected_animals = ["C", "M"]
        self.widget.refresh_graph()
        expected = self.drag()
        old_map = self.saved()
        writes = self.app.backend.records.put_count
        self.app.selected_animals = ["M", "C", "M"]
        self.widget.resize(1050, 650)
        self.widget.update_language({})
        self.widget.settings["show_grid"] = True
        self.widget.current_xlim = (-20, 20)
        self.widget.current_ylim = (-10, 20)
        self.assertTrue(self.widget.refresh_graph(keep_view=True))
        self.assertEqual(self.widget.node_positions["C"], expected)
        self.assertEqual(self.saved(), old_map)
        self.assertEqual(self.app.backend.records.put_count, writes)
        self.assertEqual(len(self.widget.figure.axes), 1)
        probe_node, probe_point = next(iter(self.widget.node_positions.items()))
        probe_px, probe_py = self.widget.ax.transData.transform(probe_point)
        text_count = len(self.widget.ax.texts)
        self.widget._on_mouse_move(SimpleNamespace(
            inaxes=self.widget.ax,
            x=float(probe_px),
            y=float(probe_py),
            xdata=float(probe_point[0]),
            ydata=float(probe_point[1]),
        ))
        self.assertFalse(hasattr(self.widget, "_hover_annotation"))
        self.assertEqual(len(self.widget.ax.texts), text_count)

    def test_explicit_refresh_replaces_only_current_map_and_keeps_core_unchanged(self):
        core = copy.deepcopy(self.app.animals)
        original = dict(self.widget._render_cache_entry.route_plan.animal_positions)
        self.app.selected_animals = ["Other"]
        self.widget.refresh_graph()
        other_key, other_map = self.widget._active_position_cache_key, self.saved()
        self.app.selected_animals = ["C"]
        self.widget.refresh_graph()
        self.drag()
        self.refresh_button()
        self.assertEqual(dict(self.widget._render_cache_entry.route_plan.animal_positions), original)
        self.assertEqual(self.saved(other_key), other_map)
        self.assertEqual(self.app.animals, core)
        self.assertEqual(self.app.backend.records.get("heritage", "graph")["animals"], {})

    def test_registry_failures_before_and_after_install_restore_previous_entry(self):
        registry = self.plugin._render_cache
        real_put = registry.put
        for after_install in (False, True):
            with self.subTest(after_install=after_install):
                old_entry, old_map, old_axes = self.widget._render_cache_entry, self.saved(), self.widget.ax
                def fail(entry):
                    if after_install:
                        real_put(entry)
                    raise RuntimeError("registry publication failed")
                with patch.object(registry, "put", side_effect=fail):
                    self.refresh_button()
                self.assertIs(registry.get(old_entry.cache_key), old_entry)
                self.assertEqual(self.saved(), old_map)
                self.assertIs(self.widget.ax, old_axes)
                self.assertFalse(self.widget._force_relayout)
                self.assertEqual(len(self.widget.figure.axes), 1)

    def test_failed_drag_restores_preview_artists_and_saved_map(self):
        w = self.widget
        old_entry, old_map = w._render_cache_entry, self.saved()
        old_point = w.node_positions["C"]
        marker = w.node_meta["C"]["marker_artist"]
        marker.set_data([old_point[0] + 5], [old_point[1]])
        self.app.backend.records.fail_put = True
        self.drag()
        self.app.backend.records.fail_put = False
        self.assertIs(w._render_cache_entry, old_entry)
        self.assertEqual(self.saved(), old_map)
        self.assertEqual(tuple(axis[0] for axis in marker.get_data()), old_point)
        self.assertFalse(w.temp_positions)
        self.assertFalse(w.drag_active)

    def test_late_artist_failure_preserves_old_axes_artists_and_map(self):
        from matplotlib.axes import Axes
        real_annotate = Axes.annotate
        old_axes, old_map = self.widget.ax, self.saved()
        old_marker = self.widget.node_meta["C"]["marker_artist"]
        def fail(axes, text, *args, **kwargs):
            result = real_annotate(axes, text, *args, **kwargs)
            if text == "C":
                raise RuntimeError("late label paint failure")
            return result
        with patch.object(Axes, "annotate", new=fail):
            self.refresh_button()
        self.assertIs(self.widget.ax, old_axes)
        self.assertIs(self.widget.node_meta["C"]["marker_artist"], old_marker)
        self.assertEqual(self.saved(), old_map)

    def test_rejected_selection_update_restores_old_write_context(self):
        old = self.widget._render_cache_entry
        self.app.selected_animals = ["Other"]
        with patch.object(self.widget._pedigree_router, "plan", side_effect=GeometryValidationError("bad layout")):
            self.assertFalse(self.widget.refresh_graph())
        self.assertEqual(self.widget._canonical_selection_ids, old.canonical_selection)
        self.assertEqual(self.widget._active_position_cache_key, old.position_cache_key)
        self.assertIs(self.widget._render_cache_entry, old)

    def test_external_position_update_invalidates_warm_frame_and_stale_drag_is_rejected(self):
        other = HeritageStore("", self.app.backend)
        old = self.widget._render_cache_entry
        positions = dict(old.route_plan.animal_positions)
        positions["C"] = (8.0, 3.6)
        other.set_position_cache_entry("guest", old.position_cache_key, positions,
                                       old.position_cache_revision, old.position_cache_dependencies)
        newer_map = self.saved()
        self.drag()
        self.assertEqual(self.saved(), newer_map)
        self.assertIs(self.widget._render_cache_entry, old)
        self.assertTrue(self.widget.refresh_graph())
        self.assertEqual(self.widget.node_positions["C"], (8.0, 3.6))

    def test_same_context_write_during_publication_rejects_candidate_without_overwriting(self):
        other = HeritageStore("", self.app.backend)
        old = self.widget._render_cache_entry
        publish = self.plugin.cache_render_entry
        newer = {}
        def interleave(entry):
            publish(entry)
            positions = dict(old.route_plan.animal_positions)
            positions["C"] = (9.0, 3.6)
            other.set_position_cache_entry("guest", old.position_cache_key, positions,
                                           old.position_cache_revision, old.position_cache_dependencies)
            newer.update(self.saved())
        with patch.object(self.plugin, "cache_render_entry", side_effect=interleave):
            self.drag()
        self.assertEqual(self.saved(), newer)
        self.assertIs(self.widget._render_cache_entry, old)
        self.assertIs(self.plugin.get_render_entry(old.cache_key), old)

    def test_unrelated_cas_interleaving_retries_without_lost_update(self):
        records = self.app.backend.records
        other = HeritageStore("", self.app.backend)
        put = records.put
        interleaved = False
        def interleave(namespace, record_id, payload, **kwargs):
            nonlocal interleaved
            if not interleaved:
                interleaved = True
                other.set_position_cache_entry("someone-else", "different-context", {"Other": (7, 8)}, "rev", ["Other"])
                other.atomic_update(lambda data: data["settings"].update(show_grid=True))
            return put(namespace, record_id, payload, **kwargs)
        # Inject a CAS race once: the retry must reload unrelated fields while
        # retaining the original current-context precondition.
        with patch.object(records, "put", side_effect=interleave):
            self.drag()
        saved = records.get("heritage", "graph")
        self.assertTrue(saved["settings"]["show_grid"])
        self.assertIn("different-context", saved["position_cache"]["someone-else"])
        self.assertEqual(self.saved()["positions"]["C"]["x"], self.widget.node_positions["C"][0])
        self.assertNotEqual(self.widget.node_positions["C"][0], 0.0)

    def test_reopen_and_different_user_keep_separate_complete_maps(self):
        expected = self.drag()
        guest_key, guest_map = self.widget._active_position_cache_key, self.saved()
        self.widget.close()
        self.plugin = HeritageTrackPlugin(self.app)
        self.widget = HeritageTrackWidget(self.plugin)
        self.widget.settings.update(animal_label_detail="nothing", show_legend=False)
        self.assertTrue(self.widget.refresh_graph())
        self.assertEqual(self.widget.node_positions["C"], expected)
        self.app.master_track = SimpleNamespace(current_username="different-user")
        self.assertTrue(self.widget.refresh_graph())
        self.assertNotEqual(self.widget.node_positions["C"], expected)
        self.drag(dx=2)
        self.assertEqual(self.plugin.store.get_position_cache_entry("guest", guest_key), guest_map)

    def test_raster_draw_failure_precedes_position_write(self):
        from matplotlib.axes import Axes
        old_axes, old_map = self.widget.ax, self.saved()
        with patch.object(Axes, "draw", side_effect=RuntimeError("rasterization failed")):
            self.refresh_button()
        self.assertIs(self.widget.ax, old_axes)
        self.assertEqual(self.saved(), old_map)

    def test_failed_explicit_geometry_repair_retains_accepted_frame(self):
        old_entry, old_map, old_axes = self.widget._render_cache_entry, self.saved(), self.widget.ax
        with patch.object(self.plugin.store, "get_invalid_node_positions", return_value={"bad": [float("inf"), 0]}), \
                patch.object(self.plugin.store, "cleanup_invalid_node_positions", side_effect=OSError("repair failed")):
            self.refresh_button()
        self.assertIs(self.widget.ax, old_axes)
        self.assertIs(self.plugin.get_render_entry(old_entry.cache_key), old_entry)
        self.assertEqual(self.saved(), old_map)

    def test_eviction_notice_is_visible_on_the_accepting_frame(self):
        with patch.object(self.plugin.store, "POSITION_CACHE_LIMIT", 1):
            self.app.selected_animals = ["Other"]
            self.assertTrue(self.widget.refresh_graph())
        self.assertIn("Position cache limit reached", self.widget.status_label.text())

    def test_chronological_singleton_drag_preserves_month_snapped_y(self):
        self.app.selected_animals = ["Other"]
        self.widget.settings["vertical_layout_mode"] = "chronological"
        self.assertTrue(self.widget.refresh_graph())
        self.assertEqual(self.widget.node_positions["Other"][1], 2010.0)
        self.drag(node="Other", dx=3, dy=10)
        self.assertEqual(self.widget.node_positions["Other"][1], 2010.0)

    def test_real_mouse_group_drag_pan_and_zoom_preserve_accepted_map(self):
        w = self.widget
        w.canvas.draw()
        family = next(iter(w.family_members))
        start = w.family_positions[family]
        px, py = w.ax.transData.transform(start)
        w._on_mouse_press(SimpleNamespace(button=1, inaxes=w.ax, xdata=start[0],
                                          ydata=start[1], x=px, y=py, dblclick=False))
        event = SimpleNamespace(button=1, inaxes=w.ax, xdata=start[0]+5,
                                ydata=start[1]+2, x=px+80, y=py+40)
        original = dict(w._route_plan.animal_positions)
        w._on_mouse_move(event)
        self.assertTrue(w.is_dragging)
        w._on_mouse_release(event)
        expected_family = w._snap_to_grid(start[0] + 5, start[1] + 2)
        self.assertEqual(w.family_positions[family], expected_family)
        for node, point in original.items():
            self.assertAlmostEqual(w.node_positions[node][0], point[0]+5)
            self.assertAlmostEqual(w.node_positions[node][1], point[1]+2)
        saved = self.saved()
        self.assertEqual(
            saved["family_positions"][family],
            {"x": float(expected_family[0]), "y": float(expected_family[1])},
        )
        w._on_scroll(SimpleNamespace(inaxes=w.ax, xdata=start[0], ydata=start[1], button="up"))
        pan_px, pan_py = w.ax.transData.transform((start[0], start[1]))
        w._on_mouse_press(SimpleNamespace(button=2, inaxes=w.ax, xdata=start[0], ydata=start[1],
                                          x=pan_px, y=pan_py))
        w._on_mouse_move(SimpleNamespace(button=2, inaxes=w.ax, xdata=start[0]+1, ydata=start[1]+1,
                                         x=pan_px+20, y=pan_py+20))
        w._on_mouse_release(SimpleNamespace(button=2, inaxes=None, x=pan_px+20, y=pan_py+20))
        self.assertEqual(self.saved(), saved)
        self.assertTrue(w.refresh_graph(keep_view=True))
        self.assertEqual(self.saved(), saved)

    def test_repeated_pan_uses_display_anchor_across_axes_boundary(self):
        """Every native motion must accumulate, including with no inaxes."""
        w = self.widget
        w.canvas.draw()
        nodes_before = dict(w.node_positions)
        families_before = dict(w.family_positions)
        saved_before = self.saved()

        bbox = w.ax.bbox
        anchor = (float(bbox.x0 + bbox.width * 0.45), float(bbox.y0 + bbox.height * 0.55))
        anchor_data = w.ax.transData.inverted().transform(anchor)
        press = SimpleNamespace(
            button=2,
            inaxes=w.ax,
            xdata=float(anchor_data[0]),
            ydata=float(anchor_data[1]),
            x=anchor[0],
            y=anchor[1],
        )
        w._on_mouse_press(press)
        self.assertTrue(w.pan_active)
        self.assertEqual(w.pan_start, anchor)

        positions = (
            (anchor[0] + 18.0, anchor[1] + 7.0, w.ax),
            (anchor[0] + 37.0, anchor[1] - 11.0, None),
            (anchor[0] + 9.0, anchor[1] - 23.0, w.ax),
            (anchor[0] - 14.0, anchor[1] + 19.0, None),
        )
        previous_position = anchor
        for x, y, inaxes in positions:
            inverse = w.ax.transData.inverted()
            start_data = inverse.transform(previous_position)
            current_data = inverse.transform((x, y))
            dx = float(current_data[0] - start_data[0])
            dy = float(current_data[1] - start_data[1])
            expected_xlim, expected_ylim = w._apply_aspect_fill(
                tuple(value - dx for value in w.ax.get_xlim()),
                tuple(value - dy for value in w.ax.get_ylim()),
            )
            w._on_mouse_move(SimpleNamespace(
                button=2,
                inaxes=inaxes,
                x=float(x),
                y=float(y),
                xdata=None,
                ydata=None,
            ))
            self.assertAlmostEqual(w.ax.get_xlim()[0], expected_xlim[0])
            self.assertAlmostEqual(w.ax.get_xlim()[1], expected_xlim[1])
            self.assertAlmostEqual(w.ax.get_ylim()[0], expected_ylim[0])
            self.assertAlmostEqual(w.ax.get_ylim()[1], expected_ylim[1])
            self.assertEqual(tuple(w.current_xlim), tuple(w.ax.get_xlim()))
            self.assertEqual(tuple(w.current_ylim), tuple(w.ax.get_ylim()))
            previous_position = (x, y)

        w._on_mouse_release(SimpleNamespace(
            button=2,
            inaxes=None,
            x=previous_position[0],
            y=previous_position[1],
        ))
        self.assertFalse(w.pan_active)
        self.assertIsNone(w.pan_start)
        self.assertFalse(w._pan_mouse_grabbed)
        self.assertEqual(w.node_positions, nodes_before)
        self.assertEqual(w.family_positions, families_before)
        self.assertEqual(self.saved(), saved_before)

    def test_native_repeated_pan_crosses_axes_boundary_and_releases_cleanly(self):
        """Qt mouse events must keep panning through the surrounding canvas."""
        w = self.widget
        w.show()
        self.qt.processEvents()
        try:
            w.canvas.draw()
            bbox = w.ax.bbox
            canvas_height = float(w.canvas.height())

            def qt_point(display_position):
                return QPoint(
                    int(round(display_position[0])),
                    int(round(canvas_height - display_position[1])),
                )

            anchor = (
                float(bbox.x0 + bbox.width * 0.45),
                float(bbox.y0 + bbox.height * 0.55),
            )
            first = (anchor[0] + 16.0, anchor[1] + 9.0)
            outside_x = bbox.x0 - 8.0 if bbox.x0 > 8.0 else bbox.x1 + 8.0
            outside = (float(outside_x), anchor[1] - 17.0)
            final_inside = (anchor[0] - 21.0, anchor[1] - 13.0)
            positions = (first, outside, final_inside)

            nodes_before = dict(w.node_positions)
            families_before = dict(w.family_positions)
            saved_before = self.saved()
            limits_before = (tuple(w.ax.get_xlim()), tuple(w.ax.get_ylim()))

            QTest.mousePress(
                w.canvas,
                Qt.MouseButton.MiddleButton,
                Qt.KeyboardModifier.NoModifier,
                qt_point(anchor),
            )
            self.qt.processEvents()
            self.assertTrue(w.pan_active)

            previous_limits = limits_before
            for display_position in positions:
                QTest.mouseMove(w.canvas, qt_point(display_position), delay=1)
                self.qt.processEvents()
                current_limits = (tuple(w.ax.get_xlim()), tuple(w.ax.get_ylim()))
                self.assertNotEqual(current_limits, previous_limits)
                previous_limits = current_limits

            QTest.mouseRelease(
                w.canvas,
                Qt.MouseButton.MiddleButton,
                Qt.KeyboardModifier.NoModifier,
                qt_point(outside),
            )
            self.qt.processEvents()
            self.assertFalse(w.pan_active)
            self.assertIsNone(w.pan_start)
            self.assertEqual(w.node_positions, nodes_before)
            self.assertEqual(w.family_positions, families_before)
            self.assertEqual(self.saved(), saved_before)
        finally:
            w.hide()


class HeritagePositionSQLiteTest(unittest.TestCase):
    def test_production_repository_merges_other_context_and_rejects_stale_same_context(self):
        from Plugins.core.backend import ProgTrackBackend
        from Plugins.core.runtime_paths import BackendProfile, RuntimePaths
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            paths = RuntimePaths(
                application_root=Path(__file__).resolve().parents[1],
                profile=BackendProfile.STANDALONE_SQLITE,
                data_root=base / "data", config_root=base / "config",
                cache_root=base / "cache", state_root=base / "state",
                database_path=base / "data/database/progtrack.sqlite3",
                managed_root=base / "managed", managed_documents=base / "managed/documents",
                managed_config_assets=base / "managed/config-assets", logs=base / "state/logs",
                runtime=base / "state/runtime", exports=base / "exports",
                preferences=base / "preferences", profile_file=base / "config/backend.json",
            )
            paths.create_mutable_roots()
            backend = ProgTrackBackend(paths, acquire_process_lock=False)
            try:
                core = copy.deepcopy(backend.load_core_data())
                first, second = HeritageStore("", backend), HeritageStore("", backend)
                first.set_position_cache_entry("alice", "A", {"C": (1, 2)}, "rev", ["C"], expected_entry=None)
                baseline = first.get_position_cache_entry("alice", "A")
                second.set_position_cache_entry("bob", "B", {"D": (3, 4)}, "rev", ["D"])
                other_map = second.get_position_cache_entry("bob", "B")
                first.set_position_cache_entry("alice", "A", {"C": (5, 6)}, "rev", ["C"], expected_entry=baseline)
                self.assertEqual(first.get_position_cache_entry("bob", "B"), other_map)
                latest = first.get_position_cache_entry("alice", "A")
                with self.assertRaises(ConflictError):
                    second.set_position_cache_entry("alice", "A", {"C": (9, 9)}, "rev", ["C"], expected_entry=baseline)
                self.assertEqual(second.get_position_cache_entry("alice", "A"), latest)
                self.assertEqual(backend.load_core_data(), core)
            finally:
                backend.close()


if __name__ == "__main__":
    unittest.main()
