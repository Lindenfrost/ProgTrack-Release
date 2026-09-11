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
# The semantic marker radius is deliberately independent of the pixel-sized
# obstacle rectangles used by the widget.  It is the single tolerance used
# when deciding whether two routes really meet at their shared animal.
_MARKER_RADIUS = 0.30
_MARKER_TOLERANCE = max(1e-6, _MARKER_RADIUS + _EPSILON)
LAYOUT_MODE_FOCUSED = "focused"
LAYOUT_MODE_OVERVIEW = "overview"


class GeometryValidationError(ValueError):
    """Raised when a non-finite value reaches a layout/render boundary."""


def is_finite_point(value: object) -> bool:
    """Return whether *value* is a two-dimensional finite point."""
    try:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return False
        return all(math.isfinite(float(component)) for component in value)
    except (TypeError, ValueError, OverflowError):
        return False


def _assert_finite_points(points: Mapping[str, Point], *, kind: str) -> None:
    """Reject malformed/non-finite point mappings with a stable error."""
    for name, point in points.items():
        if not is_finite_point(point):
            raise GeometryValidationError(
                f"non-finite {kind} geometry for {str(name).strip() or '<unnamed>'}"
            )


def _assert_finite_route_plan(plan: "RoutePlan") -> None:
    """Assert every coordinate produced by the router is finite."""
    _assert_finite_points(plan.animal_positions, kind="animal")
    _assert_finite_points(plan.family_positions, kind="family")
    for family_id, endpoint_routes in plan.routes.items():
        for endpoint, path in endpoint_routes.items():
            for point in path:
                if not is_finite_point(point):
                    raise GeometryValidationError(
                        f"non-finite route geometry for {family_id}:{endpoint}"
                    )
    for gap_name, gap_points in (
        ("crossing gap", plan.crossing_gaps),
        ("line-crossing gap", plan.line_crossing_gaps),
    ):
        for key, points in gap_points.items():
            for point in points:
                if not is_finite_point(point):
                    raise GeometryValidationError(
                        f"non-finite {gap_name} geometry for {key}"
                    )


def _assert_finite_rects(rects: Mapping[str, "Rect"], *, kind: str) -> None:
    """Reject non-finite obstacle rectangles before routing or caching."""
    for name, rect in rects.items():
        try:
            values = (rect.left, rect.right, rect.bottom, rect.top)
            finite = all(math.isfinite(float(value)) for value in values)
        except (AttributeError, TypeError, ValueError, OverflowError):
            finite = False
        if not finite:
            raise GeometryValidationError(
                f"non-finite {kind} geometry for {str(name).strip() or '<unnamed>'}"
            )


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
    # The most recently used obstacle calibration is retained with the plan.
    # A caller that recomputes an unchanged plan without supplying viewport
    # rectangles must not silently switch from pixel-calibrated masks to the
    # router's wider standalone defaults.
    last_animal_gap_obstacles: Dict[str, Rect] = field(default_factory=dict, repr=False)
    last_junction_gap_obstacles: Dict[str, Rect] = field(default_factory=dict, repr=False)
    # Internal diagnostics only: endpoint routes that had to accept an
    # obstacle overlap while preserving canonical topology.
    route_obstacle_hits: List[str] = field(default_factory=list)
    # Generation/topology diagnostics supplied by the pedigree engine.  These
    # make an unresolved level assignment visible and keep it out of the
    # accepted render cache even when route geometry itself is finite.
    layout_diagnostics: List[str] = field(default_factory=list)
    # Semantic display mode selected by the widget.  Keeping it on the plan
    # makes the decision observable and prevents downstream consumers from
    # inferring a second mode from a differently shaped focus set.
    display_mode: str = LAYOUT_MODE_OVERVIEW
    # Family junctions supplied by the user are authoritative anchors. Their
    # coordinates must survive automatic placement/recovery unchanged; route
    # gaps are still recomputed from the resulting geometry.
    manual_family_ids: Set[str] = field(default_factory=set)

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
        for gap_points in (self.crossing_gaps, self.line_crossing_gaps):
            for values in gap_points.values():
                points.extend(values)
        return points


