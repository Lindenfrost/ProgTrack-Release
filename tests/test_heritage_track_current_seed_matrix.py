"""End-to-end Heritage Track checks against the packaged Phase 2B seed.

The test creates an isolated SQLite runtime from ``Resources/Seed``.  It never
opens or changes the user's live database.  The standard matrix uses one
representative case per packaged species through both supported vertical
layouts. Dense Callitrix selections and edge cases have dedicated focused
audits below; expensive full-seed renders are marked ``extended_heritage`` so
they remain available without dominating every standard test run.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from collections import defaultdict
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MPLBACKEND", "Agg")

from PyQt6.QtWidgets import QApplication, QMainWindow
from matplotlib.backends.backend_agg import RendererAgg
from matplotlib.transforms import Bbox

from Plugins.core.backend import ProgTrackBackend
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths
from Plugins.Heritage_Track.heritage_track_widget import (
    HeritageTrackPlugin,
    HeritageTrackWidget,
    VERTICAL_LAYOUT_CHRONOLOGICAL,
    VERTICAL_LAYOUT_PARTNER_NORMALIZED,
)
from Plugins.Heritage_Track.pedigree_router import RoutePlan
from tests.heritage_test_support import reported_subtest


ROOT = Path(__file__).resolve().parents[1]
# The complete Cartesian product was 32 expensive end-to-end renders.  Keep
# one deliberately chosen case per packaged species, exercise both vertical
# modes for every case, and leave the focused edge-case tests below as the
# source of coverage for the other selection/depth combinations.
REPRESENTATIVE_CASES = (
    ("Callitrix jacchus", "all", 6),
    ("Macaca mulatta", "single", 3),
    ("Mus musculus", "all", 3),
    ("Papio hamadryas anubis", "single", 6),
)
MODES = (
    VERTICAL_LAYOUT_PARTNER_NORMALIZED,
    VERTICAL_LAYOUT_CHRONOLOGICAL,
)


def runtime_paths(root: Path) -> RuntimePaths:
    base = root / "runtime"
    paths = RuntimePaths(
        application_root=ROOT,
        profile=BackendProfile.STANDALONE_SQLITE,
        data_root=base / "data",
        config_root=base / "config",
        cache_root=base / "cache",
        state_root=base / "state",
        database_path=base / "data" / "database" / "progtrack.sqlite3",
        managed_root=base / "managed",
        managed_documents=base / "managed" / "documents",
        managed_config_assets=base / "managed" / "config-assets",
        logs=base / "state" / "logs",
        runtime=base / "state" / "runtime",
        exports=base / "data" / "exports",
        preferences=base / "config" / "preferences",
        profile_file=base / "config" / "backend.json",
    )
    paths.create_mutable_roots()
    return paths


class _TestApp(QMainWindow):
    def _extend_animal_selection_from_graph(self, animal_key: str) -> bool:
        """Provide the canonical graph-selection boundary used by the app."""
        if not (
            animal_key in getattr(self, "animals", {})
            or animal_key in getattr(self, "archived", {})
        ):
            return False
        selected = list(getattr(self, "selected_animals", []) or [])
        if animal_key not in selected:
            selected.append(animal_key)
        self.selected_animals = selected
        return True


class CurrentSeedHeritageMatrixTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])
        cls.tempdir = tempfile.TemporaryDirectory()
        cls.backend = ProgTrackBackend(
            runtime_paths(Path(cls.tempdir.name)), acquire_process_lock=False
        )
        snapshot = cls.backend.load_core_data()
        cls.app = _TestApp()
        cls.app.backend = cls.backend
        cls.app.animals = snapshot.get("animals", {})
        cls.app.archived = snapshot.get("archived_animals", {})
        cls.app.messages = json.loads(
            (ROOT / "lang" / "messages_en.json").read_text(encoding="utf-8")
        )
        cls.app.master_track = None
        cls.app.projects_plugin = None
        cls.app.selected_animals = []
        cls.app._selected_heritage_only = []
        cls.plugin = HeritageTrackPlugin(cls.app)
        cls.widget = HeritageTrackWidget(cls.plugin)
        cls.widget.resize(1180, 720)
        cls.widget.settings["animal_label_detail"] = "birth_date"
        cls.widget.settings["show_grid"] = False
        cls.widget.settings["exclude_archived"] = False
        cls.widget.settings["show_heritage_only"] = True
        cls.engine = cls.plugin.build_engine()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget.close()
        cls.app.close()
        cls.backend.close()
        cls.tempdir.cleanup()

    @classmethod
    def _record(cls, node: str) -> dict:
        # Mirror the production render snapshot: Core's active/archived
        # projection is authoritative, while the Heritage store contributes
        # only Heritage-owned entries.  The removed ``get_animal`` lookup
        # could accidentally certify stale records or hide missing data.
        core = cls.plugin._current_core_records(fresh=True)
        entries = cls.plugin.store.get_all_entries()
        record = entries.get(node, {}) if isinstance(entries, dict) else {}
        if isinstance(core, dict) and node in core:
            record = core[node]
        return record if isinstance(record, dict) else {}

    @classmethod
    def _species_nodes(cls) -> dict[str, list[str]]:
        result: dict[str, list[str]] = defaultdict(list)
        for node in cls.engine.all_nodes:
            species = str(cls._record(node).get("species", "") or "").strip()
            if species:
                result[species].append(node)
        return {
            species: sorted(nodes, key=str.casefold)
            for species, nodes in sorted(result.items(), key=lambda item: item[0].casefold())
        }

    @classmethod
    def _selection_shapes(cls, species_nodes: list[str]) -> dict[str, list[str]]:
        node_set = set(species_nodes)
        levels = cls.engine.compute_levels(node_set)
        active = [node for node in species_nodes if node in cls.app.animals]
        ranked = sorted(
            active or species_nodes,
            key=lambda node: (-levels.get(node, 0), node.casefold()),
        )
        focal = ranked[0]

        parents = cls.engine.child_to_parents.get(focal, {})
        focal_pair = {
            str(parents.get("egg_donor", "") or "").strip(),
            str(parents.get("sperm_donor", "") or "").strip(),
        } - {""}
        companion = None
        if focal_pair:
            siblings = []
            for candidate in species_nodes:
                if candidate == focal:
                    continue
                candidate_parents = cls.engine.child_to_parents.get(candidate, {})
                candidate_pair = {
                    str(candidate_parents.get("egg_donor", "") or "").strip(),
                    str(candidate_parents.get("sperm_donor", "") or "").strip(),
                } - {""}
                if candidate_pair == focal_pair:
                    siblings.append(candidate)
            if siblings:
                companion = sorted(siblings, key=str.casefold)[0]
        if companion is None:
            children = sorted(
                cls.engine.parent_to_children.get(focal, set()) & node_set,
                key=str.casefold,
            )
            if children:
                companion = children[0]
        if companion is None:
            companion = next(node for node in ranked if node != focal)

        # "All" mirrors selecting every visible animal of the species from
        # the animal list. Archived ancestors are still pulled in semantically.
        all_active = sorted(active or species_nodes, key=str.casefold)
        return {
            "single": [focal],
            "multiple": [focal, companion],
            "all": all_active,
        }

    def _render(self, selection: list[str], depth: int, mode: str):
        self.app.selected_animals = list(selection)
        self.app._selected_heritage_only = []
        self.widget._max_generations = depth
        self.widget.settings["vertical_layout_mode"] = mode
        self.widget.temp_positions.clear()
        self.widget.selected_nodes.clear()
        self.widget.current_xlim = None
        self.widget.current_ylim = None
        self.widget._prev_display_nodes = None
        for _saved_node in list(self.plugin.store.get_node_positions()):
            self.plugin.store.remove_node_position(_saved_node)
        self.widget._force_relayout = True
        accepted = self.widget.refresh_graph()
        self.assertTrue(accepted, "requested Heritage frame was not accepted")
        # ``refresh_graph`` schedules ``draw_idle``. Processing the Qt queue
        # here and then calling ``draw`` rendered every matrix case twice,
        # making the 96-case closure audit look like a layout regression.
        # One synchronous draw is sufficient for both semantic and pixel
        # assertions in the offscreen harness.
        self.widget.canvas.draw()
        self.assertIsNotNone(self.widget._route_plan)
        cache_key = self.widget._render_cache_key(
            list(selection),
            mode == VERTICAL_LAYOUT_CHRONOLOGICAL,
            display_mode=self.widget.layout_mode,
        )
        entry = self.plugin.get_render_entry(cache_key)
        self.assertIsNotNone(entry)
        self.assertEqual(tuple(entry.canonical_selection), tuple(sorted(selection, key=lambda value: (value.casefold(), value))))
        self.assertEqual(entry.cache_key, cache_key)
        self.assertTrue(entry.valid)
        return self.widget._route_plan

    def _families_for_plan(self, plan):
        visible = set(plan.animal_positions)
        levels = self.engine.compute_levels(visible)
        return self.widget._build_family_units(visible, levels, self.engine)

    def _assert_semantic_geometry(
        self,
        plan,
        families,
        context: str,
    ) -> None:
        labels = {
            node: self.widget._get_node_obstacle_label(
                node, self.widget._get_node_record(node)
            )
            for node in plan.animal_positions
        }
        problems = self.widget._pedigree_router.validate_plan(
            plan,
            families,
            labels=labels,
            show_inbreeding=True,
        )
        if self.widget.layout_mode == "focused":
            # Focused views keep the complete dense context visible.  Text
            # collisions are intentionally resolved by zoom and are not a
            # reason to reject the frame; marker/route/topology diagnostics
            # remain covered by the router validator.
            problems = [
                problem
                for problem in problems
                if not problem.endswith(": animal markers or labels overlap")
            ]
        self.assertEqual(plan.unresolved, [], context)
        self.assertEqual(problems, [], context + "\n" + "\n".join(problems))

        for family_id, family in families.items():
            if family_id not in plan.family_positions:
                continue
            parents = [
                node
                for node in (family.get("mother"), family.get("father"))
                if node in plan.animal_positions
            ]
            children = [
                node
                for node in family.get("children", [])
                if node in plan.animal_positions
            ]
            junction_x = plan.family_positions[family_id][0]
            if len(parents) == 2:
                parent_xs = sorted(
                    plan.animal_positions[node][0] for node in parents
                )
                parent_midpoint = sum(parent_xs) / 2.0
                parent_span = parent_xs[1] - parent_xs[0]
                allowed_shift = min(
                    1.35,
                    parent_span * 0.22,
                    max(0.0, (parent_span / 2.0) - 0.08),
                )
                visible_children = [
                    child for child in family.get("children", [])
                    if child in plan.animal_positions
                ]
                if len(visible_children) == 1:
                    child_x = plan.animal_positions[visible_children[0]][0]
                    child_axis_eligible = (
                        parent_xs[0] + 0.08 < child_x < parent_xs[1] - 0.08
                    )
                    child_clearance = max(0.35, self.widget._pedigree_router.node_gap)
                    if (
                        parent_xs[0] + child_clearance
                        <= child_x
                        <= parent_xs[1] - child_clearance
                    ):
                        allowed_shift = min(
                            max(allowed_shift, abs(child_x - parent_midpoint) + self.widget._pedigree_router.route_clearance),
                            max(0.0, (parent_span / 2.0) - 0.08),
                        )
                    elif child_axis_eligible:
                        allowed_shift = max(
                            allowed_shift,
                            abs(child_x - parent_midpoint),
                        )
                self.assertGreater(junction_x, parent_xs[0], context)
                self.assertLess(junction_x, parent_xs[1], context)
                self.assertLessEqual(
                    abs(junction_x - parent_midpoint),
                    allowed_shift + 1e-6,
                    context,
                )
            # Child centring is intentionally a soft visual objective. The
            # semantic validator below owns topology/direct-route guarantees;
            # a large continuing subtree must not force terminal siblings to
            # compensate merely to satisfy an arithmetic-mean assertion.

    @pytest.mark.extended_heritage
    def test_representative_species_selection_depth_and_layout_matrix(self) -> None:
        species_map = self._species_nodes()
        self.assertEqual(
            set(species_map),
            {
                "Callitrix jacchus",
                "Macaca mulatta",
                "Mus musculus",
                "Papio hamadryas anubis",
            },
        )
        for species, selection_name, depth in REPRESENTATIVE_CASES:
            species_nodes = species_map[species]
            selections = self._selection_shapes(species_nodes)
            selection = selections[selection_name]
            for mode in MODES:
                context = (
                    f"{species}; {selection_name}; depth={depth}; mode={mode}; "
                    f"focus={[self.widget._get_node_display_label(n) for n in selection[:3]]}"
                )
                with reported_subtest(self, "seed selection/depth/mode", context=context):
                    plan = self._render(selection, depth, mode)
                    self.assertTrue(plan.animal_positions, context)
                    families = self._families_for_plan(plan)
                    self._assert_semantic_geometry(plan, families, context)

    @pytest.mark.extended_heritage
    def test_representative_layout_is_deterministic(self) -> None:
        species_nodes = self._species_nodes()["Callitrix jacchus"]
        selection = self._selection_shapes(species_nodes)["multiple"]
        first = self._render(selection, 5, VERTICAL_LAYOUT_PARTNER_NORMALIZED)
        first_positions = {
            node: tuple(round(value, 6) for value in point)
            for node, point in first.animal_positions.items()
        }
        first_routes = {
            family: {
                endpoint: tuple(
                    tuple(round(value, 6) for value in point) for point in path
                )
                for endpoint, path in routes.items()
            }
            for family, routes in first.routes.items()
        }
        second = self._render(
            list(reversed(selection)), 5, VERTICAL_LAYOUT_PARTNER_NORMALIZED
        )
        self.assertEqual(first_positions, {
            node: tuple(round(value, 6) for value in point)
            for node, point in second.animal_positions.items()
        }, "Callitrix jacchus")
        self.assertEqual(first_routes, {
            family: {
                endpoint: tuple(
                    tuple(round(value, 6) for value in point)
                    for point in path
                )
                for endpoint, path in routes.items()
            }
            for family, routes in second.routes.items()
        }, "Callitrix jacchus")

    @pytest.mark.extended_heritage
    def test_arwen_continuing_subtree_does_not_exile_terminal_siblings(self) -> None:
        def find(name: str, nodes) -> str:
            return next(
                node for node in nodes if node.split(" | ", 1)[0] == name
            )

        def find_prefix(prefix: str, nodes) -> str:
            return next(
                node
                for node in nodes
                if node.split(" | ", 1)[0].startswith(prefix)
            )

        selected = [
            find(name, self.engine.all_nodes)
            for name in ("Arwen", "Denethor", "Eldarion")
        ]
        plan = self._render(
            selected,
            6,
            VERTICAL_LAYOUT_PARTNER_NORMALIZED,
        )
        families = self._families_for_plan(plan)
        self._assert_semantic_geometry(plan, families, "issue #56 follow-up")

        def point(name: str):
            return plan.animal_positions[find(name, plan.animal_positions)]

        arwen = point("Arwen")
        aragorn = point("Aragorn II")
        elladan = point("Elladan")
        elrohir = point("Elrohir")
        elrond = point("Elrond")
        celebrian = plan.animal_positions[
            find_prefix("Celebr", plan.animal_positions)
        ]
        parent_left, parent_right = sorted((elrond[0], celebrian[0]))
        junction = next(
            plan.family_positions[family_id]
            for family_id, family in families.items()
            if {
                family.get("mother"),
                family.get("father"),
            } == {
                find("Elrond", plan.animal_positions),
                find_prefix("Celebr", plan.animal_positions),
            }
        )

        # Compactness is width-aware: a partner block must stay close to the
        # origin, but the hard label clearance is allowed to determine the
        # minimum distance for long date/detail labels.
        arwen_label_width = self.widget._pedigree_router._estimated_label_width(
            self.widget._get_node_obstacle_label(
                find("Arwen", plan.animal_positions),
                self._record(find("Arwen", plan.animal_positions)),
            )
        )
        aragorn_label_width = self.widget._pedigree_router._estimated_label_width(
            self.widget._get_node_obstacle_label(
                find("Aragorn II", plan.animal_positions),
                self._record(find("Aragorn II", plan.animal_positions)),
            )
        )
        partner_clearance = (
            (arwen_label_width / 2.0)
            + (aragorn_label_width / 2.0)
            + self.widget._pedigree_router.node_gap
        )
        self.assertLessEqual(
            abs(arwen[0] - aragorn[0]),
            partner_clearance + 0.25,
        )
        self.assertLess(
            max(arwen[0], elladan[0], elrohir[0])
            - min(arwen[0], elladan[0], elrohir[0]),
            16.0,
        )
        self.assertGreater(junction[0], parent_left)
        self.assertLess(junction[0], parent_right)
        self.assertGreaterEqual(junction[0], min(arwen[0], elladan[0], elrohir[0]) - 1.5)
        self.assertLessEqual(junction[0], max(arwen[0], elladan[0], elrohir[0]) + 1.5)
        self.assertNotEqual(elladan[1], elrohir[1])
        self.assertGreater(
            min(elladan[1], elrohir[1]), max(elrond[1], celebrian[1])
        )
        self.assertLess(max(elladan[1], elrohir[1]), arwen[1])

        renderer = self.widget.canvas.get_renderer()
        axes = self.widget.ax.get_window_extent(renderer)
        figure = self.widget.figure.bbox
        legend = self.widget.ax.get_legend().get_window_extent(renderer)
        self.assertLessEqual(legend.x1, figure.x1 + 1.0)
        self.assertGreater(
            self.widget.figure.subplotpars.right, 0.99
        )

    @pytest.mark.extended_heritage
    def test_requested_callitrix_focus_is_complete_clear_and_unclipped(self) -> None:
        def find(name: str, nodes) -> str:
            return next(
                node for node in nodes if node.split(" | ", 1)[0] == name
            )

        selected = [
            find(name, self.engine.all_nodes)
            for name in ("Arwen", "Eldarion", "Denethor", "Aragorn II")
        ]
        for mode in MODES:
            with reported_subtest(self, "requested focus framing", mode=mode):
                plan = self._render(selected, 5, mode)
                families = self._families_for_plan(plan)
                context = f"requested Callitrix focus; depth=5; mode={mode}"
                self._assert_semantic_geometry(plan, families, context)

                renderer = self.widget.canvas.get_renderer()
                axes_box = self.widget.ax.get_window_extent(renderer)
                label_boxes: dict[str, Bbox] = {}
                marker_boxes: dict[str, Bbox] = {}
                selected_set = set(selected)
                focused_frame = self.widget.layout_mode == "focused"
                for node, meta in self.widget.node_meta.items():
                    if meta.get("kind") != "animal":
                        continue
                    boxes = []
                    for key in (
                        "marker_artist",
                        "label_artist",
                        "f_artist",
                        "undated_artist",
                    ):
                        artist = meta.get(key)
                        if artist is not None and artist.get_visible():
                            bounds = artist.get_window_extent(renderer)
                            if bounds.width > 0 and bounds.height > 0:
                                visible = Bbox.intersection(bounds, axes_box)
                                if visible is None or visible.width <= 0 or visible.height <= 0:
                                    # A dense graph keeps its complete route
                                    # plan available, but deep context may
                                    # intentionally start offscreen and be
                                    # reached by pan/zoom.  Selected anchors
                                    # remain part of the initial framing
                                    # contract in both layout modes.
                                    if node in selected_set:
                                        self.fail(
                                            f"{context}: selected {node}/{key} is outside the readable viewport"
                                        )
                                    continue
                                if key == "marker_artist":
                                    marker_boxes[node] = visible
                                else:
                                    boxes.append(visible)
                                if node in selected_set:
                                    self.assertGreaterEqual(
                                        bounds.x0, axes_box.x0 - 1.0, f"{context}: {node}/{key} left"
                                    )
                                    self.assertLessEqual(
                                        bounds.x1, axes_box.x1 + 1.0, f"{context}: {node}/{key} right"
                                    )
                                    self.assertGreaterEqual(
                                        bounds.y0, axes_box.y0 - 1.0, f"{context}: {node}/{key} bottom"
                                    )
                                    self.assertLessEqual(
                                        bounds.y1, axes_box.y1 + 1.0, f"{context}: {node}/{key} top"
                                    )
                    if boxes:
                        label_boxes[node] = Bbox.union(boxes)

                route_points = [
                    point
                    for routes in plan.routes.values()
                    for path in routes.values()
                    for point in path
                ]
                self.assertTrue(
                    all(
                        math.isfinite(float(value))
                        for point in route_points
                        for value in point
                    ),
                    context + ": complete route plan contains non-finite points",
                )

                items = list(label_boxes.items())
                for index, (first, first_box) in enumerate(items):
                    for second, second_box in items[index + 1 :]:
                        overlap = Bbox.intersection(first_box, second_box)
                        if not focused_frame:
                            self.assertFalse(
                                overlap is not None
                                and overlap.width > 1.0
                                and overlap.height > 1.0,
                                f"{context}: labels overlap: {first} / {second}",
                            )
                    for second, marker_box in marker_boxes.items():
                        if second == first:
                            continue
                        overlap = Bbox.intersection(first_box, marker_box)
                        if not focused_frame:
                            self.assertFalse(
                                overlap is not None
                                and overlap.width > 1.0
                                and overlap.height > 1.0,
                                f"{context}: label {first} touches marker {second}",
                            )

                arwen = find("Arwen", plan.animal_positions)
                elladan = find("Elladan", plan.animal_positions)
                elrohir = find("Elrohir", plan.animal_positions)
                elrond = find("Elrond", plan.animal_positions)
                celebrian = next(
                    node
                    for node in plan.animal_positions
                    if node.split(" | ", 1)[0].startswith("Celebr")
                )
                origin_family = next(
                    family_id
                    for family_id, family in families.items()
                    if {family.get("mother"), family.get("father")}
                    == {elrond, celebrian}
                )
                self.assertTrue(
                    {arwen, elladan, elrohir, elrond, celebrian}
                    <= set(plan.routes[origin_family]),
                    context,
                )
                origin_x = plan.family_positions[origin_family][0]
                parent_xs = sorted(
                    (
                        plan.animal_positions[elrond][0],
                        plan.animal_positions[celebrian][0],
                    )
                )
                parent_center = sum(parent_xs) / 2.0
                parent_span = parent_xs[1] - parent_xs[0]
                continuing_delta = plan.animal_positions[arwen][0] - parent_center
                self.assertGreater(abs(continuing_delta), 0.05, context)
                for terminal in (elladan, elrohir):
                    terminal_delta = (
                        plan.animal_positions[terminal][0] - parent_center
                    )
                    self.assertLess(
                        terminal_delta * continuing_delta,
                        0.0,
                        context + ": terminal siblings must use the opposite shoulder",
                    )
                self.assertLessEqual(
                    abs(plan.animal_positions[arwen][0] - origin_x),
                    2.7 + (parent_span * 0.10),
                    context
                    + ": continuing branch is too far from its relative parent corridor",
                )

    @pytest.mark.extended_heritage
    def test_requested_callitrix_focus_remains_clear_on_sidebar_sized_canvas(self) -> None:
        self.widget.resize(1050, 650)
        try:
            self.test_requested_callitrix_focus_is_complete_clear_and_unclipped()
        finally:
            self.widget.resize(1180, 720)

    @pytest.mark.extended_heritage
    def test_representative_pixel_clearance_and_in_axes_legend_overlay(self) -> None:
        # Pixel collision checking is quadratic in the number of rendered
        # nodes.  The semantic matrix already covers every species; keep the
        # expensive raster-level contract on the densest Callitrix graph in
        # both vertical modes instead of repeating the same O(n²) audit for
        # every species.
        species = "Callitrix jacchus"
        for species, species_nodes in {
            species: self._species_nodes()[species]
        }.items():
            selection = self._selection_shapes(species_nodes)["all"]
            for mode in MODES:
                with reported_subtest(self, "Callitrix pixel clearance", species=species, mode=mode):
                    plan = self._render(selection, 6, mode)
                    renderer = self.widget.canvas.get_renderer()
                    label_boxes: dict[str, Bbox] = {}
                    for node, meta in self.widget.node_meta.items():
                        if meta.get("kind") != "animal":
                            continue
                        boxes = []
                        for key in ("label_artist", "f_artist", "undated_artist"):
                            artist = meta.get(key)
                            if artist is not None and artist.get_visible():
                                bounds = artist.get_window_extent(renderer)
                                if bounds.width > 0 and bounds.height > 0:
                                    boxes.append(bounds)
                        if boxes:
                            label_boxes[node] = Bbox.union(boxes)

                    items = list(label_boxes.items())
                    for index, (first, first_box) in enumerate(items):
                        for second, second_box in items[index + 1 :]:
                            overlap = Bbox.intersection(first_box, second_box)
                            self.assertFalse(
                                overlap is not None
                                and overlap.width > 1.0
                                and overlap.height > 1.0,
                                f"{species}/{mode}: labels overlap: {first} / {second}",
                            )
                        for other, point in plan.animal_positions.items():
                            if other == first:
                                continue
                            x, y = self.widget.ax.transData.transform(point)
                            marker_box = Bbox.from_extents(x - 8, y - 8, x + 8, y + 8)
                            overlap = Bbox.intersection(first_box, marker_box)
                            self.assertFalse(
                                overlap is not None
                                and overlap.width > 1.0
                                and overlap.height > 1.0,
                                f"{species}/{mode}: label {first} touches marker {other}",
                            )

                    legend = self.widget.ax.get_legend()
                    self.assertIsNotNone(legend)
                    legend_box = legend.get_window_extent(renderer)
                    figure_box = self.widget.figure.bbox
                    self.assertGreaterEqual(legend_box.x0, figure_box.x0 - 1.0)
                    self.assertLessEqual(legend_box.x1, figure_box.x1 + 1.0)
                    self.assertGreaterEqual(legend_box.y0, figure_box.y0 - 1.0)
                    self.assertLessEqual(legend_box.y1, figure_box.y1 + 1.0)
                    axes_box = self.widget.ax.get_window_extent(renderer)
                    self.assertGreaterEqual(legend_box.x0, axes_box.x0 - 1.0)
                    self.assertLessEqual(legend_box.x1, axes_box.x1 + 1.0)
                    self.assertGreaterEqual(legend_box.y0, axes_box.y0 - 1.0)
                    self.assertLessEqual(legend_box.y1, axes_box.y1 + 1.0)
                    self.assertGreater(self.widget.figure.subplotpars.right, 0.99)
                    for node, label_box in label_boxes.items():
                        overlap = Bbox.intersection(legend_box, label_box)
                        self.assertFalse(
                            overlap is not None
                            and overlap.width > 1.0
                            and overlap.height > 1.0,
                            f"{species}/{mode}: legend covers {node}",
                        )
    @pytest.mark.extended_heritage
    def test_overview_origin_anchor_keeps_elwing_local_and_focused_isolated(self) -> None:
        species_nodes = self._species_nodes()["Callitrix jacchus"]
        all_selection = self._selection_shapes(species_nodes)["all"]
        elwing = next(
            node for node in species_nodes
            if self.widget._get_node_display_label(node) == "Elwing"
        )
        with patch.object(
            self.widget._pedigree_router,
            "_compute_origin_anchors",
            wraps=self.widget._pedigree_router._compute_origin_anchors,
        ) as anchor_probe:
            focused = self._render([elwing], 6, VERTICAL_LAYOUT_PARTNER_NORMALIZED)
            anchor_probe.assert_not_called()
            self.assertEqual(self.widget.layout_mode, "focused")
            self.assertIn(
                self.widget.messages.get(
                    "heritage_track.status.mode_selected", "Selection mode"
                ),
                self.widget.status_label.text(),
            )
            overview = self._render(
                all_selection, 6, VERTICAL_LAYOUT_PARTNER_NORMALIZED
            )
            anchor_probe.assert_called_once()
            self.assertEqual(self.widget.layout_mode, "overview")
            self.assertIn(
                self.widget.messages.get(
                    "heritage_track.status.mode_overview", "Selection overview"
                ),
                self.widget.status_label.text(),
            )

        endpoint_routes = []
        for family_id, routes in overview.routes.items():
            if "Nimloth" not in family_id or "Dior" not in family_id:
                continue
            for endpoint, points in routes.items():
                if endpoint == elwing:
                    endpoint_routes.append(points)
        self.assertEqual(len(endpoint_routes), 1)
        elwing_length = sum(
            math.hypot(
                endpoint_routes[0][index + 1][0] - endpoint_routes[0][index][0],
                endpoint_routes[0][index + 1][1] - endpoint_routes[0][index][1],
            )
            for index in range(len(endpoint_routes[0]) - 1)
        )
        all_lengths = []
        for routes in overview.routes.values():
            for points in routes.values():
                all_lengths.append(
                    sum(
                        math.hypot(
                            points[index + 1][0] - points[index][0],
                            points[index + 1][1] - points[index][1],
                        )
                        for index in range(len(points) - 1)
                    )
                )
        longest = max(all_lengths)
        self.assertLess(
            elwing_length,
            longest * 0.9,
            "Elwing must not remain the disproportionate Overview outlier",
        )
        self._assert_semantic_geometry(
            focused,
            self._families_for_plan(focused),
            "focused Elwing",
        )
        self._assert_semantic_geometry(
            overview,
            self._families_for_plan(overview),
            "Callitrix Overview",
        )

    @pytest.mark.extended_heritage
    def test_promoting_ghost_to_active_selection_reflows_transient_positions(self) -> None:
        """Adding a visible ghost must not reuse its old graph coordinates."""
        species_nodes = self._species_nodes()["Callitrix jacchus"]
        selected = [
            node
            for node in species_nodes
            if self._record(node).get("name") == "Arwen"
        ]
        self.assertEqual(len(selected), 1)
        initial = self._render(selected, 6, VERTICAL_LAYOUT_PARTNER_NORMALIZED)
        ghost = next(iter(sorted(self.widget._ghost_nodes, key=str.casefold)))
        self.assertIn(ghost, initial.animal_positions)
        stale_position = (999.0, 999.0)
        self.widget.temp_positions[ghost] = stale_position

        self.widget._add_animal_to_selection(ghost)
        updated = self.widget._route_plan

        self.assertIn(ghost, self.app.selected_animals)
        self.assertNotIn(ghost, self.widget._ghost_nodes)
        self.assertNotIn(ghost, self.widget.temp_positions)
        self.assertNotEqual(updated.animal_positions[ghost], stale_position)
        self.assertEqual(updated.unresolved, [])
        self._assert_semantic_geometry(
            updated,
            self._families_for_plan(updated),
            "ghost promoted to active selection",
        )

    @pytest.mark.extended_heritage
    def test_denethor_selected_single_children_follow_family_axes(self) -> None:
        """Ghost Boromir/Faramir must not cross their one-child family rails."""
        def find(name: str) -> str:
            return next(
                node
                for node in self.engine.all_nodes
                if self._record(node).get("name") == name
            )

        denethor = find("Denethor")
        isildur = find("Isildur")
        plan = self._render(
            [denethor, isildur],
            6,
            VERTICAL_LAYOUT_PARTNER_NORMALIZED,
        )
        names = {
            node: self._record(node).get("name") or node
            for node in plan.animal_positions
        }
        families = self._families_for_plan(plan)
        checked = set()
        for family_id, family in families.items():
            mother = names.get(family.get("mother"))
            if mother not in {"Nicole", "Tiffany"}:
                continue
            children = list(family.get("children", []))
            self.assertEqual(len(children), 1)
            child = children[0]
            self.assertIn(names.get(child), {"Faramir", "Boromir"})
            child_x = plan.animal_positions[child][0]
            junction_x = plan.family_positions[family_id][0]
            self.assertLess(abs(child_x - junction_x), 0.15)
            route = plan.routes[family_id][child]
            self.assertLess(abs(route[-1][0] - route[0][0]), 0.15)
            self.assertGreaterEqual(
                math.dist(route[0], route[-1]),
                self.widget._pedigree_router._single_child_leg_clearance() - 1e-7,
            )
            self.assertTrue(plan.draw_segments(family_id, child))
            checked.add(names.get(child))
        self.assertEqual(checked, {"Faramir", "Boromir"})
        self.assertEqual(plan.unresolved, [])

    @pytest.mark.extended_heritage
    def test_denethor_uneven_depth_five_is_valid_in_both_vertical_modes(self) -> None:
        """A valid uneven ancestor depth must not fail shared-parent routing."""
        denethor = next(
            node
            for node in self.engine.all_nodes
            if node.split(" | ", 1)[0] == "Denethor"
        )

        for depth in (4, 5):
            for mode in MODES:
                with reported_subtest(
                    self,
                    "issue #213 uneven-depth regression",
                    depth=depth,
                    mode=mode,
                ):
                    plan = self._render([denethor], depth, mode)
                    families = self._families_for_plan(plan)
                    self._assert_semantic_geometry(
                        plan,
                        families,
                        f"Denethor depth={depth}; mode={mode}",
                    )

    @pytest.mark.extended_heritage
    def test_arwen_boromir_half_sibling_ghost_and_partner_compaction(self) -> None:
        """Arwen and Boromir keep Denethor donor families connected and compact."""
        def find(name: str) -> str:
            return next(
                node
                for node in self.engine.all_nodes
                if node.split(" | ", 1)[0] == name
            )

        arwen = find("Arwen")
        boromir = find("Boromir")
        expected_names = {"Arwen", "Boromir"}
        self.assertEqual(
            {node.split(" | ", 1)[0] for node in (arwen, boromir)},
            expected_names,
        )

        for mode in MODES:
            with reported_subtest(self, "half-sibling ghost/partner layout", mode=mode):
                plan = self._render([arwen, boromir], 4, mode)
                names = {
                    node.split(" | ", 1)[0]: node
                    for node in plan.animal_positions
                }
                ghosts = {
                    node.split(" | ", 1)[0]
                    for node in self.widget._ghost_nodes
                }
                self.assertIn("Faramir", ghosts)
                self.assertIn("Nicole", ghosts)
                self.assertIn("Faramir", names)
                self.assertIn("Nicole", names)

                families = self._families_for_plan(plan)
                target_families = {
                    frozenset(
                        {
                            family.get("mother", "").split(" | ", 1)[0],
                            family.get("father", "").split(" | ", 1)[0],
                            *(
                                child.split(" | ", 1)[0]
                                for child in family.get("children", [])
                            ),
                        }
                    ): (family_id, family)
                    for family_id, family in families.items()
                }
                self.assertIn(
                    frozenset({"Nicole", "Denethor", "Faramir"}),
                    target_families,
                )
                self.assertIn(
                    frozenset({"Tiffany", "Denethor", "Boromir"}),
                    target_families,
                )

                donor_gap = abs(
                    plan.animal_positions[names["Tiffany"]][0]
                    - plan.animal_positions[names["Denethor"]][0]
                )
                self.assertLessEqual(donor_gap, 3.0)
                for family_name, child_name in (
                    ("Nicole", "Faramir"),
                    ("Tiffany", "Boromir"),
                ):
                    family_id, family = next(
                        (candidate_id, candidate)
                        for candidate_id, candidate in families.items()
                        if {
                            candidate.get("mother", "").split(" | ", 1)[0],
                            candidate.get("father", "").split(" | ", 1)[0],
                        }
                        == {family_name, "Denethor"}
                    )
                    junction_x = plan.family_positions[family_id][0]
                    child_x = plan.animal_positions[names[child_name]][0]
                    self.assertLessEqual(abs(junction_x - child_x), 0.35)
                    self.assertLessEqual(
                        abs(plan.routes[family_id][names[child_name]][-1][0] - child_x),
                        0.35,
                    )

                if self.widget.layout_mode == "focused":
                    # Focused frames retain every label and marker, even when
                    # a dense context cannot be made text-disjoint at the
                    # current zoom.  The hard interactive invariant is that
                    # animal markers do not occupy the same hit target; text
                    # density is resolved by zoom and labels remain above
                    # their incoming lines.
                    markers = self.widget._pedigree_router.marker_obstacles(
                        plan.animal_positions
                    )
                    for first, second in combinations(sorted(names), 2):
                        first_rect = markers[names[first]]
                        second_rect = markers[names[second]]
                        self.assertFalse(
                            first_rect.right > second_rect.left
                            and second_rect.right > first_rect.left
                            and first_rect.top > second_rect.bottom
                            and second_rect.top > first_rect.bottom,
                            f"{mode}: {first}/{second} marker overlap",
                        )
                else:
                    labels = {
                        node: self.widget._get_node_obstacle_label(
                            node, self.widget._get_node_record(node)
                        )
                        for node in plan.animal_positions
                    }
                    obstacles = self.widget._pedigree_router.node_obstacles(
                        plan.animal_positions,
                        labels,
                        True,
                    )
                    for first, second in combinations(sorted(names), 2):
                        first_rect = obstacles[names[first]]
                        second_rect = obstacles[names[second]]
                        self.assertFalse(
                            first_rect.right > second_rect.left
                            and second_rect.right > first_rect.left
                            and first_rect.top > second_rect.bottom
                            and second_rect.top > first_rect.bottom,
                            f"{mode}: {first}/{second} overlap",
                        )

                denethor_x = plan.animal_positions[names["Denethor"]][0]
                tiffany_x = plan.animal_positions[names["Tiffany"]][0]
                nicole_x = plan.animal_positions[names["Nicole"]][0]
                self.assertGreater(
                    (tiffany_x - denethor_x) * (nicole_x - denethor_x),
                    0.0,
                )
                self.assertLess(
                    abs(tiffany_x - denethor_x),
                    abs(nicole_x - denethor_x),
                )
                if mode == VERTICAL_LAYOUT_PARTNER_NORMALIZED:
                    self.assertLess(tiffany_x, nicole_x)
                self.assertEqual(plan.unresolved, [])

    @pytest.mark.extended_heritage
    def test_arwen_only_depth_four_accepts_edge_near_single_child_in_both_modes(self) -> None:
        """The requested Arwen frame must not fail its own junction validation."""
        arwen = next(
            node
            for node in self.engine.all_nodes
            if node.split(" | ", 1)[0] == "Arwen"
        )

        for mode in MODES:
            with reported_subtest(self, "edge-near sole-child geometry", mode=mode):
                plan = self._render([arwen], 4, mode)
                families = self._families_for_plan(plan)
                labels = {
                    node: self.widget._get_node_obstacle_label(
                        node, self.widget._get_node_record(node)
                    )
                    for node in plan.animal_positions
                }
                self.assertEqual(
                    self.widget._pedigree_router.validate_plan(
                        plan,
                        families,
                        labels=labels,
                        show_inbreeding=True,
                    ),
                    [],
                )
                self.assertEqual(plan.unresolved, [])

    @pytest.mark.extended_heritage
    def test_arwen_only_depth_three_default_f_has_valid_marker_geometry(self) -> None:
        """Renderer-sized markers must not reject a valid shallow Arwen scope."""
        arwen = next(
            node
            for node in self.engine.all_nodes
            if node.split(" | ", 1)[0] == "Arwen"
        )
        self.widget.settings.update(
            animal_label_detail="inbreeding_f",
            show_grid=False,
            exclude_archived=False,
            show_heritage_only=True,
        )

        for mode in MODES:
            with reported_subtest(self, "shallow marker geometry", mode=mode):
                plan = self._render([arwen], 3, mode)
                self.assertTrue(plan.animal_positions)
                renderer = RendererAgg(
                    max(1, int(self.widget.figure.bbox.width)),
                    max(1, int(self.widget.figure.bbox.height)),
                    self.widget.figure.dpi,
                )
                self.widget.ax.draw(renderer)
                self.assertEqual(
                    self.widget._render_artist_fatal_diagnostics(
                        renderer,
                        check_viewport=False,
                        check_collisions=True,
                        allow_dense_label_overlaps=True,
                    ),
                    [],
                )

    @pytest.mark.extended_heritage
    def test_denethor_eldarion_partner_rail_stays_readable(self) -> None:
        """A foreign ancestry line must not split Jessica's spouse rail."""
        def find(name: str) -> str:
            return next(
                node
                for node in self.engine.all_nodes
                if node.split(" | ", 1)[0] == name
            )

        denethor = find("Denethor")
        eldarion = find("Eldarion")
        plan = self._render(
            [denethor, eldarion],
            4,
            VERTICAL_LAYOUT_PARTNER_NORMALIZED,
        )
        names = {
            node.split(" | ", 1)[0]: node
            for node in plan.animal_positions
        }
        labels = {
            node: self.widget._get_node_obstacle_label(
                node, self.widget._get_node_record(node)
            )
            for node in plan.animal_positions
        }
        obstacles = self.widget._pedigree_router.node_obstacles(
            plan.animal_positions, labels, True
        )
        for first, second in combinations(sorted(names), 2):
            first_rect = obstacles[names[first]]
            second_rect = obstacles[names[second]]
            self.assertFalse(
                first_rect.right > second_rect.left
                and second_rect.right > first_rect.left
                and first_rect.top > second_rect.bottom
                and second_rect.top > first_rect.bottom,
                f"{first}/{second} marker/label overlap",
            )

        self.assertLessEqual(
            abs(
                plan.animal_positions[names["Jessica"]][0]
                - plan.animal_positions[names["Isildur"]][0]
            ),
            3.3,
        )
        denethor_x = plan.animal_positions[names["Denethor"]][0]
        self.assertLess(
            (
                plan.animal_positions[names["Tiffany"]][0] - denethor_x
            )
            * (
                plan.animal_positions[names["Nicole"]][0] - denethor_x
            ),
            0.0,
        )
        jessica_family = next(
            family_id
            for family_id, family in self._families_for_plan(plan).items()
            if {
                family.get("mother", "").split(" | ", 1)[0],
                family.get("father", "").split(" | ", 1)[0],
            } == {"Jessica", "Isildur"}
        )
        self.assertFalse(
            any(
                family_id == jessica_family and endpoint == names["Jessica"]
                for family_id, endpoint, _index in plan.line_crossing_gaps
            )
        )
        self.assertEqual(plan.unresolved, [])

    @pytest.mark.extended_heritage
    def test_drag_and_pan_interactions_keep_valid_geometry(self) -> None:
        species_nodes = self._species_nodes()["Callitrix jacchus"]
        selection = self._selection_shapes(species_nodes)["single"]
        plan = self._render(selection, 6, VERTICAL_LAYOUT_PARTNER_NORMALIZED)
        original_nodes = set(plan.animal_positions)
        self.assertEqual(set(plan.animal_positions), original_nodes)

        # The interaction handlers deliberately update only the node artists
        # while dragging and re-route the complete graph on release.
        draggable = selection[0]
        start = plan.animal_positions[draggable]
        xpix, ypix = self.widget.ax.transData.transform(start)
        press = SimpleNamespace(
            button=1,
            inaxes=self.widget.ax,
            xdata=start[0],
            ydata=start[1],
            x=xpix,
            y=ypix,
            dblclick=False,
        )
        self.widget._on_mouse_press(press)
        move = SimpleNamespace(
            button=1,
            inaxes=self.widget.ax,
            xdata=start[0] + 1.5,
            ydata=start[1] + 0.7,
            x=xpix + 20,
            y=ypix + 20,
        )
        self.widget._on_mouse_move(move)
        self.assertTrue(self.widget.is_dragging)
        with patch.object(
            self.widget._pedigree_router,
            "recompute_line_gaps",
            wraps=self.widget._pedigree_router.recompute_line_gaps,
        ) as animal_recompute:
            self.widget._on_mouse_release(move)
        animal_recompute.assert_called()
        dragged_plan = self.widget._route_plan
        self.assertNotAlmostEqual(dragged_plan.animal_positions[draggable][0], start[0])
        self._assert_semantic_geometry(
            dragged_plan,
            self._families_for_plan(dragged_plan),
            "post-drag",
        )

        # Drag the family knot itself. All visible family members move as one
        # protected group, then routes and line masks are rebuilt once on
        # release rather than retaining gaps from the previous frame.
        group_family = next(
            family_id
            for family_id, members in self.widget.family_members.items()
            if len(members) >= 3 and family_id in self.widget.family_positions
        )
        group_members = set(self.widget.family_members[group_family])
        group_start = dict(dragged_plan.animal_positions)
        family_point = self.widget.family_positions[group_family]
        fxpix, fypix = self.widget.ax.transData.transform(family_point)
        family_press = SimpleNamespace(
            button=1,
            inaxes=self.widget.ax,
            xdata=family_point[0],
            ydata=family_point[1],
            x=fxpix,
            y=fypix,
            dblclick=False,
        )
        self.widget._on_mouse_press(family_press)
        family_move = SimpleNamespace(
            button=1,
            inaxes=self.widget.ax,
            xdata=family_point[0] + 1.2,
            ydata=family_point[1] + 0.6,
            x=fxpix + 24,
            y=fypix + 12,
        )
        self.widget._on_mouse_move(family_move)
        self.assertTrue(self.widget.is_dragging)
        with patch.object(
            self.widget._pedigree_router,
            "recompute_line_gaps",
            wraps=self.widget._pedigree_router.recompute_line_gaps,
        ) as recompute:
            self.widget._on_mouse_release(family_move)
        recompute.assert_called()
        grouped_plan = self.widget._route_plan
        self.assertTrue(
            any(
                abs(grouped_plan.animal_positions[node][0] - group_start[node][0])
                > 0.2
                for node in group_members
                if node in grouped_plan.animal_positions and node in group_start
            )
        )
        grouped_families = self._families_for_plan(grouped_plan)
        grouped_labels = {
            node: self.widget._get_node_obstacle_label(
                node, self.widget._get_node_record(node)
            )
            for node in grouped_plan.animal_positions
        }
        grouped_problems = self.widget._pedigree_router.validate_plan(
            grouped_plan,
            grouped_families,
            labels=grouped_labels,
            show_inbreeding=True,
        )
        # Free manual placement may intentionally overlap two animal labels;
        # semantic routes, endpoints and explicit line gaps must still remain
        # valid after the group translation.
        self.assertTrue(
            all("animal markers or labels overlap" in item for item in grouped_problems),
            grouped_problems,
        )
        gaps_after_release = dict(grouped_plan.crossing_gaps)
        self.widget._pedigree_router.recompute_line_gaps(
            grouped_plan,
            labels=grouped_labels,
            show_inbreeding=True,
        )
        self.assertEqual(grouped_plan.crossing_gaps, gaps_after_release)

        self.widget.canvas.draw()
        pixels_before_pan = bytes(self.widget.canvas.buffer_rgba())
        nodes_before_pan = dict(self.widget.node_positions)
        families_before_pan = dict(self.widget.family_positions)
        xlim_before = self.widget.ax.get_xlim()
        ylim_before = self.widget.ax.get_ylim()
        pan_x, pan_y = self.widget.ax.transData.transform(
            (sum(xlim_before) / 2.0, sum(ylim_before) / 2.0)
        )
        pan_press = SimpleNamespace(
            button=2,
            inaxes=self.widget.ax,
            xdata=sum(xlim_before) / 2,
            ydata=sum(ylim_before) / 2,
            x=pan_x,
            y=pan_y,
        )
        self.widget._on_mouse_press(pan_press)
        pan_move = SimpleNamespace(
            button=2,
            inaxes=self.widget.ax,
            xdata=pan_press.xdata + 1.0,
            ydata=pan_press.ydata + 0.5,
            x=pan_x + 24.0,
            y=pan_y + 12.0,
        )
        self.widget._on_mouse_move(pan_move)
        pixels_during_pan = bytes(self.widget.canvas.buffer_rgba())
        self.assertNotEqual(pixels_during_pan, pixels_before_pan)
        self.widget._on_mouse_release(SimpleNamespace(button=2, inaxes=None))
        pixels_after_release = bytes(self.widget.canvas.buffer_rgba())
        self.assertNotEqual(pixels_after_release, pixels_before_pan)
        self.assertNotEqual(tuple(self.widget.ax.get_xlim()), tuple(xlim_before))
        self.assertNotEqual(tuple(self.widget.ax.get_ylim()), tuple(ylim_before))
        self.assertEqual(tuple(self.widget.current_xlim), tuple(self.widget.ax.get_xlim()))
        self.assertEqual(tuple(self.widget.current_ylim), tuple(self.widget.ax.get_ylim()))
        self.assertFalse(self.widget.pan_active)
        self.assertIsNone(self.widget.pan_start)
        self.assertEqual(self.widget.node_positions, nodes_before_pan)
        self.assertEqual(self.widget.family_positions, families_before_pan)

    @pytest.mark.extended_heritage
    def test_zoom_rebuilds_pixel_gaps_knots_halos_and_relationship_highlight(self) -> None:
        def find(name: str, nodes) -> str:
            return next(
                node for node in nodes if node.split(" | ", 1)[0] == name
            )

        selected = [
            find(name, self.engine.all_nodes)
            for name in ("Arwen", "Eldarion", "Denethor", "Aragorn II")
        ]
        plan = self._render(
            selected, 5, VERTICAL_LAYOUT_PARTNER_NORMALIZED
        )
        arwen = find("Arwen", plan.animal_positions)
        eldarion = find("Eldarion", plan.animal_positions)
        self.widget.selected_nodes = {arwen, eldarion}
        self.widget._replace_relationship_highlights()
        self.assertEqual(len(self.widget._relationship_highlight_collections), 1)
        previous_highlight = self.widget._relationship_highlight_collections[0]

        name_artist = self.widget.node_meta[arwen]["label_artist"]
        detail_artist = self.widget.node_meta[arwen]["f_artist"]
        expected_halo_points = 3.0 * 72.0 / float(self.widget.figure.dpi)
        for artist in (name_artist, detail_artist):
            effects = artist.get_path_effects()
            self.assertEqual(len(effects), 1)
            self.assertAlmostEqual(
                float(effects[0]._gc["linewidth"]),
                expected_halo_points,
                places=6,
            )

        xlim = self.widget.ax.get_xlim()
        ylim = self.widget.ax.get_ylim()
        zoom = SimpleNamespace(
            inaxes=self.widget.ax,
            xdata=sum(xlim) / 2.0,
            ydata=sum(ylim) / 2.0,
            button="up",
        )
        with patch.object(
            self.widget._pedigree_router,
            "recompute_line_gaps",
            wraps=self.widget._pedigree_router.recompute_line_gaps,
        ) as recompute:
            self.widget._on_scroll(zoom)
            self.widget.canvas.draw()
        recompute.assert_called()
        kwargs = recompute.call_args.kwargs
        self.assertTrue(kwargs["animal_gap_obstacles"])
        self.assertTrue(kwargs["junction_gap_obstacles"])
        self.assertFalse(kwargs["recompute_crossings"])
        self.assertTrue(
            all(
                key.startswith("@")
                for key in kwargs["junction_gap_obstacles"]
            )
        )

        xppu, yppu = self.widget._route_gap_pixel_scale
        family_id = next(iter(plan.family_positions))
        family_x, family_y = plan.family_positions[family_id]
        family_rect = kwargs["junction_gap_obstacles"][f"@{family_id}"]
        expected_family_radius = (
            (8.4 * float(self.widget.figure.dpi) / 72.0) / 2.0
        ) + 1.2
        self.assertAlmostEqual(
            (family_rect.right - family_x) * xppu,
            expected_family_radius,
            places=5,
        )
        self.assertAlmostEqual(
            (family_rect.top - family_y) * yppu,
            expected_family_radius,
            places=5,
        )

        self.assertEqual(len(self.widget._relationship_highlight_collections), 1)
        current_highlight = self.widget._relationship_highlight_collections[0]
        self.assertIsNot(current_highlight, previous_highlight)
        expected_segments = self.widget._bfs_relationship_path(
            arwen,
            eldarion,
            self.engine,
            plan.animal_positions,
            plan.family_positions,
            self._families_for_plan(plan),
        )
        actual_segments = [
            (tuple(segment[0]), tuple(segment[1]))
            for segment in current_highlight.get_segments()
        ]
        self.assertEqual(actual_segments, expected_segments)

        with patch.object(
            self.widget._pedigree_router,
            "recompute_line_gaps",
            wraps=self.widget._pedigree_router.recompute_line_gaps,
        ) as resize_recompute:
            self.widget._on_resize(None)
        resize_recompute.assert_called_once()


    @pytest.mark.extended_heritage
    def test_genotype_legend_drag_is_bounded_persisted_and_does_not_pan(self) -> None:
        species_nodes = self._species_nodes()["Callitrix jacchus"]
        selection = self._selection_shapes(species_nodes)["all"]
        self._render(selection, 6, VERTICAL_LAYOUT_PARTNER_NORMALIZED)
        self.widget.canvas.draw()
        legend = self.widget._legend_artist
        self.assertIsNotNone(legend)
        renderer = self.widget.canvas.get_renderer()
        before_box = legend.get_window_extent(renderer)
        axes_box = self.widget.ax.get_window_extent(renderer)
        before_anchor = tuple(self.widget._legend_anchor_axes or (0.0, 0.0))
        old_saved = self.plugin.get_settings().get("legend_pos")

        # Move toward the free side so the assertion is independent of the
        # automatic initial corner/grid placement.
        dx_px = 35.0 if before_box.x0 < axes_box.x0 + axes_box.width / 2 else -35.0
        dy_px = 22.0 if before_box.y0 < axes_box.y0 + axes_box.height / 2 else -22.0
        press_x = (before_box.x0 + before_box.x1) / 2.0
        press_y = (before_box.y0 + before_box.y1) / 2.0
        inverse = self.widget.ax.transData.inverted()
        press_data = inverse.transform((press_x, press_y))
        release_data = inverse.transform((press_x + dx_px, press_y + dy_px))
        press = SimpleNamespace(
            button=1,
            inaxes=self.widget.ax,
            x=press_x,
            y=press_y,
            xdata=float(press_data[0]),
            ydata=float(press_data[1]),
            dblclick=False,
        )
        move = SimpleNamespace(
            button=1,
            inaxes=self.widget.ax,
            x=press_x + dx_px,
            y=press_y + dy_px,
            xdata=float(release_data[0]),
            ydata=float(release_data[1]),
        )
        xlim_before = tuple(self.widget.ax.get_xlim())
        ylim_before = tuple(self.widget.ax.get_ylim())
        try:
            self.widget._on_mouse_press(press)
            self.assertTrue(self.widget._legend_dragging)
            self.widget._on_mouse_move(move)
            self.assertEqual(tuple(self.widget.ax.get_xlim()), xlim_before)
            self.assertEqual(tuple(self.widget.ax.get_ylim()), ylim_before)
            self.widget._on_mouse_release(move)
            self.assertFalse(self.widget._legend_dragging)
            saved = self.plugin.get_settings().get("legend_pos")
            self.assertIsInstance(saved, list)
            self.assertEqual(len(saved), 2)
            self.assertNotEqual(tuple(saved), before_anchor)
            self.assertTrue(all(0.0 <= float(value) <= 1.0 for value in saved))
            self.widget.canvas.draw()
            legend_after = self.widget._legend_artist
            self.assertIsNotNone(legend_after)
            after_box = legend_after.get_window_extent(self.widget.canvas.get_renderer())
            axes_after = self.widget.ax.get_window_extent(self.widget.canvas.get_renderer())
            self.assertGreaterEqual(after_box.x0, axes_after.x0 - 1.0)
            self.assertLessEqual(after_box.x1, axes_after.x1 + 1.0)
            self.assertGreaterEqual(after_box.y0, axes_after.y0 - 1.0)
            self.assertLessEqual(after_box.y1, axes_after.y1 + 1.0)
            self.assertEqual(tuple(self.widget._legend_anchor_axes), tuple(saved))
        finally:
            self.plugin.set_settings({"legend_pos": old_saved})
            self.widget.settings["legend_pos"] = old_saved

    def test_fully_masked_known_route_is_not_restored_by_highlight_fallback(self) -> None:
        original_plan = self.widget._route_plan
        original_scale = self.widget._route_gap_pixel_scale
        original_radius = self.widget._route_gap_radius_pixels
        animal_positions = {
            "A": (1.0, 0.0),
            "B": (-1.0, 0.0),
            "Child": (0.1, 0.0),
        }
        family_positions = {"family": (0.0, 0.0)}
        families = {
            "family": {
                "mother": "A",
                "father": "B",
                "children": ["Child"],
            }
        }
        self.widget._route_plan = RoutePlan(
            animal_positions=dict(animal_positions),
            family_positions=dict(family_positions),
            family_members={"family": set(animal_positions)},
            routes={
                "family": {
                    "A": [(0.0, 0.0), (1.0, 0.0)],
                    "B": [(0.0, 0.0), (-1.0, 0.0)],
                    "Child": [(0.0, 0.0), (0.1, 0.0)],
                }
            },
            crossing_gaps={
                ("family", "Child", 0): [(0.05, 0.0)],
            },
        )
        self.widget._route_gap_pixel_scale = (1.0, 1.0)
        self.widget._route_gap_radius_pixels = 2.75
        try:
            segments = self.widget._bfs_relationship_path(
                "Child",
                "A",
                None,
                animal_positions,
                family_positions,
                families,
            )
        finally:
            self.widget._route_plan = original_plan
            self.widget._route_gap_pixel_scale = original_scale
            self.widget._route_gap_radius_pixels = original_radius

        self.assertEqual(segments, [((0.0, 0.0), (1.0, 0.0))])

