# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track semantic pedigree connector router.

from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations, permutations
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, MutableMapping, Optional, Sequence, Set, Tuple

Point = Tuple[float, float]
Segment = Tuple[Point, Point]
RouteKey = Tuple[str, str, int]

_EPSILON = 1e-7
LAYOUT_MODE_FOCUSED = "focused"
LAYOUT_MODE_OVERVIEW = "overview"


@dataclass(frozen=True)
class Rect:
    """Axis-aligned render obstacle in Heritage Track data coordinates."""

    left: float
    right: float
    bottom: float
    top: float

    def contains(self, point: Point, *, margin: float = 0.0) -> bool:
        x, y = point
        return (
            self.left - margin <= x <= self.right + margin
            and self.bottom - margin <= y <= self.top + margin
        )

    def intersects(self, segment: Segment, *, margin: float = 0.0) -> bool:
        (x1, y1), (x2, y2) = segment
        if (
            max(x1, x2) < self.left - margin
            or min(x1, x2) > self.right + margin
            or max(y1, y2) < self.bottom - margin
            or min(y1, y2) > self.top + margin
        ):
            return False
        if abs(x1 - x2) <= _EPSILON:
            return (
                self.left - margin <= x1 <= self.right + margin
                and _ranges_overlap(y1, y2, self.bottom - margin, self.top + margin)
            )
        if abs(y1 - y2) <= _EPSILON:
            return (
                self.bottom - margin <= y1 <= self.top + margin
                and _ranges_overlap(x1, x2, self.left - margin, self.right + margin)
            )
        expanded = Rect(
            self.left - margin,
            self.right + margin,
            self.bottom - margin,
            self.top + margin,
        )
        if expanded.contains(segment[0]) or expanded.contains(segment[1]):
            return True
        corners = (
            (expanded.left, expanded.bottom),
            (expanded.right, expanded.bottom),
            (expanded.right, expanded.top),
            (expanded.left, expanded.top),
        )
        edges = tuple(zip(corners, corners[1:] + corners[:1]))
        return any(_segment_relation(segment, edge)[0] != "none" for edge in edges)


@dataclass
class RoutePlan:
    """Complete render geometry for one Heritage Track frame."""

    animal_positions: Dict[str, Point]
    family_positions: Dict[str, Point]
    family_members: Dict[str, Set[str]]
    routes: Dict[str, Dict[str, List[Point]]]
    crossing_gaps: Dict[RouteKey, List[Point]] = field(default_factory=dict)
    unresolved: List[str] = field(default_factory=list)
    line_crossing_gaps: Dict[RouteKey, List[Point]] = field(default_factory=dict)
    line_crossing_problems: List[str] = field(default_factory=list)
    line_crossings_ready: bool = False
    # Monotonic structural revision. Any caller that mutates positions,
    # family junctions, or routes must mark the plan before recomputing gaps.
    geometry_revision: int = 0
    gap_geometry_revision: int = -1
    pixel_gap_revision: int = 0
    # Internal diagnostics only: endpoint routes that had to accept an
    # obstacle overlap while preserving canonical topology.
    route_obstacle_hits: List[str] = field(default_factory=list)

    def mark_geometry_changed(self) -> None:
        """Invalidate structural geometry caches after an in-place edit."""
        self.geometry_revision += 1
        self.line_crossings_ready = False
        self.gap_geometry_revision = -1

    def route_segments(self, family_id: str, endpoint: str) -> List[Segment]:
        return _path_segments(self.routes.get(family_id, {}).get(endpoint, []))

    def draw_segments(
        self,
        family_id: str,
        endpoint: str,
        *,
        gap_radius: float = 0.10,
        gap_radius_pixels: Optional[float] = None,
        pixel_scale: Optional[Tuple[float, float]] = None,
    ) -> List[Segment]:
        """Return route segments split at explicit non-junction crossings."""
        output: List[Segment] = []
        for index, segment in enumerate(self.route_segments(family_id, endpoint)):
            gaps = self.crossing_gaps.get((family_id, endpoint, index), [])
            segment_gap_radius = gap_radius
            if gap_radius_pixels is not None and pixel_scale is not None:
                (x1, y1), (x2, y2) = segment
                dx = x2 - x1
                dy = y2 - y1
                data_length = math.hypot(dx, dy)
                pixel_length = math.hypot(
                    dx * max(1.0, float(pixel_scale[0])),
                    dy * max(1.0, float(pixel_scale[1])),
                )
                if pixel_length > _EPSILON:
                    segment_gap_radius = (
                        max(0.0, float(gap_radius_pixels))
                        * data_length
                        / pixel_length
                    )
            output.extend(
                _split_segment_at_gaps(segment, gaps, segment_gap_radius)
            )
        return output

    def all_points(self) -> List[Point]:
        points = list(self.animal_positions.values()) + list(self.family_positions.values())
        for endpoint_routes in self.routes.values():
            for path in endpoint_routes.values():
                points.extend(path)
        return points


@dataclass(frozen=True)
class _OwnedSegment:
    family_id: str
    endpoint: str
    index: int
    segment: Segment