@dataclass(frozen=True)
class _OwnedSegment:
    family_id: str
    endpoint: str
    index: int
    segment: Segment
    # Parent routes for multiple mating families may share a short terminal
    # rail on their way into the same marker.  Keeping this bit of topology
    # metadata lets the crossing classifier distinguish that intentional
    # terminal merge from an arbitrary same-name overlap.
    is_terminal: bool = False


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
        movable_nodes: Optional[Set[str]] = None,
        manual_family_positions: Optional[Mapping[str, Point]] = None,
        focus_nodes: Optional[Set[str]] = None,
        display_mode: Optional[str] = None,
        show_inbreeding: bool = True,
        vertical_layout_mode: str = "partner_normalized",
    ) -> RoutePlan:
        labels = labels or {}
        _assert_finite_points(animal_positions, kind="animal input")
        protected = set(protected_nodes or set())
        # ``movable_nodes`` is retained as a compatibility keyword for older
        # callers, but a node explicitly supplied as a placement candidate is
        # now a committed manual anchor. The #203 precedence rule forbids
        # every automatic recovery pass from moving it to resolve a collision.
        protected.update(
            set(movable_nodes or set()) & set(animal_positions)
        )
        focus = set(focus_nodes or set()) & set(animal_positions)
        chronological = str(vertical_layout_mode or "").strip().casefold() == "chronological"
        # The widget supplies one explicit semantic mode for the complete
        # render transaction. ``None`` retains the standalone router's old
        # focus-node convenience for external callers; widget renders never
        # use that fallback.
        if display_mode is None:
            layout_mode = (
                LAYOUT_MODE_FOCUSED
                if bool(focus) and len(focus) <= 8
                else LAYOUT_MODE_OVERVIEW
            )
        else:
            layout_mode = str(display_mode).strip().casefold()
            if layout_mode not in {LAYOUT_MODE_FOCUSED, LAYOUT_MODE_OVERVIEW}:
                layout_mode = LAYOUT_MODE_OVERVIEW
        # Focused views are intentionally dense, complete contexts.  Labels
        # and detail lines remain visible and may be disentangled by zoom; only
        # marker/marker contact remains a hard placement collision.  Keep this
        # policy on the router transaction so every recovery pass uses the
        # same rule without special-casing a seed or animal name.
        self._allow_dense_label_overlaps = layout_mode == LAYOUT_MODE_FOCUSED
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
        # The final node solver works on animal/label rectangles.  A
        # chronological parent entry can still be geometrically legal at
        # that level while its canonical vertical leg crosses a marker from
        # the same family (the two parents may occupy different date rows).
        # Repair those route-only lane conflicts before publishing junctions;
        # this keeps the canonical two-segment parent shape intact.
        if not self._is_simple_linear_family_graph(adjusted, families):
            self._repair_parent_entry_marker_lanes(
                adjusted,
                families,
                labels,
                protected,
                show_inbreeding,
                focus_nodes=focus,
                preserve_y=chronological,
            )
            # Parent-entry repair is still an animal-placement operation.  It
            # may move a complete partner block to clear a foreign marker lane
            # after the ordinary collision solver has finished.  Recheck the
            # same calibrated node/marker model before junctions are built so
            # the final route pass never starts from a stale collision state.
            post_entry_node_rects = self.node_obstacles(
                adjusted, labels, show_inbreeding
            )
            post_entry_marker_rects = self.marker_obstacles(adjusted)
            if self._collision_pairs(
                adjusted, post_entry_node_rects, post_entry_marker_rects
            ):
                self._resolve_obstacle_collisions(
                    adjusted,
                    families,
                    labels,
                    protected,
                    show_inbreeding,
                    preserve_y=chronological,
                )
        recovery_diagnostic = str(
            getattr(self, "_last_collision_recovery_diagnostic", "") or ""
        )
        _assert_finite_points(adjusted, kind="animal")
        animal_obstacles = self.node_obstacles(adjusted, labels, show_inbreeding)
        family_positions = self._place_junctions(
            adjusted,
            families,
            animal_obstacles,
            chronological=chronological,
            focused=layout_mode == LAYOUT_MODE_FOCUSED,
        )
        # Family handles are committed as part of the same position-cache
        # record as animal anchors. In chronological mode only their X
        # coordinate is user-controlled; the Y coordinate remains the fixed
        # date/layout result, matching the animal rule in the widget.
        for family_id, stored in (manual_family_positions or {}).items():
            automatic = family_positions.get(family_id)
            if automatic is None:
                continue
            family_positions[family_id] = (
                float(stored[0]),
                float(automatic[1]) if chronological else float(stored[1]),
            )
        _assert_finite_points(family_positions, kind="family")
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
        unresolved: List[str] = [recovery_diagnostic] if recovery_diagnostic else []
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
                    allowed_obstacle_names=family_members.get(family_id, set()),
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
                    new_owned.append(
                        _OwnedSegment(
                            family_id,
                            endpoint,
                            index,
                            segment,
                            is_terminal=index == len(_path_segments(path)) - 1,
                        )
                    )

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
            display_mode=layout_mode,
            manual_family_ids={
                str(family_id)
                for family_id in (manual_family_positions or {})
                if family_id in family_positions
            },
        )
        self.recompute_line_gaps(
            plan,
            labels=labels,
            show_inbreeding=show_inbreeding,
        )
        _assert_finite_route_plan(plan)
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
        _assert_finite_points(plan.animal_positions, kind="animal")
        _assert_finite_points(plan.family_positions, kind="family")
        owned_segments = self._owned_segments(plan.routes)
        cached_obstacles_are_current = (
            plan.gap_geometry_revision == plan.geometry_revision
        )
        if animal_gap_obstacles is None and cached_obstacles_are_current:
            resolved_animal_obstacles = plan.last_animal_gap_obstacles or None
        else:
            resolved_animal_obstacles = animal_gap_obstacles
        animal_obstacles = dict(
            resolved_animal_obstacles
            if resolved_animal_obstacles is not None
            else self.marker_obstacles(plan.animal_positions)
        )
        _assert_finite_rects(animal_obstacles, kind="animal obstacle")
        if junction_gap_obstacles is None and cached_obstacles_are_current:
            resolved_junction_obstacles = plan.last_junction_gap_obstacles or None
        else:
            resolved_junction_obstacles = junction_gap_obstacles
        junction_obstacles = dict(
            resolved_junction_obstacles
            if resolved_junction_obstacles is not None
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
        _assert_finite_rects(junction_obstacles, kind="junction obstacle")
        # Keep the exact calibration used for this pass so repeated calls on
        # an unchanged plan remain idempotent.  A geometry revision mismatch
        # intentionally falls back to freshly derived defaults above.
        plan.last_animal_gap_obstacles = dict(animal_obstacles)
        plan.last_junction_gap_obstacles = dict(junction_obstacles)
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
        _assert_finite_route_plan(plan)

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
        _assert_finite_points(positions, kind="marker input")
        width = max(0.02, float(half_width))
        height = max(0.02, float(half_height))
        if not math.isfinite(width) or not math.isfinite(height):
            raise GeometryValidationError("non-finite marker dimensions")
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
        _assert_finite_points(positions, kind="node input")
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
        expected_families = {
            family_id
            for family_id, family in families.items()
            if any(parent in plan.animal_positions for parent in self._parents(family))
            and any(child in plan.animal_positions for child in self._children(family))
        }
        actual_families = set(plan.routes)
        for family_id in sorted(expected_families - actual_families, key=str.casefold):
            problems.append(f"{family_id}: expected routable family is missing")
        for family_id in sorted(actual_families - set(families), key=str.casefold):
            problems.append(f"{family_id}: unexpected routed family")
        for family_id in sorted(expected_families - set(plan.family_positions), key=str.casefold):
            problems.append(f"{family_id}: expected family junction is missing")
        for family_id in sorted(set(plan.family_positions) - set(families), key=str.casefold):
            problems.append(f"{family_id}: unexpected family junction")
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
            if plan.display_mode == LAYOUT_MODE_FOCUSED:
                # A focused frame is a complete, zoomable context.  Text may
                # be dense at its initial scale; only coincident interactive
                # markers are a hard topology/interaction failure.
                obstacle_items = sorted(
                    marker_obstacles.items(), key=lambda item: item[0].casefold()
                )
            else:
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

        manual_family_ids = set(getattr(plan, "manual_family_ids", set()))
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
                    child_axis_eligible = (
                        parent_xs[0] + 0.08 < child_x < parent_xs[1] - 0.08
                    )
                    child_inside_corridor = (
                        parent_xs[0] + self.node_gap
                        <= child_x
                        <= parent_xs[1] - self.node_gap
                    )
                    child_near_parent_edge = min(
                        abs(child_x - parent_xs[0]),
                        abs(child_x - parent_xs[1]),
                    ) <= self.route_clearance + _EPSILON and (
                        child_x < parent_xs[0] - _EPSILON
                        or child_x > parent_xs[1] + _EPSILON
                    )
                    if child_inside_corridor or child_near_parent_edge:
                        allowed_shift = min(
                            max(allowed_shift, abs(child_x - midpoint) + self.route_clearance),
                            max(0.0, (span / 2.0) - 0.08),
                        )
                    elif child_axis_eligible:
                        # A single child is an owned endpoint. Its incoming
                        # line may overlap its own label, so a junction on
                        # that same X axis is valid even when the label-sized
                        # node gap is not available on both shoulders.
                        allowed_shift = max(
                            allowed_shift,
                            abs(child_x - midpoint),
                        )
                if not parent_xs[0] < junction[0] < parent_xs[1]:
                    problems.append(
                        f"{family_id}: junction is not between both parents"
                    )
                elif abs(junction[0] - midpoint) > allowed_shift + _EPSILON:
                    problems.append(
                        f"{family_id}: junction is excessively displaced from the parent midpoint"
                    )
            # A line anchor may sit below a foreign text label: labels are
            # rendered above genealogy lines and the overlap is therefore a
            # readable presentation choice, not a broken pedigree. A family
            # junction must still stay out of a foreign animal marker, which
            # is the actual interactive node geometry.
            if family_id not in manual_family_ids:
                for node, rect in marker_obstacles.items():
                    if node not in plan.family_members.get(family_id, set()) and rect.contains(junction):
                        problems.append(f"{family_id}: family junction intersects foreign marker {node}")

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
                        if (
                            family_id not in manual_family_ids
                            and rect.intersects(segment, margin=0.01)
                        ):
                            route_gaps = plan.crossing_gaps.get((family_id, endpoint, index), [])
                            if any(rect.contains(point, margin=0.08) for point in route_gaps):
                                continue
                            problems.append(
                                f"{family_id}: route to {endpoint} intersects foreign animal marker {node}"
                            )
                            break

        owned = self._owned_segments(plan.routes)
        route_parts: Dict[Tuple[str, str], List[_OwnedSegment]] = defaultdict(list)
        for segment in owned:
            route_parts[(segment.family_id, segment.endpoint)].append(segment)
        route_paths: Dict[Tuple[str, str], List[Point]] = {}
        for route_key, route_segments in route_parts.items():
            ordered = sorted(route_segments, key=lambda segment: segment.index)
            if ordered:
                route_paths[route_key] = [
                    ordered[0].segment[0],
                    *(segment.segment[1] for segment in ordered),
                ]
        for index, first in enumerate(owned):
            for second in owned[index + 1 :]:
                if first.family_id == second.family_id:
                    continue
                # A user-anchored family may reroot its connections through
                # an otherwise occupied corridor. The anchor and topology
                # remain authoritative; only its visual gaps are recomputed.
                if (
                    first.family_id in manual_family_ids
                    or second.family_id in manual_family_ids
                ):
                    continue
                relation, point = _segment_relation(first.segment, second.segment)
                if relation == "none":
                    continue
                missing_endpoint = self._missing_shared_animal_endpoint(
                    first,
                    second,
                    plan.animal_positions,
                )
                if missing_endpoint is not None:
                    problems.append(
                        f"{first.family_id}/{second.family_id}: shared animal endpoint "
                        f"{missing_endpoint} has missing geometry"
                    )
                    continue
                if self._is_shared_animal_endpoint(
                    first,
                    second,
                    point,
                    plan.animal_positions,
                    relation=relation,
                    marker_tolerance=_MARKER_TOLERANCE,
                ):
                    continue
                first_path = route_paths.get((first.family_id, first.endpoint), [])
                if first_path and self._is_shared_parent_port_join(
                    first,
                    second,
                    relation,
                    point,
                    first_path,
                    owned,
                ):
                    continue
                if self._is_terminal_shared_endpoint_merge(
                    first,
                    second,
                    relation,
                    point,
                    plan.animal_positions,
                    marker_tolerance=_MARKER_TOLERANCE,
                ):
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
        self._last_collision_recovery_diagnostic = ""
        if not adjusted:
            return adjusted

        if families:
            locked_positions = {
                node: adjusted[node]
                for node in protected
                if node in adjusted
            }
            # A very large one-parent/one-child chain is a legitimate sparse
            # pedigree, not a dense layout problem.  Its canonical geometry
            # is already collision-free when the generation rows are clear;
            # running every compacting and horizontal-constraint pass here
            # both costs quadratic-ish work and can move a long chain into
            # route-only conflicts.  Keep the ordinary recovery path as a
            # fallback whenever this conservative fast-path precondition is
            # not met or the cheap obstacle check finds a real collision.
            if (
                len(adjusted) > 256
                and not protected
                and self._is_simple_linear_family_graph(adjusted, families)
            ):
                if not preserve_y:
                    self._assign_generation_rows(adjusted, families)
                fast_obstacles = self.node_obstacles(
                    adjusted, labels, show_inbreeding
                )
                if not self._collision_pairs(
                    adjusted,
                    fast_obstacles,
                    self.marker_obstacles(adjusted),
                ):
                    return adjusted
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
                # Row clustering is only a candidate-discovery shortcut; it
                # is not proof that nearby date/detail rectangles are clear.
                # Run the sparse horizontal constraint pass as the final
                # clearance boundary so the 257-node path has the same hard
                # collision invariant as smaller graphs while retaining the
                # spatial index used by the solver.
                self._solve_horizontal_constraints(
                    adjusted,
                    families,
                    labels,
                    show_inbreeding,
                    chronological=preserve_y,
                )
                for row in self._cluster_rows(adjusted):
                    self._deoverlap_row(
                        adjusted, row, labels, protected, show_inbreeding
                    )
                self._resolve_obstacle_collisions(
                    adjusted, families, labels, protected, show_inbreeding,
                    preserve_y=preserve_y,
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
                        focus_nodes=set(focus_nodes or set()),
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
                                focus_nodes=set(focus_nodes or set()),
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
                    focus_nodes=set(focus_nodes or set()),
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
                    self._compact_overview_continuing_children(
                        adjusted,
                        families,
                        labels,
                        show_inbreeding,
                        chronological=preserve_y,
                    )
                    self._compact_overview_terminal_child_fans(
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
                        self._stagger_focused_terminal_siblings(
                            adjusted,
                            families,
                            labels,
                            set(focus_nodes),
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
                    before_parentless_fan = dict(adjusted)
                    parentless_fan_changed = self._compact_focused_parentless_multi_mate_fans(
                        adjusted,
                        families,
                        labels,
                        set(focus_nodes),
                        show_inbreeding,
                        chronological=preserve_y,
                    )
                    if parentless_fan_changed:
                        parentless_moved = {
                            node
                            for node, point in adjusted.items()
                            if node in before_parentless_fan
                            and (
                                abs(point[0] - before_parentless_fan[node][0]) > _EPSILON
                                or abs(point[1] - before_parentless_fan[node][1]) > _EPSILON
                            )
                        }
                        self._resolve_parentless_fan_collisions(
                            adjusted,
                            parentless_moved,
                            families,
                            labels,
                            protected,
                            set(focus_nodes),
                            show_inbreeding,
                            chronological=preserve_y,
                        )
                        # The preferred shoulder may initially pass through a
                        # neighbouring ghost branch.  One ordinary row
                        # de-overlap pass performs the smallest local shift
                        # before canonical junctions/routes are built.
                        for row in self._cluster_rows(adjusted):
                            self._deoverlap_row(
                                adjusted, row, labels, protected, show_inbreeding
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
                    partner_blocks=partner_blocks,
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
            self._resolve_obstacle_collisions(
                adjusted, families, labels, protected, show_inbreeding,
                preserve_y=preserve_y,
                partner_blocks=partner_blocks,
            )
            # Recovery may translate a descendant cohort after the first
            # interval pass. Recompute one-child corridors against those
            # final parent coordinates before junctions are published.
            if not protected:
                self._align_single_child_axes_final(
                    adjusted,
                    families,
                    labels,
                    protected,
                    show_inbreeding,
                    partner_blocks=partner_blocks,
                )
                # The axis pass may be blocked by a neighbouring automatic
                # node that can be moved safely by the general recovery
                # solver. Re-run the axis pass after that local recovery so
                # the published junction is both collision-free and aligned.
                self._resolve_obstacle_collisions(
                    adjusted,
                    families,
                    labels,
                    protected,
                    show_inbreeding,
                    preserve_y=preserve_y,
                    partner_blocks=partner_blocks,
                )
                # The second axis projection is a soft refinement.  It may
                # move a partner block after the hard recovery pass and can
                # therefore expose a new collision outside the one-child
                # family it is aligning.  Keep the clean pre-projection
                # geometry if that happens; the preceding pass already
                # supplied the valid routed layout.
                before_final_axis = dict(adjusted)
                self._align_single_child_axes_final(
                    adjusted,
                    families,
                    labels,
                    protected,
                    show_inbreeding,
                    partner_blocks=partner_blocks,
                )
                final_node_rects = self.node_obstacles(
                    adjusted, labels, show_inbreeding
                )
                final_marker_rects = self.marker_obstacles(adjusted)
                if self._collision_pairs(
                    adjusted, final_node_rects, final_marker_rects
                ):
                    adjusted.update(before_final_axis)
                focused_shoulder_nodes: Set[str] = set()
                focused_branch_weights: Dict[str, float] = {}
                if prefer_descendant_order and len(focus_nodes) >= 3:
                    focused_shoulder_nodes = (
                        self._enforce_focused_sibling_shoulders(
                            adjusted,
                            families,
                            labels,
                            set(focus_nodes),
                            show_inbreeding=show_inbreeding,
                        )
                    )
                    # The broad focus pass also compacts ancestor fans. Once
                    # terminal peers have been put on their intended shoulder
                    # and donor branches have been recovered, make one final
                    # direct-selection pass for the mixed sibship containing
                    # the selected node itself. This keeps the selected
                    # continuation near its actual origin without reopening
                    # every upstream branch that was already settled.
                    focused_branch_weights = (
                        self._compact_focused_terminal_sibling_fans(
                            adjusted,
                            families,
                            labels,
                            set(focus_nodes),
                            partner_blocks,
                            preserve_y=preserve_y,
                            show_inbreeding=show_inbreeding,
                            direct_focus_only=True,
                        )
                    )
                if focused_shoulder_nodes:
                    # The shoulder pass intentionally puts the terminal
                    # siblings next to the focused parent axis.  Protect
                    # those semantic anchors (and the explicitly selected
                    # nodes) while the ordinary recovery solver moves an
                    # unrelated donor branch out of the way.  Without this
                    # second phase the solver's cheapest move is to exile
                    # the terminal leaves, which makes the origin junction
                    # appear detached again.
                    focused_recovery_protected = (
                        set(protected)
                        | set(focused_shoulder_nodes)
                    )
                    self._resolve_obstacle_collisions(
                        adjusted,
                        families,
                        labels,
                        focused_recovery_protected,
                        show_inbreeding,
                        preserve_y=preserve_y,
                        partner_blocks=partner_blocks,
                    )
                if focused_branch_weights:
                    focused_recovery_protected = (
                        set(protected)
                        | set(focused_shoulder_nodes)
                        | set(focused_branch_weights)
                    )
                    self._resolve_obstacle_collisions(
                        adjusted,
                        families,
                        labels,
                        focused_recovery_protected,
                        show_inbreeding,
                        preserve_y=preserve_y,
                        partner_blocks=partner_blocks,
                    )
                # The final single-child axis projection above can
                # re-introduce an X collision after the bounded recovery
                # pass. Run one last horizontal constraint projection against
                # the post-projection coordinates so the published route plan
                # cannot carry a stale overlap into the renderer veto. In
                # chronological mode Y remains immutable; partner-normalized
                # mode uses its existing Y rows and simply receives the same
                # hard horizontal clearance boundary.
                self._solve_horizontal_constraints(
                    adjusted,
                    families,
                    labels,
                    show_inbreeding,
                    chronological=preserve_y,
                    node_weights={
                        node: self.focused_branch_weight * 4.0
                        for node in focused_shoulder_nodes
                    }
                    | {
                        node: max(
                            self.focused_branch_weight * 4.0,
                            float(weight),
                        )
                        for node, weight in focused_branch_weights.items()
                    },
                    apply_soft_alignment=False,
                )
                final_node_rects = self.node_obstacles(
                    adjusted, labels, show_inbreeding
                )
                final_marker_rects = self.marker_obstacles(adjusted)
                final_collision_pairs = self._collision_pairs(
                    adjusted, final_node_rects, final_marker_rects
                )
                if final_collision_pairs:
                    # The final weighted horizontal projection can expose a
                    # real label/marker collision after the earlier focused
                    # recovery has already run. Repair that last state with
                    # the same semantic anchors, so an unrelated branch is
                    # moved as a legal block instead of undoing the compact
                    # selected origin.
                    final_recovery_protected = (
                        set(protected)
                        | set(focused_shoulder_nodes)
                        | set(focused_branch_weights)
                    )
                    self._resolve_obstacle_collisions(
                        adjusted,
                        families,
                        labels,
                        final_recovery_protected,
                        show_inbreeding,
                        preserve_y=preserve_y,
                        partner_blocks=partner_blocks,
                    )
                    final_node_rects = self.node_obstacles(
                        adjusted, labels, show_inbreeding
                    )
                    final_marker_rects = self.marker_obstacles(adjusted)
                    final_collision_pairs = self._collision_pairs(
                        adjusted, final_node_rects, final_marker_rects
                    )
                if not final_collision_pairs:
                    # A prior bounded recovery may have recorded a transient
                    # diagnostic before this final projection cleared the
                    # actual geometry. Do not publish that stale warning as
                    # an unresolved route-plan failure.
                    self._last_collision_recovery_diagnostic = ""
                if focused_branch_weights:
                    # A hard horizontal projection may legitimately widen a
                    # focused partner block while clearing a foreign label.
                    # Reapply the direct-selection compaction once, after all
                    # such projections, so the published plan retains the
                    # selected branch's local origin relationship. The
                    # subsequent recovery is deliberately the last movement
                    # operation and is allowed to move only non-anchor
                    # semantic branches.
                    final_focus_weights = (
                        self._compact_focused_terminal_sibling_fans(
                            adjusted,
                            families,
                            labels,
                            set(focus_nodes),
                            partner_blocks,
                            preserve_y=preserve_y,
                            show_inbreeding=show_inbreeding,
                            direct_focus_only=True,
                        )
                    )
                    final_recovery_protected = (
                        set(protected)
                        | set(focused_shoulder_nodes)
                        | set(focused_branch_weights)
                        | set(final_focus_weights)
                    )
                    self._resolve_obstacle_collisions(
                        adjusted,
                        families,
                        labels,
                        final_recovery_protected,
                        show_inbreeding,
                        preserve_y=preserve_y,
                        partner_blocks=partner_blocks,
                    )
                    final_node_rects = self.node_obstacles(
                        adjusted, labels, show_inbreeding
                    )
                    final_marker_rects = self.marker_obstacles(adjusted)
                    if not self._collision_pairs(
                        adjusted, final_node_rects, final_marker_rects
                    ):
                        self._last_collision_recovery_diagnostic = ""
                if overview_mode:
                    # The final one-child-axis projection can move a shared
                    # parent block while aligning a descendant family.  That
                    # movement is valid for the descendant, but it can undo
                    # the origin-aware terminal fan compacted above (most
                    # visibly when one child is also a parent elsewhere in
                    # the Overview).  Reapply the fan rule as the final
                    # overview-only position pass; it is collision-safe and
                    # therefore cannot publish a new node/marker overlap.
                    self._compact_overview_terminal_child_fans(
                        adjusted,
                        families,
                        labels,
                        show_inbreeding,
                    )
                self._compact_excess_partner_gaps(
                    adjusted,
                    families,
                    labels,
                    protected,
                    focus_nodes=set(focus_nodes or set()),
                    show_inbreeding=show_inbreeding,
                )
                self._compact_chronological_parent_gaps(
                    adjusted,
                    families,
                    labels,
                    protected,
                    show_inbreeding,
                    focus_nodes=set(focus_nodes or set()),
                    focused=prefer_descendant_order,
                    chronological=preserve_y,
                )
                # A late focused compaction can expose a label/marker overlap
                # after the weighted recovery has deliberately protected its
                # semantic shoulder.  Run one final ordinary recovery with
                # only explicit/manual and selected anchors frozen; unrelated
                # branches must remain movable so the published frame cannot
                # be rejected for a collision introduced by that refinement.
                final_node_rects = self.node_obstacles(
                    adjusted, labels, show_inbreeding
                )
                final_marker_rects = self.marker_obstacles(adjusted)
                if self._collision_pairs(
                    adjusted, final_node_rects, final_marker_rects
                ):
                    self._resolve_obstacle_collisions(
                        adjusted,
                        families,
                        labels,
                        set(protected) | set(focus_nodes or set()),
                        show_inbreeding,
                        preserve_y=preserve_y,
                        partner_blocks=partner_blocks,
                    )
                # The last generic recovery may move a terminal leaf while
                # clearing an unrelated obstacle. Reapply the bounded branch
                # role lanes after that pass so a continuing sibling and its
                # terminal peers cannot be re-coupled by generic compaction.
                # This is a final structural presentation seam; it does not
                # alter family topology or manual/protected coordinates.
                if prefer_descendant_order and focus_nodes:
                    # Generic recovery above may separate a focused
                    # multi-mate hub again while clearing an unrelated
                    # obstacle. Reapply the same-side fan rule at the final
                    # presentation seam, including mates that have a visible
                    # origin family, then protect only the derived fan during
                    # its bounded collision cleanup.
                    before_final_mate_fan = dict(adjusted)
                    self._compact_focused_parentless_multi_mate_fans(
                        adjusted,
                        families,
                        labels,
                        set(focus_nodes),
                        show_inbreeding,
                        chronological=preserve_y,
                    )
                    final_mate_fan_nodes = {
                        node
                        for node, point in adjusted.items()
                        if node in before_final_mate_fan
                        and (
                            abs(point[0] - before_final_mate_fan[node][0]) > _EPSILON
                            or abs(point[1] - before_final_mate_fan[node][1]) > _EPSILON
                        )
                    }
                    if final_mate_fan_nodes:
                        final_fan_node_rects = self.node_obstacles(
                            adjusted,
                            labels,
                            show_inbreeding,
                        )
                        final_fan_marker_rects = self.marker_obstacles(adjusted)
                        if self._collision_pairs(
                            adjusted,
                            final_fan_node_rects,
                            final_fan_marker_rects,
                        ):
                            self._resolve_obstacle_collisions(
                                adjusted,
                                families,
                                labels,
                                set(protected)
                                | set(focus_nodes)
                                | set(final_mate_fan_nodes),
                                show_inbreeding,
                                preserve_y=preserve_y,
                                partner_blocks=partner_blocks,
                            )
                    self._stagger_focused_terminal_siblings(
                        adjusted,
                        families,
                        labels,
                        set(focus_nodes),
                        preserve_y=preserve_y,
                        show_inbreeding=show_inbreeding,
                    )
                    # The terminal-lane pass intentionally changes only the
                    # Partner-normalized Y lanes, but that can make a label or
                    # marker meet an unrelated branch at its retained X.  It
                    # is therefore followed by the same bounded semantic
                    # recovery used everywhere else; publishing a frame with
                    # a late, unvalidated collision would violate #195.
                    final_node_rects = self.node_obstacles(
                        adjusted, labels, show_inbreeding
                    )
                    final_marker_rects = self.marker_obstacles(adjusted)
                    if self._collision_pairs(
                        adjusted, final_node_rects, final_marker_rects
                    ):
                        self._resolve_obstacle_collisions(
                            adjusted,
                            families,
                            labels,
                            set(protected) | set(focus_nodes),
                            show_inbreeding,
                            preserve_y=preserve_y,
                            partner_blocks=partner_blocks,
                        )
                    # The final generic recovery can move an unselected
                    # terminal peer back across the focused branch shoulder.
                    # Re-establish the structural branch split as the last
                    # focused operation, then protect only those derived
                    # peers during one bounded collision recovery.  This keeps
                    # chronological Y coordinates authoritative and never
                    # hides, drops, or name-special-cases any node or label.
                    final_shoulder_nodes = (
                        self._enforce_focused_sibling_shoulders(
                            adjusted,
                            families,
                            labels,
                            set(focus_nodes),
                            show_inbreeding=show_inbreeding,
                        )
                    )
                    if final_shoulder_nodes:
                        self._resolve_obstacle_collisions(
                            adjusted,
                            families,
                            labels,
                            set(protected)
                            | set(focus_nodes)
                            | set(final_shoulder_nodes),
                            show_inbreeding,
                            preserve_y=preserve_y,
                            partner_blocks=partner_blocks,
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
        self._resolve_obstacle_collisions(
            adjusted, families, labels, protected, show_inbreeding,
            preserve_y=preserve_y,
        )
        return adjusted

    def _family_components(
        self,
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
    ) -> List[Set[str]]:
        """Return deterministic visible family components for rigid moves."""
        adjacency: Dict[str, Set[str]] = {node: set() for node in positions}
        for family_id, family in families.items():
            members = [
                node for node in self._parents(family) + self._children(family)
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
            components.append(component)
        return sorted(components, key=lambda group: min(node.casefold() for node in group))

    def _branch_movement_groups(
        self,
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
        partner_blocks: Mapping[str, Set[str]],
        requested_nodes: Optional[Set[str]] = None,
    ) -> Dict[str, List[Set[str]]]:
        """Build bounded child branches without absorbing origin parents/hubs."""
        children_by_parent: Dict[str, Set[str]] = defaultdict(set)
        families_by_parent: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = defaultdict(list)
        parents_by_child: Dict[str, Set[str]] = defaultdict(set)
        origin_families_by_child: Dict[str, Set[str]] = defaultdict(set)
        for family_id, family in families.items():
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            family_key = str(family_id)
            for child in children:
                parents_by_child[child].update(parents)
                origin_families_by_child[child].add(family_key)
            for parent in parents:
                children_by_parent[parent].update(children)
                families_by_parent[parent].append((family_key, tuple(sorted(children, key=str.casefold))))
        root_groups: Dict[Tuple[str, str], Set[str]] = {}
        for parent in families_by_parent:
            families_by_parent[parent].sort(key=lambda item: item[0].casefold())
        roots = set(positions) if requested_nodes is None else set(requested_nodes) & set(positions)
        for root in sorted(roots, key=str.casefold):
            origin_families = sorted(origin_families_by_child.get(root, {""}), key=str.casefold)
            for origin_family in origin_families:
                group = set(partner_blocks.get(root, {root}))
                pending = list(group)
                seen = set(group)
                while pending:
                    parent = pending.pop()
                    # Downward traversal is deliberately rooted by the
                    # (root, origin-family) pair.  Multiple outgoing families
                    # mean multiple mates/children, not a shared hub; only a
                    # later reconvergence makes a descendant ineligible.
                    for _family_key, children in families_by_parent.get(parent, []):
                        for child in children:
                            child_block = set(partner_blocks.get(child, {child}))
                            if child_block & seen:
                                continue
                            seen.update(child_block)
                            group.update(child_block)
                            pending.extend(child_block)
                root_groups[(root, origin_family)] = group
        # A reconvergence is meaningful only among roots that are siblings in
        # the same origin family. Nested ancestor/descendant requests may
        # legitimately share descendants and must not invalidate one another.
        owners: Dict[Tuple[str, str], Set[str]] = defaultdict(set)
        for (root, origin_family), group in root_groups.items():
            for member in group:
                if member != root:
                    owners[(origin_family, member)].add(root)
        groups: Dict[str, List[Set[str]]] = {}
        for node in sorted(roots, key=str.casefold):
            candidates: List[Set[str]] = []
            base = set(partner_blocks.get(node, {node}))
            candidates.append(base)
            # Only the root/its partner block owns a branch alternative.  A
            # broad ancestor group must not become a legal candidate merely
            # because the conflict endpoint is one of its descendants.
            for (root, _origin_family), group in sorted(
                root_groups.items(), key=lambda item: (item[0][0].casefold(), item[0][1].casefold())
            ):
                root_block = set(partner_blocks.get(root, {root}))
                terminal_descendant = (
                    node not in root_block
                    and bool(parents_by_child.get(node))
                    and not bool(children_by_parent.get(node))
                    and node in group
                )
                if node not in root_block and not terminal_descendant:
                    continue
                shared_boundary = {
                    member
                    for member in group
                    if member not in root_block
                    and owners.get((_origin_family, member), set()) - {root}
                }
                candidate_group = set(group) - shared_boundary
                if candidate_group not in candidates:
                    candidates.append(candidate_group)
            groups[node] = sorted(
                candidates,
                key=lambda group: (
                    len(group),
                    tuple(sorted((member.casefold(), member) for member in group)),
                ),
            )
        return groups

    @staticmethod
    def _collision_pairs(
        positions: Mapping[str, Point],
        node_obstacles: Mapping[str, Rect],
        marker_obstacles: Mapping[str, Rect],
        *,
        include_labels: bool = True,
    ) -> List[Tuple[str, str]]:
        """Return stable node pairs for marker/label intersections.

        Focused displays pass ``include_labels=False`` so a dense but complete
        frame is still publishable: zoom is the user-facing way to separate
        crowded text.  Marker/marker intersections remain hard because they
        would make the interactive node hit targets ambiguous.
        """
        entities: List[Tuple[str, str, Rect]] = []
        for node in sorted(positions, key=lambda value: (value.casefold(), value)):
            if include_labels:
                entities.append((node, "label", node_obstacles[node]))
            entities.append((node, "marker", marker_obstacles[node]))
        # Broad phase: long labels may span multiple cells, so every entity
        # is indexed into each covered cell before exact rectangle checks.
        cell_size = 2.5
        spatial: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for index, (_node, _kind, rect) in enumerate(entities):
            for ix in range(math.floor(rect.left / cell_size), math.floor(rect.right / cell_size) + 1):
                for iy in range(math.floor(rect.bottom / cell_size), math.floor(rect.top / cell_size) + 1):
                    spatial[(ix, iy)].append(index)
        candidate_indexes: Set[Tuple[int, int]] = set()
        for indexes in spatial.values():
            for first_index, second_index in combinations(sorted(set(indexes)), 2):
                candidate_indexes.add((first_index, second_index))
        pairs: Set[Tuple[str, str]] = set()
        for first_index, second_index in sorted(candidate_indexes):
            first, second = entities[first_index], entities[second_index]
            if first[0] == second[0]:
                continue
            if not (
                _ranges_overlap(first[2].left, first[2].right, second[2].left, second[2].right)
                and _ranges_overlap(first[2].bottom, first[2].top, second[2].bottom, second[2].top)
            ):
                continue
            pair = tuple(sorted((first[0], second[0]), key=lambda value: (value.casefold(), value)))
            pairs.add(pair)
        return sorted(pairs, key=lambda pair: tuple((value.casefold(), value) for value in pair))

    def _directional_collision_shift(
        self,
        first: str,
        second: str,
        node_rects: Mapping[str, Rect],
        marker_rects: Mapping[str, Rect],
        direction: int,
    ) -> float:
        """Return the boundary-derived shift for moving one endpoint."""
        required = 0.0
        first_rects = (node_rects[first], marker_rects[first])
        second_rects = (node_rects[second], marker_rects[second])
        for left, right in ((first_rects[0], second_rects[0]),
                            (first_rects[0], second_rects[1]),
                            (first_rects[1], second_rects[0]),
                            (first_rects[1], second_rects[1])):
            if not _ranges_overlap(left.bottom, left.top, right.bottom, right.top):
                continue
            if direction > 0:
                required = max(required, right.right + self.node_gap - left.left)
            else:
                required = max(required, left.right + self.node_gap - right.left)
        return max(0.0, required)

    def _placement_candidate_is_legal(
        self,
        candidate: Mapping[str, Point],
        baseline: Mapping[str, Point],
        protected: Set[str],
        partner_orders: Mapping[Tuple[str, str], int],
        sibling_orders: Mapping[Tuple[str, str], int],
        partner_groups: Mapping[str, Set[str]],
        *,
        preserve_y: bool,
    ) -> bool:
        if not all(is_finite_point(point) for point in candidate.values()):
            return False
        for node in protected:
            if node in baseline and candidate.get(node) != baseline[node]:
                return False
        if preserve_y and any(
            abs(candidate[node][1] - baseline[node][1]) > _EPSILON
            for node in baseline
        ):
            return False
        for (left, right), _order in partner_orders.items():
            if left in candidate and right in candidate:
                if candidate[left][0] >= candidate[right][0]:
                    return False
        for (left, right), _order in sibling_orders.items():
            if left in candidate and right in candidate:
                if candidate[left][0] >= candidate[right][0]:
                    return False
        # A legal trial cannot insert an unrelated node into an established
        # same-row partner block.  Uniform branch moves preserve internal
        # vectors; the only permitted non-uniform case is the explicit
        # internal-row widening candidate generated below.
        for group in {frozenset(value) for value in partner_groups.values() if len(value) > 1}:
            members = [node for node in group if node in candidate]
            if len(members) < 2:
                continue
            ys = [candidate[node][1] for node in members]
            if max(ys) - min(ys) > 0.42:
                continue
            left = min(candidate[node][0] for node in members)
            right = max(candidate[node][0] for node in members)
            for node, (x, y) in candidate.items():
                if node in group or abs(y - ys[0]) > 0.42:
                    continue
                if left < x < right:
                    return False
        return True

    def _resolve_obstacle_collisions(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        show_inbreeding: bool,
        *,
        preserve_y: bool = False,
        partner_blocks: Optional[Mapping[str, Set[str]]] = None,
        movable_nodes: Optional[Set[str]] = None,
        allow_label_overlaps: Optional[bool] = None,
    ) -> bool:
        """Perform bounded pre-junction collision recovery on legal blocks."""
        self._last_collision_recovery_diagnostic = ""
        if allow_label_overlaps is None:
            allow_label_overlaps = bool(
                getattr(self, "_allow_dense_label_overlaps", False)
            )
        if len(positions) < 2:
            return True
        protected = set(protected)
        if movable_nodes is not None:
            # Kept for source compatibility with the pre-#203 call boundary.
            # Explicit candidate nodes are manual anchors, not an escape hatch
            # for collision recovery; automatic nodes remain the only legal
            # recovery subjects.
            protected |= set(movable_nodes) & set(positions)
        baseline = dict(positions)
        # Discover whether recovery is needed before constructing any
        # component, branch, partner-order, or sibling-cohort state.  Sparse
        # large graphs are the dominant stress case and normally arrive
        # collision-free; making them pay that semantic setup cost defeats
        # the bounded-recovery contract even though no recovery candidate is
        # required.
        initial_node_rects = self.node_obstacles(
            baseline, labels, show_inbreeding
        )
        initial_marker_rects = self.marker_obstacles(baseline)
        initial_pairs = self._collision_pairs(
            baseline,
            initial_node_rects,
            initial_marker_rects,
            include_labels=not allow_label_overlaps,
        )
        if not initial_pairs:
            return True
        if partner_blocks is None:
            setup_positions = dict(baseline)
            partner_blocks = self._pack_partner_blocks_on_rows(
                setup_positions, families, labels, protected
            )
        components = self._family_components(baseline, families)
        component_by_node = {
            node: component for component in components for node in component
        }
        parents_by_node: Dict[str, Set[str]] = defaultdict(set)
        children_by_node: Dict[str, Set[str]] = defaultdict(set)
        for family in families.values():
            visible_parents = [node for node in self._parents(family) if node in baseline]
            visible_children = [node for node in self._children(family) if node in baseline]
            for child in visible_children:
                parents_by_node[child].update(visible_parents)
            for parent in visible_parents:
                children_by_node[parent].update(visible_children)
        partner_orders: Dict[Tuple[str, str], int] = {}
        sibling_orders: Dict[Tuple[str, str], int] = {}
        for group in partner_blocks.values():
            ordered = sorted(
                group, key=lambda node: (baseline[node][0], node.casefold())
            )
            for left, right in zip(ordered, ordered[1:]):
                partner_orders[(left, right)] = 1
        for family in families.values():
            children = [node for node in self._children(family) if node in baseline]
            ordered = sorted(
                children, key=lambda node: (baseline[node][0], node.casefold())
            )
            for left, right in zip(ordered, ordered[1:]):
                sibling_orders[(left, right)] = 1
        budget = min(4096, max(24, 8 * (len(baseline) + len(families))))
        attempts = 0
        accepted = dict(baseline)
        seen = {
            tuple(
                sorted(
                    (node.casefold(), node, round(x, 9), round(y, 9))
                    for node, (x, y) in accepted.items()
                )
            )
        }

        def collision_state(state: Mapping[str, Point]):
            node_rects = self.node_obstacles(state, labels, show_inbreeding)
            marker_rects = self.marker_obstacles(state)
            pairs = self._collision_pairs(
                state,
                node_rects,
                marker_rects,
                include_labels=not allow_label_overlaps,
            )
            # The first recovery objective is the collision-progress prefix:
            # remove whole inter-animal pairs before optimizing clearance.
            # Horizontal penetration is the next geometric measure because
            # the solver moves semantic blocks on X.  The final tie-break is
            # the existing route scorer's provisional foreign-marker count;
            # this is deliberately different from counting arbitrary marker
            # rectangles that happen to overlap one another.
            horizontal_penetration = 0.0
            for left, right in pairs:
                overlaps = []
                for first in (node_rects[left], marker_rects[left]):
                    for second in (node_rects[right], marker_rects[right]):
                        overlaps.append(
                            (
                                max(0.0, min(first.right, second.right) - max(first.left, second.left)),
                                max(0.0, min(first.top, second.top) - max(first.bottom, second.bottom)),
                            )
                        )
                positive = [
                    value for value in overlaps
                    if value[0] > _EPSILON and value[1] > _EPSILON
                ]
                if not positive:
                    continue
                width, _height = max(positive, key=lambda value: (value[0], value[1]))
                horizontal_penetration += width
            if not pairs:
                return pairs, (0, 0.0, 0)
            _node_hits, foreign_marker_hits, _crossings = self._layout_geometry_score(
                state,
                families,
                labels,
                show_inbreeding,
                chronological=preserve_y,
            )
            return pairs, (
                len(pairs),
                round(horizontal_penetration, 9),
                foreign_marker_hits,
            )

        anchor_nodes = set(protected)

        def anchor_collision_count(pairs: Sequence[Tuple[str, str]]) -> int:
            return sum(
                1
                for first, second in pairs
                if first in anchor_nodes or second in anchor_nodes
            )

        requested_nodes = {node for pair in initial_pairs for node in pair}
        branch_groups = self._branch_movement_groups(
            baseline, families, partner_blocks, requested_nodes=requested_nodes
        )
        branch_group_cache = dict(branch_groups)

        def legal_groups(node: str) -> List[Set[str]]:
            """Lazily discover semantic groups for newly exposed conflicts."""
            if node not in branch_group_cache:
                discovered = self._branch_movement_groups(
                    baseline,
                    families,
                    partner_blocks,
                    requested_nodes={node},
                )
                branch_group_cache.update(discovered)
            groups = [set(group) for group in branch_group_cache.get(node, [])]
            component = component_by_node.get(node, {node})
            partner = set(partner_blocks.get(node, {node}))

            # A donor/mate can be a parent of a visible child without being a
            # root of the selected ancestry.  In that case the root-oriented
            # branch discovery above may expose only the endpoint itself,
            # even though moving it alone would detach the family axis. Build
            # one deterministic downward branch candidate for every conflict
            # endpoint so a free reproduction branch moves as a semantic unit.
            downstream: Set[str] = set(partner) & set(baseline)
            pending_downstream = sorted(downstream, key=str.casefold, reverse=True)
            while pending_downstream:
                parent = pending_downstream.pop()
                for child in sorted(
                    children_by_node.get(parent, set()), key=str.casefold, reverse=True
                ):
                    child_block = set(partner_blocks.get(child, {child})) & set(baseline)
                    additions = child_block - downstream
                    if not additions:
                        continue
                    downstream.update(additions)
                    pending_downstream.extend(additions)
            if (
                preserve_y
                and downstream not in groups
                and not downstream & protected
            ):
                groups.append(downstream)
            groups = [
                group for group in groups
                if len(group) > 1
                or component == {node}
                or (
                    bool(parents_by_node.get(node))
                    and not bool(children_by_node.get(node))
                    and partner == {node}
                    # A terminal descendant of a continuing sibling branch
                    # still belongs to that branch.  Only a terminal child
                    # whose parent is an origin/root may move independently;
                    # otherwise a singleton rescue would shear the child off
                    # its parent and change the branch vector.
                    and not any(
                        parents_by_node.get(parent)
                        for parent in parents_by_node.get(node, set())
                    )
                )
            ]
            # A singleton is legal only for a structurally independent node;
            # family/partner members must move through a semantic cohort.
            if component == {node} and partner == {node}:
                groups.append({node})
            return groups

        def sibling_cohort_groups(node: str) -> List[Set[str]]:
            """Return ordered prefix/suffix cohorts for a blocked sibling root."""
            cohorts: List[Set[str]] = []
            for family_id in sorted(families, key=str.casefold):
                family = families[family_id]
                children = [child for child in self._children(family) if child in baseline]
                if node not in children or len(children) < 2:
                    continue
                ordered = sorted(
                    children,
                    key=lambda child: (baseline[child][0], child.casefold(), child),
                )
                index = ordered.index(node)
                root_groups: Dict[str, Set[str]] = {}
                for root in ordered:
                    alternatives = legal_groups(root)
                    if alternatives:
                        root_groups[root] = set(alternatives[-1])
                for start, end in (
                    (0, index + 1),
                    (index, len(ordered)),
                ):
                    cohort = set()
                    for root in ordered[start:end]:
                        cohort.update(root_groups.get(root, {root}))
                    if node in cohort and cohort not in cohorts:
                        cohorts.append(cohort)
            return cohorts

        for _sweep in range(6):
            sweep_progress = False
            while attempts < budget:
                pairs, prefix = collision_state(accepted)
                if not pairs:
                    positions.update(accepted)
                    return True
                anchor_prefix = anchor_collision_count(pairs)
                progressed = False
                for first, second in pairs:
                    rects = self.node_obstacles(accepted, labels, show_inbreeding)
                    marker_rects = self.marker_obstacles(accepted)
                    def group_shift(
                        moving: Set[str], other: Set[str], direction: int,
                    ) -> float:
                        moving_rects = [
                            rect
                            for node in moving
                            for rect in (rects[node], marker_rects[node])
                        ]
                        other_rects = [
                            rect
                            for node in other
                            for rect in (rects[node], marker_rects[node])
                        ]
                        required = 0.0
                        for moving_rect in moving_rects:
                            for other_rect in other_rects:
                                if not _ranges_overlap(
                                    moving_rect.bottom, moving_rect.top,
                                    other_rect.bottom, other_rect.top,
                                ):
                                    continue
                                if direction < 0:
                                    required = max(
                                        required,
                                        moving_rect.right + self.node_gap - other_rect.left,
                                    )
                                else:
                                    required = max(
                                        required,
                                        other_rect.right + self.node_gap - moving_rect.left,
                                )
                        return max(0.0, required)

                    def group_shift_candidates(
                        moving: Set[str], other: Set[str]
                    ) -> List[float]:
                        """Return bounded boundary and overshoot shifts.

                        The boundary-derived shift is the smallest legal
                        displacement for the active pair.  In a dense
                        chronological row that boundary can place the moving
                        branch directly against a second protected anchor,
                        leaving the greedy recovery solver in a local minimum.
                        A short deterministic overshoot ladder lets the same
                        semantic block reach the next free interval without
                        turning collision recovery into an unbounded search.
                        """
                        left = group_shift(moving, other, -1)
                        right = group_shift(moving, other, 1)
                        extras = (
                            (0.0, 0.50, 1.00, 2.00, 4.00, 8.00, 16.00)
                            if protected
                            else (0.0,)
                        )
                        shifts: List[float] = []
                        for extra in extras:
                            for magnitude, sign in ((left + extra, -1.0), (right + extra, 1.0)):
                                shift = sign * magnitude
                                if abs(shift) <= _EPSILON:
                                    continue
                                if shift not in shifts:
                                    shifts.append(shift)
                        return shifts

                    candidates: List[Tuple[Set[str], float]] = []
                    same_component = (
                        component_by_node.get(first) is component_by_node.get(second)
                    )
                    first_groups = legal_groups(first) + sibling_cohort_groups(first)
                    second_groups = legal_groups(second) + sibling_cohort_groups(second)
                    same_partner_block = partner_blocks.get(first, {first}) == partner_blocks.get(
                        second, {second}
                    )
                    if same_partner_block and len(partner_blocks.get(first, {first})) > 1:
                        candidates.append((set(), 0.0))
                    for group in first_groups:
                        if group.isdisjoint({second}) and not group & protected:
                            candidates.extend(
                                (group, shift)
                                for shift in group_shift_candidates(group, {second})
                            )
                    for group in second_groups:
                        if group.isdisjoint({first}) and not group & protected:
                            candidates.extend(
                                (group, shift)
                                for shift in group_shift_candidates(group, {first})
                            )
                    if not same_component:
                        for group in (
                            component_by_node.get(first, {first}),
                            component_by_node.get(second, {second}),
                        ):
                            if not group & protected:
                                moved_group = set(group)
                                stationary = {second} if first in group else {first}
                                candidates.extend(
                                    (moved_group, shift)
                                    for shift in group_shift_candidates(
                                        moved_group, stationary
                                    )
                                )
                    ranked: List[Tuple[Tuple[object, ...], Dict[str, Point]]] = []
                    for group, delta in candidates:
                        if not group and delta == 0.0:
                            trial = dict(accepted)
                            row = [
                                node for node in partner_blocks.get(first, {first})
                                if abs(accepted[node][1] - accepted[first][1]) <= 0.42
                            ]
                            self._deoverlap_row(trial, row, labels, protected, show_inbreeding)
                        else:
                            if attempts >= budget:
                                break
                            trial = dict(accepted)
                            for node in group:
                                x, y = trial[node]
                                trial[node] = (x + delta, y)
                        attempts += 1
                        key = tuple(
                            sorted(
                                (node.casefold(), node, round(x, 9), round(y, 9))
                                for node, (x, y) in trial.items()
                            )
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        if not self._placement_candidate_is_legal(
                            trial, baseline, protected, partner_orders, sibling_orders,
                            partner_blocks, preserve_y=preserve_y,
                        ):
                            continue
                        trial_pairs, trial_prefix = collision_state(trial)
                        if (first, second) in trial_pairs:
                            continue
                        trial_anchor_prefix = anchor_collision_count(trial_pairs)
                        if protected:
                            # During focused/manual recovery the protected
                            # anchors are the semantic invariant.  Prefer a
                            # state with fewer anchor collisions even when a
                            # free branch temporarily collides with another
                            # free branch.  At equal anchor count, accept only
                            # a non-increasing aggregate pair count; this
                            # permits a bounded conflict-identity transition
                            # without allowing an expanding search.
                            if trial_anchor_prefix > anchor_prefix:
                                continue
                            if (
                                trial_anchor_prefix == anchor_prefix
                                and trial_prefix[0] > prefix[0]
                            ):
                                continue
                        elif trial_prefix >= prefix:
                            continue
                        # A legal branch move can remove the active pair while
                        # exposing another pair with the same aggregate count
                        # (for example when a focused chronological shoulder
                        # is between two date-anchored donor branches).  The
                        # old strict-prefix gate treated that as no progress
                        # and stopped in a local minimum.  Pair identity is a
                        # real progress signal: the active conflict is gone,
                        # the state is deduplicated below, and the next sweep
                        # can solve the newly exposed pair.  Keep the original
                        # monotonic penetration objective whenever possible;
                        # equal-prefix transitions are bounded by ``seen`` and
                        # the global recovery budget.
                        moved = sum(1 for node in trial if trial[node] != accepted[node])
                        displacement = sum(
                            abs(trial[node][0] - accepted[node][0]) for node in trial
                        )
                        span = max(point[0] for point in trial.values()) - min(
                            point[0] for point in trial.values()
                        )
                        rank = (
                            (
                                trial_anchor_prefix,
                                trial_prefix,
                            )
                            if protected
                            else (trial_prefix,)
                        )
                        ranked.append(
                            (
                                (
                                    rank,
                                    moved,
                                    round(displacement, 9),
                                    round(span, 9),
                                    tuple((node.casefold(), node) for node in sorted(group)),
                                ),
                                trial,
                            )
                        )
                    if ranked:
                        _rank, accepted = min(ranked, key=lambda item: item[0])
                        progressed = True
                        sweep_progress = True
                        break
                if not progressed:
                    break
            if not sweep_progress:
                break

        if protected:
            # The ordinary greedy pass intentionally optimizes the aggregate
            # collision prefix.  A focused frame can still reach a local
            # minimum where moving one free branch removes an anchor pair but
            # exposes another one at the same total count.  Perform a small
            # anchor-first rescue search before publishing the diagnostic.
            # This remains semantic: only legal branch groups are moved, Y is
            # immutable in chronological mode, and the existing budget/seen
            # safeguards still bound the search.
            def rescue_shifts(
                state: Mapping[str, Point],
                moving: Set[str],
                stationary: Set[str],
            ) -> List[float]:
                node_rects = self.node_obstacles(state, labels, show_inbreeding)
                marker_rects = self.marker_obstacles(state)
                moving_rects = [
                    rect
                    for node in moving
                    for rect in (node_rects[node], marker_rects[node])
                ]
                stationary_rects = [
                    rect
                    for node in stationary
                    for rect in (node_rects[node], marker_rects[node])
                ]
                shifts: List[float] = []
                for direction in (-1, 1):
                    required = 0.0
                    for moving_rect in moving_rects:
                        for stationary_rect in stationary_rects:
                            if not _ranges_overlap(
                                moving_rect.bottom,
                                moving_rect.top,
                                stationary_rect.bottom,
                                stationary_rect.top,
                            ):
                                continue
                            if direction < 0:
                                required = max(
                                    required,
                                    moving_rect.right
                                    + self.node_gap
                                    - stationary_rect.left,
                                )
                            else:
                                required = max(
                                    required,
                                    stationary_rect.right
                                    + self.node_gap
                                    - moving_rect.left,
                                )
                    for extra in (0.0, 0.50, 1.00, 2.00, 4.00, 8.00, 16.00, 32.00):
                        magnitude = required + extra
                        if magnitude <= _EPSILON:
                            continue
                        shift = direction * magnitude
                        if shift not in shifts:
                            shifts.append(shift)
                return shifts

            for _rescue_round in range(12):
                pairs, prefix = collision_state(accepted)
                anchor_prefix = anchor_collision_count(pairs)
                anchor_pairs = [
                    pair
                    for pair in pairs
                    if pair[0] in anchor_nodes or pair[1] in anchor_nodes
                ]
                if not anchor_pairs:
                    break
                rescue_ranked: List[
                    Tuple[Tuple[object, ...], Dict[str, Point]]
                ] = []
                for first, second in anchor_pairs:
                    for endpoint, other in ((first, second), (second, first)):
                        if endpoint in protected:
                            continue
                        groups = legal_groups(endpoint) + sibling_cohort_groups(endpoint)
                        for group in groups:
                            if not group or group & protected or group & {other}:
                                continue
                            for shift in rescue_shifts(accepted, group, {other}):
                                trial = dict(accepted)
                                for node in group:
                                    x, y = trial[node]
                                    trial[node] = (x + shift, y)
                                if not self._placement_candidate_is_legal(
                                    trial,
                                    baseline,
                                    protected,
                                    partner_orders,
                                    sibling_orders,
                                    partner_blocks,
                                    preserve_y=preserve_y,
                                ):
                                    continue
                                trial_pairs, trial_prefix = collision_state(trial)
                                if (first, second) in trial_pairs:
                                    continue
                                trial_anchor_prefix = anchor_collision_count(trial_pairs)
                                if trial_anchor_prefix > anchor_prefix:
                                    continue
                                if (
                                    trial_anchor_prefix == anchor_prefix
                                    and trial_prefix[0] > prefix[0]
                                ):
                                    continue
                                key = tuple(
                                    sorted(
                                        (
                                            node.casefold(),
                                            node,
                                            round(x, 9),
                                            round(y, 9),
                                        )
                                        for node, (x, y) in trial.items()
                                    )
                                )
                                if key in seen and trial_anchor_prefix >= anchor_prefix:
                                    continue
                                displacement = sum(
                                    abs(trial[node][0] - accepted[node][0])
                                    for node in trial
                                )
                                span = max(point[0] for point in trial.values()) - min(
                                    point[0] for point in trial.values()
                                )
                                rescue_ranked.append(
                                    (
                                        (
                                            trial_anchor_prefix,
                                            trial_prefix,
                                            round(displacement, 9),
                                            round(span, 9),
                                            tuple(
                                                (node.casefold(), node)
                                                for node in sorted(group)
                                            ),
                                        ),
                                        trial,
                                    )
                                )
                if not rescue_ranked:
                    break
                _rank, accepted = min(rescue_ranked, key=lambda item: item[0])
                seen.add(
                    tuple(
                        sorted(
                            (
                                node.casefold(),
                                node,
                                round(x, 9),
                                round(y, 9),
                            )
                            for node, (x, y) in accepted.items()
                        )
                    )
                )

        # A greedy sweep can legitimately settle on a state where the last
        # collision is between two members of one sibling fan, while the
        # branch candidate that clears it was considered earlier against an
        # older conflict identity.  Give that final semantic conflict a small
        # deterministic repair pass.  This is deliberately group-only: a
        # continuing child and its visible descendants translate together,
        # while a terminal sibling may move as its own owned branch.  The pass
        # never moves a protected node and accepts only a globally improving
        # candidate, so it cannot turn recovery into an unbounded optimizer.
        for _fan_repair_round in range(24):
            pairs, prefix = collision_state(accepted)
            if not pairs:
                break
            repaired = False
            for first, second in pairs:
                for endpoint, other in ((first, second), (second, first)):
                    groups = legal_groups(endpoint) + sibling_cohort_groups(endpoint)
                    groups = [
                        set(group)
                        for group in groups
                        if endpoint in group
                        and group
                        and not group & protected
                        and len(group) > 1
                    ] or [
                        set(group)
                        for group in groups
                        if endpoint in group
                        and group
                        and not group & protected
                    ]
                    for group in groups:
                        if other in group:
                            continue
                        node_rects = self.node_obstacles(
                            accepted, labels, show_inbreeding
                        )
                        marker_rects = self.marker_obstacles(accepted)
                        moving_rects = [
                            rect
                            for node in group
                            for rect in (node_rects[node], marker_rects[node])
                        ]
                        stationary_rects = [
                            rect
                            for node in accepted
                            if node not in group
                            for rect in (node_rects[node], marker_rects[node])
                        ]
                        if not stationary_rects:
                            continue
                        shifts: List[float] = []
                        for direction in (-1, 1):
                            required = 0.0
                            for moving_rect in moving_rects:
                                for stationary_rect in stationary_rects:
                                    if not _ranges_overlap(
                                        moving_rect.bottom,
                                        moving_rect.top,
                                        stationary_rect.bottom,
                                        stationary_rect.top,
                                    ):
                                        continue
                                    if direction < 0:
                                        required = max(
                                            required,
                                            moving_rect.right
                                            + self.node_gap
                                            - stationary_rect.left,
                                        )
                                    else:
                                        required = max(
                                            required,
                                            stationary_rect.right
                                            + self.node_gap
                                            - moving_rect.left,
                                        )
                            for extra in (0.0, 0.25, 0.50, 1.0, 2.0, 4.0, 8.0):
                                magnitude = required + extra
                                if magnitude <= _EPSILON:
                                    continue
                                shift = direction * magnitude
                                if shift not in shifts:
                                    shifts.append(shift)
                        ranked_repairs: List[
                            Tuple[Tuple[object, ...], Dict[str, Point]]
                        ] = []
                        for shift in shifts:
                            trial = dict(accepted)
                            for node in group:
                                x, y = trial[node]
                                trial[node] = (x + shift, y)
                            if not self._placement_candidate_is_legal(
                                trial,
                                baseline,
                                protected,
                                partner_orders,
                                sibling_orders,
                                partner_blocks,
                                preserve_y=preserve_y,
                            ):
                                continue
                            trial_pairs, trial_prefix = collision_state(trial)
                            if len(trial_pairs) >= len(pairs):
                                continue
                            displacement = sum(
                                abs(trial[node][0] - accepted[node][0])
                                for node in trial
                            )
                            ranked_repairs.append(
                                (
                                    (
                                        len(trial_pairs),
                                        trial_prefix,
                                        round(displacement, 9),
                                        tuple(
                                            (node.casefold(), node)
                                            for node in sorted(group)
                                        ),
                                    ),
                                    trial,
                                )
                            )
                        if ranked_repairs:
                            _repair_rank, accepted = min(
                                ranked_repairs, key=lambda item: item[0]
                            )
                            repaired = True
                            break
                    if repaired:
                        break
                if repaired:
                    break
            if not repaired:
                break
        positions.update(accepted)
        remaining = collision_state(accepted)[0]
        if remaining:
            self._last_collision_recovery_diagnostic = (
                "unresolved node/marker collision recovery: "
                + ", ".join(f"{left}/{right}" for left, right in remaining)
            )
        return not remaining

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
        focus_nodes: Optional[Set[str]] = None,
    ) -> Dict[str, Set[str]]:
        """Keep same-row mates contiguous without recursively moving ancestry.

        A partner component is treated as one row block, so unrelated animals
        can never be inserted between a pair (or between a multi-mate hub and
        its mate fan).  The block order follows the compact seed layout.
        """

        block_by_node: Dict[str, Set[str]] = {}
        focus = set(focus_nodes or set()) & set(positions)
        focus_ancestry: Set[str] = set(focus)
        if prefer_descendant_order and focus:
            parent_map: Dict[str, Set[str]] = defaultdict(set)
            for family in families.values():
                visible_parents = [
                    parent for parent in self._parents(family) if parent in positions
                ]
                for child in self._children(family):
                    if child in positions:
                        parent_map[child].update(visible_parents)
            pending = sorted(focus, key=str.casefold, reverse=True)
            while pending:
                node = pending.pop()
                for parent in sorted(
                    parent_map.get(node, set()),
                    key=str.casefold,
                    reverse=True,
                ):
                    if parent not in focus_ancestry:
                        focus_ancestry.add(parent)
                        pending.append(parent)
        rows = sorted(
            self._cluster_rows(positions),
            key=lambda row: -sum(positions[node][1] for node in row) / len(row),
        )
        row_by_node = {
            node: row[0]
            for row in rows
            for node in row
        }
        # Index family membership once.  Scanning every family for every row
        # is quadratic for a sparse large pedigree where most rows contain a
        # single node.  The index preserves the same sorted family order but
        # limits each row/component to families that can actually affect it.
        family_ids_by_row: Dict[str, List[str]] = defaultdict(list)
        family_ids_by_node: Dict[str, Set[str]] = defaultdict(set)
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            members = [
                node
                for node in self._parents(family) + self._children(family)
                if node in positions
            ]
            for node in members:
                family_ids_by_node[node].add(family_id)
            parents = [
                parent
                for parent in self._parents(family)
                if parent in positions
            ]
            if len(parents) != 2:
                continue
            first, second = sorted(parents, key=str.casefold)
            first_row = row_by_node.get(first)
            if first_row is not None and first_row == row_by_node.get(second):
                family_ids_by_row[first_row].append(family_id)

        for row in rows:
            row_set = set(row)
            adjacency: Dict[str, Set[str]] = {node: set() for node in row}
            edges: Set[Tuple[str, str]] = set()
            for family_id in family_ids_by_row.get(row[0], ()):
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
                # In a focused ancestry frame, a two-mate fan with one
                # selected descendant has one unambiguous semantic branch:
                # the selected branch's mate is the inner partner and the
                # other mate is the outer partner.  Put the shared hub at the
                # stable left boundary of that focused fan.  This is a
                # structural rule (hub degree, family membership and focus
                # ancestry), not a seed/name ordering shortcut.  Ambiguous
                # fans retain the ordinary deterministic ordering below.
                if len(component) == 3:
                    hubs = [
                        node for node in component
                        if len(adjacency[node] & component) == 2
                    ]
                    if len(hubs) == 1:
                        hub = hubs[0]
                        focused_partners: List[str] = []
                        ordinary_partner: Optional[str] = None
                        for first, second in edges:
                            if hub not in {first, second}:
                                continue
                            partner = second if first == hub else first
                            family_ids = [
                                family_id
                                for family_id in family_ids_by_node.get(hub, ())
                                if family_id in family_ids_by_node.get(partner, ())
                            ]
                            family_is_focused = any(
                                any(
                                    child in focus_ancestry
                                    for child in self._children(families[family_id])
                                    if child in positions
                                )
                                for family_id in family_ids
                            )
                            if family_is_focused:
                                focused_partners.append(partner)
                            else:
                                ordinary_partner = partner
                        if len(focused_partners) == 1 and ordinary_partner is not None:
                            return [hub, focused_partners[0], ordinary_partner]
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
                component_family_ids: Set[str] = set()
                for node in component:
                    component_family_ids.update(family_ids_by_node.get(node, ()))
                for family_id in sorted(component_family_ids, key=str.casefold):
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

    def _compact_excess_partner_gaps(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        *,
        focus_nodes: Optional[Set[str]] = None,
        show_inbreeding: bool = True,
    ) -> bool:
        """Close unjustified same-row gaps inside mating blocks.

        The row packer establishes order and minimum clearance, but later
        focused branch projection can leave a pair many layout units apart
        while still treating it as one partner block.  That makes the family
        rail visually span unrelated ancestry.  Move only a prefix or suffix
        of the affected block, keep all ordering constraints, and accept the
        smallest collision-free improvement.  The rule is independent of
        names, species, or selection shape.
        """
        if len(positions) < 2 or not families:
            return False

        block_seed = dict(positions)
        partner_blocks = self._pack_partner_blocks_on_rows(
            block_seed,
            families,
            labels,
            protected,
            focus_nodes=set(focus_nodes or set()),
        )
        groups = {
            frozenset(group)
            for group in partner_blocks.values()
            if len(group) > 1
        }
        if not groups:
            return False

        partner_orders: Dict[Tuple[str, str], int] = {}
        for group in groups:
            ordered = sorted(
                group,
                key=lambda node: (positions[node][0], node.casefold()),
            )
            for left, right in zip(ordered, ordered[1:]):
                partner_orders[(left, right)] = 1
        sibling_orders: Dict[Tuple[str, str], int] = {}
        for family in families.values():
            children = [
                child for child in self._children(family) if child in positions
            ]
            ordered = sorted(
                children,
                key=lambda node: (positions[node][0], node.casefold()),
            )
            for left, right in zip(ordered, ordered[1:]):
                sibling_orders[(left, right)] = 1

        frozen = set(protected) | set(focus_nodes or set())
        current = dict(positions)
        changed = False
        for _pass in range(min(8, max(1, len(groups) * 2))):
            best: Optional[
                Tuple[
                    Tuple[float, int, float, float, Tuple[str, ...]],
                    Dict[str, Point],
                ]
            ] = None
            current_rects = self.node_obstacles(
                current, labels, show_inbreeding
            )
            current_markers = self.marker_obstacles(current)
            current_collisions = self._collision_pairs(
                current, current_rects, current_markers
            )
            for group in sorted(
                groups,
                key=lambda value: tuple(sorted(value, key=str.casefold)),
            ):
                ordered = sorted(
                    group,
                    key=lambda node: (current[node][0], node.casefold()),
                )
                for index, (left, right) in enumerate(zip(ordered, ordered[1:])):
                    if abs(current[left][1] - current[right][1]) > 0.42:
                        continue
                    required = (
                        self._estimated_label_width(
                            str(labels.get(left, left))
                        )
                        / 2.0
                        + self._estimated_label_width(
                            str(labels.get(right, right))
                        )
                        / 2.0
                        + self.node_gap
                        + 0.18
                    )
                    actual = current[right][0] - current[left][0]
                    excess = actual - required
                    if excess <= 0.35:
                        continue
                    suffix = set(ordered[index + 1 :])
                    prefix = set(ordered[: index + 1])
                    for moving, direction in ((suffix, -1.0), (prefix, 1.0)):
                        if not moving or moving & frozen:
                            continue
                        trial = dict(current)
                        for node in moving:
                            x, y = trial[node]
                            trial[node] = (x + (direction * excess), y)
                        if not self._placement_candidate_is_legal(
                            trial,
                            current,
                            frozen,
                            partner_orders,
                            sibling_orders,
                            partner_blocks,
                            preserve_y=True,
                        ):
                            continue
                        trial_rects = self.node_obstacles(
                            trial, labels, show_inbreeding
                        )
                        trial_markers = self.marker_obstacles(trial)
                        trial_collisions = self._collision_pairs(
                            trial, trial_rects, trial_markers
                        )
                        if len(trial_collisions) > len(current_collisions):
                            continue
                        displacement = sum(
                            abs(trial[node][0] - current[node][0])
                            for node in moving
                        )
                        rank = (
                            # Resolve the largest unjustified rail first. A
                            # small local gap elsewhere must not consume the
                            # bounded pass budget while a mating pair still
                            # spans an entire unrelated branch.
                            -round(excess, 9),
                            len(trial_collisions),
                            round(displacement, 9),
                            round(abs(excess), 9),
                            tuple(sorted(moving, key=str.casefold)),
                        )
                        if best is None or rank < best[0]:
                            best = (rank, trial)
            if best is None:
                break
            _rank, current = best
            changed = True

        if changed:
            positions.update(current)
        return changed

    def _compact_chronological_parent_gaps(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        show_inbreeding: bool,
        *,
        focus_nodes: Optional[Set[str]] = None,
        focused: bool = False,
        chronological: bool = False,
    ) -> bool:
        """Close oversized parent spans without changing date-derived Y.

        Chronological partners are intentionally allowed to occupy different
        date lanes, so the same-row partner-gap pass cannot see a wide X span
        between them.  Keep that date geometry immutable and make only the
        smallest collision-free X correction needed to keep a family rail
        compact.  Shared hubs are preferred as fixed boundaries; when a
        parent participates in fewer visible families it is the safer
        movement subject.  The rule is structural and label-aware, and never
        depends on a particular seed identity.
        """
        if len(positions) < 2 or not families or not focus_nodes:
            return False

        # Only compact parent spans belonging to the requested ancestry
        # branch.  A chronological overview can contain many unrelated
        # family rails whose independent date geometry must not be shifted as
        # a side effect of opening one focused frame.
        parent_map: Dict[str, Set[str]] = defaultdict(set)
        for family in families.values():
            visible_parents = [
                parent for parent in self._parents(family) if parent in positions
            ]
            for child in self._children(family):
                if child in positions:
                    parent_map[child].update(visible_parents)
        focus_lineage = set(focus_nodes) & set(positions)
        pending = sorted(focus_lineage, key=str.casefold, reverse=True)
        while pending:
            node = pending.pop()
            for parent in sorted(
                parent_map.get(node, set()),
                key=str.casefold,
                reverse=True,
            ):
                if parent not in focus_lineage:
                    focus_lineage.add(parent)
                    pending.append(parent)

        visible_family_count: Dict[str, int] = defaultdict(int)
        for family in families.values():
            parents = [
                parent for parent in self._parents(family) if parent in positions
            ]
            if len(parents) == 2:
                for parent in parents:
                    visible_family_count[parent] += 1

        changed = False
        # Three layout units is the established compact family-rail bound.
        # Longer labels may still require more room in a same-row collision;
        # these parents are on distinct chronological lanes, so the measured
        # collision check below remains the authority for whether the tighter
        # span is legal.
        compact_span = 3.0

        def has_foreign_junction_collision(candidate: Mapping[str, Point]) -> bool:
            candidate_obstacles = self.node_obstacles(
                candidate,
                labels,
                show_inbreeding,
            )
            junctions = self._place_junctions(
                candidate,
                families,
                candidate_obstacles,
                chronological=True,
                focused=focused,
            )
            for family_id, junction in junctions.items():
                family_members = {
                    node
                    for node in self._parents(families[family_id])
                    + self._children(families[family_id])
                    if node in candidate
                }
                if any(
                    node not in family_members
                    and obstacle.contains(junction)
                    for node, obstacle in candidate_obstacles.items()
                ):
                    return True
            return False

        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [
                parent for parent in self._parents(family) if parent in positions
            ]
            if len(parents) != 2:
                continue
            focus_members = set(self._children(family))
            if chronological:
                focus_members |= set(parents)
            if not (focus_members & focus_lineage):
                continue
            left, right = sorted(
                parents,
                key=lambda node: (positions[node][0], node.casefold()),
            )
            gap = positions[right][0] - positions[left][0]
            children = [
                child for child in self._children(family) if child in positions
            ]
            # In a chronological frame a contextual one-child family can
            # have its child just beyond the current parent interval because
            # its date-derived X lane was retained.  Bring the complete,
            # movable parent pair toward that child before the knot is built;
            # otherwise the knot is forced to the parent interval while the
            # child rail remains visibly diagonal.  This is a relation-based
            # correction and applies equally to active animals and ghosts.
            if chronological and len(children) == 1:
                child_x = positions[children[0]][0]
                alignment_inset = min(
                    0.18,
                    max(0.0, (gap / 2.0) - 0.04),
                )
                pair_shift = 0.0
                if child_x < positions[left][0] + alignment_inset:
                    pair_shift = child_x - (positions[left][0] + alignment_inset)
                elif child_x > positions[right][0] - alignment_inset:
                    pair_shift = child_x - (positions[right][0] - alignment_inset)
                if abs(pair_shift) > _EPSILON and children[0] not in protected:
                    # An unanchored contextual child is the safest movement
                    # subject: moving it into the existing parent corridor
                    # preserves shared hubs and their other mate rails. This
                    # is especially important when the boundary parent is a
                    # multi-mate hub; translating that hub would put its
                    # partners on opposing shoulders. Chronological Y stays
                    # untouched because only X is corrected here.
                    child_trial = dict(positions)
                    child_trial[children[0]] = (
                        child_x - pair_shift,
                        positions[children[0]][1],
                    )
                    child_obstacles = self.node_obstacles(
                        child_trial,
                        labels,
                        show_inbreeding,
                    )
                    if not self._collision_pairs(
                        child_trial,
                        child_obstacles,
                        self.marker_obstacles(child_trial),
                    ) and not has_foreign_junction_collision(child_trial):
                        positions.update(child_trial)
                        changed = True
                        left, right = sorted(
                            parents,
                            key=lambda node: (
                                positions[node][0],
                                node.casefold(),
                            ),
                        )
                        gap = positions[right][0] - positions[left][0]
                        child_x = positions[children[0]][0]
                        pair_shift = 0.0
                if abs(pair_shift) > _EPSILON and not any(
                    parent in protected for parent in parents
                ):
                    pair_trial = dict(positions)
                    for parent in parents:
                        parent_x, parent_y = pair_trial[parent]
                        pair_trial[parent] = (parent_x + pair_shift, parent_y)
                    pair_obstacles = self.node_obstacles(
                        pair_trial,
                        labels,
                        show_inbreeding,
                    )
                    if not self._collision_pairs(
                        pair_trial,
                        pair_obstacles,
                        self.marker_obstacles(pair_trial),
                    ) and not has_foreign_junction_collision(pair_trial):
                        positions.update(pair_trial)
                        changed = True
                        left, right = sorted(
                            parents,
                            key=lambda node: (
                                positions[node][0],
                                node.casefold(),
                            ),
                        )
                        gap = positions[right][0] - positions[left][0]
            if gap <= compact_span + _EPSILON:
                continue
            excess = gap - compact_span
            candidates: List[Tuple[Tuple[int, float, str], Dict[str, Point]]] = []
            for mover, direction in ((left, 1.0), (right, -1.0)):
                if mover in protected:
                    continue
                trial = dict(positions)
                x, y = trial[mover]
                trial[mover] = (x + (direction * excess), y)
                if self._collision_pairs(
                    trial,
                    self.node_obstacles(trial, labels, show_inbreeding),
                    self.marker_obstacles(trial),
                ):
                    continue
                if has_foreign_junction_collision(trial):
                    continue
                candidates.append(
                    (
                        (
                            visible_family_count.get(mover, 0),
                            round(abs(excess), 9),
                            mover.casefold(),
                        ),
                        trial,
                    )
                )
            if not candidates:
                continue
            _rank, selected = min(candidates, key=lambda item: item[0])
            positions.update(selected)
            changed = True

            # A compact chronological parent pair can leave its sole child
            # just outside the usable parent corridor.  In that state the
            # junction is forced toward the midpoint even though a small
            # inward child placement would produce the intended perpendicular
            # family rail.  Reuse the same marker-clearance inset as the
            # junction validator, keep Y untouched, and accept the child move
            # only when the complete node and junction checks remain clear.
            if len(children) == 1:
                child = children[0]
                parent_left, parent_right = sorted(
                    positions[parent][0] for parent in parents
                )
                parent_span = parent_right - parent_left
                inset = min(
                    self.node_gap,
                    max(0.0, (parent_span / 2.0) - 0.08),
                )
                low, high = parent_left + inset, parent_right - inset
                child_x, child_y = positions[child]
                if child_x < low - _EPSILON or child_x > high + _EPSILON:
                    # If the child is outside the usable corridor, moving the
                    # child alone can collide with a neighbouring dated
                    # label. Translate the complete two-parent origin by the
                    # smallest amount that puts the child into the corridor;
                    # this preserves the family span and leaves the
                    # chronological child Y untouched.
                    pair_shift = (
                        child_x - low
                        if child_x < low
                        else child_x - high
                    )
                    pair_trial = dict(positions)
                    for parent in parents:
                        parent_x, parent_y = pair_trial[parent]
                        pair_trial[parent] = (
                            parent_x + pair_shift,
                            parent_y,
                        )
                    pair_obstacles = self.node_obstacles(
                        pair_trial,
                        labels,
                        show_inbreeding,
                    )
                    if not self._collision_pairs(
                        pair_trial,
                        pair_obstacles,
                        self.marker_obstacles(pair_trial),
                    ) and not has_foreign_junction_collision(pair_trial):
                        positions.update(pair_trial)
                        changed = True
        return changed

    def _classify_visible_branch_roles(
        self,
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
    ) -> Dict[str, str]:
        """Classify visible nodes by their bounded downward branch shape.

        A node is continuing when it is a visible parent of at least one
        visible child in a family. All other visible nodes are terminal for
        this frame. The classification is deliberately derived from the
        current effective positions/family projection, so ghosts, missing
        co-parents, half-siblings, multiple mates, archived boundaries, and
        depth clipping follow the same structural rule without relying on
        names, roles, or birth-date heuristics.
        """
        roles: Dict[str, str] = {
            str(node): "terminal" for node in positions
        }
        for family in families.values():
            visible_children = {
                child
                for child in self._children(family)
                if child in positions
            }
            if not visible_children:
                continue
            for parent in self._parents(family):
                if parent in positions:
                    roles[parent] = "continuing"
        return roles

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
        direct_focus_only: bool = False,
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
        branch_roles = self._classify_visible_branch_roles(positions, families)

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
            return branch_roles.get(node) == "continuing"

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
                child
                for child in continuing
                if child in (focus_nodes if direct_focus_only else focus_lineage)
            ]
            if not focused_branches or any(node in focus_nodes for node in terminal):
                continue

            # Only a single continuing branch can be compacted as one unit.
            # With multiple continuing siblings their individual subtrees keep
            # their own positions; the independent terminal group still moves
            # to the opposite shoulder.
            pre_compaction_positions = {
                node: positions[node]
                for node in set(parents) | set(continuing) | set(terminal)
                if node in positions
            }
            trunk = continuing[0] if len(continuing) == 1 else None
            moving: Set[str] = set()
            if trunk is not None:
                moving = set(partner_blocks.get(trunk, {trunk})) | {trunk}
                pre_compaction_positions.update(
                    {
                        node: positions[node]
                        for node in moving
                        if node in positions
                    }
                )
                # A continuing child may already have a visible mating family
                # even when the partner did not share the current row closely
                # enough to enter ``partner_blocks``.  Keep that second parent
                # in the compact continuation block and first close the
                # measured label gap.  This is the generic family relation;
                # it must not depend on a particular seed name or role.
                for descendant_family_id in sorted(
                    outgoing.get(trunk, []), key=str.casefold
                ):
                    if preserve_y:
                        # Chronological mode keeps the date-derived X/Y
                        # arrangement under the ordinary focused compaction;
                        # partner-pair tightening is a partner-normalized
                        # readability refinement only.
                        continue
                    descendant_family = families[descendant_family_id]
                    descendant_children = [
                        child
                        for child in self._children(descendant_family)
                        if child in positions
                    ]
                    # Compact a partner block only when this mating family
                    # lies on the requested continuation itself.  A focused
                    # ancestor can have several unrelated outgoing families;
                    # pulling every mate into the same visual block would
                    # crowd sibling branches that are merely contextual.
                    if not any(
                        child in focus_nodes or child in focus_lineage
                        for child in descendant_children
                    ):
                        continue
                    descendant_parents = [
                        parent
                        for parent in self._parents(descendant_family)
                        if parent in positions
                    ]
                    if len(descendant_parents) != 2:
                        continue
                    partner = next(
                        (
                            parent
                            for parent in descendant_parents
                            if parent != trunk
                        ),
                        None,
                    )
                    if partner is None:
                        continue
                    pre_compaction_positions.setdefault(partner, positions[partner])
                    required_partner_gap = (
                        self._estimated_label_width(str(labels.get(trunk, trunk))) / 2.0
                        + self._estimated_label_width(str(labels.get(partner, partner))) / 2.0
                        + self.node_gap
                    )
                    trunk_x = positions[trunk][0]
                    partner_x = positions[partner][0]
                    actual_partner_gap = abs(partner_x - trunk_x)
                    if actual_partner_gap > required_partner_gap + 0.04:
                        side_to_partner = 1.0 if partner_x >= trunk_x else -1.0
                        compact_partner_x = trunk_x + side_to_partner * required_partner_gap
                        positions[partner] = (
                            compact_partner_x,
                            positions[partner][1],
                        )
                        moving.update(
                            set(partner_blocks.get(partner, {partner})) | {partner}
                        )
                for node in moving:
                    pre_compaction_positions.setdefault(node, positions[node])
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
                        # Descendant-first fan ownership is downward only:
                        # the continuing root's origin parents and unrelated
                        # co-parents are fixed boundaries, never additions to
                        # the moved branch. A child's established partner
                        # block is still kept together.
                        additions = set()
                        for descendant in self._children(descendant_family):
                            if descendant not in positions:
                                continue
                            additions.update(partner_blocks.get(descendant, {descendant}))
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
            target_offset = max(1.0, min(2.1, (block_width / 2.0) + 0.35))
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

            terminal_baseline = {
                node: positions[node]
                for node in terminal
            }
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
            if direct_focus_only and preserve_y:
                # The final direct pass repairs the selected continuation
                # after the hard X projection. Chronological terminal dates
                # were already placed by the shoulder pass; reassigning
                # their X positions here can create a new same-date label
                # collision with the selected branch. Keep those established
                # anchors and move only the continuation block.
                positions.update(terminal_baseline)

            # Do not keep a compacting move that creates a rendered-box
            # collision elsewhere in the same generation. Large overviews
            # contain many same-date terminal siblings; a local rollback is
            # safer than letting one family repair another by pushing a whole
            # branch away.
            touched = set(parents) | set(continuing) | set(terminal) | set(moving)
            snapshot = {
                node: pre_compaction_positions.get(node, positions[node])
                for node in touched
                if node in positions
            }
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
                # The opposite-shoulder placement is a semantic focus
                # invariant, not an optional symmetry hint.  Keep the
                # bounded candidate even when it temporarily touches another
                # member of the connected focus scope; the subsequent hard
                # recovery pass moves the least constrained branch and
                # resolves that contact without undoing the shoulder choice.
                # A full rollback here was the reason Elrohir repeatedly
                # returned to Arwen's side in the current seed.
                # The final direct-selection pass follows that recovery
                # boundary, so it must retain its compact branch candidate
                # even when the local pre-check sees a donor label. The
                # normal broad pass keeps its conservative rollback behavior.
                if not direct_focus_only:
                    positions.update(snapshot)
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

    def _stagger_focused_terminal_siblings(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        focus_nodes: Set[str],
        *,
        preserve_y: bool = False,
        show_inbreeding: bool = True,
    ) -> bool:
        """Give terminal members of a continuing sibship distinct Y lanes.

        Focused packing can legitimately reject a large rigid branch move when
        another visible family already claims part of that branch.  The
        terminal siblings must not then fall back to the same row: their
        labels and descendant rails become one visual object.  This correction
        changes only the bounded terminal leaves of a mixed sibling family;
        it never claims an ancestor, a co-parent, or a descendant subtree.

        Chronological mode keeps the date-derived Y coordinates authoritative.
        In that mode the same shoulder separation is applied on X only.
        """
        if not focus_nodes or not positions:
            return False

        branch_roles = self._classify_visible_branch_roles(positions, families)
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

        focus_lineage = set(focus_nodes) & set(positions)
        pending = sorted(focus_lineage, key=str.casefold, reverse=True)
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
            return branch_roles.get(node) == "continuing"

        changed = False
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
            if (
                not continuing
                or not terminal
                or not any(child in focus_lineage for child in continuing)
                or any(child in focus_nodes for child in terminal)
            ):
                continue

            terminal_y = [positions[node][1] for node in terminal]
            if max(terminal_y) - min(terminal_y) > 0.18:
                continue

            parent_y = [positions[node][1] for node in parents]
            continuation_y = [positions[node][1] for node in continuing]
            descending = min(continuation_y) >= max(parent_y)
            direction = 1.0 if descending else -1.0
            parent_edge = max(parent_y) if descending else min(parent_y)
            closest_continuing = (
                min(continuation_y) if descending else max(continuation_y)
            )
            generation_gap = max(2.4, abs(closest_continuing - parent_edge))
            fan_step = min(2.1, max(1.55, generation_gap * 0.20))
            clearance = min(5.8, max(4.4, generation_gap * 0.50))
            if descending:
                base_y = max(min(terminal_y), parent_edge + clearance)
            else:
                base_y = min(max(terminal_y), parent_edge - clearance)
            ordered_terminal = sorted(
                terminal,
                key=lambda node: (positions[node][0], node.casefold()),
            )
            if preserve_y:
                parent_center = sum(positions[node][0] for node in parents) / 2.0
                continuing_center = sum(
                    positions[node][0] for node in continuing
                ) / len(continuing)
                continuing_delta = continuing_center - parent_center
                if abs(continuing_delta) <= 0.05:
                    terminal_center = sum(
                        positions[node][0] for node in terminal
                    ) / len(terminal)
                    continuing_delta = terminal_center - parent_center
                if abs(continuing_delta) <= 0.05:
                    continuing_delta = 1.0
                side = -1.0 if continuing_delta > 0.0 else 1.0
                parent_span = abs(
                    positions[parents[0]][0] - positions[parents[1]][0]
                )
                base_offset = max(
                    1.35,
                    (parent_span / 2.0) + 0.55,
                    abs(continuing_delta) * 0.5,
                )
                side_step = max(1.55, min(2.2, base_offset * 0.55))
                if side < 0.0:
                    ordered_terminal.reverse()
                for index, node in enumerate(ordered_terminal):
                    _old_x, y = positions[node]
                    positions[node] = (
                        parent_center + side * (base_offset + index * side_step),
                        y,
                    )
                changed = True
                continue
            if not descending:
                ordered_terminal.reverse()

            for index, node in enumerate(ordered_terminal):
                x, _old_y = positions[node]
                positions[node] = (x, base_y + (direction * index * fan_step))
            # The normal bounded collision solver runs after this focused
            # refinement and owns any newly exposed horizontal conflicts. Do
            # not reject the semantic Y separation here merely because a
            # neighbouring label still occupies the old X shoulder; doing so
            # re-couples the terminal leaves that this rule is meant to split.
            changed = True

        return changed

    def _enforce_focused_sibling_shoulders(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        focus_nodes: Set[str],
        *,
        show_inbreeding: bool = True,
    ) -> Set[str]:
        """Keep side siblings on the shoulder opposite a focused branch.

        The earlier focused compaction pass can be followed by the final
        single-child-axis projection.  That projection is intentionally
        topology-agnostic and may move one of the other children of the
        focused origin back across the parent axis.  Establish the semantic
        shoulder once more immediately before the final hard clearance pass;
        the returned nodes are weighted as protected visual anchors while
        unrelated branches make room for them.
        """
        if not positions or not focus_nodes:
            return set()

        parents_by_child: Dict[str, Set[str]] = {
            node: set() for node in positions
        }
        for family in families.values():
            parents = [
                parent for parent in self._parents(family) if parent in positions
            ]
            for child in self._children(family):
                if child in positions:
                    parents_by_child[child].update(parents)

        focus_lineage = set(focus_nodes) & set(positions)
        pending = sorted(focus_lineage, key=str.casefold, reverse=True)
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

        def half_width(node: str) -> float:
            return self._estimated_label_width(
                str(labels.get(node, node)).strip()
            ) / 2.0

        weighted: Set[str] = set()
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
            # Re-apply the shoulder only at the mixed sibship that directly
            # contains a selected child.  Applying the same rule to every
            # ancestor family in the reverse focus lineage can move several
            # unrelated terminal fans onto the same narrow shoulder and
            # create a new collision elsewhere in the graph.
            focused_children = [
                child for child in children if child in focus_nodes
            ]
            if len(focused_children) != 1:
                continue

            parent_center = sum(positions[parent][0] for parent in parents) / 2.0
            focused_child = focused_children[0]
            side_delta = positions[focused_child][0] - parent_center
            if abs(side_delta) <= _EPSILON:
                child_center = sum(positions[child][0] for child in children) / len(children)
                side_delta = child_center - parent_center
            side = 1.0 if side_delta >= 0.0 else -1.0
            peers = [child for child in children if child != focused_child]
            peers.sort(key=lambda node: (positions[node][0], node.casefold(), node))
            # Leave a marker/label-sized shoulder between the parent axis and
            # the nearest peer, then keep the peers separated as one fan.
            cursor = parent_center - side * (0.72 + half_width(peers[0]))
            target_points: Dict[str, Point] = {}
            for index, peer in enumerate(peers):
                _old_x, old_y = positions[peer]
                target_points[peer] = (round(float(cursor), 10), old_y)
                if index + 1 < len(peers):
                    next_peer = peers[index + 1]
                    cursor -= side * (
                        half_width(peer) + self.node_gap + half_width(next_peer)
                    )

            # Keep the fan at the compact shoulder first.  If an unrelated
            # branch occupies that shoulder, the following bounded recovery
            # pass moves that branch as a semantic unit.  Moving the focused
            # terminal leaves outward until they happen to clear a donor was
            # self-defeating: it preserved a local pixel invariant by
            # recreating the long origin corridor this method is responsible
            # for preventing.
            for peer, point in target_points.items():
                positions[peer] = point
            weighted.update(peers)

        return weighted

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

    def _compact_overview_continuing_children(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
        *,
        chronological: bool = False,
    ) -> bool:
        """Apply a bounded local pull to continuing branches in Overview mode.

        A multi-child family can acquire a long diagonal when one child also
        owns a descendant family: the child is packed with its spouse branch,
        while its birth family remains on the opposite shoulder. Pulling that
        continuing child a small fraction toward its two-parent corridor keeps
        the ancestry local without moving terminal siblings or surrounding
        components. Candidates are accepted only when the existing local
        node/marker collision checks remain clear.
        """
        if not positions or not families:
            return False

        parent_families: Dict[str, List[str]] = defaultdict(list)
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [parent for parent in self._parents(family) if parent in positions]
            children = [child for child in self._children(family) if child in positions]
            if len(parents) == 2 and children:
                for child in children:
                    parent_families[child].append(family_id)

        # Use the same local label/marker footprints as the final renderer.
        # A full route score is intentionally avoided here: Overview seed
        # graphs can contain hundreds of nodes, and this small pull must not
        # turn every render into a quadratic global optimization.
        obstacles = self.node_obstacles(positions, labels, show_inbreeding)
        markers = self.marker_obstacles(
            positions,
            half_width=0.32,
            half_height=0.60 if chronological else 0.42,
        )

        def overlaps(first: Rect, second: Rect) -> bool:
            return (
                first.right > second.left
                and second.right > first.left
                and first.top > second.bottom
                and second.top > first.bottom
            )

        changed = False
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [parent for parent in self._parents(family) if parent in positions]
            children = [child for child in self._children(family) if child in positions]
            if len(parents) != 2 or len(children) < 2:
                continue
            continuing = [child for child in children if parent_families.get(child)]
            if not continuing:
                continue
            midpoint = sum(positions[parent][0] for parent in parents) / 2.0
            for child in sorted(continuing, key=str.casefold):
                current_x, current_y = positions[child]
                delta = (midpoint - current_x) * 0.20
                # This is deliberately a small local optimization. Avoid
                # moving already-local branches and cap sparse jumps.
                if abs(delta) < 0.50:
                    continue
                delta = max(-2.20, min(2.20, delta))
                candidate_point = (current_x + delta, current_y)
                child_obstacle = self.node_obstacles(
                    {child: candidate_point}, labels, show_inbreeding
                )[child]
                child_marker = self.marker_obstacles(
                    {child: candidate_point},
                    half_width=0.32,
                    half_height=0.60 if chronological else 0.42,
                )[child]
                if any(
                    overlaps(child_obstacle, other_obstacle)
                    or overlaps(child_obstacle, markers[other])
                    or overlaps(other_obstacle, child_marker)
                    for other, other_obstacle in obstacles.items()
                    if other != child
                ):
                    continue
                positions[child] = candidate_point
                obstacles[child] = child_obstacle
                markers[child] = child_marker
                changed = True
        return changed

    def _compact_overview_terminal_child_fans(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
    ) -> bool:
        """Center terminal multi-child fans on their real parent corridor.

        A terminal child has no visible descendant branch that can justify a
        distant barycentric placement.  In a complete Overview it should stay
        with its sibling fan and the two-parent corridor.  This pass handles
        the common all-terminal case as one ordered group, preserving the
        children as readable siblings while preventing one stale seed
        coordinate from becoming a disproportionate route outlier.
        """
        if not positions or not families:
            return False

        child_has_visible_family: Set[str] = set()
        children_by_parent: Dict[str, Set[str]] = defaultdict(set)
        family_ids_by_parent: Dict[str, List[object]] = defaultdict(list)
        for family_id, family in sorted(
            families.items(), key=lambda item: str(item[0]).casefold()
        ):
            visible_children = {
                child for child in self._children(family) if child in positions
            }
            for parent in self._parents(family):
                if parent not in positions:
                    continue
                child_has_visible_family.add(parent)
                children_by_parent[parent].update(visible_children)
                family_ids_by_parent[parent].append(family_id)

        def branch_group(root: str) -> Set[str]:
            """Return a visible downward branch without absorbing ancestors."""
            group = {root}
            pending = [root]
            while pending:
                parent = pending.pop()
                for family_id in sorted(
                    family_ids_by_parent.get(parent, []),
                    key=lambda value: str(value).casefold(),
                ):
                    family = families.get(family_id)
                    if family is None:
                        continue
                    for member in self._parents(family):
                        if member in positions and member not in group:
                            group.add(member)
                            pending.append(member)
                    for child in self._children(family):
                        if child in positions and child not in group:
                            group.add(child)
                            pending.append(child)
            return group

        def half_width(node: str) -> float:
            return self._estimated_label_width(
                str(labels.get(node, node)).strip()
            ) / 2.0

        def clear(candidate: Mapping[str, Point]) -> bool:
            return not self._collision_pairs(
                candidate,
                self.node_obstacles(candidate, labels, show_inbreeding),
                self.marker_obstacles(candidate),
            )

        changed = False
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

            parent_center = sum(positions[parent][0] for parent in parents) / 2.0
            current_distance = max(
                abs(positions[child][0] - parent_center) for child in children
            )
            if current_distance <= 8.0:
                continue

            continuing = [
                child for child in children if child in child_has_visible_family
            ]
            if continuing:
                # A continuing child may still be the outlier. Move its full
                # downward branch to the nearest ordered slot beside the
                # remaining siblings; never move a branch through a sibling
                # or through an existing semantic group.
                ordered_children = sorted(
                    children,
                    key=lambda child: (
                        positions[child][0],
                        child.casefold(),
                        child,
                    ),
                )
                for child in sorted(
                    continuing,
                    key=lambda item: abs(positions[item][0] - parent_center),
                    reverse=True,
                ):
                    others = [item for item in ordered_children if item != child]
                    if not others:
                        continue
                    child_x = positions[child][0]
                    child_half = half_width(child)
                    if child_x > max(positions[item][0] for item in others):
                        nearest = max(others, key=lambda item: positions[item][0])
                        target = max(
                            parent_center,
                            positions[nearest][0]
                            + half_width(nearest)
                            + self.node_gap
                            + child_half,
                        )
                    elif child_x < min(positions[item][0] for item in others):
                        nearest = min(others, key=lambda item: positions[item][0])
                        target = min(
                            parent_center,
                            positions[nearest][0]
                            - half_width(nearest)
                            - self.node_gap
                            - child_half,
                        )
                    else:
                        continue
                    if abs(target - child_x) >= abs(child_x - parent_center) - 0.05:
                        continue
                    moving = branch_group(child)
                    if moving & (set(parents) | set(others)):
                        continue
                    delta = target - child_x
                    candidate = dict(positions)
                    for node in moving:
                        x, y = candidate[node]
                        candidate[node] = (x + delta, y)
                    if not clear(candidate):
                        candidate = None
                        for fraction in (
                            0.15,
                            0.25,
                            0.35,
                            0.50,
                            0.65,
                            0.80,
                            1.00,
                        ):
                            trial = dict(positions)
                            for node in moving:
                                x, y = trial[node]
                                trial[node] = (x + (delta * fraction), y)
                            if clear(trial):
                                candidate = trial
                                break
                        if candidate is None:
                            continue
                    for node in moving:
                        positions[node] = candidate[node]
                    changed = True
                continue

            ordered = sorted(
                children,
                key=lambda child: (
                    positions[child][0],
                    child.casefold(),
                    child,
                ),
            )
            total_width = sum(2.0 * half_width(child) for child in ordered)
            total_width += self.node_gap * max(0, len(ordered) - 1)
            cursor = parent_center - (total_width / 2.0)
            target_x: Dict[str, float] = {}
            for child in ordered:
                target = cursor + half_width(child)
                target_x[child] = target
                cursor = target + half_width(child) + self.node_gap

            candidate = dict(positions)
            for child, target in target_x.items():
                candidate[child] = (target, candidate[child][1])
            if not clear(candidate):
                # Try monotonic fractions toward the compact fan if another
                # visible component occupies the ideal parent-centered slots.
                # A partial pull is accepted only when it remains globally
                # clear and materially shortens the worst child route.
                candidate = None
                for fraction in (
                    0.15,
                    0.25,
                    0.35,
                    0.50,
                    0.65,
                    0.80,
                    1.00,
                ):
                    trial = dict(positions)
                    for child, target in target_x.items():
                        current_x = positions[child][0]
                        trial[child] = (
                            current_x + ((target - current_x) * fraction),
                            trial[child][1],
                        )
                    if clear(trial):
                        candidate = trial
                        break
                if candidate is None:
                    continue

            after_distance = max(
                abs(candidate[child][0] - parent_center) for child in children
            )
            if after_distance >= current_distance - 0.05:
                continue
            for child in children:
                positions[child] = candidate[child]
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

    def _route_marker_hit_details(
        self,
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        *,
        show_inbreeding: bool,
        chronological: bool,
    ) -> List[Tuple[str, str, str, int]]:
        """Return canonical route segments that cross a foreign marker.

        This deliberately mirrors the route portion of ``validate_plan``
        before a ``RoutePlan`` exists, but scopes the repair signal to the
        parent-entry case it owns: a marker belonging to the other visible
        parent of the same family.  Direct child rays and unrelated foreign
        markers have separate routing/masking policies and must not cause a
        partner block to move here.
        """
        obstacles = self.node_obstacles(positions, labels, show_inbreeding)
        junctions = self._place_junctions(
            positions,
            families,
            obstacles,
            chronological=chronological,
        )
        markers = self.marker_obstacles(positions)
        hits: List[Tuple[str, str, str, int]] = []
        for family_id in sorted(junctions, key=str.casefold):
            family = families.get(family_id, {})
            parents = set(self._parents(family)) & set(positions)
            junction = junctions[family_id]
            for endpoint in self._ordered_endpoints(family, positions):
                # This repair owns only the canonical parent-entry lanes.
                # Direct child rays have a separate, intentionally maskable
                # foreign-marker policy and must never cause a parent/mate
                # block to be shifted as a side effect of this pass.
                if endpoint not in parents:
                    continue
                if endpoint in parents:
                    path = _simplify_path(
                        [
                            junction,
                            (positions[endpoint][0], junction[1]),
                            positions[endpoint],
                        ]
                    )
                else:
                    path = [junction, positions[endpoint]]
                for index, segment in enumerate(_path_segments(path)):
                    for foreign, rect in markers.items():
                        if foreign == endpoint or foreign not in parents:
                            continue
                        if rect.intersects(segment, margin=0.01):
                            hits.append((family_id, endpoint, foreign, index))
        return hits

    def _repair_parent_entry_marker_lanes(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        show_inbreeding: bool,
        *,
        focus_nodes: Optional[Set[str]] = None,
        preserve_y: bool = False,
    ) -> bool:
        """Move legal same-row blocks away from foreign parent-entry markers.

        Node collision recovery cannot see a route-only conflict when two
        parents are on different date rows.  The canonical parent route still
        has to descend vertically into its endpoint, so a nearby parent
        marker can sit directly in that lane without overlapping either
        animal rectangle.  Search a bounded set of boundary-derived uniform
        block shifts and accept only candidates that strictly reduce the
        actual canonical route-marker hit count without adding node/marker
        collisions or changing protected/focused anchors.
        """
        if len(positions) < 2 or not families:
            return False

        baseline = dict(positions)
        block_seed = dict(baseline)
        partner_blocks = self._pack_partner_blocks_on_rows(
            block_seed,
            families,
            labels,
            protected,
        )
        partner_orders: Dict[Tuple[str, str], int] = {}
        for group in partner_blocks.values():
            ordered = sorted(
                group,
                key=lambda node: (baseline[node][0], node.casefold()),
            )
            for left, right in zip(ordered, ordered[1:]):
                partner_orders[(left, right)] = 1
        sibling_orders: Dict[Tuple[str, str], int] = {}
        for family in families.values():
            children = [
                node for node in self._children(family) if node in baseline
            ]
            ordered = sorted(
                children,
                key=lambda node: (baseline[node][0], node.casefold()),
            )
            for left, right in zip(ordered, ordered[1:]):
                sibling_orders[(left, right)] = 1

        frozen = set(protected) | set(focus_nodes or set())
        current = dict(baseline)
        changed = False
        for _pass in range(min(8, max(1, len(families)))):
            hits = self._route_marker_hit_details(
                current,
                families,
                labels,
                show_inbreeding=show_inbreeding,
                chronological=preserve_y,
            )
            if not hits:
                break
            current_node_rects = self.node_obstacles(
                current, labels, show_inbreeding
            )
            current_marker_rects = self.marker_obstacles(current)
            current_collisions = self._collision_pairs(
                current,
                current_node_rects,
                current_marker_rects,
            )
            candidates: List[
                Tuple[
                    Tuple[int, int, float, Tuple[str, ...]],
                    Dict[str, Point],
                ]
            ] = []
            marker_margin = max(0.02, self.route_clearance * 0.25)
            for family_id, endpoint, foreign, _segment_index in hits:
                family = families.get(family_id, {})
                possible_nodes = [endpoint]
                possible_nodes.extend(
                    node
                    for node in self._parents(family)
                    if node in current and node not in possible_nodes
                )
                foreign_rect = current_marker_rects.get(foreign)
                for moving_node in possible_nodes:
                    group = set(
                        partner_blocks.get(moving_node, {moving_node})
                    ) & set(current)
                    if not group or group & frozen:
                        continue
                    x = current[moving_node][0]
                    deltas = {
                        round(foreign_rect.left - marker_margin - x, 7),
                        round(foreign_rect.right + marker_margin - x, 7),
                    } if foreign_rect is not None else set()
                    for magnitude in (0.16, 0.32, 0.64, 1.28, 2.56):
                        deltas.update(
                            {
                                round(-magnitude, 7),
                                round(magnitude, 7),
                            }
                        )
                    for delta in sorted(deltas):
                        if abs(delta) <= _EPSILON:
                            continue
                        trial = dict(current)
                        for node in group:
                            node_x, node_y = trial[node]
                            trial[node] = (node_x + delta, node_y)
                        if not self._placement_candidate_is_legal(
                            trial,
                            current,
                            frozen,
                            partner_orders,
                            sibling_orders,
                            partner_blocks,
                            preserve_y=preserve_y,
                        ):
                            continue
                        trial_node_rects = self.node_obstacles(
                            trial, labels, show_inbreeding
                        )
                        trial_marker_rects = self.marker_obstacles(trial)
                        trial_collisions = self._collision_pairs(
                            trial,
                            trial_node_rects,
                            trial_marker_rects,
                        )
                        if len(trial_collisions) > len(current_collisions):
                            continue
                        trial_hits = self._route_marker_hit_details(
                            trial,
                            families,
                            labels,
                            show_inbreeding=show_inbreeding,
                            chronological=preserve_y,
                        )
                        if len(trial_hits) >= len(hits):
                            continue
                        displacement = sum(
                            abs(trial[node][0] - current[node][0])
                            for node in group
                        )
                        candidates.append(
                            (
                                (
                                    len(trial_hits),
                                    len(trial_collisions),
                                    round(displacement, 9),
                                    tuple(sorted(group, key=str.casefold)),
                                ),
                                trial,
                            )
                        )
            if not candidates:
                break
            _rank, current = min(candidates, key=lambda item: item[0])
            changed = True

        if changed:
            positions.update(current)
        return changed

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
        """Keep contextual mates together around an indirectly focused hub.

        Ghost status is deliberately irrelevant. The pass handles only an
        unselected hub whose terminal child lies on the selected ancestry
        line, so directly selected hubs and overview layouts retain their
        current geometry. A mate may have one visible origin family; moving
        that mate's X coordinate is still safer than moving the shared hub,
        and the normal route validation keeps the resulting ancestry leg
        honest. Candidate fans may share one shoulder when that is the least
        disruptive collision-free arrangement.
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
                # A continuing child owns a larger descendant subtree and is
                # handled by the branch compaction passes. A mate with a
                # visible origin family is still eligible: its X coordinate
                # can move while its origin remains a fixed ancestry boundary.
                if parent_families.get(child) or (
                    origin_families.get(mate) and not chronological
                ):
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
            current_sides = {
                -1 if positions[mate][0] < hub_x else 1
                for _family_id, mate, _child in records
                if abs(positions[mate][0] - hub_x) > _EPSILON
            }
            mixed_sides = len(current_sides) > 1
            preferred_side: Optional[float] = None
            # The focused terminal branch is the semantic anchor of this
            # fan. Preserve its existing shoulder whenever it is available;
            # otherwise an incidental family-id ordering can pull the
            # selected branch across the hub and reverse the partner order.
            for _family_id, mate, child in records:
                if child not in focus_nodes:
                    continue
                delta = positions[mate][0] - hub_x
                if abs(delta) > _EPSILON:
                    preferred_side = 1.0 if delta > 0.0 else -1.0
                    break
            if preferred_side is None and mixed_sides:
                # If no focused mate has a usable shoulder, retain the
                # existing shoulder of the non-focused branch as a fallback.
                for _family_id, mate, child in records:
                    if child in focus_nodes:
                        continue
                    delta = positions[mate][0] - hub_x
                    if abs(delta) > _EPSILON:
                        preferred_side = 1.0 if delta > 0.0 else -1.0
                        break
            hub_half = self._estimated_label_width(str(labels.get(hub, hub))) / 2.0
            candidates: List[
                Tuple[
                    Tuple[int, float, float, int, int, float, int, float, Tuple[str, ...]],
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
                    if mixed_sides and preferred_side is not None and not chronological:
                        # A preferred shoulder can be occupied by a nearby
                        # ghost sibling at a slightly different normalized
                        # row.  Try tiny local vertical offsets for the
                        # parentless mate before considering a broader move;
                        # this keeps the family together without allowing a
                        # marker/name collision to survive the soft shift.
                        for _family_id, mate, _child in ordered:
                            best_geometry = self._layout_geometry_score(
                                candidate,
                                families,
                                labels,
                                show_inbreeding,
                                chronological=chronological,
                            )
                            for delta in (
                                -2.00,
                                -1.60,
                                -1.40,
                                -1.20,
                                -0.80,
                                -0.50,
                                0.50,
                                0.80,
                                1.20,
                                1.40,
                                1.60,
                                2.00,
                            ):
                                trial = dict(candidate)
                                trial[mate] = (
                                    candidate[mate][0],
                                    candidate[mate][1] + delta,
                                )
                                trial_geometry = self._layout_geometry_score(
                                    trial,
                                    families,
                                    labels,
                                    show_inbreeding,
                                    chronological=chronological,
                                )
                                if trial_geometry < best_geometry:
                                    candidate = trial
                                    best_geometry = trial_geometry
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
                                focus_order_penalty,
                                float(geometry[0]),
                                geometry[1],
                                geometry[2],
                                0
                                if preferred_side is None or side == preferred_side
                                else 1,
                                round(span, 9),
                                int(focus_order_penalty),
                                round(displacement, 9),
                                tuple(
                                    f"{side:+.0f}:{mate.casefold()}"
                                    for _fid, mate, _child in ordered
                                ),
                            ),
                            candidate,
                        )
                    )

            candidate_pool = candidates
            if preferred_side is not None:
                preferred = [item for item in candidates if item[0][4] == 0]
                if preferred:
                    candidate_pool = preferred
            score, best = min(candidate_pool, key=lambda item: item[0])
            preferred_side_candidate = (
                preferred_side is not None and int(score[4]) == 0
            )
            # In a chronological focused frame, keeping all mates of one
            # visible hub on one shoulder is a semantic readability rule.
            # Labels may be dense and the X lanes are intentionally compact,
            # so do not reject that rule merely because the candidate has a
            # larger text-overlap count or a wider fan. Hard marker hits and
            # unrelated route crossings remain non-negotiable.
            chronological_preferred_fan = (
                chronological and preferred_side_candidate
            )
            if int(score[1]) > baseline_geometry[0] and not (
                preferred_side_candidate
                and int(score[1]) <= baseline_geometry[0] + 2
            ):
                continue
            if int(score[2]) > baseline_geometry[1] and not (
                preferred_side_candidate
                and int(score[2]) <= baseline_geometry[1] + 1
            ):
                continue
            if chronological_preferred_fan and (
                int(score[1]) > baseline_geometry[0]
                or int(score[2]) > baseline_geometry[1]
            ):
                continue
            if int(score[3]) > baseline_geometry[2] + 1 and not (
                preferred_side_candidate
                and int(score[3]) <= baseline_geometry[2] + 1
            ):
                continue
            if (
                float(score[5]) + 0.75 >= baseline_span
                and not chronological_preferred_fan
                and not (preferred_side_candidate and float(score[5]) <= baseline_span + 2.0)
            ):
                continue
            changed_nodes = {
                node
                for node in best
                if node in positions
                and (
                    abs(best[node][0] - positions[node][0]) > _EPSILON
                    or abs(best[node][1] - positions[node][1]) > _EPSILON
                )
            }
            for node in moved | changed_nodes:
                positions[node] = best[node]
            changed = True
        return changed

    def _resolve_parentless_fan_collisions(
        self,
        positions: Dict[str, Point],
        moved_nodes: Set[str],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        protected: Set[str],
        focus_nodes: Set[str],
        show_inbreeding: bool,
        *,
        chronological: bool = False,
    ) -> None:
        """Clear local ghost collisions introduced by a same-side fan.

        Parentless-mate compaction is allowed to choose a shoulder that is
        occupied by a terminal ghost from a neighbouring branch.  Shift only
        that terminal branch, and only when a bounded vertical trial strictly
        reduces marker/name overlaps.  This keeps the optimization local and
        avoids a second global packing pass.
        """
        if not moved_nodes:
            return
        parent_nodes = {
            parent
            for family in families.values()
            for parent in self._parents(family)
        }
        terminal_nodes = {
            node
            for node in positions
            if node not in parent_nodes
            and node not in moved_nodes
            and node not in focus_nodes
            and node not in protected
        }
        if not terminal_nodes:
            return

        def overlaps(first: Rect, second: Rect) -> bool:
            return (
                first.right > second.left
                and second.right > first.left
                and first.top > second.bottom
                and second.top > first.bottom
            )

        for _pass in range(2):
            obstacles = self.node_obstacles(positions, labels, show_inbreeding)
            moved_rects = [
                obstacles[node] for node in moved_nodes if node in obstacles
            ]
            blockers = [
                node
                for node in sorted(terminal_nodes, key=str.casefold)
                if node in obstacles
                and any(overlaps(obstacles[node], rect) for rect in moved_rects)
            ]
            if not blockers:
                return

            baseline = self._layout_geometry_score(
                positions,
                families,
                labels,
                show_inbreeding,
                chronological=chronological,
            )
            changed = False
            for blocker in blockers:
                best_geometry = baseline
                best_point = positions[blocker]
                for delta in (
                    -1.00,
                    1.00,
                    -1.50,
                    1.50,
                    -2.00,
                    2.00,
                    -2.50,
                    2.50,
                    -3.00,
                    3.00,
                    -3.50,
                    3.50,
                ):
                    trial = dict(positions)
                    if chronological:
                        # A chronological Y coordinate is a birth-date lane,
                        # not free packing space.  Resolve this local fan
                        # collision horizontally instead; moving the blocker
                        # to a different date row would falsify the plot.
                        trial[blocker] = (
                            positions[blocker][0] + delta,
                            positions[blocker][1],
                        )
                    else:
                        trial[blocker] = (
                            positions[blocker][0],
                            positions[blocker][1] + delta,
                        )
                    geometry = self._layout_geometry_score(
                        trial,
                        families,
                        labels,
                        show_inbreeding,
                        chronological=chronological,
                    )
                    if geometry < best_geometry:
                        best_geometry = geometry
                        best_point = trial[blocker]
                if best_point != positions[blocker] and best_geometry[0] < baseline[0]:
                    positions[blocker] = best_point
                    baseline = best_geometry
                    changed = True
            if not changed:
                return

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
        partner_blocks: Optional[Mapping[str, Set[str]]] = None,
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

        # Apply corrections in dependency order.  A parent that is itself a
        # one-child result must settle before its descendant family computes
        # the current parent interval; lexicographic family order allowed a
        # later ancestor adjustment to invalidate an already aligned child.
        component_by_family: Dict[str, int] = {}
        for component_index, (_parents, lane_entries) in enumerate(components):
            for lane_entry in lane_entries:
                component_by_family[str(lane_entry["family_id"])] = component_index
        component_edges: Dict[int, Set[int]] = defaultdict(set)
        component_indegree: Dict[int, int] = {index: 0 for index in range(len(components))}
        family_children: Dict[str, Set[str]] = {
            str(entry["family_id"]): {str(entry["child"])}
            for _parents, lane_entries in components
            for entry in lane_entries
        }
        family_parents: Dict[str, Set[str]] = {
            str(entry["family_id"]): set(entry["parents"])
            for _parents, lane_entries in components
            for entry in lane_entries
        }
        for first_family, first_children in family_children.items():
            first_component = component_by_family[first_family]
            for second_family, second_parents in family_parents.items():
                second_component = component_by_family[second_family]
                if first_component == second_component:
                    continue
                if first_children & second_parents and second_component not in component_edges[first_component]:
                    component_edges[first_component].add(second_component)
                    component_indegree[second_component] += 1
        ready = [
            index for index, degree in component_indegree.items() if degree == 0
        ]
        ready.sort(key=lambda index: min(
            str(entry["family_id"]).casefold()
            for entry in components[index][1]
        ))
        ordered_component_indices: List[int] = []
        while ready:
            current = ready.pop(0)
            ordered_component_indices.append(current)
            for successor in sorted(component_edges.get(current, set())):
                component_indegree[successor] -= 1
                if component_indegree[successor] == 0:
                    ready.append(successor)
                    ready.sort(key=lambda index: min(
                        str(entry["family_id"]).casefold()
                        for entry in components[index][1]
                    ))
        ordered_component_indices.extend(
            index for index in range(len(components))
            if index not in ordered_component_indices
        )

        for component_index in ordered_component_indices:
            _component_parents, lane_entries = components[component_index]
            group_children = {str(entry["child"]) for entry in lane_entries}
            # Parent blocks may have moved while earlier dependency
            # components were aligned. Re-read every interval and preferred
            # child coordinate at the point of use; the values captured while
            # collecting entries are only discovery hints.
            for lane_entry in lane_entries:
                current_child = str(lane_entry["child"])
                current_parents = [
                    parent for parent in lane_entry["parents"] if parent in positions
                ]
                if len(current_parents) < 2:
                    continue
                left, right = sorted(positions[parent][0] for parent in current_parents)
                span = max(0.0, right - left)
                inset = min(0.18, max(0.0, (span / 2.0) - 0.04))
                low, high = left + inset, right - inset
                lane_entry["current"] = positions[current_child][0]
                lane_entry["low"] = low
                lane_entry["high"] = high
                lane_entry["preferred"] = min(max((left + right) / 2.0, low), high)
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
                moving_block = set(
                    (partner_blocks or {}).get(child, {child})
                ) & set(positions)
                if not moving_block:
                    moving_block = {child}
                for candidate_x in candidates:
                    trial = dict(positions)
                    for assigned_child, assigned_x in assigned.items():
                        assigned_block = set(
                            (partner_blocks or {}).get(assigned_child, {assigned_child})
                        ) & set(trial)
                        delta = assigned_x - positions[assigned_child][0]
                        for member in assigned_block:
                            trial[member] = (
                                positions[member][0] + delta,
                                trial[member][1],
                            )
                    delta = candidate_x - positions[child][0]
                    for member in moving_block:
                        trial[member] = (
                            positions[member][0] + delta,
                            trial[member][1],
                        )
                    obstacles = self.node_obstacles(trial, labels, show_inbreeding)
                    markers = self.marker_obstacles(
                        trial, half_width=0.30, half_height=0.42
                    )
                    collision = False
                    # Validate the complete moved partner block, not only its
                    # child.  A prior version could accept an axis candidate
                    # whose child was clear while a moved partner crossed an
                    # unrelated terminal marker, leaving a fatal collision
                    # in the published frame.
                    for moving_node in moving_block:
                        for other in trial:
                            if other == moving_node or other in moving_block:
                                continue
                            # Do not test against a same-lane child that has
                            # not received its new coordinate yet; otherwise
                            # two crossing branches block each other before
                            # the lane has been assigned.
                            if other in group_children and other not in assigned:
                                continue
                            if (
                                overlaps(obstacles[moving_node], obstacles[other])
                                or overlaps(obstacles[moving_node], markers[other])
                                or overlaps(markers[moving_node], obstacles[other])
                                or overlaps(markers[moving_node], markers[other])
                            ):
                                collision = True
                                break
                        if collision:
                            break
                    if not collision:
                        chosen = candidate_x
                        break
                assigned[child] = chosen

            original_group_positions = {
                member: positions[member]
                for child in group_children
                for member in (
                    set((partner_blocks or {}).get(child, {child})) & set(positions)
                )
            }
            for child, chosen in assigned.items():
                delta = chosen - original_group_positions.get(
                    child, positions[child]
                )[0]
                moving_block = set(
                    (partner_blocks or {}).get(child, {child})
                ) & set(positions)
                for member in moving_block:
                    positions[member] = (
                        original_group_positions.get(member, positions[member])[0] + delta,
                        positions[member][1],
                    )

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
        children_by_parent: Dict[str, Set[str]] = defaultdict(set)
        for child, parents in parent_map.items():
            for parent in parents:
                children_by_parent[parent].add(child)

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

        # Generation order is a hard constraint.  Earlier versions repeatedly
        # raised both partners to the same row and could therefore move an
        # ancestor down onto a descendant through an indirect partner chain.
        # Keep the deterministic ancestry levels and only propagate parent
        # lower bounds; same-row partner alignment is intentionally a soft
        # horizontal preference handled by the packing passes above.
        for _pass in range(max(1, len(positions))):
            changed = False
            # Soft partner-row preference: raise only a lower partner whose
            # direct children remain strictly below the proposed row.  This
            # keeps ordinary family rows compact while preventing an
            # ancestor/descendant pair from being placed into one row.
            for family_id in sorted(families, key=str.casefold):
                parents = [
                    parent for parent in self._parents(families[family_id])
                    if parent in levels
                ]
                if len(parents) != 2:
                    continue
                target = max(levels[parents[0]], levels[parents[1]])
                for partner in parents:
                    if levels[partner] >= target:
                        continue
                    children = children_by_parent.get(partner, set())
                    if all(
                        child in cycle_nodes
                        or levels[child] > target
                        for child in children
                    ):
                        levels[partner] = target
                        changed = True

            for child in sorted(parent_map, key=str.casefold):
                usable = [
                    parent
                    for parent in sorted(parent_map[child], key=str.casefold)
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
        apply_soft_alignment: bool = True,
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
        # Keep family-centering equations sparse.  A dense vector per family
        # turns a sparse graph with N nodes and O(N) families into an O(N²)
        # allocation before the actual projection even starts.  The sparse
        # representation is mathematically identical: each equation still
        # contains the same child/parent coefficients, but only non-zero
        # terms are stored.
        soft_center_rows: List[
            Tuple[Tuple[Tuple[int, float], ...], float]
        ] = []
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
                for foreign in corridor_candidates(corridor):
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
            coefficient_by_index: Dict[int, float] = defaultdict(float)
            child_coefficient = 1.0 / len(centered_children)
            parent_coefficient = -1.0 / len(parents)
            for child in centered_children:
                coefficient_by_index[index[child]] += child_coefficient
            for parent in parents:
                coefficient_by_index[index[parent]] += parent_coefficient
            coefficients = tuple(
                sorted(
                    (
                        node_index,
                        coefficient,
                    )
                    for node_index, coefficient in coefficient_by_index.items()
                    if abs(coefficient) > 1e-12
                )
            )
            soft_center_rows.append(
                (
                    coefficients,
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
            for coefficients, strength in soft_center_rows:
                residual = sum(
                    coefficient * candidate[node_index]
                    for node_index, coefficient in coefficients
                )
                if abs(residual) <= 1e-7:
                    continue
                gain = sum(
                    coefficient * metric_inverse[node_index] * coefficient
                    for node_index, coefficient in coefficients
                )
                if gain <= 1e-10:
                    continue
                factor = -(residual * strength * scale) / gain
                largest = max(
                    (
                        abs(
                            metric_inverse[node_index]
                            * coefficient
                            * factor
                        )
                        for node_index, coefficient in coefficients
                    ),
                    default=0.0,
                )
                step_scale = (
                    max_node_step / largest
                    if largest > max_node_step
                    else 1.0
                )
                factor *= step_scale
                for node_index, coefficient in coefficients:
                    candidate[node_index] += (
                        metric_inverse[node_index] * coefficient * factor
                    )

        # A few bounded sweeps bring badly slanted one-child branches back
        # toward vertical without reinstating an exact global equation.  The
        # final post-projection invocation disables this soft phase: it is a
        # hard-clearance repair and must not undo an already compact semantic
        # partner block while repairing one newly exposed collision.
        if apply_soft_alignment:
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
            # The input is ordered by ascending Y.  Row centers therefore
            # remain ordered as well, and a point can only join the most
            # recently created row: if it is farther than ``tolerance`` from
            # that row, every earlier row is farther still.  Avoiding the
            # historical scan over all row centers keeps sparse large graphs
            # linear after the initial sort while preserving the same
            # first-match clustering semantics.
            if not rows or abs(y - row_centers[-1]) > tolerance:
                rows.append([node])
                row_centers.append(y)
            else:
                row = rows[-1]
                row.append(node)
                row_centers[-1] += (y - row_centers[-1]) / len(row)
        return rows

    @classmethod
    def _is_simple_linear_family_graph(
        cls,
        positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
    ) -> bool:
        """Return whether the visible graph is one unbranched family chain.

        This deliberately describes topology, not a fixture or a node-name
        convention: every visible family must have exactly one parent and one
        child, every visible node must participate in the chain, and no node
        may have more than one incoming or outgoing family edge.  Requiring
        ``V - 1`` edges makes the accepted graph a single path rather than a
        collection of unrelated short edges.  The predicate is used only for
        the large-graph performance shortcut; all other pedigrees continue
        through the full collision/recovery pipeline.
        """
        if len(positions) <= 256 or len(families) != len(positions) - 1:
            return False
        parent_degree: Dict[str, int] = defaultdict(int)
        child_degree: Dict[str, int] = defaultdict(int)
        participating: Set[str] = set()
        for family in families.values():
            parents = [node for node in cls._parents(family) if node in positions]
            children = [node for node in cls._children(family) if node in positions]
            if len(parents) != 1 or len(children) != 1:
                return False
            parent = parents[0]
            child = children[0]
            parent_degree[parent] += 1
            child_degree[child] += 1
            if parent_degree[parent] > 1 or child_degree[child] > 1:
                return False
            participating.update((parent, child))
        return participating == set(positions)

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
        focused: bool = False,
    ) -> Dict[str, Point]:
        if self._is_simple_linear_family_graph(positions, families):
            # In a collision-free one-parent/one-child chain, the midpoint is
            # the canonical family knot.  It keeps the two adjacent vertical
            # legs disjoint except at their semantic animal endpoint and
            # avoids the generic obstacle candidate sweep for every family.
            # The large-graph arrangement fast path has already proved that
            # the visible node/marker rectangles are clear; non-linear or
            # colliding pedigrees retain the full obstacle-aware placement.
            linear_junctions: Dict[str, Point] = {}
            for family_id in sorted(families, key=str.casefold):
                family = families[family_id]
                parents = [
                    node for node in self._parents(family) if node in positions
                ]
                children = [
                    node for node in self._children(family) if node in positions
                ]
                if len(parents) != 1 or len(children) != 1:
                    return {}
                parent_x, parent_y = positions[parents[0]]
                child_x, child_y = positions[children[0]]
                linear_junctions[family_id] = (
                    (parent_x + child_x) / 2.0,
                    (parent_y + child_y) / 2.0,
                )
            return linear_junctions
        grouped: Dict[
            Tuple[float, float],
            List[Tuple[str, float, float, float, bool, float, float, float]],
        ] = {}

        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            if not parents:
                continue
            if not children:
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
                child_axis_eligible = (
                    len(children) == 1
                    # A one-child family is best represented by a
                    # perpendicular child rail whenever the child is between
                    # the two parent endpoints.  The child label is an owned
                    # endpoint and may therefore overlap its own incoming
                    # line; requiring a full label-sized ``node_gap`` here
                    # needlessly displaced the family knot in dense frames.
                    and parent_left + _EPSILON < child_x < parent_right - _EPSILON
                )
                child_inside_corridor = (
                    len(children) == 1
                    and parent_left + self.node_gap <= child_x <= parent_right - self.node_gap
                )
                child_near_parent_edge = (
                    len(children) == 1
                    and chronological
                    and focused
                    and (
                        child_x < parent_left - _EPSILON
                        or child_x > parent_right + _EPSILON
                    )
                    and min(
                        abs(child_x - parent_left),
                        abs(child_x - parent_right),
                    )
                    <= self.route_clearance + _EPSILON
                )
                corridor_shift = 0.0
                if child_axis_eligible or child_near_parent_edge:
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
                corridor_shift = maximum_shift
                base_x = parent_x + max(
                    -maximum_shift, min(maximum_shift, desired_shift)
                )
            else:
                corridor_shift = 0.0
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
                    corridor_shift,
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
        ] = []
        vertically_repositioned: Set[str] = set()
        for entries in grouped.values():
            entries.sort(key=lambda item: (item[5], item[6], item[0].casefold()))
            lane_right_edges: List[float] = []
            entry_lanes: Dict[str, int] = {}
            for family_id, _base_x, _py, _cy, _fixed, left, right, _corridor_shift in entries:
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
            all_single_child = all(
                len(self._children(families.get(family_id, {}))) == 1
                for family_id, *_rest in entries
            )
            for family_id, base_x, parent_y, child_y, bounded_x, left, right, corridor_shift in entries:
                # Only parent intervals that actually overlap receive distinct
                # rails.  Fractions stay in the clear corridor between marker
                # and label boxes; unrelated families remain on one tidy row.
                family_children = [
                    child
                    for child in self._children(families.get(family_id, {}))
                    if child in positions
                ]
                single_child = all_single_child and len(
                    family_children
                ) == 1
                fraction = (
                    (
                        0.42
                        if lane_count == 1
                        else 0.34
                        + (0.14 * lane_rank[entry_lanes[family_id]] / (lane_count - 1))
                    )
                    if single_child
                    else (
                        0.52
                        if lane_count == 1
                        else 0.38
                        + (0.24 * lane_rank[entry_lanes[family_id]] / (lane_count - 1))
                    )
                )
                y = parent_y + ((child_y - parent_y) * fraction)
                low_y, high_y = sorted((parent_y, child_y))
                padding = min(0.25, (high_y - low_y) * 0.15)
                y_bounds = (low_y + padding, high_y - padding)
                if len(family_children) == 1 and family_children[0] in obstacles:
                    # A single-child knot is allowed to stay on the parent
                    # midpoint.  If the mixed row fraction puts that knot
                    # inside the child's label rectangle, prefer the clear
                    # vertical corridor immediately on the parent side of
                    # the child.  Otherwise _free_junction_point would pick
                    # a lateral boundary candidate and create a false
                    # parent-midpoint displacement.
                    child_rect = obstacles[family_children[0]]
                    if child_rect.contains((base_x, y), margin=0.04):
                        if child_y >= parent_y:
                            safe_y = child_rect.bottom - self.junction_clearance - 0.04
                        else:
                            safe_y = child_rect.top + self.junction_clearance + 0.04
                        base_y = min(max(safe_y, y_bounds[0]), y_bounds[1])
                        vertically_repositioned.add(family_id)
                    else:
                        base_y = y
                else:
                    base_y = y
                x_bounds: Optional[Tuple[float, float]] = None
                if bounded_x:
                    midpoint = (left + right) / 2.0
                    x_bounds = (
                        midpoint - corridor_shift,
                        midpoint + corridor_shift,
                    )
                raw.append(
                    (
                        family_id,
                        (base_x, base_y),
                        bounded_x,
                        x_bounds,
                        y_bounds,
                    )
                )

        placed: Dict[str, Point] = {}
        # Parent-rail conflicts only compare junctions on the same Y lane.
        # Index those lanes for large graphs so a sparse set of unrelated
        # junctions does not turn this late presentation check into an
        # all-pairs scan.  Small graphs retain the original mapping traversal
        # and therefore the exact legacy iteration behavior.
        rail_cell = max(_EPSILON * 2.0, 1e-9)
        placed_rail_index: Optional[Dict[int, List[str]]] = (
            defaultdict(list) if len(obstacles) > 256 else None
        )
        placed_order: Dict[str, int] = {}
        # Family knots whose parent intervals overlap must not share an exact
        # horizontal rail.  A later family can otherwise start inside an
        # earlier family's shoulder and inherit a collinear route segment,
        # especially in Chronological mode where adjacent generation spans
        # often produce the same interpolated Y value.  Keep the X corridor
        # and all parent/child bounds intact, but reserve a small deterministic
        # Y lane for the later knot.  This is a layout invariant, not a seed-
        # specific exception and leaves genuinely shared parent ports to the
        # route-level topology classifier below.
        raw_metadata: Dict[
            str,
            Tuple[Tuple[str, ...], Tuple[float, float], Optional[str]],
        ] = {}
        for family_id, _base, _bounded_x, _x_bounds, y_bounds in raw:
            parents = tuple(
                parent
                for parent in self._parents(families.get(family_id, {}))
                if parent in positions
            )
            children = tuple(
                child
                for child in self._children(families.get(family_id, {}))
                if child in positions
            )
            excluded_node = children[0] if len(children) == 1 else None
            raw_metadata[family_id] = (
                parents,
                y_bounds or (float("-inf"), float("inf")),
                excluded_node,
            )

        def parent_rail_intervals(family_id: str, point: Point) -> Tuple[Tuple[float, float], ...]:
            parents, _y_bounds, _excluded_node = raw_metadata[family_id]
            return tuple(
                sorted((point[0], positions[parent][0]))
                for parent in parents
                if abs(point[0] - positions[parent][0]) > _EPSILON
            )

        def rails_conflict(family_id: str, point: Point, other_id: str, other_point: Point) -> bool:
            if abs(point[1] - other_point[1]) > _EPSILON:
                return False
            for left, right in parent_rail_intervals(family_id, point):
                for other_left, other_right in parent_rail_intervals(other_id, other_point):
                    if min(right, other_right) - max(left, other_left) > 0.02:
                        return True
            return False

        def avoid_parent_rail_conflicts(family_id: str, point: Point) -> Point:
            if not placed:
                return point

            def same_lane_items(candidate: Point):
                if placed_rail_index is None:
                    return list(placed.items())
                bucket = math.floor(candidate[1] / rail_cell)
                found: Dict[str, Point] = {}
                for lane in (bucket - 1, bucket, bucket + 1):
                    for other_id in placed_rail_index.get(lane, ()):
                        other_point = placed.get(other_id)
                        if other_point is not None and abs(
                            candidate[1] - other_point[1]
                        ) <= _EPSILON:
                            found[other_id] = other_point
                return [
                    (other_id, found[other_id])
                    for other_id in sorted(
                        found,
                        key=lambda value: placed_order.get(value, 0),
                    )
                ]

            conflict_ids = [
                other_id
                for other_id, other_point in same_lane_items(point)
                if rails_conflict(family_id, point, other_id, other_point)
            ]
            if not conflict_ids:
                return point
            _parents, y_bounds, excluded_node = raw_metadata[family_id]
            low_y, high_y = y_bounds
            lane_gap = max(0.24, self.route_clearance + 0.04)
            candidate_ys: Set[float] = {round(point[1], 7)}
            for other_id in conflict_ids:
                other_y = placed[other_id][1]
                for candidate in (other_y - lane_gap, other_y + lane_gap):
                    if low_y <= candidate <= high_y:
                        candidate_ys.add(round(candidate, 7))
            candidates: List[Point] = []
            for candidate_y in sorted(candidate_ys):
                candidate = (point[0], float(candidate_y))
                if any(
                    rect.contains(candidate, margin=0.04)
                    for _name, rect in foreign_obstacle_candidates(
                        candidate, excluded_node
                    )
                ):
                    continue
                nearby_lane_items = same_lane_items(candidate)
                if any(
                    abs(candidate[0] - other_point[0]) < self.junction_clearance * 2.0
                    and abs(candidate[1] - other_point[1]) < self.junction_clearance * 2.0
                    for _other_id, other_point in nearby_lane_items
                ):
                    continue
                if any(
                    rails_conflict(family_id, candidate, other_id, other_point)
                    for other_id, other_point in nearby_lane_items
                ):
                    continue
                candidates.append(candidate)
            if not candidates:
                return point
            return min(
                candidates,
                key=lambda candidate: (
                    abs(candidate[1] - point[1]),
                    candidate[1],
                ),
            )

        # Large pedigrees used to rescan every node obstacle and every placed
        # junction for every candidate point.  Keep the exact legacy scoring
        # for normal/current-seed graphs, but use a small uniform index for
        # stress graphs so local obstacle queries remain bounded.
        spatial_cell = 2.0
        obstacle_index: Optional[Dict[Tuple[int, int], List[Rect]]] = None
        placed_index: Optional[Dict[Tuple[int, int], List[Point]]] = None
        named_obstacle_index: Optional[
            Dict[Tuple[int, int], List[Tuple[str, Rect]]]
        ] = None
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
            named_obstacle_index = self._build_rect_spatial_index(
                obstacles,
                cell_size=spatial_cell,
            )
            placed_index = defaultdict(list)

        def foreign_obstacle_candidates(
            point: Point,
            excluded_node: Optional[str],
        ) -> Sequence[Tuple[str, Rect]]:
            """Return only animal rectangles near a junction candidate.

            The old implementation materialized a copy of every obstacle
            for every one-child family.  On a sparse large pedigree that was
            an unnecessary O(F*N) cost before any candidate was evaluated.
            The same exact rectangle predicate is retained, but large graphs
            use the already calibrated broad-phase index.
            """
            if named_obstacle_index is None:
                return tuple(
                    (name, rect)
                    for name, rect in obstacles.items()
                    if name != excluded_node
                )
            radius = 0.04
            ix0 = math.floor((point[0] - radius) / spatial_cell)
            ix1 = math.floor((point[0] + radius) / spatial_cell)
            iy0 = math.floor((point[1] - radius) / spatial_cell)
            iy1 = math.floor((point[1] + radius) / spatial_cell)
            found: Dict[str, Rect] = {}
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    for name, rect in named_obstacle_index.get((ix, iy), ()):
                        if name != excluded_node:
                            found[name] = rect
            return tuple(found.items())

        def escape_foreign_junction_obstacles(
            family_id: str,
            point: Point,
            *,
            bounded_x: bool,
            x_bounds: Optional[Tuple[float, float]],
            chronological_layout: bool,
        ) -> Point:
            """Move a knot out of an unavoidable foreign label footprint.

            The node solver deliberately models the complete rendered label
            rectangle. A family knot is a line anchor, however, and labels
            are painted above lines. When a dense focused frame leaves no
            collision-free point inside the normal vertical interpolation
            corridor, keep the canonical X corridor and move the knot to the
            nearest clear edge of the blocking label. This is only a
            presentation fallback: animal Y coordinates stay untouched in a
            chronological layout, and the topology/route shape is unchanged.
            """
            _parents, _y_bounds, excluded_node = raw_metadata[family_id]

            def foreign_hits(candidate: Point) -> bool:
                return any(
                    rect.contains(candidate, margin=0.04)
                    for _name, rect in foreign_obstacle_candidates(
                        candidate, excluded_node
                    )
                )

            if not foreign_hits(point):
                return point

            # Chronological animal rows are the hard date geometry. A
            # label-over-line overlap is acceptable there and must not cause a
            # family handle to jump to another date lane merely to avoid text.
            if chronological_layout:
                return point

            candidate_points: Set[Point] = set()
            for _name, rect in foreign_obstacle_candidates(point, excluded_node):
                if not rect.contains(point, margin=0.04):
                    continue
                candidate_points.update(
                    {
                        (
                            point[0],
                            rect.bottom - self.junction_clearance - 0.04,
                        ),
                        (
                            point[0],
                            rect.top + self.junction_clearance + 0.04,
                        ),
                        (
                            rect.left - self.junction_clearance - 0.04,
                            point[1],
                        ),
                        (
                            rect.right + self.junction_clearance + 0.04,
                            point[1],
                        ),
                    }
                )

            def legal(candidate: Point) -> bool:
                if bounded_x and x_bounds is not None and not (
                    x_bounds[0] - _EPSILON
                    <= candidate[0]
                    <= x_bounds[1] + _EPSILON
                ):
                    return False
                if foreign_hits(candidate):
                    return False
                return not any(
                    abs(candidate[0] - other[0]) < self.junction_clearance * 2.0
                    and abs(candidate[1] - other[1]) < self.junction_clearance * 2.0
                    for other in placed.values()
                )

            legal_candidates = [
                (float(round(x, 7)), float(round(y, 7)))
                for x, y in candidate_points
                if legal((x, y))
            ]
            if not legal_candidates:
                return point
            return min(
                legal_candidates,
                key=lambda candidate: (
                    abs(candidate[0] - point[0]) + abs(candidate[1] - point[1]),
                    abs(candidate[1] - point[1]),
                    candidate[0],
                    candidate[1],
                ),
            )

        for family_id, base, bounded_x, x_bounds, y_bounds in sorted(
            raw,
            key=lambda item: (item[1][1], item[1][0], item[0].casefold()),
        ):
            family_children = {
                child
                for child in self._children(families.get(family_id, {}))
                if child in positions
            }
            junction_obstacles = obstacles
            excluded_obstacle_ids: Set[int] = set()
            # A narrow chronological one-child interval can put the proposed
            # knot inside that child's *text* rectangle even though the knot
            # is its own family endpoint.  Treating the endpoint's label as a
            # foreign junction obstacle forces _free_junction_point to move
            # the knot laterally, violating the parent-midpoint contract. The
            # route is allowed to enter its own endpoint marker/label; foreign
            # animal and already placed-family obstacles remain hard.
            if len(family_children) == 1:
                child = next(iter(family_children))
                # The child is an owned endpoint, never a foreign junction
                # obstacle.  Excluding it for every single-child family is
                # important when the midpoint is just outside its expanded
                # text rectangle: otherwise the free-point search can still
                # choose a lateral candidate and detach the child rail by a
                # fraction of a layout unit.
                excluded_obstacle_ids.add(id(obstacles[child]))
                # For a child whose X already lies inside the two-parent
                # corridor, keep the knot on that child axis.  The only
                # reason the free-point search should move it sideways is a
                # genuinely foreign obstacle.  First lift/lower the knot out
                # of the parent text band when the generation interval has
                # enough room; this preserves both parent readability and
                # the perpendicular child rail.
                if bounded_x and x_bounds is not None:
                    child_y = positions[child][1]
                    parent_nodes = [
                        parent
                        for parent in self._parents(families.get(family_id, {}))
                        if parent in positions and parent in obstacles
                    ]
                    if parent_nodes:
                        parent_y = sum(positions[parent][1] for parent in parent_nodes) / len(parent_nodes)
                        if child_y >= parent_y:
                            parent_clear_y = max(
                                obstacles[parent].top
                                for parent in parent_nodes
                            ) + self.junction_clearance + 0.04
                            base_y = max(base_y, parent_clear_y)
                        else:
                            parent_clear_y = min(
                                obstacles[parent].bottom
                                for parent in parent_nodes
                            ) - self.junction_clearance - 0.04
                            base_y = min(base_y, parent_clear_y)
                        base_y = min(max(base_y, y_bounds[0]), y_bounds[1])
                        # Carry the adjusted ordinate into the candidate
                        # search.  Keeping only the local scalar above would
                        # leave ``base`` unchanged and silently undo this
                        # collision-free parent-band correction.
                        base = (base[0], base_y)
                # A one-child family uses one horizontal parent rail and one
                # direct child rail.  The parent rail must stay on the parent
                # side of the child's marker; otherwise it starts a fraction
                # of a unit below the child centre and still cuts through the
                # owned marker while travelling to both parents.  This is a
                # semantic invariant for every layout, not a seed-specific
                # exception.  Keep the existing y corridor where possible,
                # but lower/raise its child-facing edge by a small explicit
                # marker margin before the free-point search.
                child_y = positions[child][1]
                marker_margin = max(0.02, self.route_clearance * 0.25)
                if child_y >= parent_y:
                    child_side_limit = child_y - _MARKER_RADIUS - marker_margin
                    y_bounds = (
                        y_bounds[0],
                        min(y_bounds[1], child_side_limit),
                    )
                    base = (base[0], min(base[1], child_side_limit))
                else:
                    child_side_limit = child_y + _MARKER_RADIUS + marker_margin
                    y_bounds = (
                        max(y_bounds[0], child_side_limit),
                        y_bounds[1],
                    )
                    base = (base[0], max(base[1], child_side_limit))
            point = self._free_junction_point(
                base,
                junction_obstacles,
                placed,
                bounded_x=bounded_x,
                x_bounds=x_bounds,
                y_bounds=y_bounds,
                obstacle_index=obstacle_index,
                placed_index=placed_index,
                spatial_cell=spatial_cell,
                excluded_obstacle_ids=excluded_obstacle_ids,
            )
            point = escape_foreign_junction_obstacles(
                family_id,
                point,
                bounded_x=bounded_x,
                x_bounds=x_bounds,
                chronological_layout=chronological,
            )
            point = avoid_parent_rail_conflicts(family_id, point)
            placed[family_id] = point
            placed_order[family_id] = len(placed_order)
            if placed_rail_index is not None:
                placed_rail_index[math.floor(point[1] / rail_cell)].append(
                    family_id
                )
            if placed_index is not None:
                cell = (
                    math.floor(point[0] / spatial_cell),
                    math.floor(point[1] / spatial_cell),
                )
                placed_index[cell].append(point)
        return placed

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
        excluded_obstacle_ids: Optional[Set[int]] = None,
    ) -> Point:
        base_x, base_y = base
        excluded_ids = set(excluded_obstacle_ids or set())

        def indexed_rects(x: float, y: float, radius: float = 0.05) -> List[Rect]:
            if obstacle_index is None:
                return [
                    rect
                    for rect in obstacles.values()
                    if id(rect) not in excluded_ids
                ]
            ix0 = math.floor((x - radius) / spatial_cell)
            ix1 = math.floor((x + radius) / spatial_cell)
            iy0 = math.floor((y - radius) / spatial_cell)
            iy1 = math.floor((y + radius) / spatial_cell)
            found: Dict[int, Rect] = {}
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    for rect in obstacle_index.get((ix, iy), ()):
                        if id(rect) not in excluded_ids:
                            found[id(rect)] = rect
            return list(found.values())

        def indexed_rects_for_x(low: float, high: float) -> List[Rect]:
            if obstacle_index is None:
                return [
                    rect
                    for rect in obstacles.values()
                    if id(rect) not in excluded_ids
                ]
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
        allowed_obstacle_names: Optional[Set[str]] = None,
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
                allowed_obstacle_names=allowed_obstacle_names,
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
            allowed_obstacle_names=allowed_obstacle_names,
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
        allowed_obstacle_names: Optional[Set[str]] = None,
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
                # A family route may pass behind another endpoint belonging
                # to the same semantic family (for example the horizontal
                # parent rail can cross a sibling label).  That is not a
                # foreign relationship or a routing failure: the normal gap
                # computation masks the covered marker/label interval.  Only
                # foreign animal/family obstacles remain recovery/diagnostic
                # hits.
                if allowed_obstacle_names and obstacle_name in allowed_obstacle_names:
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
                current_owned = _OwnedSegment(
                    family_id,
                    endpoint,
                    index,
                    segment,
                    is_terminal=index == len(segments) - 1,
                )
                shared_parent_join = self._is_shared_parent_port_join(
                    current_owned,
                    other,
                    relation,
                    point,
                    path,
                    owned_segments,
                )
                if shared_parent_join:
                    continue
                # Multiple mating families legitimately merge on the way into
                # their shared animal port.  Keep that terminal rail
                # continuous, but do not suppress arbitrary same-name
                # crossings (the structural classifier checks those against
                # the actual marker below).
                if self._is_terminal_shared_endpoint_merge(
                    current_owned,
                    other,
                    relation,
                    point,
                    {endpoint: path[-1]},
                ):
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
        route_parts: Dict[Tuple[str, str], List[_OwnedSegment]] = defaultdict(list)
        for owned in segments:
            route_parts[(owned.family_id, owned.endpoint)].append(owned)
        route_paths: Dict[Tuple[str, str], List[Point]] = {}
        for route_key, route_segments in route_parts.items():
            ordered = sorted(route_segments, key=lambda owned: owned.index)
            if not ordered:
                continue
            route_paths[route_key] = [
                ordered[0].segment[0],
                *(owned.segment[1] for owned in ordered),
            ]

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
            missing_endpoint = self._missing_shared_animal_endpoint(
                first,
                second,
                animal_positions,
            )
            if missing_endpoint is not None:
                problems.append(
                    f"{first.family_id}/{second.family_id}: shared animal endpoint "
                    f"{missing_endpoint} has missing geometry"
                )
                continue
            if self._is_shared_animal_endpoint(
                first,
                second,
                point,
                animal_positions,
                relation=relation,
                marker_tolerance=_MARKER_TOLERANCE,
            ):
                continue
            first_path = route_paths.get((first.family_id, first.endpoint), [])
            if first_path and self._is_shared_parent_port_join(
                first,
                second,
                relation,
                point,
                first_path,
                segments,
            ):
                continue
            if self._is_terminal_shared_endpoint_merge(
                first,
                second,
                relation,
                point,
                animal_positions,
                marker_tolerance=_MARKER_TOLERANCE,
            ):
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
                segments = _path_segments(routes[family_id][endpoint])
                for index, segment in enumerate(segments):
                    output.append(
                        _OwnedSegment(
                            family_id,
                            endpoint,
                            index,
                            segment,
                            is_terminal=index == len(segments) - 1,
                        )
                    )
        return output

    @staticmethod
    def _is_shared_animal_endpoint(
        first: _OwnedSegment,
        second: _OwnedSegment,
        point: Optional[Point],
        animal_positions: Mapping[str, Point],
        *,
        relation: Optional[str] = None,
        marker_tolerance: float = _MARKER_TOLERANCE,
    ) -> bool:
        """Return whether a route interaction is confined to one marker.

        Route endpoint names alone are not sufficient: a malformed or
        side-crossing route can carry the same endpoint name while meeting
        far away from its animal.  Point crossings use Euclidean distance to
        the marker.  Collinear overlaps are exempt only when *both* ends of
        their overlap are inside the same marker radius, so any extension
        outside the marker remains a reported conflict.
        """
        first_endpoint = str(first.endpoint or "").strip()
        second_endpoint = str(second.endpoint or "").strip()
        if not first_endpoint or first_endpoint != second_endpoint:
            return False
        position = animal_positions.get(first_endpoint)
        if not is_finite_point(position):
            return False
        try:
            tolerance = max(1e-6, float(marker_tolerance))
        except (TypeError, ValueError, OverflowError):
            tolerance = _MARKER_TOLERANCE
        if not math.isfinite(tolerance):
            tolerance = _MARKER_TOLERANCE
        center = (float(position[0]), float(position[1]))
        if relation == "overlap" or point is None:
            overlap = _segment_overlap(first.segment, second.segment)
            if overlap is None:
                return False
            return all(
                math.hypot(overlap_point[0] - center[0], overlap_point[1] - center[1])
                <= tolerance + _EPSILON
                for overlap_point in overlap
            )
        if not is_finite_point(point):
            return False
        return (
            math.hypot(float(point[0]) - center[0], float(point[1]) - center[1])
            <= tolerance + _EPSILON
        )

    @staticmethod
    def _is_terminal_shared_endpoint_merge(
        first: _OwnedSegment,
        second: _OwnedSegment,
        relation: str,
        point: Optional[Point],
        animal_positions: Mapping[str, Point],
        *,
        marker_tolerance: float = _MARKER_TOLERANCE,
    ) -> bool:
        """Allow only an intentional terminal rail into a shared marker.

        The canonical parent-entry route can contain a common vertical stub
        for multiple families mating with one animal.  That stub is a
        topology merge, not a foreign crossing, and is retained for compact
        multi-mate drawings.  It must be terminal on both routes and touch
        the real marker; manually constructed/non-terminal overlaps remain
        errors under :meth:`_is_shared_animal_endpoint`.
        """
        del point
        if relation != "overlap" or not first.is_terminal or not second.is_terminal:
            return False
        if not all(
            abs(segment[0][0] - segment[1][0]) <= _EPSILON
            for segment in (first.segment, second.segment)
        ):
            return False
        first_endpoint = str(first.endpoint or "").strip()
        second_endpoint = str(second.endpoint or "").strip()
        if not first_endpoint or first_endpoint != second_endpoint:
            return False
        position = animal_positions.get(first_endpoint)
        if not is_finite_point(position):
            return False
        try:
            tolerance = max(1e-6, float(marker_tolerance))
        except (TypeError, ValueError, OverflowError):
            tolerance = _MARKER_TOLERANCE
        if not math.isfinite(tolerance):
            tolerance = _MARKER_TOLERANCE
        overlap = _segment_overlap(first.segment, second.segment)
        if overlap is None:
            return False
        center = (float(position[0]), float(position[1]))
        return any(
            math.hypot(overlap_point[0] - center[0], overlap_point[1] - center[1])
            <= tolerance + _EPSILON
            for overlap_point in overlap
        )

    def _is_shared_parent_port_join(
        self,
        current: _OwnedSegment,
        other: _OwnedSegment,
        relation: str,
        point: Optional[Point],
        path: Sequence[Point],
        owned_segments: Sequence[_OwnedSegment],
    ) -> bool:
        """Allow canonical parent rails to meet at one shared animal port.

        Multiple mating families may enter the same parent.  Their horizontal
        shoulders can therefore meet the common vertical entry at the port
        above that animal.  This is a bounded topological join, not a generic
        crossing: both routes must be the canonical two-segment parent-entry
        shape, the endpoint identity must match, and the intersection must be
        the exact outer end of both terminal vertical entries.
        """
        if relation == "none":
            return False
        if current.endpoint != other.endpoint:
            return False
        current_segments = _path_segments(path)
        if len(current_segments) != 2 or current.index >= len(current_segments):
            return False
        endpoint_position = path[-1]

        def port_for(segments: Sequence[Segment]) -> Optional[Point]:
            if len(segments) != 2:
                return None
            terminal = segments[-1]
            if abs(terminal[0][0] - terminal[1][0]) > _EPSILON:
                return None
            if not _points_equal(terminal[1], endpoint_position):
                return None
            if abs(terminal[0][0] - endpoint_position[0]) > _EPSILON:
                return None
            return terminal[0]

        current_port = port_for(current_segments)
        if current_port is None:
            return False
        other_route = sorted(
            (
                candidate
                for candidate in owned_segments
                if candidate.family_id == other.family_id
                and candidate.endpoint == other.endpoint
            ),
            key=lambda candidate: candidate.index,
        )
        other_segments = [candidate.segment for candidate in other_route]
        other_port = port_for(other_segments)
        if other_port is None:
            return False
        if not _points_equal(current_port, other_port):
            # Date-lane parents can enter one shared animal at different
            # heights. Their terminal vertical entries may therefore overlap
            # from the shared marker to the lower of the two ports even though
            # the port coordinates are not identical. This is still one
            # semantic parent connection, provided both complete routes keep
            # the canonical two-segment shape and the overlap reaches only the
            # shared marker. Arbitrary horizontal/child-route overlap remains
            # invalid because it does not satisfy these structural predicates.
            if relation != "overlap" or len(other_segments) != 2:
                return False
            other_terminal = other_segments[-1]
            current_terminal = current_segments[-1]
            if not (
                abs(current_terminal[0][0] - current_terminal[1][0]) <= _EPSILON
                and abs(other_terminal[0][0] - other_terminal[1][0]) <= _EPSILON
                and abs(current_terminal[0][0] - endpoint_position[0]) <= _EPSILON
                and abs(other_terminal[0][0] - endpoint_position[0]) <= _EPSILON
                and _points_equal(current_terminal[1], endpoint_position)
                and _points_equal(other_terminal[1], endpoint_position)
            ):
                return False
            overlap = _segment_overlap(current.segment, other.segment)
            if overlap is None:
                return False
            if not any(
                math.hypot(
                    overlap_point[0] - endpoint_position[0],
                    overlap_point[1] - endpoint_position[1],
                )
                <= _MARKER_TOLERANCE + _EPSILON
                for overlap_point in overlap
            ):
                return False
        if relation == "overlap":
            # ``_segment_relation`` has no single point for a collinear
            # overlap.  Once both complete canonical parent-entry routes
            # terminate at the same shared animal port, their common
            # horizontal shoulder or vertical terminal stub is one semantic
            # connection and is safe to retain.
            return _segment_overlap(current.segment, other.segment) is not None
        if point is None:
            return False
        return _points_equal(point, current_port)

    @staticmethod
    def _missing_shared_animal_endpoint(
        first: _OwnedSegment,
        second: _OwnedSegment,
        animal_positions: Mapping[str, Point],
    ) -> Optional[str]:
        """Return a shared endpoint whose marker geometry is unavailable."""
        first_endpoint = str(first.endpoint or "").strip()
        second_endpoint = str(second.endpoint or "").strip()
        if not first_endpoint or first_endpoint != second_endpoint:
            return None
        if not is_finite_point(animal_positions.get(first_endpoint)):
            return first_endpoint
        return None

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


def _segment_overlap(first: Segment, second: Segment) -> Optional[Segment]:
    """Return the closed collinear overlap interval, if one exists."""
    p, p2 = first
    q, q2 = second
    r = (p2[0] - p[0], p2[1] - p[1])
    s = (q2[0] - q[0], q2[1] - q[1])

    def cross(first_vector: Point, second_vector: Point) -> float:
        return (first_vector[0] * second_vector[1]) - (
            first_vector[1] * second_vector[0]
        )

    length_sq = (r[0] * r[0]) + (r[1] * r[1])
    if length_sq <= _EPSILON:
        return None
    q_minus_p = (q[0] - p[0], q[1] - p[1])
    if abs(cross(r, s)) > _EPSILON or abs(cross(q_minus_p, r)) > _EPSILON:
        return None
    t0 = ((q_minus_p[0] * r[0]) + (q_minus_p[1] * r[1])) / length_sq
    q2_minus_p = (q2[0] - p[0], q2[1] - p[1])
    t1 = ((q2_minus_p[0] * r[0]) + (q2_minus_p[1] * r[1])) / length_sq
    overlap_low = max(0.0, min(t0, t1))
    overlap_high = min(1.0, max(t0, t1))
    if overlap_high < overlap_low - _EPSILON:
        return None
    return (
        (p[0] + (overlap_low * r[0]), p[1] + (overlap_low * r[1])),
        (p[0] + (overlap_high * r[0]), p[1] + (overlap_high * r[1])),
    )


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