class PedigreeRouter:
    """Deterministic, obstacle-aware router for semantic family connections."""

    def __init__(
        self,
        *,
        automatic_x_scale: float = 1.45,
        node_gap: float = 0.38,
        route_clearance: float = 0.16,
        junction_clearance: float = 0.30,
    ):
        self.automatic_x_scale = max(1.0, float(automatic_x_scale))
        self.node_gap = max(0.05, float(node_gap))
        self.route_clearance = max(0.05, float(route_clearance))
        self.junction_clearance = max(0.15, float(junction_clearance))
        # Labels are point-sized, so their data-space footprint depends on the
        # drawable axes. Keep the default neutral for standalone router users;
        # the current widget's in-axes legend does not narrow this geometry.
        self.label_width_scale = 1.0
        self.label_height_scale = 1.0
        # Highest conflict-free value in the current-seed terminal-sibling
        # sweep; it protects compact focused branches without forcing knots.
        self.focused_branch_weight = 512.0

    def plan(
        self,
        animal_positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
        *,
        labels: Optional[Mapping[str, str]] = None,
        protected_nodes: Optional[Set[str]] = None,
        focus_nodes: Optional[Set[str]] = None,
        show_inbreeding: bool = True,
        vertical_layout_mode: str = "partner_normalized",
    ) -> RoutePlan:
        labels = labels or {}
        protected = set(protected_nodes or set())
        focus = set(focus_nodes or set()) & set(animal_positions)
        chronological = str(vertical_layout_mode or "").strip().casefold() == "chronological"
        # Keep the mode explicit at the router boundary.  The overview-only
        # origin-anchor rule must never leak into a focused/small selection.
        layout_mode = (
            LAYOUT_MODE_FOCUSED
            if bool(focus) and len(focus) <= 8
            else LAYOUT_MODE_OVERVIEW
        )
        adjusted = self._arrange_nodes(
            animal_positions,
            families,
            labels,
            protected,
            show_inbreeding,
            preserve_y=chronological,
            prefer_descendant_order=layout_mode == LAYOUT_MODE_FOCUSED,
            focus_nodes=focus,
        )
        animal_obstacles = self.node_obstacles(adjusted, labels, show_inbreeding)
        family_positions = self._place_junctions(
            adjusted,
            families,
            animal_obstacles,
            chronological=chronological,
        )
        family_members = self._family_members(adjusted, families)
        cycle_nodes = self._parentage_cycle_nodes(adjusted, families)

        junction_obstacles = {
            f"@{family_id}": Rect(
                point[0] - self.junction_clearance,
                point[0] + self.junction_clearance,
                point[1] - self.junction_clearance,
                point[1] + self.junction_clearance,
            )
            for family_id, point in family_positions.items()
        }
        obstacles = {**animal_obstacles, **junction_obstacles}

        routes: Dict[str, Dict[str, List[Point]]] = {}
        owned_segments: List[_OwnedSegment] = []
        unresolved: List[str] = []
        route_obstacle_hits: List[str] = []
        route_obstacle_index = (
            self._build_rect_spatial_index(obstacles)
            if len(adjusted) > 256
            else None
        )
        # Route scoring is incremental: for a large sparse pedigree, testing
        # every new segment against every already-owned segment is needlessly
        # quadratic. Keep a conservative bounding-box index for this path.
        # Normal/current-seed graphs retain the exact all-segment scorer; large
        # graphs only narrow the candidates (the relation test is unchanged).
        owned_segment_index = (
            defaultdict(list) if len(adjusted) > 256 else None
        )
        if cycle_nodes:
            unresolved.append(
                "invalid directed parentage cycle: "
                + ", ".join(sorted(cycle_nodes, key=str.casefold))
            )

        ordered_families = sorted(
            family_positions,
            key=lambda fid: (
                -round(family_positions[fid][1], 6),
                round(family_positions[fid][0], 6),
                fid.casefold(),
            ),
        )
        for family_id in ordered_families:
            family = families.get(family_id, {})
            endpoint_routes: Dict[str, List[Point]] = {}
            endpoints = self._ordered_endpoints(family, adjusted)
            parents = set(self._parents(family)) & set(adjusted)
            junction = family_positions[family_id]
            new_owned: List[_OwnedSegment] = []

            for endpoint in endpoints:
                path, path_has_overlap, path_hits_obstacle = self._route_endpoint(
                    family_id,
                    endpoint,
                    junction,
                    adjusted[endpoint],
                    obstacles,
                    owned_segments,
                    parent_entry=endpoint in parents,
                    obstacle_index=route_obstacle_index,
                    owned_segment_index=owned_segment_index,
                )
                endpoint_routes[endpoint] = path
                if path_hits_obstacle:
                    route_obstacle_hits.append(f"{family_id}:{endpoint}")
                if path_has_overlap:
                    unresolved.append(
                        f"{family_id}: route to {endpoint} shares a segment with another family"
                    )
                for index, segment in enumerate(_path_segments(path)):
                    new_owned.append(_OwnedSegment(family_id, endpoint, index, segment))

            routes[family_id] = endpoint_routes
            owned_segments.extend(new_owned)
            if owned_segment_index is not None:
                for owned in new_owned:
                    self._index_owned_segment(owned_segment_index, owned)

        plan = RoutePlan(
            animal_positions=adjusted,
            family_positions=family_positions,
            family_members=family_members,
            routes=routes,
            unresolved=sorted(set(unresolved)),
            route_obstacle_hits=sorted(set(route_obstacle_hits)),
        )
        self.recompute_line_gaps(
            plan,
            labels=labels,
            show_inbreeding=show_inbreeding,
        )
        return plan

    def recompute_line_gaps(
        self,
        plan: RoutePlan,
        *,
        labels: Optional[Mapping[str, str]] = None,
        show_inbreeding: bool = True,
        animal_gap_obstacles: Optional[Mapping[str, Rect]] = None,
        junction_gap_obstacles: Optional[Mapping[str, Rect]] = None,
        recompute_crossings: bool = True,
    ) -> None:
        """Rebuild every visible crossing/obstacle gap from current geometry.

        Manual animal and family-group moves create a fresh route plan on
        release. Keeping gap discovery in this single public operation makes
        that redraw incapable of reusing masks from the previous coordinates;
        tests can also exercise it directly after an interaction transform.

        Animal *labels* deliberately are not gap obstacles. They are rendered
        with a white halo, so masking the underlying genealogy line to the
        complete name/detail rectangle would create a misleading detached
        stub. Only the marker itself, a family junction, or a real line/line
        crossing may introduce a visible gap. The widget supplies pixel-
        calibrated marker rectangles after its final viewport is known.
        """
        labels = labels or {}
        owned_segments = self._owned_segments(plan.routes)
        animal_obstacles = dict(
            animal_gap_obstacles
            if animal_gap_obstacles is not None
            else self.marker_obstacles(plan.animal_positions)
        )
        junction_obstacles = dict(
            junction_gap_obstacles
            if junction_gap_obstacles is not None
            else {
                f"@{family_id}": Rect(
                    point[0] - self.junction_clearance,
                    point[0] + self.junction_clearance,
                    point[1] - self.junction_clearance,
                    point[1] + self.junction_clearance,
                )
                for family_id, point in plan.family_positions.items()
            }
        )
        if recompute_crossings or not plan.line_crossings_ready:
            crossing_gaps, crossing_problems = self._find_crossing_gaps(
                owned_segments,
                plan.animal_positions,
            )
            plan.line_crossing_gaps = {
                key: list(points) for key, points in crossing_gaps.items()
            }
            plan.line_crossing_problems = list(crossing_problems)
            plan.line_crossings_ready = True
        else:
            # Zoom and resize are affine view transforms: route coordinates and
            # line/line intersections do not change. Reusing this structural
            # scan avoids an O(E²) segment comparison on every wheel event;
            # marker and family-knot obstacles below are still rebuilt from the
            # final current pixel scale.
            crossing_gaps = {
                key: list(points)
                for key, points in plan.line_crossing_gaps.items()
            }
            crossing_problems = list(plan.line_crossing_problems)
        obstacle_gaps = self._find_obstacle_gaps(
            owned_segments,
            {**animal_obstacles, **junction_obstacles},
        )
        for key, points in obstacle_gaps.items():
            crossing_gaps.setdefault(key, []).extend(points)
        plan.crossing_gaps = {
            key: sorted(
                {(round(x, 7), round(y, 7)) for x, y in points},
                key=lambda point: (point[0], point[1]),
            )
            for key, points in crossing_gaps.items()
            if points
        }
        # Repeated recomputation must replace, rather than accumulate, the two
        # diagnostics generated by crossing discovery.  Other topology and
        # endpoint-routing diagnostics remain owned by ``plan``.
        retained = [
            problem
            for problem in plan.unresolved
            if not problem.endswith("different families share a segment")
            and not problem.endswith(
                "different families touch without a routable gap"
            )
        ]
        plan.unresolved = sorted(set(retained + crossing_problems))
        plan.gap_geometry_revision = plan.geometry_revision
        plan.pixel_gap_revision += 1

    @staticmethod
    def marker_obstacles(
        positions: Mapping[str, Point],
        *,
        half_width: float = 0.30,
        half_height: float = 0.30,
    ) -> Dict[str, Rect]:
        """Return marker-only route masks in data coordinates.

        Marker dimensions are deliberately independent of name/detail text.
        Callers with a live canvas should convert the point-sized marker to
        data units and pass the resulting half sizes.
        """
        width = max(0.02, float(half_width))
        height = max(0.02, float(half_height))
        return {
            node: Rect(x - width, x + width, y - height, y + height)
            for node, (x, y) in positions.items()
        }

    def node_obstacles(
        self,
        positions: Mapping[str, Point],
        labels: Mapping[str, str],
        show_inbreeding: bool,
    ) -> Dict[str, Rect]:
        obstacles: Dict[str, Rect] = {}
        for node, (x, y) in positions.items():
            label = str(labels.get(node, node)).strip()
            # The renderer uses a 9 pt primary label and a 7 pt secondary
            # label.  A character-count-only estimate of ``0.075`` data units
            # per character was consistently too small for proportional fonts
            # (especially for dates, IDs, and names containing wide glyphs),
            # allowing labels to touch even when the marker centres did not.
            # Keep one bounded estimate in the router so placement, routing,
            # and validation use the same footprint.
            label_half_width = max(0.48, (self._estimated_label_width(label) / 2.0))
            # Text is point-sized while positions are data-sized.  The widget
            # supplies the vertical display scale so focused views with a
            # compressed Y aspect still reserve the real two-line label and
            # marker footprint in pixels.
            height_scale = max(1.0, float(self.label_height_scale))
            bottom_offset = (0.78 if show_inbreeding else 0.56) * height_scale
            top_offset = 0.34 * height_scale
            obstacles[node] = Rect(
                x - label_half_width,
                x + label_half_width,
                y - bottom_offset,
                y + top_offset,
            )
        return obstacles

    def _estimated_label_width(self, label: str) -> float:
        """Estimate the rendered width of a Heritage label in data units.

        Matplotlib text extents are only available after a renderer exists.
        This conservative, proportional-font estimate is deliberately shared
        by all geometry passes and capped only by the actual text length; it
        prevents dense rows without making short names needlessly far apart.
        """
        text = str(label or "").strip()
        if not text:
            return 1.56
        width = 0.30  # left/right breathing room around marker and text
        for char in text:
            if char.isspace():
                width += 0.075
            elif char in "MW@%#&QGOD" or ord(char) > 0x2E80:
                width += 0.175
            elif char in "ilI.,:;!|()[]{}'`\"":
                width += 0.085
            else:
                width += 0.170
        # Even a short name can have the wider secondary line rendered below
        # it (for example ``F: 0.0000``).  Keeping that real minimum here also
        # prevents two short partner names from visually merging.
        return max(1.56, width) * max(1.0, float(self.label_width_scale))

    def validate_plan(
        self,
        plan: RoutePlan,
        families: Mapping[str, Mapping[str, object]],
        *,
        labels: Optional[Mapping[str, str]] = None,
        show_inbreeding: bool = True,
    ) -> List[str]:
        """Return semantic geometry violations; intended for tests and diagnostics."""
        labels = labels or {}
        problems: List[str] = []
        animal_obstacles = self.node_obstacles(
            plan.animal_positions,
            labels,
            show_inbreeding,
        )
        marker_obstacles = self.marker_obstacles(plan.animal_positions)
        if all(
            isinstance(labels.get(node, node), str)
            for node in plan.animal_positions
        ):
            obstacle_items = sorted(
                animal_obstacles.items(), key=lambda item: item[0].casefold()
            )
            for index, (first_node, first_rect) in enumerate(obstacle_items):
                for second_node, second_rect in obstacle_items[index + 1 :]:
                    if (
                        _ranges_overlap(
                            first_rect.left,
                            first_rect.right,
                            second_rect.left,
                            second_rect.right,
                        )
                        and _ranges_overlap(
                            first_rect.bottom,
                            first_rect.top,
                            second_rect.bottom,
                            second_rect.top,
                        )
                    ):
                        problems.append(
                            f"{first_node}/{second_node}: animal markers or labels overlap"
                        )

        for family_id, endpoint_routes in plan.routes.items():
            family = families.get(family_id, {})
            expected = set(self._ordered_endpoints(family, plan.animal_positions))
            parents = set(self._parents(family)) & set(plan.animal_positions)
            if set(endpoint_routes) != expected:
                problems.append(f"{family_id}: routed endpoints do not match semantic family members")
            junction = plan.family_positions.get(family_id)
            if junction is None:
                problems.append(f"{family_id}: missing family junction")
                continue
            if len(parents) == 2:
                parent_xs = sorted(plan.animal_positions[parent][0] for parent in parents)
                midpoint = sum(parent_xs) / 2.0
                span = parent_xs[1] - parent_xs[0]
                allowed_shift = min(
                    1.35,
                    span * 0.22,
                    max(0.0, (span / 2.0) - 0.08),
                )
                visible_children = [
                    child for child in self._children(family)
                    if child in plan.animal_positions
                ]
                if len(visible_children) == 1:
                    child_x = plan.animal_positions[visible_children[0]][0]
                    child_clearance = max(0.35, self.node_gap)
                    if (
                        parent_xs[0] + child_clearance
                        <= child_x
                        <= parent_xs[1] - child_clearance
                    ):
                        allowed_shift = min(
                            max(allowed_shift, abs(child_x - midpoint) + self.route_clearance),
                            max(0.0, (span / 2.0) - 0.08),
                        )
                if not parent_xs[0] < junction[0] < parent_xs[1]:
                    problems.append(
                        f"{family_id}: junction is not between both parents"
                    )
                elif abs(junction[0] - midpoint) > allowed_shift + _EPSILON:
                    problems.append(
                        f"{family_id}: junction is excessively displaced from the parent midpoint"
                    )
            for node, rect in animal_obstacles.items():
                if node not in plan.family_members.get(family_id, set()) and rect.contains(junction):
                    problems.append(f"{family_id}: family junction intersects foreign node {node}")

            for endpoint, path in endpoint_routes.items():
                if not path or not _points_equal(path[0], junction):
                    problems.append(f"{family_id}: route to {endpoint} does not start at its junction")
                    continue
                if endpoint not in plan.animal_positions or not _points_equal(
                    path[-1], plan.animal_positions[endpoint]
                ):
                    problems.append(f"{family_id}: route to {endpoint} does not end at its animal")
                    continue
                segments = _path_segments(path)
                if endpoint in parents and not _has_parent_entry_shape(segments):
                    problems.append(
                        f"{family_id}: parent route to {endpoint} lacks horizontal junction entry and vertical parent entry"
                    )
                if endpoint in parents and len(segments) > 2:
                    problems.append(
                        f"{family_id}: parent route to {endpoint} contains an unnecessary multi-bend dogleg"
                    )
                if endpoint not in parents and len(segments) != 1:
                    problems.append(
                        f"{family_id}: descendant route to {endpoint} is not one direct segment"
                    )
                for index, segment in enumerate(segments):
                    for node, rect in marker_obstacles.items():
                        # A route is expected to enter its own endpoint marker.
                        # That marker is never a foreign obstacle, regardless
                        # of which canonical parent segment first reaches its
                        # label/marker rectangle.
                        if node == endpoint:
                            continue
                        if rect.intersects(segment, margin=0.01):
                            route_gaps = plan.crossing_gaps.get((family_id, endpoint, index), [])
                            if any(rect.contains(point, margin=0.08) for point in route_gaps):
                                continue
                            problems.append(
                                f"{family_id}: route to {endpoint} intersects foreign animal marker {node}"
                            )
                            break

        owned = self._owned_segments(plan.routes)
        for index, first in enumerate(owned):
            for second in owned[index + 1 :]:
                if first.family_id == second.family_id:
                    continue
                relation, point = _segment_relation(first.segment, second.segment)
                if relation == "none":
                    continue
                if self._is_shared_animal_endpoint(first, second, point, plan.animal_positions):
                    continue
                if relation == "overlap":
                    problems.append(
                        f"{first.family_id}/{second.family_id}: different families share a segment"
                    )
                elif not self._crossing_is_gapped(first, second, point, plan.crossing_gaps):
                    problems.append(
                        f"{first.family_id}/{second.family_id}: crossing lacks a visible gap"
                    )

        return sorted(set(problems))

    def _arrange_nodes(
        self,
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        show_inbreeding: bool,
        *,
        preserve_y: bool = False,
        prefer_descendant_order: bool = False,
        focus_nodes: Optional[Set[str]] = None,
    ) -> Dict[str, Point]:
        adjusted = {node: (float(point[0]), float(point[1])) for node, point in positions.items()}
        if not adjusted:
            return adjusted

        if families:
            locked_positions = {
                node: adjusted[node]
                for node in protected
                if node in adjusted
            }
            # A large overview stress graph should remain responsive even
            # when it contains a long sparse pedigree.  The full refinement
            # pipeline is retained for all normal/current-seed views; above
            # this explicit budget use only linear generation rows and local
            # row de-overlap.  The semantic routes are still produced by the
            # same canonical router below, so this is a performance fallback,
            # not a second topology implementation.
            if len(adjusted) > 256 and not protected:
                if not preserve_y:
                    self._assign_generation_rows(adjusted, families)
                for row in self._cluster_rows(adjusted):
                    self._deoverlap_row(
                        adjusted, row, labels, protected, show_inbreeding
                    )
                return adjusted
            if not preserve_y:
                self._assign_generation_rows(adjusted, families)
            # Keep the seed/layout-pipeline origin of every visible child
            # stable while the large overview is packed. Without this
            # snapshot, nested sole-child families can move a shared partner
            # block again in every overview round.
            overview_mode = (
                not prefer_descendant_order and not protected
            )
            overview_origin_anchors = (
                self._compute_origin_anchors(adjusted, families)
                if overview_mode
                else {}
            )
            # ``LayoutPipeline`` already supplies component-aware, stable X
            # coordinates.  The former recursive partner-block pass laid out
            # the same shared ancestry once for every descendant root.  In a
            # pedigree DAG (siblings, repeated mates, or inbreeding) that
            # double-counted whole branches and could throw a compact family
            # tens of units away from its relatives.  Keep the seed ordering
            # and limit this pass to row clearance plus the one-child rule.
            partner_blocks: Dict[str, Set[str]] = {}
            if not protected:
                if prefer_descendant_order:
                    # A focused ancestry view starts with the selected/young
                    # end and reflects complete branches outward from there.
                    # Repeated origin-first sweeps would undo that orientation
                    # in consanguineous pedigrees.
                    partner_blocks = self._pack_partner_blocks_on_rows(
                        adjusted,
                        families,
                        labels,
                        protected,
                        prefer_descendant_order=True,
                    )
                    for row in self._cluster_rows(adjusted):
                        self._deoverlap_row(
                            adjusted, row, labels, protected, show_inbreeding
                        )
                    for _round in range(3):
                        self._align_single_child_axes_conservatively(
                            adjusted,
                            families,
                            labels,
                            show_inbreeding,
                            partner_blocks=partner_blocks,
                        )
                        if not self._rotate_partner_blocks_toward_ancestry(
                            adjusted,
                            families,
                            partner_blocks,
                        ):
                            break
                else:
                    # Overview mode uses one origin-aware row sweep. Repeating
                    # the same correction after partner packing was the source
                    # of the Elwing drift: nested sole-child blocks were moved
                    # again from already-shifted coordinates.
                    for _round in range(5):
                        for _sweep in range(2):
                            partner_blocks = self._pack_partner_blocks_on_rows(
                                adjusted,
                                families,
                                labels,
                                protected,
                                prefer_descendant_order=False,
                            )
                        for row in self._cluster_rows(adjusted):
                            self._deoverlap_row(
                                adjusted, row, labels, protected, show_inbreeding
                            )
                        self._align_single_child_axes_conservatively(
                            adjusted,
                            families,
                            labels,
                            show_inbreeding,
                            partner_blocks=partner_blocks,
                            origin_anchors=overview_origin_anchors,
                            overview_mode=True,
                        )
                        self._rotate_partner_blocks_toward_ancestry(
                            adjusted,
                            families,
                            partner_blocks,
                        )
                # Alignment and ancestry reflection deliberately move whole
                # branches.  In a deep pedigree that movement can make two
                # previously separate mate blocks overlap in X even though
                # each block is internally ordered.  Repack the completed
                # rows once more before the soft family-axis projection so
                # no unrelated animal is left between a visible pair (the
                # Elrond/Celebrian versus Elros/Madison regression).
                partner_blocks = self._pack_partner_blocks_on_rows(
                    adjusted,
                    families,
                    labels,
                    protected,
                    prefer_descendant_order=prefer_descendant_order,
                )
                for row in self._cluster_rows(adjusted):
                    self._deoverlap_row(
                        adjusted, row, labels, protected, show_inbreeding
                    )
                self._orient_isolated_pairs_by_visible_ancestry(
                    adjusted,
                    families,
                    labels,
                    partner_blocks,
                    show_inbreeding=show_inbreeding,
                    chronological=preserve_y,
                    focus_nodes=set(focus_nodes or set()),
                )
                # Refine soft family alignment only after the top-down block
                # placement and branch reflections have supplied a compact,
                # crossing-aware starting point.
                for _pass in range(2):
                    before = {node: point[0] for node, point in adjusted.items()}
                    self._solve_horizontal_constraints(
                        adjusted,
                        families,
                        labels,
                        show_inbreeding,
                        chronological=preserve_y,
                    )
                    if max(
                        (
                            abs(adjusted[node][0] - before[node])
                            for node in adjusted
                        ),
                        default=0.0,
                    ) <= 1e-6:
                        break
                if overview_mode:
                    self._compact_overview_multi_mate_fans(
                        adjusted,
                        families,
                        labels,
                        show_inbreeding,
                    )
                if prefer_descendant_order and focus_nodes:
                    for _compact_round in range(1):
                        node_weights = self._compact_focused_terminal_sibling_fans(
                            adjusted,
                            families,
                            labels,
                            set(focus_nodes),
                            partner_blocks,
                            preserve_y=preserve_y,
                            show_inbreeding=show_inbreeding,
                        )
                        if not node_weights:
                            break
                        # A family projection can reveal a collision that was
                        # not present in its input seed. Two rediscovery passes
                        # cover the current dense fixtures without repeating
                        # expensive pair projections on every redraw.
                        for _round in range(2):
                            before = {
                                node: point[0] for node, point in adjusted.items()
                            }
                            self._solve_horizontal_constraints(
                                adjusted,
                                families,
                                labels,
                                show_inbreeding,
                                chronological=preserve_y,
                                node_weights=node_weights,
                            )
                            if max(
                                abs(adjusted[node][0] - before[node])
                                for node in adjusted
                            ) <= 1e-6:
                                break
                    # Re-form a split parentless-mate fan only for an
                    # indirectly focused hub. Directly selected hubs retain
                    # their established layout.
                    self._compact_focused_parentless_multi_mate_fans(
                        adjusted,
                        families,
                        labels,
                        set(focus_nodes),
                        show_inbreeding,
                        chronological=preserve_y,
                    )
                self._compact_disconnected_family_components(
                    adjusted,
                    families,
                    labels,
                    show_inbreeding,
                )
                # The horizontal solver may move a ghost/terminal single child
                # after the earlier conservative preconditioner. Restore the
                # perpendicular family axis once, collision-safely, before the
                # final junctions and routes are built.
                self._align_single_child_axes_final(
                    adjusted,
                    families,
                    labels,
                    protected,
                    show_inbreeding,
                )
            else:
                partner_blocks = self._pack_partner_blocks_on_rows(
                    adjusted,
                    families,
                    labels,
                    protected,
                    prefer_descendant_order=prefer_descendant_order,
                )
                for row in self._cluster_rows(adjusted):
                    self._deoverlap_row(
                        adjusted, row, labels, protected, show_inbreeding
                    )
            # Manual locks constrain only the explicitly moved nodes.  Keeping
            # one lock must not disable tidy placement for every new or
            # automatic node in the component.
            adjusted.update(locked_positions)
            if locked_positions:
                self._shift_automatic_components_from_locks(
                    adjusted,
                    families,
                    labels,
                    protected,
                    show_inbreeding,
                )
            return adjusted

        if preserve_y:
            for row in self._cluster_rows(adjusted):
                self._deoverlap_row(adjusted, row, labels, protected, show_inbreeding)
            return adjusted

        automatic = [node for node in adjusted if node not in protected]
        if automatic and not protected:
            if len(automatic) >= 4:
                columns = max(2, int(math.ceil(math.sqrt(len(automatic) * 1.6))))
                ordered = sorted(
                    automatic,
                    key=lambda node: (adjusted[node][0], node.casefold()),
                )
                center_x = sum(adjusted[node][0] for node in automatic) / len(automatic)
                center_y = sum(adjusted[node][1] for node in automatic) / len(automatic)
                row_count = int(math.ceil(len(ordered) / columns))
                for row_index in range(row_count):
                    row = ordered[row_index * columns : (row_index + 1) * columns]
                    widths = [
                        max(0.80, (len(str(labels.get(node, node)).strip()) * 0.150) + 0.28)
                        for node in row
                    ]
                    row_width = sum(widths) + (0.72 * max(0, len(row) - 1))
                    cursor = center_x - (row_width / 2.0)
                    y = center_y + ((row_index - ((row_count - 1) / 2.0)) * 2.0)
                    for node, width in zip(row, widths):
                        adjusted[node] = (cursor + (width / 2.0), y)
                        cursor += width + 0.72
            else:
                center = sum(adjusted[node][0] for node in automatic) / len(automatic)
                adjusted = {
                    node: (center + ((x - center) * self.automatic_x_scale), y)
                    for node, (x, y) in adjusted.items()
                }

        rows = self._cluster_rows(adjusted)
        for row in rows:
            self._deoverlap_row(adjusted, row, labels, protected, show_inbreeding)
        return adjusted

    def _compact_disconnected_family_components(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
    ) -> None:
        """Pack independent pedigrees side by side without changing internals.

        Barycentric row sweeps are meaningful only inside one connected
        pedigree.  When all animals of a species are selected, comparing a
        small unrelated family with a large pedigree on every generation row
        can accumulate a huge empty horizontal gulf.  Translate only complete
        connected components here; true singletons are left for the widget's
        compact singleton grid.
        """
        adjacency: Dict[str, Set[str]] = {node: set() for node in positions}
        for family in families.values():
            members = [
                node
                for node in self._parents(family) + self._children(family)
                if node in positions
            ]
            for node in members:
                adjacency[node].update(member for member in members if member != node)

        components: List[Set[str]] = []
        unseen = set(positions)
        while unseen:
            seed = min(unseen, key=str.casefold)
            pending = [seed]
            component: Set[str] = set()
            while pending:
                node = pending.pop()
                if node in component:
                    continue
                component.add(node)
                pending.extend(adjacency[node] - component)
            unseen -= component
            if len(component) > 1:
                components.append(component)
        if len(components) < 2:
            return

        obstacles = self.node_obstacles(positions, labels, show_inbreeding)
        bounds = {
            frozenset(component): (
                min(obstacles[node].left for node in component),
                max(obstacles[node].right for node in component),
            )
            for component in components
        }
        ordered = sorted(
            components,
            key=lambda component: (
                bounds[frozenset(component)][0],
                min(node.casefold() for node in component),
            ),
        )
        original_left = min(bounds[frozenset(component)][0] for component in ordered)
        original_right = max(bounds[frozenset(component)][1] for component in ordered)
        original_center = (original_left + original_right) / 2.0
        gap = 3.2
        widths = [
            bounds[frozenset(component)][1] - bounds[frozenset(component)][0]
            for component in ordered
        ]
        packed_width = sum(widths) + gap * (len(widths) - 1)
        cursor = original_center - (packed_width / 2.0)
        for component, width in zip(ordered, widths):
            left, _right = bounds[frozenset(component)]
            shift = cursor - left
            for node in component:
                x, y = positions[node]
                positions[node] = (x + shift, y)
            cursor += width + gap

    def _pack_partner_blocks_on_rows(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        *,
        prefer_descendant_order: bool = False,
    ) -> Dict[str, Set[str]]:
        """Keep same-row mates contiguous without recursively moving ancestry.

        A partner component is treated as one row block, so unrelated animals
        can never be inserted between a pair (or between a multi-mate hub and
        its mate fan).  The block order follows the compact seed layout.
        """

        block_by_node: Dict[str, Set[str]] = {}
        rows = sorted(
            self._cluster_rows(positions),
            key=lambda row: -sum(positions[node][1] for node in row) / len(row),
        )
        for row in rows:
            row_set = set(row)
            adjacency: Dict[str, Set[str]] = {node: set() for node in row}
            edges: Set[Tuple[str, str]] = set()
            for family_id in sorted(families, key=str.casefold):
                family = families[family_id]
                parents = [parent for parent in self._parents(family) if parent in row_set]
                if len(parents) != 2:
                    continue
                first, second = sorted(parents, key=str.casefold)
                adjacency[first].add(second)
                adjacency[second].add(first)
                edges.add((first, second))

            components: List[Set[str]] = []
            unseen = set(row)
            while unseen:
                start = min(unseen, key=str.casefold)
                stack = [start]
                component: Set[str] = set()
                while stack:
                    node = stack.pop()
                    if node in component:
                        continue
                    component.add(node)
                    stack.extend(adjacency[node] - component)
                unseen -= component
                components.append(component)
                for node in component:
                    block_by_node[node] = component

            if len(row) < 2 or set(row) & protected:
                continue

            original_order = sorted(row, key=lambda node: (positions[node][0], node.casefold()))
            original_rank = {node: index for index, node in enumerate(original_order)}

            def order_component(component: Set[str]) -> List[str]:
                baseline = sorted(component, key=lambda node: (positions[node][0], node.casefold()))
                if len(component) <= 2:
                    return baseline
                component_edges = [
                    edge for edge in edges if edge[0] in component and edge[1] in component
                ]
                if len(component) <= 7:
                    def score(candidate: Tuple[str, ...]) -> Tuple[int, int, Tuple[str, ...]]:
                        ranks = {node: index for index, node in enumerate(candidate)}
                        broken = sum(
                            max(0, abs(ranks[first] - ranks[second]) - 1)
                            for first, second in component_edges
                        )
                        displacement = sum(
                            abs(index - original_rank[node])
                            for index, node in enumerate(candidate)
                        )
                        return broken, displacement, tuple(node.casefold() for node in candidate)

                    return list(min(permutations(baseline), key=score))

                hub = max(
                    component,
                    key=lambda node: (len(adjacency[node]), -original_rank[node], node.casefold()),
                )
                others = [node for node in baseline if node != hub]
                split = len(others) // 2
                return others[:split] + [hub] + others[split:]

            component_targets: Dict[frozenset[str], float] = {}
            component_seed_targets: Dict[frozenset[str], float] = {}
            component_origin_targets: Dict[frozenset[str], Optional[float]] = {}
            component_descendant_targets: Dict[frozenset[str], Optional[float]] = {}
            for component in components:
                component_seed_targets[frozenset(component)] = (
                    sum(positions[node][0] for node in component) / len(component)
                )
                relationship_targets: List[float] = []
                origin_targets: List[float] = []
                descendant_targets: List[float] = []
                for family_id in sorted(families, key=str.casefold):
                    family = families[family_id]
                    parents = [parent for parent in self._parents(family) if parent in component]
                    children = [child for child in self._children(family) if child in positions]
                    if len(parents) == 2 and children:
                        descendant_center = (
                            sum(positions[child][0] for child in children) / len(children)
                        )
                        relationship_targets.append(descendant_center)
                        descendant_targets.append(descendant_center)
                    visible_parents = [
                        parent for parent in self._parents(family) if parent in positions
                    ]
                    component_children = [child for child in children if child in component]
                    if visible_parents and component_children:
                        parent_center = (
                            sum(positions[parent][0] for parent in visible_parents)
                            / len(visible_parents)
                        )
                        origin_targets.extend(
                            parent_center for _child in component_children
                        )
                        # Origin placement is the stronger ordering signal:
                        # it decides on which side of a multiple-mate fan the
                        # complete descendant branch belongs.  Descendant
                        # barycentres still break ties and compact the result.
                        relationship_targets.extend(
                            parent_center
                            for _child in component_children
                            for _weight in range(4)
                        )
                component_targets[frozenset(component)] = (
                    sum(relationship_targets) / len(relationship_targets)
                    if relationship_targets
                    else sum(positions[node][0] for node in component) / len(component)
                )
                component_origin_targets[frozenset(component)] = (
                    sum(origin_targets) / len(origin_targets)
                    if origin_targets
                    else None
                )
                component_descendant_targets[frozenset(component)] = (
                    sum(descendant_targets) / len(descendant_targets)
                    if descendant_targets
                    else None
                )

            ordered_components = sorted(
                components,
                key=lambda component: (
                    (
                        component_descendant_targets[frozenset(component)]
                        if prefer_descendant_order
                        and component_descendant_targets[frozenset(component)] is not None
                        else component_seed_targets[frozenset(component)]
                        if prefer_descendant_order
                        else component_origin_targets[frozenset(component)]
                        if component_origin_targets[frozenset(component)] is not None
                        else component_targets[frozenset(component)]
                    ),
                    component_targets[frozenset(component)],
                    min(node.casefold() for node in component),
                ),
            )
            ordered_blocks = [order_component(component) for component in ordered_components]

            def width(node: str) -> float:
                return self._estimated_label_width(str(labels.get(node, node)).strip())

            block_gap = self.node_gap + 0.72
            partner_gap = self.node_gap + 0.18
            block_widths = [
                sum(width(node) for node in block)
                + partner_gap * max(0, len(block) - 1)
                for block in ordered_blocks
            ]
            targets = [
                (
                    component_descendant_targets[frozenset(component)]
                    if prefer_descendant_order
                    and component_descendant_targets[frozenset(component)] is not None
                    else component_seed_targets[frozenset(component)]
                    if prefer_descendant_order
                    else component_targets[frozenset(component)]
                )
                for component in ordered_components
            ]
            centers = list(targets)
            for index in range(1, len(centers)):
                required = (
                    (block_widths[index - 1] / 2.0)
                    + block_gap
                    + (block_widths[index] / 2.0)
                )
                centers[index] = max(centers[index], centers[index - 1] + required)
            if centers:
                # Translate the feasible sequence back towards all requested
                # descendant centres without changing its clearances.
                offset = sum(target - center for target, center in zip(targets, centers)) / len(centers)
                centers = [center + offset for center in centers]

            for block, block_width, center in zip(ordered_blocks, block_widths, centers):
                cursor = center - (block_width / 2.0)
                for node_index, node in enumerate(block):
                    if node_index:
                        cursor += partner_gap
                    node_width = width(node)
                    positions[node] = (cursor + (node_width / 2.0), positions[node][1])
                    cursor += node_width


        return block_by_node
    def _compact_focused_terminal_sibling_fans(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        focus_nodes: Set[str],
        partner_blocks: Mapping[str, Set[str]],
        *,
        preserve_y: bool = False,
        show_inbreeding: bool = True,
    ) -> Dict[str, float]:
        """Separate continuing sibling subtrees from terminal sibling leaves.

        An exact sibling barycentre can create a very wide tree when one child
        continues through a partner and descendants while its siblings are
        leaves: the large continuing subtree pushes the leaves far away merely
        to balance their arithmetic mean. In a focused view the complete
        continuing block is translated toward its origin when needed; leaf
        siblings use the opposite, otherwise empty shoulder as a deterministic
        compact fan. The two groups are not one symmetry equation. In
        chronological mode only X changes: every real birth-date Y is kept.
        Topology remains in the family routes rather than in artificial
        geometric symmetry.
        """

        if not focus_nodes:
            return {}

        row_tolerance = 0.18

        node_weights: Dict[str, float] = {}

        outgoing: Dict[str, List[str]] = {node: [] for node in positions}
        parents_by_child: Dict[str, Set[str]] = {
            node: set() for node in positions
        }
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            visible_parents = [
                parent for parent in self._parents(family) if parent in positions
            ]
            visible_children = [
                child for child in self._children(family) if child in positions
            ]
            if not visible_children:
                continue
            for parent in visible_parents:
                outgoing.setdefault(parent, []).append(family_id)
            for child in visible_children:
                parents_by_child.setdefault(child, set()).update(visible_parents)

        # A continuing sibling is part of the focused branch even when the
        # actual selection is one or more generations below it. Limiting this
        # rule to ``child in focus_nodes`` missed exactly that common ancestor-
        # view case and let terminal siblings remain symmetry-coupled. Do not
        # expand from newly discovered ancestors to their other descendants:
        # those are visible side branches, not the selected primary lineage.
        focus_lineage = set(focus_nodes)
        pending = sorted(focus_nodes, key=str.casefold, reverse=True)
        while pending:
            node = pending.pop()
            for parent in sorted(
                parents_by_child.get(node, set()),
                key=str.casefold,
                reverse=True,
            ):
                if parent not in focus_lineage:
                    focus_lineage.add(parent)
                    pending.append(parent)

        def is_continuing(node: str) -> bool:
            return any(
                any(
                    child in positions
                    for child in self._children(families[family_id])
                )
                for family_id in outgoing.get(node, [])
            )

        changed = False
        claimed: Set[str] = set()
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [
                parent for parent in self._parents(family) if parent in positions
            ]
            children = [
                child for child in self._children(family) if child in positions
            ]
            if len(parents) != 2 or len(children) < 2:
                continue

            continuing = [child for child in children if is_continuing(child)]
            terminal = [child for child in children if not is_continuing(child)]
            if not continuing or not terminal:
                continue
            focused_branches = [
                child for child in continuing if child in focus_lineage
            ]
            if not focused_branches or any(node in focus_nodes for node in terminal):
                continue

            # Only a single continuing branch can be compacted as one unit.
            # With multiple continuing siblings their individual subtrees keep
            # their own positions; the independent terminal group still moves
            # to the opposite shoulder.
            trunk = continuing[0] if len(continuing) == 1 else None
            moving: Set[str] = set()
            if trunk is not None:
                moving = set(partner_blocks.get(trunk, {trunk})) | {trunk}
                # Expand only downwards from the continuing child. Origin
                # ancestors stay fixed, while mates and descendants move as
                # one visual subtree.
                pending_descendants = sorted(
                    moving, key=str.casefold, reverse=True
                )
                while pending_descendants:
                    parent = pending_descendants.pop()
                    for descendant_family_id in sorted(
                        outgoing.get(parent, []), key=str.casefold
                    ):
                        descendant_family = families[descendant_family_id]
                        additions = {
                            node
                            for node in (
                                self._parents(descendant_family)
                                + self._children(descendant_family)
                            )
                            if node in positions
                        }
                        for addition in sorted(
                            additions - moving,
                            key=str.casefold,
                            reverse=True,
                        ):
                            moving.add(addition)
                            pending_descendants.append(addition)

            fixed_family = set(parents) | set(terminal)
            if moving.intersection(fixed_family) or moving.intersection(claimed):
                continue

            parent_center = sum(positions[parent][0] for parent in parents) / 2.0
            branch_center = sum(positions[node][0] for node in continuing) / len(continuing)
            direction_source = branch_center - parent_center
            if abs(direction_source) <= _EPSILON:
                direction_source = 1.0
            side = 1.0 if direction_source >= 0.0 else -1.0

            block = (
                set(partner_blocks.get(trunk, {trunk})) | {trunk}
                if trunk is not None
                else set(continuing)
            )
            block_width = 0.0
            if block:
                block_width = (
                    max(
                        positions[node][0]
                        + self._estimated_label_width(str(labels.get(node, node))) / 2.0
                        for node in block
                    )
                    - min(
                        positions[node][0]
                        - self._estimated_label_width(str(labels.get(node, node))) / 2.0
                        for node in block
                    )
                )
            # Keep the continuing block close enough that its direct route
            # still reads as part of this sibship.  A wider bound left Arwen
            # more than four layout units from the family knot in the
            # chronological view even though the opposite shoulder was free.
            # The block still moves as one, so partner and descendant spacing
            # cannot be distorted by this compaction.
            target_offset = max(1.0, min(2.2, (block_width / 2.0) + 0.35))
            shift_x = 0.0
            if trunk is not None:
                trunk_x, _trunk_y = positions[trunk]
                current_offset = abs(trunk_x - parent_center)
                # This is a compaction rule, never a reason to widen an
                # already tidy continuing branch.
                if current_offset > target_offset * 1.04:
                    target_trunk_x = parent_center + (side * target_offset)
                    shift_x = target_trunk_x - trunk_x
            parent_top = max(positions[parent][1] for parent in parents)
            closest_branch_y = min(positions[node][1] for node in continuing)
            generation_gap = max(2.4, abs(closest_branch_y - parent_top))
            # Keep the continuing branch close to its real generation row.
            # A large artificial lift made its direct origin connection look
            # much longer than the neighbouring terminal sibling routes.
            branch_moved = abs(shift_x) > _EPSILON
            lift = (
                min(0.8, max(0.4, generation_gap * 0.10))
                if branch_moved and not preserve_y
                else 0.0
            )
            if branch_moved:
                for node in moving:
                    x, y = positions[node]
                    positions[node] = (x + shift_x, y + lift)

            terminal_count = len(terminal)
            fan_step = min(2.1, max(1.55, generation_gap * 0.20))
            base_y = max(
                min(positions[node][1] for node in terminal),
                parent_top + min(5.8, max(4.4, generation_gap * 0.50)),
            )
            terminal_order = sorted(
                terminal,
                key=lambda node: (
                    side * positions[node][0],
                    node.casefold(),
                ),
                reverse=True,
            )
            for index, node in enumerate(terminal_order):
                # Every terminal leaf stays on the shoulder opposite the
                # continuing subtree. The lower leaf is farthest from the
                # direct continuing route; higher leaves step back toward the
                # axis but never cross it.
                opposite_distance = 0.25 + (
                    (terminal_count - 1 - index) * 0.95
                )
                x = parent_center - (side * fan_step * opposite_distance)
                y = positions[node][1] if preserve_y else base_y + (index * fan_step)
                positions[node] = (x, y)

            # Do not keep a compacting move that creates a rendered-box
            # collision elsewhere in the same generation. Large overviews
            # contain many same-date terminal siblings; a local rollback is
            # safer than letting one family repair another by pushing a whole
            # branch away.
            touched = set(parents) | set(continuing) | set(terminal) | set(moving)
            snapshot = {node: positions[node] for node in touched if node in positions}
            obstacles_after = self.node_obstacles(positions, labels, show_inbreeding)
            collision = False
            ordered_touched = sorted(touched, key=str.casefold)
            for left_node, right_node in combinations(ordered_touched, 2):
                if abs(positions[left_node][1] - positions[right_node][1]) > row_tolerance:
                    continue
                left_rect = obstacles_after[left_node]
                right_rect = obstacles_after[right_node]
                if (
                    left_rect.right > right_rect.left
                    and right_rect.right > left_rect.left
                    and left_rect.top > right_rect.bottom
                    and right_rect.top > left_rect.bottom
                ):
                    collision = True
                    break
            if collision:
                for node, point in snapshot.items():
                    positions[node] = point
                for node in touched:
                    node_weights.pop(node, None)
                continue

            # Preserve this compact seed preferentially while leaving the
            # general solver enough freedom to clear genuine conflicts.
            if branch_moved:
                for node in moving | set(continuing):
                    node_weights[node] = max(
                        node_weights.get(node, 1.0), self.focused_branch_weight
                    )
                # Preserve the complete mixed-family frame through the final
                # hard-collision projection. Otherwise that pass can move the
                # parents away from the compacted continuing block, recreating
                # the long origin diagonal it was meant to remove. Unrelated
                # branches remain free to make the required clearance.
                for node in set(parents) | set(terminal):
                    node_weights[node] = max(
                        node_weights.get(node, 1.0), 30.0
                    )

            claimed.update(moving)
            claimed.update(continuing)
            claimed.update(terminal)
            changed = True

        return node_weights if changed else {}

    def _rotate_partner_blocks_toward_ancestry(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        partner_blocks: Mapping[str, Set[str]],
    ) -> bool:
        """Reflect/reorder mate blocks when their ancestry branches cross.

        Slots stay fixed, so row clearances and the shared offspring knot do
        not move.  Only the animals (and therefore their deeper origin
        branches) exchange sides.  This is the local tree rotation that turns
        two long crossing diagonals into two short outward connections.
        """

        origin_parents: Dict[str, List[str]] = {}
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [parent for parent in self._parents(family) if parent in positions]
            for child in self._children(family):
                if child in positions and len(parents) == 2:
                    origin_parents[child] = parents

        unique_blocks: Dict[frozenset[str], Set[str]] = {
            frozenset(block): set(block)
            for block in partner_blocks.values()
            if len(block) >= 2
        }
        changed = False
        for block in sorted(
            unique_blocks.values(),
            key=lambda members: (
                sum(positions[node][1] for node in members) / len(members),
                min(node.casefold() for node in members),
            ),
        ):
            if len(block) > 7:
                continue
            slots = sorted(positions[node][0] for node in block)
            baseline = tuple(sorted(block, key=lambda node: (positions[node][0], node.casefold())))

            def ancestry_center(node: str) -> Optional[float]:
                parents = origin_parents.get(node, [])
                if len(parents) != 2:
                    return None
                return sum(positions[parent][0] for parent in parents) / 2.0

            if sum(ancestry_center(node) is not None for node in block) < 2:
                continue

            baseline_rank = {node: index for index, node in enumerate(baseline)}

            def score(candidate: Tuple[str, ...]) -> Tuple[float, int, Tuple[str, ...]]:
                diagonal = 0.0
                for slot, node in zip(slots, candidate):
                    center = ancestry_center(node)
                    if center is not None:
                        diagonal += abs(slot - center)
                churn = sum(
                    abs(index - baseline_rank[node])
                    for index, node in enumerate(candidate)
                )
                return round(diagonal, 9), churn, tuple(node.casefold() for node in candidate)

            best = min(permutations(baseline), key=score)
            if best == baseline or score(best)[0] + 0.08 >= score(baseline)[0]:
                continue
            y_by_node = {node: positions[node][1] for node in block}
            for slot, node in zip(slots, best):
                positions[node] = (slot, y_by_node[node])
            changed = True
        return changed

    def _orient_isolated_pairs_by_visible_ancestry(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        partner_blocks: Mapping[str, Set[str]],
        *,
        show_inbreeding: bool,
        chronological: bool = False,
        focus_nodes: Optional[Set[str]] = None,
    ) -> bool:
        """Softly mirror an isolated pair toward its visible ancestry.

        The supplied family graph is already clipped to the selected depth,
        so a missing origin here deliberately means that ancestry stops in
        the current view.  A partner with one visible origin is placed toward
        that origin and its founder mate occupies the outer slot.  Both
        mirror candidates are scored first; no flip may worsen marker hits,
        route crossings, or same-row rendered-box overlap.  Multi-mate and
        parent/offspring pairings remain under their dedicated layout rules.
        """

        focus = set(focus_nodes or set())
        origin_ids: Dict[str, List[str]] = {node: [] for node in positions}
        mating_ids: Dict[str, List[str]] = {node: [] for node in positions}
        parent_map: Dict[str, Set[str]] = {node: set() for node in positions}
        children_map: Dict[str, Set[str]] = {node: set() for node in positions}
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            if len(parents) != 2 or not children:
                continue
            for parent in parents:
                mating_ids.setdefault(parent, []).append(family_id)
                children_map.setdefault(parent, set()).update(children)
            for child in children:
                origin_ids.setdefault(child, []).append(family_id)
                parent_map.setdefault(child, set()).update(parents)

        lineage_nodes = set(focus)
        pending = sorted(focus, key=str.casefold, reverse=True)
        while pending:
            node = pending.pop()
            for parent in sorted(
                parent_map.get(node, set()),
                key=str.casefold,
                reverse=True,
            ):
                if parent not in lineage_nodes:
                    lineage_nodes.add(parent)
                    pending.append(parent)
        pending = sorted(focus, key=str.casefold, reverse=True)
        while pending:
            node = pending.pop()
            for child in sorted(
                children_map.get(node, set()),
                key=str.casefold,
                reverse=True,
            ):
                if child not in lineage_nodes:
                    lineage_nodes.add(child)
                    pending.append(child)

        def is_ancestor(ancestor: str, descendant: str) -> bool:
            pending = sorted(
                parent_map.get(descendant, set()),
                key=str.casefold,
                reverse=True,
            )
            seen: Set[str] = set()
            while pending:
                node = pending.pop()
                if node == ancestor:
                    return True
                if node in seen:
                    continue
                seen.add(node)
                pending.extend(
                    sorted(
                        parent_map.get(node, set()) - seen,
                        key=str.casefold,
                        reverse=True,
                    )
                )
            return False

        def provisional_junctions(
            candidate: Mapping[str, Point],
        ) -> Dict[str, Point]:
            """Cheap mirror-comparison knots using production base geometry."""
            output: Dict[str, Point] = {}
            for family_id in sorted(families, key=str.casefold):
                family = families[family_id]
                parents = [
                    node for node in self._parents(family) if node in candidate
                ]
                children = [
                    node for node in self._children(family) if node in candidate
                ]
                if len(parents) != 2 or not children:
                    continue
                parent_x = sum(candidate[node][0] for node in parents) / 2.0
                child_xs = sorted(candidate[node][0] for node in children)
                middle = len(child_xs) // 2
                child_x = (
                    child_xs[middle]
                    if len(child_xs) % 2
                    else (child_xs[middle - 1] + child_xs[middle]) / 2.0
                )
                parent_ys = [candidate[node][1] for node in parents]
                parent_mid_y = sum(parent_ys) / len(parent_ys)
                child_ys = [candidate[node][1] for node in children]
                if min(child_ys) >= parent_mid_y:
                    child_y = min(child_ys)
                elif max(child_ys) <= parent_mid_y:
                    child_y = max(child_ys)
                else:
                    child_y = min(
                        child_ys,
                        key=lambda value: abs(value - parent_mid_y),
                    )
                parent_y = max(parent_ys) if chronological else (
                    max(parent_ys)
                    if child_y >= parent_mid_y
                    else min(parent_ys)
                )
                parent_left = min(candidate[node][0] for node in parents)
                parent_right = max(candidate[node][0] for node in parents)
                parent_span = parent_right - parent_left
                maximum_shift = min(
                    1.35,
                    parent_span * 0.22,
                    max(0.0, (parent_span / 2.0) - 0.08),
                )
                desired_shift = (child_x - parent_x) * 0.55
                junction_x = parent_x + max(
                    -maximum_shift, min(maximum_shift, desired_shift)
                )
                output[family_id] = (
                    junction_x,
                    parent_y + ((child_y - parent_y) * 0.52),
                )
            return output

        def hard_geometry_score(
            candidate: Mapping[str, Point],
        ) -> Tuple[Tuple[int, int, int], Dict[str, Point]]:
            obstacles = self.node_obstacles(
                candidate, labels, show_inbreeding
            )
            label_overlaps = 0
            ordered_nodes = sorted(candidate, key=str.casefold)
            for first, second in combinations(ordered_nodes, 2):
                if abs(candidate[first][1] - candidate[second][1]) > 0.18:
                    continue
                left = obstacles[first]
                right = obstacles[second]
                if (
                    left.right > right.left
                    and right.right > left.left
                    and left.top > right.bottom
                    and right.top > left.bottom
                ):
                    label_overlaps += 1

            junctions = provisional_junctions(candidate)
            proxies: List[Tuple[str, Set[str], Segment]] = []
            for family_id in sorted(junctions, key=str.casefold):
                family = families[family_id]
                parents = [
                    node for node in self._parents(family) if node in candidate
                ]
                children = [
                    node for node in self._children(family) if node in candidate
                ]
                members = set(parents) | set(children)
                junction = junctions[family_id]
                for parent in parents:
                    parent_point = candidate[parent]
                    proxies.append(
                        (
                            family_id,
                            members,
                            (junction, (parent_point[0], junction[1])),
                        )
                    )
                    proxies.append(
                        (
                            family_id,
                            members,
                            ((parent_point[0], junction[1]), parent_point),
                        )
                    )
                for child in children:
                    proxies.append(
                        (family_id, members, (junction, candidate[child]))
                    )

            markers = self.marker_obstacles(candidate)
            marker_hits = 0
            for family_id, junction in junctions.items():
                family = families[family_id]
                members = {
                    node
                    for node in self._parents(family) + self._children(family)
                    if node in candidate
                }
                marker_hits += sum(
                    1
                    for node, rect in markers.items()
                    if node not in members
                    and rect.contains(junction, margin=0.04)
                )
            for _family_id, members, segment in proxies:
                if _path_length(segment) <= _EPSILON:
                    continue
                marker_hits += sum(
                    1
                    for node, rect in markers.items()
                    if node not in members
                    and rect.intersects(segment, margin=0.04)
                )

            crossings = 0
            for first, second in combinations(proxies, 2):
                if first[0] == second[0] or first[1] & second[1]:
                    continue
                relation, _point = _segment_relation(first[2], second[2])
                if relation in {"cross", "overlap"}:
                    crossings += 1
            return (marker_hits, crossings, label_overlaps), junctions

        changed = False
        partner_gap = self.node_gap + 0.18
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            if len(parents) != 2 or not children:
                continue
            if focus and not (set(children) & lineage_nodes):
                continue
            first, second = parents
            if (
                len(mating_ids.get(first, [])) != 1
                or len(mating_ids.get(second, [])) != 1
                or is_ancestor(first, second)
                or is_ancestor(second, first)
            ):
                continue
            block = (
                set(partner_blocks.get(first, {first}))
                | set(partner_blocks.get(second, {second}))
            )
            if len(block) > 2 or not block.issubset({first, second}):
                continue

            anchors: Dict[str, str] = {}
            ambiguous = False
            for node in parents:
                usable = [
                    value
                    for value in origin_ids.get(node, [])
                    if value != family_id
                ]
                if len(usable) == 1:
                    anchors[node] = usable[0]
                elif len(usable) > 1:
                    ambiguous = True
                    break
            if ambiguous or not anchors:
                # With no visible upstream signal, preserve the seed order;
                # there is intentionally no hard sex-based fallback.
                continue

            current_order = tuple(
                sorted(parents, key=lambda node: (positions[node][0], node.casefold()))
            )
            midpoint = sum(positions[node][0] for node in parents) / 2.0

            def score(
                order: Tuple[str, str],
            ) -> Tuple[Tuple[int, int, int, float, float, Tuple[str, str]], Dict[str, Point]]:
                candidate = dict(positions)
                left, right = order
                separation = (
                    self._estimated_label_width(str(labels.get(left, left))) / 2.0
                    + partner_gap
                    + self._estimated_label_width(str(labels.get(right, right))) / 2.0
                )
                candidate[left] = (midpoint - separation / 2.0, positions[left][1])
                candidate[right] = (midpoint + separation / 2.0, positions[right][1])
                hard, candidate_junctions = hard_geometry_score(candidate)
                ancestry_run = sum(
                    math.hypot(
                        candidate[node][0] - candidate_junctions[origin_id][0],
                        candidate[node][1] - candidate_junctions[origin_id][1],
                    )
                    for node, origin_id in anchors.items()
                    if origin_id in candidate_junctions
                )
                churn = sum(
                    abs(candidate[node][0] - positions[node][0])
                    for node in parents
                )
                return (
                    hard
                    + (
                        round(ancestry_run, 9),
                        round(churn, 9),
                        tuple(node.casefold() for node in order),
                    ),
                    candidate,
                )

            baseline_score, _baseline_positions = score(current_order)
            mirror_score, mirror_positions = score(tuple(reversed(current_order)))
            baseline_hard = baseline_score[:3]
            mirror_hard = mirror_score[:3]
            no_worse_hard = all(
                mirror <= baseline
                for mirror, baseline in zip(mirror_hard, baseline_hard)
            )
            improves_hard = no_worse_hard and any(
                mirror < baseline
                for mirror, baseline in zip(mirror_hard, baseline_hard)
            )
            improves_ancestry = (
                no_worse_hard
                and mirror_hard == baseline_hard
                and mirror_score[3] + 0.08 < baseline_score[3]
            )
            if not (improves_hard or improves_ancestry):
                continue
            for node in parents:
                positions[node] = mirror_positions[node]
            changed = True
        return changed


    def _compact_overview_multi_mate_fans(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
    ) -> bool:
        """Keep multi-mate hubs and their direct child fans spatially cohesive.

        The chronological Overview preserves each animal's birth-date Y lane,
        so partners can fall into different row clusters and bypass the normal
        same-row partner-block pass.  For a hub with multiple visible mates,
        direct child branches therefore provide the stable horizontal ordering:
        the mate and (when terminal) child are placed on the same shoulder and
        in the same near-to-far order.  The pass is intentionally Overview-only
        and conservative for partners with visible ancestry; focused layouts
        retain their established rules.
        """
        parent_families: Dict[str, List[str]] = defaultdict(list)
        child_families: Dict[str, List[str]] = defaultdict(list)
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            if len(parents) != 2 or not children:
                continue
            for parent in parents:
                parent_families[parent].append(family_id)
            for child in children:
                child_families[child].append(family_id)

        changed = False
        claimed: Set[str] = set()
        for hub in sorted(parent_families, key=str.casefold):
            family_ids = parent_families[hub]
            if len(family_ids) < 2:
                continue
            records: List[Dict[str, object]] = []
            hub_x = positions[hub][0]
            for family_id in sorted(family_ids, key=str.casefold):
                family = families[family_id]
                parents = [node for node in self._parents(family) if node in positions]
                children = [node for node in self._children(family) if node in positions]
                if len(parents) != 2 or not children:
                    continue
                mate = parents[0] if parents[1] == hub else parents[1] if parents[0] == hub else ""
                if not mate or mate in claimed:
                    continue
                child_center = sum(positions[node][0] for node in children) / len(children)
                delta = child_center - hub_x
                if abs(delta) <= _EPSILON:
                    delta = positions[mate][0] - hub_x
                side = 1.0 if delta >= 0.0 else -1.0
                records.append(
                    {
                        "family_id": family_id,
                        "mate": mate,
                        "children": children,
                        "child_center": child_center,
                        "side": side,
                        "terminal": all(not parent_families.get(child) for child in children),
                        "has_origin": bool(child_families.get(mate)),
                    }
                )
            if len(records) < 2:
                continue

            # Assign each visible branch a compact slot on the shoulder chosen
            # by its child center.  A branch nearer the hub receives the inner
            # slot; outer branches remain ordered farther out.
            targets: Dict[str, float] = {}
            child_targets: Dict[str, float] = {}

            def has_collision(candidate: Mapping[str, Point], moved: Set[str]) -> bool:
                obstacles = self.node_obstacles(candidate, labels, show_inbreeding)
                markers = self.marker_obstacles(
                    candidate,
                    half_width=0.32,
                    # The chronological text baseline can be a fraction of a
                    # data unit above the router's nominal label box. Reserve
                    # a little extra vertical clearance to match the final
                    # rendered label-to-marker pixel test.
                    half_height=0.60,
                )
                for first in sorted(moved, key=str.casefold):
                    for second in sorted(candidate, key=str.casefold):
                        if first == second:
                            continue
                        def rect_overlap(first_rect: Rect, second_rect: Rect) -> bool:
                            return (
                                first_rect.right > second_rect.left
                                and second_rect.right > first_rect.left
                                and first_rect.top > second_rect.bottom
                                and second_rect.top > first_rect.bottom
                            )

                        # Check both label/label and label/marker contact. The
                        # latter mirrors the final pixel regression and catches
                        # date-lane cases where a secondary label reaches a
                        # neighboring marker although data-space labels do not.
                        if rect_overlap(obstacles[first], obstacles[second]):
                            return True
                        if rect_overlap(obstacles[first], markers[second]):
                            return True
                        if rect_overlap(obstacles[second], markers[first]):
                            return True
                return False

            for side in (-1.0, 1.0):
                side_records = [
                    item for item in records if item["side"] == side
                ]
                side_records.sort(
                    key=lambda item: (
                        abs(float(item["child_center"]) - hub_x),
                        str(item["family_id"]).casefold(),
                    )
                )
                for index, item in enumerate(side_records):
                    mate = str(item["mate"])
                    children = [str(child) for child in item["children"]]
                    mate_half = self._estimated_label_width(
                        str(labels.get(mate, mate))
                    ) / 2.0
                    hub_half = self._estimated_label_width(
                        str(labels.get(hub, hub))
                    ) / 2.0
                    child_half = max(
                        (
                            self._estimated_label_width(
                                str(labels.get(child, child))
                            ) / 2.0
                            for child in children
                        ),
                        default=0.48,
                    )
                    base_distance = max(
                        2.0,
                        hub_half + mate_half + self.node_gap + 0.55,
                    )
                    slot_step = max(
                        1.7,
                        mate_half + child_half + self.node_gap + 0.65,
                    )
                    ideal_distance = base_distance + (index * slot_step)
                    current_distance = abs(positions[mate][0] - hub_x)

                    # A partner already close to the hub should not be pushed
                    # outward merely to satisfy a geometric template.
                    if current_distance + 0.05 < ideal_distance:
                        ideal_distance = current_distance

                    candidate_distances = [
                        ideal_distance + (step * 0.55)
                        for step in range(17)
                        if ideal_distance + (step * 0.55)
                        <= max(ideal_distance + 0.01, min(current_distance, ideal_distance + 8.8))
                    ]
                    if not candidate_distances:
                        candidate_distances = [current_distance]

                    chosen_mate = positions[mate][0]
                    # Only record a child when the coupled candidate actually
                    # assigned a new terminal slot.  Keeping the initial
                    # position here would make the conservative child-pull
                    # below look as if the child had already been moved.
                    chosen_child: Dict[str, float] = {}
                    found = False
                    for distance in candidate_distances:
                        target = hub_x + (side * distance)
                        if bool(item["has_origin"]):
                            target = positions[mate][0] + max(
                                -1.35,
                                min(1.35, target - positions[mate][0]),
                            )
                        child_distance = max(
                            0.85,
                            distance - max(0.9, child_half * 0.35),
                        )
                        proposed_child = (
                            hub_x + (side * child_distance)
                            if bool(item["terminal"])
                            else None
                        )
                        candidate = dict(positions)
                        for previous, previous_target in targets.items():
                            candidate[previous] = (
                                previous_target,
                                candidate[previous][1],
                            )
                        for previous, previous_target in child_targets.items():
                            candidate[previous] = (
                                previous_target,
                                candidate[previous][1],
                            )
                        candidate[mate] = (target, candidate[mate][1])
                        if proposed_child is not None:
                            for child in children:
                                candidate[child] = (
                                    proposed_child,
                                    candidate[child][1],
                                )
                        moved = (
                            set(targets)
                            | set(child_targets)
                            | {mate}
                            | (set(children) if proposed_child is not None else set())
                        )
                        if has_collision(candidate, moved):
                            continue
                        chosen_mate = target
                        if proposed_child is not None:
                            chosen_child = {
                                child: proposed_child for child in children
                            }
                        found = True
                        break

                    if not found:
                        # A terminal child can have a neighboring birth-date
                        # label that blocks the ideal mate/child pair. Preserve
                        # the family improvement by trying the mate alone;
                        # the child receives a separate conservative pull below.
                        for distance in candidate_distances:
                            target = hub_x + (side * distance)
                            if bool(item["has_origin"]):
                                target = positions[mate][0] + max(
                                    -1.35,
                                    min(1.35, target - positions[mate][0]),
                                )
                            candidate = dict(positions)
                            for previous, previous_target in targets.items():
                                candidate[previous] = (
                                    previous_target,
                                    candidate[previous][1],
                                )
                            for previous, previous_target in child_targets.items():
                                candidate[previous] = (
                                    previous_target,
                                    candidate[previous][1],
                                )
                            candidate[mate] = (target, candidate[mate][1])
                            moved = set(targets) | set(child_targets) | {mate}
                            if has_collision(candidate, moved):
                                continue
                            chosen_mate = target
                            found = True
                            break

                    if found:
                        targets[mate] = chosen_mate
                        child_targets.update(chosen_child)

                    if found and bool(item["terminal"]):
                        # If the coupled child candidate was blocked, search a
                        # short, monotonic series of pulls toward the hub.  A
                        # fixed quarter-pull could remain inside a neighboring
                        # birth-date label; the first collision-free fraction
                        # keeps the terminal half-sibling as close as the
                        # rendered geometry permits without touching another
                        # marker or label.
                        for child in children:
                            if child in chosen_child:
                                continue
                            current_child = positions[child][0]
                            for fraction in (
                                0.25, 0.30, 0.35, 0.40, 0.45,
                                0.50, 0.60, 0.70, 0.80, 0.90,
                            ):
                                proposed_child = current_child + (
                                    (hub_x - current_child) * fraction
                                )
                                candidate = dict(positions)
                                for previous, previous_target in targets.items():
                                    candidate[previous] = (
                                        previous_target,
                                        candidate[previous][1],
                                    )
                                for previous, previous_target in child_targets.items():
                                    candidate[previous] = (
                                        previous_target,
                                        candidate[previous][1],
                                    )
                                candidate[child] = (
                                    proposed_child,
                                    candidate[child][1],
                                )
                                moved = (
                                    set(targets)
                                    | set(child_targets)
                                    | {child}
                                )
                                if not has_collision(candidate, moved):
                                    child_targets[child] = proposed_child
                                    break

            candidate = dict(positions)
            for node, target in targets.items():
                candidate[node] = (target, candidate[node][1])
            for node, target in child_targets.items():
                candidate[node] = (target, candidate[node][1])

            # Reject a fan as a unit if the compact slots create a rendered
            # marker/name collision anywhere on a shared row. The regular
            # solver remains responsible for unrelated branches.
            moved = set(targets) | set(child_targets)
            if has_collision(candidate, moved):
                continue

            # Keep the operation monotonic: it must reduce the combined
            # distance from each branch's hub corridor, not merely reorder it.
            before_cost = sum(abs(positions[node][0] - hub_x) for node in moved)
            after_cost = sum(abs(candidate[node][0] - hub_x) for node in moved)
            if after_cost >= before_cost - 1e-6:
                continue
            for node in moved:
                positions[node] = candidate[node]
            claimed.update(moved)
            changed = True
        return changed

    def _layout_geometry_score(
        self,
        candidate: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
        *,
        chronological: bool = False,
    ) -> Tuple[int, int, int]:
        """Return rendered-node hits, route-marker hits, and crossings."""
        obstacles = self.node_obstacles(candidate, labels, show_inbreeding)
        nodes = sorted(candidate, key=str.casefold)
        node_hits = 0
        for first, second in combinations(nodes, 2):
            left, right = obstacles[first], obstacles[second]
            if (
                left.right > right.left
                and right.right > left.left
                and left.top > right.bottom
                and right.top > left.bottom
            ):
                node_hits += 1

        junctions = self._place_junctions(
            candidate, families, obstacles, chronological=chronological
        )
        proxies: List[Tuple[str, Set[str], Segment]] = []
        for family_id in sorted(junctions, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in candidate]
            children = [node for node in self._children(family) if node in candidate]
            members = set(parents) | set(children)
            junction = junctions[family_id]
            for parent in parents:
                point = candidate[parent]
                proxies.extend(
                    (
                        (family_id, members, (junction, (point[0], junction[1]))),
                        (family_id, members, ((point[0], junction[1]), point)),
                    )
                )
            for child in children:
                proxies.append((family_id, members, (junction, candidate[child])))

        markers = self.marker_obstacles(candidate)
        marker_hits = sum(
            1
            for _family_id, members, segment in proxies
            for node, rect in markers.items()
            if node not in members
            and _path_length(segment) > _EPSILON
            and rect.intersects(segment, margin=0.04)
        )
        crossings = sum(
            1
            for first, second in combinations(proxies, 2)
            if first[0] != second[0]
            and not (first[1] & second[1])
            and _segment_relation(first[2], second[2])[0] in {"cross", "overlap"}
        )
        return node_hits, marker_hits, crossings

    def _compact_focused_parentless_multi_mate_fans(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        focus_nodes: Set[str],
        show_inbreeding: bool,
        *,
        chronological: bool = False,
    ) -> bool:
        """Keep parentless mates together around an indirectly focused hub.

        Ghost status is deliberately irrelevant. The pass handles only an
        unselected hub whose terminal child lies on the selected ancestry
        line, so directly selected hubs and overview layouts retain their
        current geometry. Candidate fans may share one shoulder when that is
        the least disruptive collision-free arrangement.
        """
        if not focus_nodes:
            return False
        parent_families: Dict[str, List[str]] = defaultdict(list)
        origin_families: Dict[str, List[str]] = defaultdict(list)
        parents_by_child: Dict[str, Set[str]] = defaultdict(set)
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            if len(parents) != 2 or not children:
                continue
            for parent in parents:
                parent_families[parent].append(family_id)
            for child in children:
                origin_families[child].append(family_id)
                parents_by_child[child].update(parents)

        lineage = set(focus_nodes)
        pending = sorted(focus_nodes, key=str.casefold, reverse=True)
        while pending:
            child = pending.pop()
            for parent in sorted(
                parents_by_child.get(child, set()), key=str.casefold, reverse=True
            ):
                if parent not in lineage:
                    lineage.add(parent)
                    pending.append(parent)

        changed = False
        partner_gap = self.node_gap + 0.18
        for hub in sorted(parent_families, key=str.casefold):
            if hub in focus_nodes or len(parent_families[hub]) < 2:
                continue
            records: List[Tuple[str, str, str]] = []
            for family_id in sorted(parent_families[hub], key=str.casefold):
                family = families[family_id]
                parents = [node for node in self._parents(family) if node in positions]
                children = [node for node in self._children(family) if node in positions]
                if len(parents) != 2 or len(children) != 1:
                    continue
                mate = parents[0] if parents[1] == hub else parents[1]
                child = children[0]
                if origin_families.get(mate) or parent_families.get(child):
                    continue
                records.append((family_id, mate, child))
            if (
                len(records) < 2
                or len(records) > 4
                or not any(child in lineage for _fid, _mate, child in records)
            ):
                continue

            moved = {
                node for _family_id, mate, child in records for node in (mate, child)
            }
            baseline_geometry = self._layout_geometry_score(
                positions,
                families,
                labels,
                show_inbreeding,
                chronological=chronological,
            )
            baseline_span = max(positions[node][0] for node in moved | {hub}) - min(
                positions[node][0] for node in moved | {hub}
            )
            hub_x = positions[hub][0]
            hub_half = self._estimated_label_width(str(labels.get(hub, hub))) / 2.0
            candidates: List[
                Tuple[
                    Tuple[float, float, int, float, int, int, Tuple[str, ...]],
                    Dict[str, Point],
                ]
            ] = []
            for side in (-1.0, 1.0):
                for ordered in permutations(records):
                    candidate = dict(positions)
                    previous_mate_x = hub_x
                    previous_mate_half = hub_half
                    previous_child_half: Optional[float] = None
                    for order_index, (_family_id, mate, child) in enumerate(ordered):
                        mate_half = self._estimated_label_width(
                            str(labels.get(mate, mate))
                        ) / 2.0
                        child_half = self._estimated_label_width(
                            str(labels.get(child, child))
                        ) / 2.0
                        step = previous_mate_half + mate_half + partner_gap
                        if previous_child_half is not None:
                            step = max(
                                step,
                                2.0 * (
                                    previous_child_half + child_half + self.node_gap
                                ),
                            )
                        mate_x = previous_mate_x + (side * step)
                        child_x = (hub_x + mate_x) / 2.0
                        candidate[mate] = (mate_x, candidate[mate][1])
                        child_y = candidate[child][1]
                        if not chronological and order_index:
                            # Stagger an outer terminal ghost by less than one
                            # normalized lane so its knot stays below its label.
                            child_y += min(0.8, order_index * 0.8)
                        candidate[child] = (child_x, child_y)
                        previous_mate_x = mate_x
                        previous_mate_half = mate_half
                        previous_child_half = child_half

                    # Use the real obstacle-aware family knots as the final
                    # direct-child axes. Same-side mate intervals share a
                    # hub and therefore use separate route lanes; aligning
                    # after lane allocation prevents a new diagonal child
                    # ray while retaining the compact fan.
                    for _align_pass in range(2):
                        candidate_obstacles = self.node_obstacles(
                            candidate, labels, show_inbreeding
                        )
                        candidate_junctions = self._place_junctions(
                            candidate,
                            families,
                            candidate_obstacles,
                            chronological=chronological,
                        )
                        for candidate_family, _mate, child in ordered:
                            if candidate_family in candidate_junctions:
                                candidate[child] = (
                                    candidate_junctions[candidate_family][0],
                                    candidate[child][1],
                                )
                    geometry = self._layout_geometry_score(
                        candidate, families, labels, show_inbreeding,
                        chronological=chronological,
                    )
                    span = max(candidate[node][0] for node in moved | {hub}) - min(
                        candidate[node][0] for node in moved | {hub}
                    )
                    displacement = sum(
                        abs(candidate[node][0] - positions[node][0]) for node in moved
                    )
                    focus_order_penalty = sum(
                        index
                        for index, (_fid, _mate, child) in enumerate(ordered)
                        if child in focus_nodes
                    )
                    candidates.append(
                        (
                            (
                                float(geometry[0]),
                                round(span, 9),
                                focus_order_penalty,
                                round(displacement, 9),
                                geometry[1],
                                geometry[2],
                                tuple(
                                    f"{side:+.0f}:{mate.casefold()}"
                                    for _fid, mate, _child in ordered
                                ),
                            ),
                            candidate,
                        )
                    )

            score, best = min(candidates, key=lambda item: item[0])
            if int(score[0]) > baseline_geometry[0]:
                continue
            if int(score[4]) > baseline_geometry[1]:
                continue
            if int(score[5]) > baseline_geometry[2] + 1:
                continue
            if float(score[1]) + 0.75 >= baseline_span:
                continue
            for node in moved:
                positions[node] = best[node]
            changed = True
        return changed

    def _align_single_child_axes_conservatively(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
        *,
        partner_blocks: Optional[Mapping[str, Set[str]]] = None,
        origin_anchors: Optional[Mapping[str, float]] = None,
        overview_mode: bool = False,
    ) -> None:
        """Move a sole-child block toward its preferred family axis.

        This is a conservative preconditioner: it keeps partner blocks and
        labels clear while reducing the work left for the final soft
        projection. The later solver may retain a small offset where exact
        centring would create a collision or pull another subtree apart.
        """

        row_tolerance = 0.18

        def half_width(node: str) -> float:
            return self._estimated_label_width(str(labels.get(node, node)).strip()) / 2.0

        ordered_families = sorted(
            families.items(),
            key=lambda item: (
                max(
                    (positions[child][1] for child in self._children(item[1]) if child in positions),
                    default=0.0,
                ),
                item[0].casefold(),
            ),
        )
        for _family_id, family in ordered_families:
            parents = [parent for parent in self._parents(family) if parent in positions]
            children = [child for child in self._children(family) if child in positions]
            if len(parents) != 2 or len(children) != 1:
                continue

            child = children[0]
            child_x, child_y = positions[child]
            parent_xs = sorted(positions[parent][0] for parent in parents)
            left, right = parent_xs
            # Keep the child within the clear parent interval without forcing
            # the whole partner block to the arithmetic midpoint. The later
            # sole-child junction pull supplies the strong perpendicular
            # preference inside that feasible corridor.
            inset = min(0.34, max(0.0, ((right - left) / 2.0) - 0.04))
            low = left + inset
            high = right - inset
            if low > high:
                low = high = (left + right) / 2.0
            if low - _EPSILON <= child_x <= high + _EPSILON:
                continue
            preferred = min(max(child_x, low), high)
            moving = set((partner_blocks or {}).get(child, {child}))
            moving = {
                node
                for node in moving
                if node in positions and abs(positions[node][1] - child_y) <= row_tolerance
            } or {child}
            # Work in deltas so an established partner block moves as one.
            allowed: List[Tuple[float, float]] = [(low - child_x, high - child_x)]
            for other, (other_x, other_y) in positions.items():
                if other in moving or abs(other_y - child_y) > row_tolerance:
                    continue
                for moving_node in moving:
                    moving_x, _moving_y = positions[moving_node]
                    required = half_width(moving_node) + half_width(other) + self.node_gap
                    forbidden_left = other_x - required - moving_x
                    forbidden_right = other_x + required - moving_x
                    next_allowed: List[Tuple[float, float]] = []
                    for start, end in allowed:
                        if forbidden_right <= start or forbidden_left >= end:
                            next_allowed.append((start, end))
                            continue
                        if forbidden_left > start:
                            next_allowed.append((start, min(end, forbidden_left)))
                        if forbidden_right < end:
                            next_allowed.append((max(start, forbidden_right), end))
                    allowed = [
                        interval
                        for interval in next_allowed
                        if interval[1] - interval[0] > _EPSILON
                    ]
                    if not allowed:
                        break
                if not allowed:
                    break

            if not allowed:
                continue

            # Overview-only: cap sole-child block movement relative to the
            # immutable pre-pack origin, preventing repeated overview drift.
            if overview_mode and origin_anchors:
                origin_x = origin_anchors.get(child)
                if origin_x is not None:
                    row_xs = sorted(
                        positions[node][0]
                        for node in positions
                        if abs(positions[node][1] - child_y) <= row_tolerance
                    )
                    row_gaps = [
                        right_x - left_x
                        for left_x, right_x in zip(row_xs, row_xs[1:])
                        if right_x - left_x > _EPSILON
                    ]
                    if row_gaps:
                        row_gaps.sort()
                        local_spacing = row_gaps[len(row_gaps) // 2]
                    else:
                        local_spacing = self.node_gap + 0.8
                    parent_span = max(0.0, right - left)
                    origin_budget = max(
                        1.0,
                        min(2.6, max(local_spacing, parent_span * 0.55)),
                    )
                    anchor_start = origin_x - origin_budget - child_x
                    anchor_end = origin_x + origin_budget - child_x
                    anchored_allowed: List[Tuple[float, float]] = []
                    for start, end in allowed:
                        start = max(start, anchor_start)
                        end = min(end, anchor_end)
                        if end - start > _EPSILON:
                            anchored_allowed.append((start, end))
                    if anchored_allowed:
                        allowed = anchored_allowed
                    else:
                        # No obstacle-free delta is both legal and close to
                        # the origin. Keeping the existing block is safer than
                        # creating the long diagonal this correction is meant
                        # to prevent; the bounded junction pass can still
                        # clear the local family geometry afterward.
                        continue

            preferred_delta = preferred - child_x
            candidates = [
                min(max(preferred_delta, start), end)
                for start, end in allowed
            ]
            delta = min(candidates, key=lambda value: (abs(value - preferred_delta), abs(value)))
            for moving_node in moving:
                moving_x, moving_y = positions[moving_node]
                positions[moving_node] = (moving_x + delta, moving_y)

    def _align_single_child_axes_final(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        show_inbreeding: bool,
    ) -> None:
        """Restore perpendicular axes for visible single-child families.

        The horizontal solver may separate labels after the earlier
        preconditioner.  When several one-child families share a hub, moving
        them one at a time makes each candidate collide with the other child's
        *old* position and leaves both diagonal.  Candidates are therefore
        solved per Y lane: unassigned siblings are temporarily ignored, then
        each accepted midpoint candidate becomes an obstacle for the next one.
        This keeps the correction local and deterministic.
        """
        if not positions:
            return

        def overlaps(first: Rect, second: Rect) -> bool:
            return (
                first.right > second.left
                and second.right > first.left
                and first.top > second.bottom
                and second.top > first.bottom
            )

        entries: List[Dict[str, object]] = []
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            if len(parents) != 2 or len(children) != 1:
                continue
            child = children[0]
            if child in protected:
                continue
            left, right = sorted(positions[parent][0] for parent in parents)
            span = max(0.0, right - left)
            inset = min(0.18, max(0.0, (span / 2.0) - 0.04))
            low, high = left + inset, right - inset
            preferred = min(max((left + right) / 2.0, low), high)
            entries.append(
                {
                    "child": child,
                    "y": positions[child][1],
                    "current": positions[child][0],
                    "low": low,
                    "high": high,
                    "preferred": preferred,
                    "family_id": family_id,
                    "parents": set(parents),
                }
            )

        # Couple only one-child families that share a visible parent hub.
        # Chronological mode gives their children different Y lanes, so a
        # same-row grouping would miss exactly the Denethor/Faramir/Boromir
        # case while grouping unrelated branches would be too aggressive.
        components: List[Tuple[Set[str], List[Dict[str, object]]]] = []
        for entry in entries:
            parents = set(entry["parents"])
            matches = [
                index
                for index, (component_parents, _component_entries) in enumerate(components)
                if parents & component_parents
            ]
            if not matches:
                components.append((set(parents), [entry]))
                continue
            first = matches[0]
            component_parents, component_entries = components[first]
            component_parents.update(parents)
            component_entries.append(entry)
            for index in reversed(matches[1:]):
                other_parents, other_entries = components.pop(index)
                component_parents.update(other_parents)
                component_entries.extend(other_entries)

        for _component_parents, lane_entries in components:
            group_children = {str(entry["child"]) for entry in lane_entries}
            assigned: Dict[str, float] = {}
            ordered = sorted(
                lane_entries,
                key=lambda entry: (
                    float(entry["preferred"]),
                    str(entry["family_id"]).casefold(),
                ),
            )
            for entry in ordered:
                child = str(entry["child"])
                current_x = float(entry["current"])
                low = float(entry["low"])
                high = float(entry["high"])
                preferred = float(entry["preferred"])
                candidates: List[float] = []
                for offset in (
                    0.0, -0.10, 0.10, -0.20, 0.20, -0.30, 0.30,
                    -0.45, 0.45, -0.65, 0.65, -0.90, 0.90,
                ):
                    candidate = min(max(preferred + offset, low), high)
                    if all(abs(candidate - existing) > 1e-7 for existing in candidates):
                        candidates.append(candidate)
                candidates.append(current_x)

                chosen = current_x
                for candidate_x in candidates:
                    trial = dict(positions)
                    for assigned_child, assigned_x in assigned.items():
                        trial[assigned_child] = (assigned_x, trial[assigned_child][1])
                    trial[child] = (candidate_x, trial[child][1])
                    obstacles = self.node_obstacles(trial, labels, show_inbreeding)
                    markers = self.marker_obstacles(
                        trial, half_width=0.30, half_height=0.42
                    )
                    child_obstacle = obstacles[child]
                    child_marker = markers[child]
                    collision = False
                    for other in trial:
                        if other == child:
                            continue
                        # Do not test against a same-lane child that has not
                        # received its new coordinate yet; otherwise two
                        # crossing single-child branches block each other.
                        if other in group_children and other not in assigned:
                            continue
                        if overlaps(child_obstacle, obstacles[other]):
                            collision = True
                            break
                        if overlaps(child_obstacle, markers[other]):
                            collision = True
                            break
                        if overlaps(obstacles[other], child_marker):
                            collision = True
                            break
                    if not collision:
                        chosen = candidate_x
                        break
                assigned[child] = chosen

            original_group_positions = {
                child: positions[child] for child in group_children
            }
            for child, chosen in assigned.items():
                positions[child] = (chosen, positions[child][1])

            # The greedy candidate order may leave a later child at its old
            # coordinate when every new candidate is blocked.  Never retain a
            # partially applied group that now overlaps another sibling; the
            # previous geometry is preferable to introducing a new collision.
            group_obstacles = self.node_obstacles(
                positions, labels, show_inbreeding
            )
            group_markers = self.marker_obstacles(
                positions, half_width=0.30, half_height=0.42
            )
            group_children_ordered = sorted(group_children, key=str.casefold)
            group_collision = any(
                (
                    overlaps(group_obstacles[child], group_obstacles[other])
                    or overlaps(group_obstacles[child], group_markers[other])
                    or overlaps(group_obstacles[other], group_markers[child])
                )
                for child in group_children_ordered
                for other in positions
                if other != child
            )
            if group_collision:
                positions.update(original_group_positions)

    def _compute_origin_anchors(
        self,
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
    ) -> Dict[str, float]:
        """Snapshot visible ancestry origins before Overview packing.

        A node with visible parent families is anchored to the weighted median
        of those family corridors.  A two-parent corridor receives twice the
        weight of a one-parent corridor; a node without any visible parent
        family keeps its original X coordinate.  The result is immutable for
        the current arrangement cycle, so later sweeps cannot turn a moved
        partner into the next sweep's origin.
        """
        origin_candidates: Dict[str, List[Tuple[float, float]]] = {
            node: [] for node in positions
        }
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [
                parent for parent in self._parents(family) if parent in positions
            ]
            if not parents:
                continue
            corridor = sum(positions[parent][0] for parent in parents) / len(parents)
            weight = 2.0 if len(parents) >= 2 else 1.0
            for child in self._children(family):
                if child in origin_candidates:
                    origin_candidates[child].append((corridor, weight))

        anchors: Dict[str, float] = {}
        for node, point in positions.items():
            candidates = origin_candidates.get(node, [])
            if not candidates:
                anchors[node] = float(point[0])
                continue
            ordered = sorted(candidates, key=lambda item: (item[0], item[1]))
            total_weight = sum(weight for _value, weight in ordered)
            threshold = total_weight / 2.0
            cumulative = 0.0
            for value, weight in ordered:
                cumulative += weight
                if cumulative >= threshold:
                    anchors[node] = float(value)
                    break
            else:
                anchors[node] = float(ordered[-1][0])
        return anchors

    def _assign_generation_rows(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
    ) -> None:
        """Assign compact generations while preserving consanguineous exceptions."""
        parent_map: Dict[str, Set[str]] = {node: set() for node in positions}
        for family in families.values():
            parents = {parent for parent in self._parents(family) if parent in positions}
            for child in self._children(family):
                if child in positions:
                    parent_map[child].update(parents)

        levels: Dict[str, int] = {}
        visiting: List[str] = []
        cycle_nodes: Set[str] = set()

        def level_of(node: str) -> int:
            if node in levels:
                return levels[node]
            if node in visiting:
                cycle_start = visiting.index(node)
                cycle_nodes.update(visiting[cycle_start:])
                return 0
            visiting.append(node)
            parent_levels = [level_of(parent) for parent in sorted(parent_map[node], key=str.casefold)]
            visiting.pop()
            levels[node] = max(parent_levels, default=-1) + 1
            return levels[node]

        for node in sorted(positions, key=str.casefold):
            level_of(node)

        def is_ancestor(ancestor: str, descendant: str) -> bool:
            pending = list(parent_map.get(descendant, set()))
            seen: Set[str] = set()
            while pending:
                current = pending.pop()
                if current == ancestor:
                    return True
                if current in seen:
                    continue
                seen.add(current)
                pending.extend(parent_map.get(current, set()) - seen)
            return False

        max_passes = max(4, len(positions) * 3)
        for _pass in range(max_passes):
            changed = False
            for family in families.values():
                parents = [parent for parent in self._parents(family) if parent in positions]
                if len(parents) != 2:
                    continue
                first, second = parents
                if is_ancestor(first, second) or is_ancestor(second, first):
                    continue
                partner_level = max(levels[first], levels[second])
                for parent in parents:
                    if levels[parent] < partner_level:
                        levels[parent] = partner_level
                        changed = True

            for child, parents in parent_map.items():
                usable = [
                    parent
                    for parent in parents
                    if not (child in cycle_nodes and parent in cycle_nodes)
                ]
                if not usable:
                    continue
                required = max(levels[parent] for parent in usable) + 1
                if levels[child] < required:
                    levels[child] = required
                    changed = True
            if not changed:
                break

        # Leave enough vertical runway for direct descendant rays to clear the
        # rendered marker/name/F boxes of inner siblings.  A tighter two-unit
        # rank made four-child fans geometrically impossible without extreme
        # horizontal expansion.
        level_spacing = 3.6
        for node, (x, _old_y) in positions.items():
            positions[node] = (x, levels.get(node, 0) * level_spacing)



    def _solve_horizontal_constraints(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
        *,
        chronological: bool = False,
        node_weights: Optional[Mapping[str, float]] = None,
    ) -> None:
        """Resolve label/route collisions while retaining family block order.

        Besides separating complete animal-label rectangles, the projection
        clears the canonical parent entries and offspring corridors around a
        family knot. This deliberately makes a branch wider before the
        endpoint router builds its canonical two-segment parent entry; labels
        remain readable through their halo rather than by breaking that line
        (the Elrond/Jessica and Arwen/Taylor regressions).
        """
        if len(positions) < 2:
            return
        try:
            import numpy as np
        except ImportError:
            for row in self._cluster_rows(positions):
                self._deoverlap_row(
                    positions, row, labels, set(), show_inbreeding
                )
            return

        nodes = sorted(positions, key=str.casefold)
        index = {node: offset for offset, node in enumerate(nodes)}
        initial = np.asarray([positions[node][0] for node in nodes], dtype=float)
        obstacles = self.node_obstacles(positions, labels, show_inbreeding)
        route_obstacles = self.marker_obstacles(positions)
        soft_center_rows: List[Tuple[np.ndarray, float]] = []
        collision_pairs: List[Tuple[int, int, float, float]] = []
        row_pair_requirements: List[Tuple[int, int, float]] = []

        # Preserve the visual left-to-right order while separating any pair
        # whose complete marker/name/detail rectangles share vertical space.
        # For large stress graphs, discover only pairs sharing a vertical cell;
        # sparse generations therefore avoid an O(N²) all-pairs scan.
        if len(nodes) > 256:
            vertical_cell = 2.0
            row_index: Dict[int, List[str]] = defaultdict(list)
            for node in nodes:
                rect = obstacles[node]
                low = math.floor(rect.bottom / vertical_cell)
                high = math.floor(rect.top / vertical_cell)
                for bucket in range(low, high + 1):
                    row_index[bucket].append(node)
            candidate_pairs: Set[Tuple[str, str]] = set()
            for bucket_nodes in row_index.values():
                ordered_bucket = sorted(bucket_nodes, key=lambda value: index[value])
                for first, second in combinations(ordered_bucket, 2):
                    candidate_pairs.add((first, second))
        else:
            candidate_pairs = {
                (first, second)
                for left_offset, first in enumerate(nodes)
                for second in nodes[left_offset + 1 :]
            }
        for first, second in sorted(
            candidate_pairs, key=lambda pair: (index[pair[0]], index[pair[1]])
        ):
            first_rect = obstacles[first]
            second_rect = obstacles[second]
            if not _ranges_overlap(
                first_rect.bottom,
                first_rect.top,
                second_rect.bottom,
                second_rect.top,
            ):
                continue
            required = (
                (first_rect.right - first_rect.left) / 2.0
                + (second_rect.right - second_rect.left) / 2.0
                + self.node_gap
            )
            # Record the clearance now; its direction is taken from the
            # completed origin-aware partner/sibship sweep below.
            row_pair_requirements.append(
                (index[first], index[second], required)
            )

        # A parent connection is semantically easiest to read as one
        # horizontal segment out of the knot followed by one vertical entry
        # into the parent.  If that vertical corridor cuts through a foreign
        # animal/label, expand the participating branches horizontally.  The
        # family springs below distribute the movement through the related
        # ancestors and descendants instead of detaching just one marker.
        route_index: Optional[Dict[Tuple[int, int], List[str]]] = None
        route_cell = 2.0
        if len(route_obstacles) > 256:
            route_index = defaultdict(list)
            for node, rect in route_obstacles.items():
                low_x = math.floor(rect.left / route_cell)
                high_x = math.floor(rect.right / route_cell)
                low_y = math.floor(rect.bottom / route_cell)
                high_y = math.floor(rect.top / route_cell)
                for ix in range(low_x, high_x + 1):
                    for iy in range(low_y, high_y + 1):
                        route_index[(ix, iy)].append(node)

        def corridor_candidates(corridor: Segment) -> List[str]:
            if route_index is None:
                return sorted(route_obstacles, key=str.casefold)
            (x1, y1), (x2, y2) = corridor
            margin = self.route_clearance + 0.35
            low_x = math.floor((min(x1, x2) - margin) / route_cell)
            high_x = math.floor((max(x1, x2) + margin) / route_cell)
            low_y = math.floor((min(y1, y2) - margin) / route_cell)
            high_y = math.floor((max(y1, y2) + margin) / route_cell)
            names: Set[str] = set()
            for ix in range(low_x, high_x + 1):
                for iy in range(low_y, high_y + 1):
                    names.update(route_index.get((ix, iy), ()))
            return sorted(names, key=str.casefold)

        provisional_junctions = self._place_junctions(
            positions,
            families,
            obstacles,
            chronological=chronological,
        )
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [parent for parent in self._parents(family) if parent in index]
            children = [child for child in self._children(family) if child in index]
            junction = provisional_junctions.get(family_id)
            if not parents or not children or junction is None:
                continue
            family_members = set(parents) | set(children)
            for parent in parents:
                parent_x, parent_y = positions[parent]
                corridor = ((parent_x, parent_y), (parent_x, junction[1]))
                for foreign in corridor_candidates(corridor):
                    rect = route_obstacles[foreign]
                    if foreign in family_members:
                        continue
                    if not rect.intersects(corridor, margin=self.route_clearance):
                        continue
                    difference = initial[index[foreign]] - initial[index[parent]]
                    if abs(difference) <= _EPSILON:
                        # Push a left parent farther left and a right parent
                        # farther right when both currently share an X.
                        direction = 1.0 if parent_x <= junction[0] else -1.0
                    else:
                        direction = 1.0 if difference > 0.0 else -1.0
                    required = (
                        (rect.right - rect.left) / 2.0
                        + self.route_clearance
                    )
                    collision_pairs.append(
                        (index[parent], index[foreign], required, direction)
                    )
            for child in children:
                child_x, child_y = positions[child]
                corridor = (junction, (child_x, child_y))
                for foreign in sorted(route_obstacles, key=str.casefold):
                    if foreign in family_members:
                        continue
                    rect = route_obstacles[foreign]
                    if not rect.intersects(corridor, margin=self.route_clearance):
                        continue
                    difference = initial[index[foreign]] - initial[index[child]]
                    if abs(difference) <= _EPSILON:
                        direction = 1.0 if child_x <= junction[0] else -1.0
                    else:
                        direction = 1.0 if difference > 0.0 else -1.0
                    required = (
                        (rect.right - rect.left) / 2.0
                        + self.route_clearance
                    )
                    collision_pairs.append(
                        (index[child], index[foreign], required, direction)
                    )

        # Family axes are visual preferences, not pedigree invariants. Keep a
        # sole child close to its parent axis and a sibling fan roughly
        # balanced, but never force another branch to compensate exactly for a
        # large descendant subtree. These springs are applied before the hard
        # obstacle clearances below.
        continuing_children = {
            parent
            for candidate in families.values()
            if any(child in index for child in self._children(candidate))
            for parent in self._parents(candidate)
            if parent in index
        }
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [
                parent for parent in self._parents(family) if parent in index
            ]
            children = [
                child for child in self._children(family) if child in index
            ]
            if not parents or not children:
                continue
            branch_children = [
                child for child in children if child in continuing_children
            ]
            terminal_children = [
                child for child in children if child not in continuing_children
            ]
            # Terminal leaves and siblings that own descendant families are
            # related semantically, but must not be one symmetry equation.
            # Otherwise a broad continuing subtree makes the terminal leaves
            # compensate across the parent axis and its direct family lines.
            if branch_children and terminal_children:
                # The two visual groups are seeded separately after the
                # general solver. Do not couple either group to the other (or
                # to a compensating common barycentre) here.
                continue
            centered_children = children
            vector = np.zeros(len(nodes), dtype=float)
            for child in centered_children:
                vector[index[child]] += 1.0 / len(centered_children)
            for parent in parents:
                vector[index[parent]] -= 1.0 / len(parents)
            soft_center_rows.append(
                (
                    vector,
                    0.45
                    if len(centered_children) == 1
                    else 0.16,
                )
            )

        if not soft_center_rows and not collision_pairs and not row_pair_requirements:
            return
        # Weighted projection preserves the block order selected above while
        # resolving real collisions. Family centring is deliberately a soft
        # layout preference now: an exact sibling barycentre made a large
        # continuing descendant subtree push small terminal siblings far away
        # merely to satisfy an equation that carries no pedigree semantics.
        # The movement metric is diagonal by construction. Materialising an
        # N×N matrix and calling a general O(N³) inverse made dense pedigrees
        # pay cubic setup cost, followed by O(N²) matrix-vector products in
        # every collision projection. The reciprocal diagonal is exactly the
        # same metric inverse and keeps both operations linear in node count.
        metric_inverse = np.asarray(
            [
                1.0
                / max(1.0, float((node_weights or {}).get(node, 1.0)))
                for node in nodes
            ],
            dtype=float,
        )
        candidate = initial.copy()

        def relax_family_centers(scale: float, max_node_step: float) -> None:
            for vector, strength in soft_center_rows:
                residual = float(np.dot(vector, candidate))
                if abs(residual) <= 1e-7:
                    continue
                movement = metric_inverse * vector
                gain = float(np.dot(vector, movement))
                if gain <= 1e-10:
                    continue
                delta = movement * (-(residual * strength * scale) / gain)
                largest = float(np.max(np.abs(delta)))
                if largest > max_node_step:
                    delta *= max_node_step / largest
                candidate[:] = candidate + delta

        # A few bounded sweeps bring badly slanted one-child branches back
        # toward vertical without reinstating an exact global equation.
        for _round in range(3):
            relax_family_centers(1.0, 1.0)

        # The repeated origin-aware block sweeps supply the meaningful layer
        # order (including extended sibling blocks around multiple mates).
        # Preserve that order while separating complete rendered boxes; a
        # clearance move must not interleave two previously contiguous
        # families.
        for first, second, required in row_pair_requirements:
            difference = initial[second] - initial[first]
            if abs(difference) <= _EPSILON:
                direction = 1.0 if nodes[first].casefold() <= nodes[second].casefold() else -1.0
            else:
                direction = 1.0 if difference > 0.0 else -1.0
            collision_pairs.append((first, second, required, direction))

        # Multiple observations can describe the same ordered separation.
        # Retain the strongest one so the projection remains deterministic
        # and large pedigrees do not repeat identical work.
        strongest_pairs: Dict[Tuple[int, int, float], float] = {}
        for first, second, required, direction in collision_pairs:
            key = (first, second, direction)
            strongest_pairs[key] = max(required, strongest_pairs.get(key, 0.0))
        collision_pairs = [
            (first, second, required, direction)
            for (first, second, direction), required in strongest_pairs.items()
        ]

        def clear_collisions() -> None:
            # Alternating weighted projections push colliding label rectangles
            # apart. The initial direction is stable, but a zero-distance pair
            # can still choose the direction with the greater feasible move.
            for _pass in range(160):
                worst_deficit = 0.0
                changed = False
                for first, second, required, direction in collision_pairs:
                    difference = candidate[second] - candidate[first]
                    deficit = required - (difference * direction)
                    worst_deficit = max(worst_deficit, deficit)
                    if deficit <= 1e-7:
                        continue
                    raw = np.zeros(len(nodes), dtype=float)
                    raw[first] = -direction
                    raw[second] = direction
                    movement = metric_inverse * raw
                    gain = float(np.dot(raw, movement))
                    if gain <= 1e-10:
                        continue
                    candidate[:] = candidate + (
                        movement * ((deficit * 1.002) / gain)
                    )
                    changed = True
                if not changed or worst_deficit <= 1e-7:
                    break

        if metric_inverse.size:
            clear_collisions()
        for node, x in zip(nodes, candidate):
            positions[node] = (round(float(x), 10), positions[node][1])

    def _shift_automatic_components_from_locks(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        show_inbreeding: bool,
    ) -> None:
        """Move unlocked components as blocks when an unrelated manual lock occupies them."""
        adjacency: Dict[str, Set[str]] = {node: set() for node in positions}
        for family in families.values():
            members = [
                node
                for node in self._parents(family) + self._children(family)
                if node in positions
            ]
            for node in members:
                adjacency[node].update(member for member in members if member != node)

        automatic = set(positions) - protected
        components: List[Set[str]] = []
        seen: Set[str] = set()
        for seed in sorted(automatic, key=str.casefold):
            if seed in seen:
                continue
            component: Set[str] = set()
            pending = [seed]
            while pending:
                node = pending.pop()
                if node in component or node not in automatic:
                    continue
                component.add(node)
                pending.extend(adjacency[node] - component)
            seen.update(component)
            components.append(component)

        obstacles = self.node_obstacles(positions, labels, show_inbreeding)
        lock_gap = self.node_gap + self.route_clearance
        for component in components:
            if any(adjacency[node] & protected for node in component):
                continue
            for locked in sorted(protected, key=str.casefold):
                locked_rect = obstacles.get(locked)
                if locked_rect is None:
                    continue
                component_rects = [obstacles[node] for node in component]
                left = min(rect.left for rect in component_rects)
                right = max(rect.right for rect in component_rects)
                bottom = min(rect.bottom for rect in component_rects)
                top = max(rect.top for rect in component_rects)
                if not (
                    _ranges_overlap(left, right, locked_rect.left, locked_rect.right)
                    and _ranges_overlap(bottom, top, locked_rect.bottom, locked_rect.top)
                ):
                    continue
                shift_left = (locked_rect.left - lock_gap) - right
                shift_right = (locked_rect.right + lock_gap) - left
                shift = min((shift_left, shift_right), key=lambda value: (abs(value), value))
                for node in component:
                    x, y = positions[node]
                    positions[node] = (x + shift, y)
                    rect = obstacles[node]
                    obstacles[node] = Rect(
                        rect.left + shift,
                        rect.right + shift,
                        rect.bottom,
                        rect.top,
                    )

    @staticmethod
    def _cluster_rows(positions: Mapping[str, Point], tolerance: float = 0.42) -> List[List[str]]:
        rows: List[List[str]] = []
        row_centers: List[float] = []
        for node in sorted(positions, key=lambda name: (positions[name][1], positions[name][0], name.casefold())):
            y = positions[node][1]
            target: Optional[int] = None
            for index, center in enumerate(row_centers):
                if abs(y - center) <= tolerance:
                    target = index
                    break
            if target is None:
                rows.append([node])
                row_centers.append(y)
            else:
                rows[target].append(node)
                row_centers[target] = sum(positions[item][1] for item in rows[target]) / len(rows[target])
        return rows

    def _deoverlap_row(
        self,
        positions: Dict[str, Point],
        row: Sequence[str],
        labels: Mapping[str, str],
        protected: Set[str],
        show_inbreeding: bool,
    ) -> None:
        if len(row) < 2:
            return

        def half_width(node: str) -> float:
            label = str(labels.get(node, node)).strip()
            return self._estimated_label_width(label) / 2.0

        original_center = sum(positions[node][0] for node in row) / len(row)
        ordered = sorted(row, key=lambda node: (positions[node][0], node.casefold()))

        if not (set(row) & protected):
            previous: Optional[str] = None
            for node in ordered:
                if previous is not None:
                    required = half_width(previous) + half_width(node) + self.node_gap
                    x, y = positions[node]
                    positions[node] = (max(x, positions[previous][0] + required), y)
                previous = node
            new_center = sum(positions[node][0] for node in row) / len(row)
            offset = original_center - new_center
            for node in row:
                x, y = positions[node]
                positions[node] = (x + offset, y)
            return

        for _pass in range(len(row) * 3):
            changed = False
            ordered = sorted(row, key=lambda node: (positions[node][0], node.casefold()))
            for left, right in zip(ordered, ordered[1:]):
                required = half_width(left) + half_width(right) + self.node_gap
                actual = positions[right][0] - positions[left][0]
                if actual + _EPSILON >= required:
                    continue
                deficit = required - actual
                lx, ly = positions[left]
                rx, ry = positions[right]
                if left in protected and right in protected:
                    continue
                if left in protected:
                    positions[right] = (rx + deficit, ry)
                elif right in protected:
                    positions[left] = (lx - deficit, ly)
                else:
                    positions[left] = (lx - (deficit / 2.0), ly)
                    positions[right] = (rx + (deficit / 2.0), ry)
                changed = True
            if not changed:
                break

    def _place_junctions(
        self,
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
        obstacles: Mapping[str, Rect],
        *,
        chronological: bool = False,
    ) -> Dict[str, Point]:
        grouped: Dict[
            Tuple[float, float],
            List[Tuple[str, float, float, float, bool, float, float]],
        ] = {}
        collapsed: List[
            Tuple[
                str,
                Point,
                bool,
                Optional[Tuple[float, float]],
                Optional[Tuple[float, float]],
            ]
        ] = []

        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            if not parents:
                continue
            if not children:
                collapsed.append(
                    (
                        family_id,
                        self._collapsed_junction(parents, positions),
                        len(parents) == 2,
                        None,
                        None,
                    )
                )
                continue
            parent_x = sum(positions[node][0] for node in parents) / len(parents)
            child_xs = sorted(positions[node][0] for node in children)
            middle = len(child_xs) // 2
            child_x = (
                child_xs[middle]
                if len(child_xs) % 2
                else (child_xs[middle - 1] + child_xs[middle]) / 2.0
            )
            parent_ys = [positions[node][1] for node in parents]
            parent_mid_y = sum(parent_ys) / len(parent_ys)
            child_ys = [positions[node][1] for node in children]
            # Use the child row nearest to the parents as the clear corridor
            # boundary.  Averaging a staggered sibling fan placed the knot
            # immediately below its lowest child, producing faint, detached-
            # looking stubs even though topology was complete.
            if min(child_ys) >= parent_mid_y:
                child_y = min(child_ys)
            elif max(child_ys) <= parent_mid_y:
                child_y = max(child_ys)
            else:
                child_y = min(child_ys, key=lambda value: abs(value - parent_mid_y))
            # In an ancestor/offspring pairing the parents intentionally occupy
            # different ranks.  Place the junction beyond the parent closest
            # to the children, leaving room for the canonical horizontal rail
            # instead of squeezing it into that parent's marker/label box.
            parent_y = max(parent_ys) if chronological else (
                max(parent_ys)
                if child_y >= parent_mid_y
                else min(parent_ys)
            )
            bounded_between_parents = len(parents) == 2
            if bounded_between_parents:
                parent_left = min(positions[node][0] for node in parents)
                parent_right = max(positions[node][0] for node in parents)
                parent_span = parent_right - parent_left
                # The midpoint remains the strong default, but a bounded pull
                # toward the median child/subtree centre avoids long diagonal
                # fans and extreme compensating sibling placements.  The knot
                # always remains visibly between both parent endpoints.
                # A sole visible child should receive a near-perpendicular
                # family rail whenever the parent interval permits it.  The
                # former universal 1.35-unit cap could leave a wide-parent,
                # single-child family diagonally detached even though the
                # child was safely between both parents.  Keep the historic
                # cap for sibling groups; only a one-child family may expand
                # to the child's bounded corridor (with marker clearance).
                corridor_limit = max(
                    0.0, (parent_span / 2.0) - 0.08
                )
                child_clearance = max(0.35, self.node_gap)
                child_inside_corridor = (
                    len(children) == 1
                    and parent_left + child_clearance <= child_x <= parent_right - child_clearance
                )
                if child_inside_corridor:
                    maximum_shift = min(
                        corridor_limit,
                        max(1.35, abs(child_x - parent_x) + self.route_clearance),
                    )
                    desired_shift = child_x - parent_x
                else:
                    maximum_shift = min(
                        1.35,
                        parent_span * 0.22,
                        corridor_limit,
                    )
                    desired_shift = (child_x - parent_x) * 0.55
                base_x = parent_x + max(
                    -maximum_shift, min(maximum_shift, desired_shift)
                )
            else:
                base_x = (parent_x + child_x) / 2.0
            key = (round(parent_y, 5), round(child_y, 5))
            grouped.setdefault(key, []).append(
                (
                    family_id,
                    base_x,
                    parent_y,
                    child_y,
                    bounded_between_parents,
                    min(positions[node][0] for node in parents),
                    max(positions[node][0] for node in parents),
                )
            )

        raw: List[
            Tuple[
                str,
                Point,
                bool,
                Optional[Tuple[float, float]],
                Optional[Tuple[float, float]],
            ]
        ] = list(collapsed)
        for entries in grouped.values():
            entries.sort(key=lambda item: (item[5], item[6], item[0].casefold()))
            lane_right_edges: List[float] = []
            entry_lanes: Dict[str, int] = {}
            for family_id, _base_x, _py, _cy, _fixed, left, right in entries:
                lane = next(
                    (
                        index
                        for index, lane_right in enumerate(lane_right_edges)
                        if lane_right < left - self.route_clearance
                    ),
                    len(lane_right_edges),
                )
                if lane == len(lane_right_edges):
                    lane_right_edges.append(right)
                else:
                    lane_right_edges[lane] = right
                entry_lanes[family_id] = lane

            lane_count = len(lane_right_edges)
            lane_order = sorted(
                range(lane_count),
                key=lambda lane: (
                    -max(
                        (
                            len(self._children(families.get(family_id, {})))
                            for family_id, assigned_lane in entry_lanes.items()
                            if assigned_lane == lane
                        ),
                        default=0,
                    ),
                    lane,
                ),
            )
            lane_rank = {lane: rank for rank, lane in enumerate(lane_order)}
            for family_id, base_x, parent_y, child_y, bounded_x, left, right in entries:
                # Only parent intervals that actually overlap receive distinct
                # rails.  Fractions stay in the clear corridor between marker
                # and label boxes; unrelated families remain on one tidy row.
                fraction = (
                    0.52
                    if lane_count == 1
                    else 0.38 + (0.24 * lane_rank[entry_lanes[family_id]] / (lane_count - 1))
                )
                y = parent_y + ((child_y - parent_y) * fraction)
                low_y, high_y = sorted((parent_y, child_y))
                padding = min(0.25, (high_y - low_y) * 0.15)
                x_bounds: Optional[Tuple[float, float]] = None
                if bounded_x:
                    midpoint = (left + right) / 2.0
                    parent_span = right - left
                    maximum_shift = min(
                        1.35,
                        parent_span * 0.22,
                        max(0.0, (parent_span / 2.0) - 0.08),
                    )
                    x_bounds = (
                        midpoint - maximum_shift,
                        midpoint + maximum_shift,
                    )
                raw.append(
                    (
                        family_id,
                        (base_x, y),
                        bounded_x,
                        x_bounds,
                        (low_y + padding, high_y - padding),
                    )
                )

        placed: Dict[str, Point] = {}
        # Large pedigrees used to rescan every node obstacle and every placed
        # junction for every candidate point.  Keep the exact legacy scoring
        # for normal/current-seed graphs, but use a small uniform index for
        # stress graphs so local obstacle queries remain bounded.
        spatial_cell = 2.0
        obstacle_index: Optional[Dict[Tuple[int, int], List[Rect]]] = None
        placed_index: Optional[Dict[Tuple[int, int], List[Point]]] = None
        if len(obstacles) > 256:
            obstacle_index = defaultdict(list)
            for rect in obstacles.values():
                left = math.floor(rect.left / spatial_cell)
                right = math.floor(rect.right / spatial_cell)
                bottom = math.floor(rect.bottom / spatial_cell)
                top = math.floor(rect.top / spatial_cell)
                for ix in range(left, right + 1):
                    for iy in range(bottom, top + 1):
                        obstacle_index[(ix, iy)].append(rect)
            placed_index = defaultdict(list)

        for family_id, base, bounded_x, x_bounds, y_bounds in sorted(
            raw,
            key=lambda item: (item[1][1], item[1][0], item[0].casefold()),
        ):
            point = self._free_junction_point(
                base,
                obstacles,
                placed,
                bounded_x=bounded_x,
                x_bounds=x_bounds,
                y_bounds=y_bounds,
                obstacle_index=obstacle_index,
                placed_index=placed_index,
                spatial_cell=spatial_cell,
            )
            placed[family_id] = point
            if placed_index is not None:
                cell = (
                    math.floor(point[0] / spatial_cell),
                    math.floor(point[1] / spatial_cell),
                )
                placed_index[cell].append(point)
        return placed

    @staticmethod
    def _collapsed_junction(parents: Sequence[str], positions: Mapping[str, Point]) -> Point:
        x = sum(positions[node][0] for node in parents) / len(parents)
        y = sum(positions[node][1] for node in parents) / len(parents)
        return x, y + 0.65

    def _free_junction_point(
        self,
        base: Point,
        obstacles: Mapping[str, Rect],
        placed: Mapping[str, Point],
        *,
        bounded_x: bool,
        x_bounds: Optional[Tuple[float, float]],
        y_bounds: Optional[Tuple[float, float]],
        obstacle_index: Optional[Mapping[Tuple[int, int], Sequence[Rect]]] = None,
        placed_index: Optional[Mapping[Tuple[int, int], Sequence[Point]]] = None,
        spatial_cell: float = 2.0,
    ) -> Point:
        base_x, base_y = base

        def indexed_rects(x: float, y: float, radius: float = 0.05) -> List[Rect]:
            if obstacle_index is None:
                return list(obstacles.values())
            ix0 = math.floor((x - radius) / spatial_cell)
            ix1 = math.floor((x + radius) / spatial_cell)
            iy0 = math.floor((y - radius) / spatial_cell)
            iy1 = math.floor((y + radius) / spatial_cell)
            found: Dict[int, Rect] = {}
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    for rect in obstacle_index.get((ix, iy), ()):
                        found[id(rect)] = rect
            return list(found.values())

        def indexed_rects_for_x(low: float, high: float) -> List[Rect]:
            if obstacle_index is None:
                return list(obstacles.values())
            # Boundary candidates are only a heuristic.  For large graphs a
            # bounded local window is sufficient and avoids a full scan.
            center = (low + high) / 2.0
            radius = max(abs(high - low) / 2.0, spatial_cell)
            return indexed_rects(center, base_y, radius=radius)
        x_candidates = [base_x]
        if bounded_x and x_bounds is not None:
            low_x, high_x = x_bounds
            for step in range(1, 5 if obstacle_index is not None else 13):
                offset = step * 0.16
                for candidate in (base_x - offset, base_x + offset):
                    if low_x <= candidate <= high_x:
                        x_candidates.append(candidate)
            for rect in indexed_rects_for_x(low_x, high_x):
                for candidate in (
                    rect.left - self.junction_clearance,
                    rect.right + self.junction_clearance,
                ):
                    if low_x <= candidate <= high_x:
                        x_candidates.append(candidate)
            x_candidates.extend((low_x, high_x))
        elif not bounded_x:
            for step in range(1, 5 if obstacle_index is not None else 17):
                offset = step * 0.32
                x_candidates.extend((base_x - offset, base_x + offset))
            if obstacle_index is None:
                boundary_rects = list(obstacles.values())
            else:
                boundary_rects = indexed_rects_for_x(
                    base_x - (16 * 0.32), base_x + (16 * 0.32)
                )
            for rect in boundary_rects:
                x_candidates.extend(
                    (
                        rect.left - self.junction_clearance,
                        rect.right + self.junction_clearance,
                    )
                )

        y_candidates = [base_y]
        if y_bounds is not None:
            low_y, high_y = y_bounds
            for step in range(1, 4 if obstacle_index is not None else 9):
                offset = step * 0.12
                for candidate in (base_y - offset, base_y + offset):
                    if low_y <= candidate <= high_y:
                        y_candidates.append(candidate)
            boundary_rects = (
                list(obstacles.values())
                if obstacle_index is None
                else indexed_rects_for_x(
                    base_x - (16 * 0.32), base_x + (16 * 0.32)
                )
            )
            for rect in boundary_rects:
                for candidate in (
                    rect.bottom - self.junction_clearance,
                    rect.top + self.junction_clearance,
                ):
                    if low_y <= candidate <= high_y:
                        y_candidates.append(candidate)

        def score(point: Point) -> Tuple[float, float, float]:
            x, y = point
            blocked = sum(
                1 for rect in indexed_rects(x, y, radius=0.04)
                if rect.contains(point, margin=0.04)
            )
            if placed_index is None:
                nearby_points = placed.values()
            else:
                radius = self.junction_clearance * 2.0
                ix0 = math.floor((x - radius) / spatial_cell)
                ix1 = math.floor((x + radius) / spatial_cell)
                iy0 = math.floor((y - radius) / spatial_cell)
                iy1 = math.floor((y + radius) / spatial_cell)
                nearby_points = [
                    other
                    for ix in range(ix0, ix1 + 1)
                    for iy in range(iy0, iy1 + 1)
                    for other in placed_index.get((ix, iy), ())
                ]
            crowded = sum(
                1
                for other in nearby_points
                if abs(other[0] - x) < (self.junction_clearance * 2.0)
                and abs(other[1] - y) < (self.junction_clearance * 2.0)
            )
            distance = abs(x - base_x) + abs(y - base_y)
            return (blocked * 10000.0) + (crowded * 1500.0) + distance, x, y

        points = {
            (round(x, 7), round(y, 7))
            for x in x_candidates
            for y in y_candidates
        }
        best_x, best_y = min(points, key=score)
        return float(best_x), float(best_y)

    @staticmethod
    def _build_rect_spatial_index(
        obstacles: Mapping[str, Rect],
        cell_size: float = 2.0,
    ) -> Dict[Tuple[int, int], List[Tuple[str, Rect]]]:
        index: Dict[Tuple[int, int], List[Tuple[str, Rect]]] = defaultdict(list)
        for name, rect in obstacles.items():
            low_x = math.floor(rect.left / cell_size)
            high_x = math.floor(rect.right / cell_size)
            low_y = math.floor(rect.bottom / cell_size)
            high_y = math.floor(rect.top / cell_size)
            for ix in range(low_x, high_x + 1):
                for iy in range(low_y, high_y + 1):
                    index[(ix, iy)].append((name, rect))
        return index

    @staticmethod
    def _rect_candidates_for_segment(
        segment: Segment,
        obstacles: Mapping[str, Rect],
        index: Optional[Mapping[Tuple[int, int], Sequence[Tuple[str, Rect]]]],
        cell_size: float = 2.0,
    ) -> Sequence[Tuple[str, Rect]]:
        if index is None:
            return obstacles.items()
        (x1, y1), (x2, y2) = segment
        margin = 0.02
        low_x = math.floor((min(x1, x2) - margin) / cell_size)
        high_x = math.floor((max(x1, x2) + margin) / cell_size)
        low_y = math.floor((min(y1, y2) - margin) / cell_size)
        high_y = math.floor((max(y1, y2) + margin) / cell_size)
        found: Dict[str, Rect] = {}
        for ix in range(low_x, high_x + 1):
            for iy in range(low_y, high_y + 1):
                for name, rect in index.get((ix, iy), ()):
                    found[name] = rect
        return found.items()

    def _route_endpoint(
        self,
        family_id: str,
        endpoint: str,
        start: Point,
        end: Point,
        obstacles: Mapping[str, Rect],
        owned_segments: Sequence[_OwnedSegment],
        *,
        parent_entry: bool,
        obstacle_index: Optional[Mapping[Tuple[int, int], Sequence[Tuple[str, Rect]]]] = None,
        owned_segment_index: Optional[
            Mapping[Tuple[int, int], Sequence[_OwnedSegment]]
        ] = None,
    ) -> Tuple[List[Point], bool, bool]:
        if not parent_entry:
            path = [start, end]
            _score, overlap, obstacle_hit = self._score_path(
                family_id,
                endpoint,
                path,
                obstacles,
                owned_segments,
                obstacle_index=obstacle_index,
                owned_segment_index=owned_segment_index,
            )
            return path, overlap, obstacle_hit

        # Parent connections have one canonical, unambiguous shape: leave the
        # family node horizontally and enter the parent vertically. Earlier
        # obstacle candidates could add two extra bends; even when technically
        # collision-free those doglegs looked like another family rail. Node
        # placement owns collision avoidance, while marker masks and text
        # halos preserve readability for the unavoidable remainder.
        canonical = _simplify_path([start, (end[0], start[1]), end])
        _score, overlap, obstacle_hit = self._score_path(
            family_id,
            endpoint,
            canonical,
            obstacles,
            owned_segments,
            obstacle_index=obstacle_index,
            owned_segment_index=owned_segment_index,
        )
        return canonical, overlap, obstacle_hit

    def _score_path(
        self,
        family_id: str,
        endpoint: str,
        path: Sequence[Point],
        obstacles: Mapping[str, Rect],
        owned_segments: Sequence[_OwnedSegment],
        *,
        obstacle_index: Optional[Mapping[Tuple[int, int], Sequence[Tuple[str, Rect]]]] = None,
        owned_segment_index: Optional[
            Mapping[Tuple[int, int], Sequence[_OwnedSegment]]
        ] = None,
    ) -> Tuple[float, bool, bool]:
        segments = _path_segments(path)
        score = _path_length(path) + (max(0, len(segments) - 1) * 0.30)
        overlap = False
        obstacle_hit = False

        for index, segment in enumerate(segments):
            for obstacle_name, rect in self._rect_candidates_for_segment(
                segment, obstacles, obstacle_index
            ):
                if obstacle_name == f"@{family_id}":
                    continue
                if obstacle_name == endpoint and index == len(segments) - 1:
                    continue
                if rect.intersects(segment, margin=0.01):
                    obstacle_hit = True
                    score += 10000.0
                    break
            if obstacle_hit:
                continue
            if owned_segment_index is None:
                prior_segments = owned_segments
            else:
                prior_segments = self._owned_segment_candidates(
                    segment,
                    owned_segment_index,
                )
            for other in prior_segments:
                relation, point = _segment_relation(segment, other.segment)
                if relation == "none":
                    continue
                if other.endpoint == endpoint:
                    # Multiple mating families legitimately merge on the way
                    # into their shared animal port.  Treating that merge as a
                    # crossing creates doglegs and even a spurious gap in the
                    # common terminal stub.
                    continue
                if relation == "overlap":
                    overlap = True
                    score += 8000.0
                else:
                    score += 850.0
        return score, overlap, obstacle_hit

    @staticmethod
    def _index_owned_segment(
        index: MutableMapping[Tuple[int, int], List[_OwnedSegment]],
        owned: _OwnedSegment,
        cell_size: float = 2.0,
    ) -> None:
        (x1, y1), (x2, y2) = owned.segment
        low_x = math.floor(min(x1, x2) / cell_size)
        high_x = math.floor(max(x1, x2) / cell_size)
        low_y = math.floor(min(y1, y2) / cell_size)
        high_y = math.floor(max(y1, y2) / cell_size)
        for ix in range(low_x, high_x + 1):
            for iy in range(low_y, high_y + 1):
                index[(ix, iy)].append(owned)

    @staticmethod
    def _owned_segment_candidates(
        segment: Segment,
        index: Mapping[Tuple[int, int], Sequence[_OwnedSegment]],
        cell_size: float = 2.0,
    ) -> Sequence[_OwnedSegment]:
        (x1, y1), (x2, y2) = segment
        low_x = math.floor(min(x1, x2) / cell_size)
        high_x = math.floor(max(x1, x2) / cell_size)
        low_y = math.floor(min(y1, y2) / cell_size)
        high_y = math.floor(max(y1, y2) / cell_size)
        found: Dict[Tuple[str, str, int], _OwnedSegment] = {}
        for ix in range(low_x, high_x + 1):
            for iy in range(low_y, high_y + 1):
                for owned in index.get((ix, iy), ()):
                    found[(owned.family_id, owned.endpoint, owned.index)] = owned
        return tuple(found.values())

    def _find_obstacle_gaps(
        self,
        segments: Sequence[_OwnedSegment],
        obstacles: Mapping[str, Rect],
    ) -> Dict[RouteKey, List[Point]]:
        """Mask the unavoidable part of a straight route that passes behind a foreign node."""
        gaps: Dict[RouteKey, List[Point]] = {}
        spatial_index = (
            self._build_rect_spatial_index(obstacles)
            if len(obstacles) > 256
            else None
        )
        for owned in segments:
            (x1, y1), (x2, y2) = owned.segment
            dx = x2 - x1
            dy = y2 - y1
            segment_length = math.hypot(dx, dy)
            if segment_length <= _EPSILON:
                continue
            for obstacle_name, rect in self._rect_candidates_for_segment(
                owned.segment, obstacles, spatial_index
            ):
                if obstacle_name in (owned.endpoint, f"@{owned.family_id}"):
                    continue
                # Live callers already expand marker and family-knot boxes by
                # the desired physical pixel margin. A fixed data-space pad
                # would make the visible gap change size with zoom/aspect.
                clipped = self._segment_rect_interval(owned.segment, rect)
                if clipped is None:
                    continue
                start, end = clipped
                if end - start <= _EPSILON:
                    continue
                covered_length = (end - start) * segment_length
                sample_count = max(1, int(math.ceil(covered_length / 0.14)))
                key = (owned.family_id, owned.endpoint, owned.index)
                for sample_index in range(sample_count):
                    fraction = start + ((sample_index + 0.5) * (end - start) / sample_count)
                    gaps.setdefault(key, []).append(
                        (x1 + (dx * fraction), y1 + (dy * fraction))
                    )
        for key, points in gaps.items():
            gaps[key] = sorted(
                {(round(x, 7), round(y, 7)) for x, y in points},
                key=lambda point: (point[0], point[1]),
            )
        return gaps

    @staticmethod
    def _segment_rect_interval(
        segment: Segment,
        rect: Rect,
        *,
        padding: float = 0.0,
    ) -> Optional[Tuple[float, float]]:
        (x1, y1), (x2, y2) = segment
        dx = x2 - x1
        dy = y2 - y1
        start = 0.0
        end = 1.0
        for origin, delta, low, high in (
            (x1, dx, rect.left - padding, rect.right + padding),
            (y1, dy, rect.bottom - padding, rect.top + padding),
        ):
            if abs(delta) <= _EPSILON:
                if origin < low or origin > high:
                    return None
                continue
            first = (low - origin) / delta
            second = (high - origin) / delta
            entry, exit_ = sorted((first, second))
            start = max(start, entry)
            end = min(end, exit_)
            if start > end:
                return None
        start = max(0.0, start)
        end = min(1.0, end)
        if start > end:
            return None
        return start, end

    def _find_crossing_gaps(
        self,
        segments: Sequence[_OwnedSegment],
        animal_positions: Mapping[str, Point],
    ) -> Tuple[Dict[RouteKey, List[Point]], List[str]]:
        gaps: Dict[RouteKey, List[Point]] = {}
        problems: List[str] = []

        if len(segments) <= 256:
            candidate_pairs = (
                (index, second_index)
                for index, _first in enumerate(segments)
                for second_index in range(index + 1, len(segments))
            )
        else:
            # Exact broad-phase index for sparse large graphs.  Every segment
            # is inserted into all cells touched by its bounding box, so the
            # narrow-phase relation check below sees exactly the same result
            # as the legacy all-pairs scan.
            cell_size = 2.0
            segment_index: Dict[Tuple[int, int], List[int]] = defaultdict(list)
            for segment_index_number, owned in enumerate(segments):
                (x1, y1), (x2, y2) = owned.segment
                low_x = math.floor(min(x1, x2) / cell_size)
                high_x = math.floor(max(x1, x2) / cell_size)
                low_y = math.floor(min(y1, y2) / cell_size)
                high_y = math.floor(max(y1, y2) / cell_size)
                for ix in range(low_x, high_x + 1):
                    for iy in range(low_y, high_y + 1):
                        segment_index[(ix, iy)].append(segment_index_number)

            def indexed_pairs(index: int):
                first = segments[index]
                (x1, y1), (x2, y2) = first.segment
                low_x = math.floor(min(x1, x2) / cell_size)
                high_x = math.floor(max(x1, x2) / cell_size)
                low_y = math.floor(min(y1, y2) / cell_size)
                high_y = math.floor(max(y1, y2) / cell_size)
                candidates: Set[int] = set()
                for ix in range(low_x, high_x + 1):
                    for iy in range(low_y, high_y + 1):
                        candidates.update(segment_index.get((ix, iy), ()))
                return ((index, second) for second in sorted(candidates) if second > index)

            candidate_pairs = (
                pair
                for index in range(len(segments))
                for pair in indexed_pairs(index)
            )

        for index, second_index in candidate_pairs:
            first = segments[index]
            second = segments[second_index]
            if first.family_id == second.family_id:
                continue
            relation, point = _segment_relation(first.segment, second.segment)
            if relation == "none":
                continue
            if self._is_shared_animal_endpoint(first, second, point, animal_positions):
                continue
            if relation == "overlap":
                problems.append(
                    f"{first.family_id}/{second.family_id}: different families share a segment"
                )
                continue
            if point is None:
                continue
            target = second if _point_is_interior(second.segment, point) else first
            # A horizontal first segment is the visible partner/family
            # shoulder between the junction and an animal. If a foreign
            # vertical continuation crosses that shoulder, keep the family
            # connection continuous and place the small crossing gap in the
            # vertical route instead. This is deliberately a rendering
            # priority only: it does not move either family or perturb the
            # otherwise good local layout.
            first_horizontal_shoulder = (
                first.index == 0
                and abs(first.segment[0][1] - first.segment[1][1]) <= _EPSILON
            )
            second_horizontal_shoulder = (
                second.index == 0
                and abs(second.segment[0][1] - second.segment[1][1]) <= _EPSILON
            )
            if first_horizontal_shoulder != second_horizontal_shoulder:
                foreign = second if first_horizontal_shoulder else first
                foreign_is_vertical = (
                    abs(foreign.segment[0][0] - foreign.segment[1][0]) <= _EPSILON
                )
                if foreign_is_vertical and _point_is_interior(foreign.segment, point):
                    target = foreign
            if not _point_is_interior(target.segment, point):
                problems.append(
                    f"{first.family_id}/{second.family_id}: different families touch without a routable gap"
                )
                continue
            key = (target.family_id, target.endpoint, target.index)
            gaps.setdefault(key, []).append(point)
        return gaps, problems

    @staticmethod
    def _ordered_endpoints(
        family: Mapping[str, object],
        positions: Mapping[str, Point],
    ) -> List[str]:
        parents = [node for node in PedigreeRouter._parents(family) if node in positions]
        children = [node for node in PedigreeRouter._children(family) if node in positions]
        parents.sort(key=lambda node: (positions[node][0], node.casefold()))
        children.sort(key=lambda node: (positions[node][0], node.casefold()))
        return parents + children

    @staticmethod
    def _parents(family: Mapping[str, object]) -> List[str]:
        values = [
            str(family.get("mother", "")).strip(),
            str(family.get("father", "")).strip(),
        ]
        return [value for value in values if value]

    @staticmethod
    def _children(family: Mapping[str, object]) -> List[str]:
        raw = family.get("children", [])
        if not isinstance(raw, (list, tuple, set)):
            return []
        values = [str(value).strip() for value in raw if str(value).strip()]
        if isinstance(raw, set):
            values.sort(key=str.casefold)
        return values

    @staticmethod
    def _family_members(
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
    ) -> Dict[str, Set[str]]:
        return {
            family_id: {
                node
                for node in PedigreeRouter._parents(family) + PedigreeRouter._children(family)
                if node in positions
            }
            for family_id, family in families.items()
        }

    @staticmethod
    def _parentage_cycle_nodes(
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
    ) -> Set[str]:
        """Return nodes in corrupt directed parentage cycles without recursing forever."""
        parents_by_child: Dict[str, Set[str]] = {node: set() for node in positions}
        for family in families.values():
            parents = {
                parent
                for parent in PedigreeRouter._parents(family)
                if parent in positions
            }
            for child in PedigreeRouter._children(family):
                if child in positions:
                    parents_by_child[child].update(parents)

        state: Dict[str, int] = {}
        stack: List[str] = []
        stack_index: Dict[str, int] = {}
        cycle_nodes: Set[str] = set()

        def visit(node: str) -> None:
            state[node] = 1
            stack_index[node] = len(stack)
            stack.append(node)
            for parent in sorted(parents_by_child[node], key=str.casefold):
                parent_state = state.get(parent, 0)
                if parent_state == 0:
                    visit(parent)
                elif parent_state == 1:
                    cycle_nodes.update(stack[stack_index[parent] :])
            stack.pop()
            stack_index.pop(node, None)
            state[node] = 2

        for node in sorted(positions, key=str.casefold):
            if state.get(node, 0) == 0:
                visit(node)
        return cycle_nodes

    @staticmethod
    def _owned_segments(routes: Mapping[str, Mapping[str, Sequence[Point]]]) -> List[_OwnedSegment]:
        output: List[_OwnedSegment] = []
        for family_id in sorted(routes, key=str.casefold):
            for endpoint in sorted(routes[family_id], key=str.casefold):
                for index, segment in enumerate(_path_segments(routes[family_id][endpoint])):
                    output.append(_OwnedSegment(family_id, endpoint, index, segment))
        return output

    @staticmethod
    def _is_shared_animal_endpoint(
        first: _OwnedSegment,
        second: _OwnedSegment,
        point: Optional[Point],
        animal_positions: Mapping[str, Point],
    ) -> bool:
        del point, animal_positions
        return first.endpoint == second.endpoint

    @staticmethod
    def _crossing_is_gapped(
        first: _OwnedSegment,
        second: _OwnedSegment,
        point: Optional[Point],
        gaps: Mapping[RouteKey, Sequence[Point]],
    ) -> bool:
        if point is None:
            return False
        for owned in (first, second):
            key = (owned.family_id, owned.endpoint, owned.index)
            if any(_points_equal(point, gap) for gap in gaps.get(key, [])):
                return True
        return False


def _ranges_overlap(a1: float, a2: float, b1: float, b2: float) -> bool:
    return max(min(a1, a2), min(b1, b2)) <= min(max(a1, a2), max(b1, b2)) + _EPSILON


def _points_equal(first: Point, second: Point) -> bool:
    return abs(first[0] - second[0]) <= _EPSILON and abs(first[1] - second[1]) <= _EPSILON


def _path_segments(path: Sequence[Point]) -> List[Segment]:
    return [
        (first, second)
        for first, second in zip(path, path[1:])
        if not _points_equal(first, second)
    ]


def _simplify_path(path: Sequence[Point]) -> List[Point]:
    result: List[Point] = []
    for point in path:
        normalized = (float(point[0]), float(point[1]))
        if result and _points_equal(result[-1], normalized):
            continue
        result.append(normalized)
        while len(result) >= 3:
            a, b, c = result[-3:]
            if (abs(a[0] - b[0]) <= _EPSILON and abs(b[0] - c[0]) <= _EPSILON) or (
                abs(a[1] - b[1]) <= _EPSILON and abs(b[1] - c[1]) <= _EPSILON
            ):
                result.pop(-2)
            else:
                break
    return result


def _path_length(path: Sequence[Point]) -> float:
    return sum(math.hypot(a[0] - b[0], a[1] - b[1]) for a, b in zip(path, path[1:]))


def _has_parent_entry_shape(segments: Sequence[Segment]) -> bool:
    if len(segments) < 2:
        return False
    first, last = segments[0], segments[-1]
    first_is_horizontal = abs(first[0][1] - first[1][1]) <= _EPSILON
    last_is_vertical = abs(last[0][0] - last[1][0]) <= _EPSILON
    return first_is_horizontal and last_is_vertical


def _segment_relation(first: Segment, second: Segment) -> Tuple[str, Optional[Point]]:
    p, p2 = first
    q, q2 = second
    if (
        max(p[0], p2[0]) < min(q[0], q2[0]) - _EPSILON
        or min(p[0], p2[0]) > max(q[0], q2[0]) + _EPSILON
        or max(p[1], p2[1]) < min(q[1], q2[1]) - _EPSILON
        or min(p[1], p2[1]) > max(q[1], q2[1]) + _EPSILON
    ):
        return "none", None
    r = (p2[0] - p[0], p2[1] - p[1])
    s = (q2[0] - q[0], q2[1] - q[1])
    q_minus_p = (q[0] - p[0], q[1] - p[1])

    def cross(first_vector: Point, second_vector: Point) -> float:
        return (first_vector[0] * second_vector[1]) - (first_vector[1] * second_vector[0])

    r_cross_s = cross(r, s)
    qmp_cross_r = cross(q_minus_p, r)
    r_length_sq = (r[0] * r[0]) + (r[1] * r[1])
    if r_length_sq <= _EPSILON:
        return "none", None

    if abs(r_cross_s) <= _EPSILON and abs(qmp_cross_r) <= _EPSILON:
        t0 = ((q_minus_p[0] * r[0]) + (q_minus_p[1] * r[1])) / r_length_sq
        q2_minus_p = (q2[0] - p[0], q2[1] - p[1])
        t1 = ((q2_minus_p[0] * r[0]) + (q2_minus_p[1] * r[1])) / r_length_sq
        overlap_low = max(0.0, min(t0, t1))
        overlap_high = min(1.0, max(t0, t1))
        if overlap_high < overlap_low - _EPSILON:
            return "none", None
        point = (p[0] + (overlap_low * r[0]), p[1] + (overlap_low * r[1]))
        if overlap_high - overlap_low > _EPSILON:
            return "overlap", None
        return "cross", point

    if abs(r_cross_s) <= _EPSILON:
        return "none", None

    t = cross(q_minus_p, s) / r_cross_s
    u = cross(q_minus_p, r) / r_cross_s
    if -_EPSILON <= t <= 1.0 + _EPSILON and -_EPSILON <= u <= 1.0 + _EPSILON:
        return "cross", (p[0] + (t * r[0]), p[1] + (t * r[1]))
    return "none", None


def _point_is_interior(segment: Segment, point: Point) -> bool:
    return not _points_equal(segment[0], point) and not _points_equal(segment[1], point)


def _split_segment_at_gaps(segment: Segment, gaps: Sequence[Point], radius: float) -> List[Segment]:
    if not gaps:
        return [segment]
    (x1, y1), (x2, y2) = segment
    dx, dy = x2 - x1, y2 - y1
    length_sq = (dx * dx) + (dy * dy)
    length = math.sqrt(length_sq)
    if length <= _EPSILON:
        return []
    gap_half_t = radius / length
    values = sorted(
        max(0.0, min(1.0, (((point[0] - x1) * dx) + ((point[1] - y1) * dy)) / length_sq))
        for point in gaps
    )
    cursor = 0.0
    output: List[Segment] = []

    def point_at(value: float) -> Point:
        return x1 + (value * dx), y1 + (value * dy)

    for value in values:
        before = max(0.0, value - gap_half_t)
        after = min(1.0, value + gap_half_t)
        if before - cursor > _EPSILON:
            output.append((point_at(cursor), point_at(before)))
        cursor = max(cursor, after)
    if 1.0 - cursor > _EPSILON:
        output.append((point_at(cursor), point_at(1.0)))
    return output
