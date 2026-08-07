# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track semantic pedigree connector router.

from __future__ import annotations

import math
from itertools import permutations
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

Point = Tuple[float, float]
Segment = Tuple[Point, Point]
RouteKey = Tuple[str, str, int]

_EPSILON = 1e-7


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

    def route_segments(self, family_id: str, endpoint: str) -> List[Segment]:
        return _path_segments(self.routes.get(family_id, {}).get(endpoint, []))

    def draw_segments(
        self,
        family_id: str,
        endpoint: str,
        *,
        gap_radius: float = 0.10,
    ) -> List[Segment]:
        """Return route segments split at explicit non-junction crossings."""
        output: List[Segment] = []
        for index, segment in enumerate(self.route_segments(family_id, endpoint)):
            gaps = self.crossing_gaps.get((family_id, endpoint, index), [])
            output.extend(_split_segment_at_gaps(segment, gaps, gap_radius))
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
        max_y_lanes: int = 5,
        max_x_lanes: int = 4,
    ):
        self.automatic_x_scale = max(1.0, float(automatic_x_scale))
        self.node_gap = max(0.05, float(node_gap))
        self.route_clearance = max(0.05, float(route_clearance))
        self.junction_clearance = max(0.15, float(junction_clearance))
        self.max_y_lanes = max(4, int(max_y_lanes))
        self.max_x_lanes = max(4, int(max_x_lanes))
        # The widget may reserve part of the canvas for a legend.  Labels are
        # point-sized, so their data-space footprint grows when the drawable
        # axes become narrower.  Keep the default neutral for standalone
        # router users; the widget updates this factor before planning.
        self.label_width_scale = 1.0

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
        adjusted = self._arrange_nodes(
            animal_positions,
            families,
            labels,
            protected,
            show_inbreeding,
            preserve_y=chronological,
            prefer_descendant_order=bool(focus) and len(focus) <= 8,
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
                path, path_has_overlap, _path_hits_obstacle = self._route_endpoint(
                    family_id,
                    endpoint,
                    junction,
                    adjusted[endpoint],
                    obstacles,
                    owned_segments,
                    parent_entry=endpoint in parents,
                )
                endpoint_routes[endpoint] = path
                if path_has_overlap:
                    unresolved.append(
                        f"{family_id}: route to {endpoint} shares a segment with another family"
                    )
                for index, segment in enumerate(_path_segments(path)):
                    new_owned.append(_OwnedSegment(family_id, endpoint, index, segment))

            routes[family_id] = endpoint_routes
            owned_segments.extend(new_owned)

        crossing_gaps, crossing_problems = self._find_crossing_gaps(
            owned_segments,
            adjusted,
        )
        obstacle_gaps = self._find_obstacle_gaps(owned_segments, obstacles)
        for key, points in obstacle_gaps.items():
            crossing_gaps.setdefault(key, []).extend(points)
        unresolved.extend(crossing_problems)

        plan = RoutePlan(
            animal_positions=adjusted,
            family_positions=family_positions,
            family_members=family_members,
            routes=routes,
            crossing_gaps=crossing_gaps,
            unresolved=sorted(set(unresolved)),
        )
        return plan

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
            bottom_offset = 0.78 if show_inbreeding else 0.56
            obstacles[node] = Rect(
                x - label_half_width,
                x + label_half_width,
                y - bottom_offset,
                y + 0.34,
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
                if (
                    not parent_xs[0] < junction[0] < parent_xs[1]
                    or abs(junction[0] - midpoint) > _EPSILON
                ):
                    problems.append(f"{family_id}: junction is not centered between both parents")
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
                if endpoint not in parents and len(segments) != 1:
                    problems.append(
                        f"{family_id}: descendant route to {endpoint} is not one direct segment"
                    )
                for index, segment in enumerate(segments):
                    for node, rect in animal_obstacles.items():
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
                                f"{family_id}: route to {endpoint} intersects foreign node/label {node}"
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
            if not preserve_y:
                self._assign_generation_rows(adjusted, families)
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
                    self._orient_focus_parent_pairs(
                        adjusted,
                        families,
                        set(focus_nodes or set()),
                        partner_blocks,
                    )
                else:
                    # Overview mode alternates origin-aware row sweeps with
                    # branch reflection so extended sibships remain ordered
                    # around multiple-mate founders.
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
                # rows once more before the exact family-axis projection so
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
                if prefer_descendant_order:
                    self._orient_focus_parent_pairs(
                        adjusted,
                        families,
                        set(focus_nodes or set()),
                        partner_blocks,
                    )
                # Enforce the final exact geometry only after the top-down
                # block placement and branch reflections have supplied a
                # compact, crossing-aware starting point.
                for _pass in range(3):
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
                self._compact_disconnected_family_components(
                    adjusted,
                    families,
                    labels,
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

    @staticmethod
    def _orient_focus_parent_pairs(
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        focus_nodes: Set[str],
        partner_blocks: Mapping[str, Set[str]],
    ) -> None:
        """Give a focused root pair a stable outward branch orientation.

        For the direct parents of a selected animal, father-left/mother-right
        chooses one of two otherwise equivalent mirror images.  It is limited
        to a simple two-node partner block: lower branches and multi-mate fans
        remain free to rotate toward the side that minimizes crossings.
        """
        if not focus_nodes:
            return
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            children = set(PedigreeRouter._children(family))
            if not (children & focus_nodes):
                continue
            mother = str(family.get("mother") or "").strip()
            father = str(family.get("father") or "").strip()
            if mother not in positions or father not in positions:
                continue
            block = set(partner_blocks.get(father, set()))
            if block != {father, mother}:
                continue
            slots = sorted((positions[father][0], positions[mother][0]))
            father_y = positions[father][1]
            mother_y = positions[mother][1]
            positions[father] = (slots[0], father_y)
            positions[mother] = (slots[1], mother_y)

    def _align_single_child_axes_conservatively(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
        *,
        partner_blocks: Optional[Mapping[str, Set[str]]] = None,
    ) -> None:
        """Move a sole-child block toward its future exact family axis.

        This is a conservative preconditioner: it keeps partner blocks and
        labels clear while reducing the work left for the final equality
        projection.  The later solver still enforces the exact mathematical
        parent midpoint for the family knot and sole child.
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
            # Keep the junction clear of the parent marker when the available
            # interval permits it.  For a tight pair the midpoint is the only
            # unambiguous position.
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
            preferred_delta = preferred - child_x
            candidates = [
                min(max(preferred_delta, start), end)
                for start, end in allowed
            ]
            delta = min(candidates, key=lambda value: (abs(value - preferred_delta), abs(value)))
            for moving_node in moving:
                moving_x, moving_y = positions[moving_node]
                positions[moving_node] = (moving_x + delta, moving_y)

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

    def _arrange_partner_blocks(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        *,
        normalize_partner_y: bool = True,
    ) -> None:
        """Lay out each visible ancestry component as a tidy recursive family tree."""
        original_x = {node: point[0] for node, point in positions.items()}
        origin_family: Dict[str, str] = {}
        for family_id in sorted(families, key=str.casefold):
            for child in self._children(families[family_id]):
                if child in positions:
                    origin_family.setdefault(child, family_id)

        parent_families: Dict[str, List[str]] = {}
        for family_id in sorted(families, key=str.casefold):
            for parent in self._parents(families[family_id]):
                if parent in positions:
                    parent_families.setdefault(parent, []).append(family_id)

        # Siblings that start independent descendant branches keep a stable
        # left/right order.  Their unrelated mates are placed on the outside
        # of that split, which keeps the two family blocks contiguous.
        branch_side: Dict[str, int] = {}
        for family in families.values():
            all_children = [
                child
                for child in self._children(family)
                if child in positions
            ]
            branch_children = [
                child
                for child in all_children
                if parent_families.get(child)
            ]
            if not branch_children:
                continue
            ordered_branches = sorted(
                branch_children,
                key=lambda child: (original_x[child], child.casefold()),
            )
            if len(ordered_branches) >= 2:
                midpoint = (len(ordered_branches) - 1) / 2.0
                for index, child in enumerate(ordered_branches):
                    if index < midpoint:
                        branch_side[child] = -1
                    elif index > midpoint:
                        branch_side[child] = 1
                continue

            child = ordered_branches[0]
            ordered_siblings = sorted(
                all_children,
                key=lambda sibling: (original_x[sibling], sibling.casefold()),
            )
            index = ordered_siblings.index(child)
            midpoint = (len(ordered_siblings) - 1) / 2.0
            if index < midpoint:
                branch_side[child] = -1
            elif index > midpoint:
                branch_side[child] = 1
            else:
                mates = [
                    parent
                    for family_id in parent_families[child]
                    for parent in self._parents(families[family_id])
                    if parent in original_x and parent != child
                ]
                mate_center = (
                    sum(original_x[mate] for mate in mates) / len(mates)
                    if mates
                    else original_x[child]
                )
                branch_side[child] = -1 if mate_center < original_x[child] else 1

        def fan_slot(index: int) -> int:
            distance = (index // 2) + 1
            return -distance if index % 2 == 0 else distance

        # A parent with several mates owns one deterministic fan.  Each family
        # receives a unique side/distance so neither mates nor their children
        # can collapse onto the same coordinates.
        mate_fan_slots: Dict[Tuple[str, str], int] = {}
        for hub, family_ids in parent_families.items():
            if len(family_ids) < 2:
                continue
            ordered_family_ids = sorted(
                family_ids,
                key=lambda family_id: (
                    sum(
                        original_x[child]
                        for child in self._children(families[family_id])
                        if child in original_x
                    )
                    / max(
                        1,
                        sum(
                            1
                            for child in self._children(families[family_id])
                            if child in original_x
                        ),
                    ),
                    family_id.casefold(),
                ),
            )
            for index, family_id in enumerate(ordered_family_ids):
                mate_fan_slots[(family_id, hub)] = fan_slot(index)

        def node_width(node: str) -> float:
            label = str(labels.get(node, node)).strip()
            return max(0.80, (len(label) * 0.150) + 0.28)

        width_memo: Dict[str, float] = {}
        family_gap = 0.90
        sibling_gap = 0.72

        def ancestry_width(node: str, stack: Set[str]) -> float:
            if node in width_memo:
                return width_memo[node]
            if node in stack:
                return node_width(node)
            family_id = origin_family.get(node)
            family = families.get(family_id, {}) if family_id else {}
            parents = [parent for parent in self._parents(family) if parent in positions]
            if not parents:
                width_memo[node] = node_width(node)
                return width_memo[node]
            next_stack = set(stack)
            next_stack.add(node)
            parent_widths = [ancestry_width(parent, next_stack) for parent in parents]
            width_memo[node] = max(
                node_width(node),
                sum(parent_widths) + (family_gap * max(0, len(parent_widths) - 1)),
            )
            return width_memo[node]

        visible_parent_nodes = {
            parent
            for family in families.values()
            for parent in self._parents(family)
            if parent in positions
        }
        roots = [
            family_id
            for family_id, family in families.items()
            if any(
                child in positions and child not in visible_parent_nodes
                for child in self._children(family)
            )
        ]
        if not roots:
            roots = list(families)
        roots = sorted(
            set(roots),
            key=lambda family_id: (
                min(
                    (positions[child][0] for child in self._children(families[family_id]) if child in positions),
                    default=0.0,
                ),
                family_id.casefold(),
            ),
        )

        # LayoutPipeline already supplies compact, component-aware seed X
        # coordinates.  Do not sum the complete ancestry width once per leaf
        # family: in a DAG that counts shared ancestors repeatedly and turns a
        # 30-unit component into hundreds of horizontal units.  Each leaf
        # family starts at its seed child barycenter; recursive placement then
        # reserves ancestry exactly once through ``placed``.
        root_centers: Dict[str, float] = {
            family_id: (
                sum(
                    original_x[child]
                    for child in self._children(families[family_id])
                    if child in original_x
                )
                / max(
                    1,
                    sum(
                        1
                        for child in self._children(families[family_id])
                        if child in original_x
                    ),
                )
            )
            for family_id in roots
        }

        placed: Set[str] = set()
        laid_out_families: Set[str] = set()

        def place_siblings(children: Sequence[str], center: float, anchor: Optional[str]) -> None:
            ordered = sorted(children, key=lambda node: (positions[node][0], node.casefold()))
            unplaced = [node for node in ordered if node not in placed]
            if not unplaced:
                return
            active_gap = 2.00 if len(children) > 2 else sibling_gap
            if anchor and anchor in positions:
                anchor_x = positions[anchor][0]
                left_cursor = anchor_x - (node_width(anchor) / 2.0) - active_gap
                right_cursor = anchor_x + (node_width(anchor) / 2.0) + active_gap
                anchor_side = branch_side.get(anchor, 0)
                if anchor_side:
                    directional = sorted(
                        unplaced,
                        key=lambda child: (original_x[child], child.casefold()),
                        reverse=anchor_side > 0,
                    )
                    for child in directional:
                        width = node_width(child)
                        if anchor_side < 0:
                            positions[child] = (
                                right_cursor + (width / 2.0),
                                positions[child][1],
                            )
                            right_cursor += width + active_gap
                        else:
                            positions[child] = (
                                left_cursor - (width / 2.0),
                                positions[child][1],
                            )
                            left_cursor -= width + active_gap
                        placed.add(child)
                    return
                for index, child in enumerate(unplaced):
                    width = node_width(child)
                    if index % 2 == 0:
                        positions[child] = (right_cursor + (width / 2.0), positions[child][1])
                        right_cursor += width + active_gap
                    else:
                        positions[child] = (left_cursor - (width / 2.0), positions[child][1])
                        left_cursor -= width + active_gap
                    placed.add(child)
                return

            if len(unplaced) == 1:
                child = unplaced[0]
                positions[child] = (center, positions[child][1])
                placed.add(child)
                return
            if len(unplaced) == 2:
                first, second = unplaced
                separation = (node_width(first) + node_width(second)) / 2.0 + sibling_gap
                positions[first] = (center - (separation / 2.0), positions[first][1])
                positions[second] = (center + (separation / 2.0), positions[second][1])
                placed.update(unplaced)
                return

            slot_width = max(node_width(child) for child in unplaced) + active_gap
            first_x = center - (slot_width * (len(unplaced) - 1) / 2.0)
            for index, child in enumerate(unplaced):
                positions[child] = (first_x + (index * slot_width), positions[child][1])
                placed.add(child)

        def layout_family(
            family_id: str,
            center: float,
            anchor_child: Optional[str],
            stack: Set[str],
        ) -> None:
            if family_id in stack or family_id in laid_out_families:
                return
            family = families.get(family_id, {})
            parents = [parent for parent in self._parents(family) if parent in positions]
            parents.sort(key=lambda parent: (positions[parent][0], parent.casefold()))
            children = [child for child in self._children(family) if child in positions]
            if not parents:
                return
            placed_children = [child for child in children if child in placed]
            if placed_children:
                center = sum(positions[child][0] for child in placed_children) / len(placed_children)
            laid_out_families.add(family_id)
            next_stack = set(stack)
            next_stack.add(family_id)

            widths = [ancestry_width(parent, set()) for parent in parents]
            movable_parents = {parent for parent in parents if parent not in placed}
            already_placed = [parent for parent in parents if parent in placed]
            if len(parents) == 1:
                parent_node = parents[0]
                if parent_node not in placed:
                    positions[parent_node] = (center, positions[parent_node][1])
            elif len(parents) == 2:
                first, second = parents
                separation = (widths[0] + widths[1]) / 2.0 + family_gap
                left_parent, right_parent = first, second
                distance_multiplier = 1

                fan_hubs = sorted(
                    (
                        parent
                        for parent in parents
                        if (family_id, parent) in mate_fan_slots
                    ),
                    key=lambda parent: (-len(parent_families[parent]), parent.casefold()),
                )
                if fan_hubs:
                    hub = fan_hubs[0]
                    mate = second if hub == first else first
                    slot = mate_fan_slots[(family_id, hub)]
                    distance_multiplier = abs(slot)
                    if slot < 0:
                        left_parent, right_parent = mate, hub
                    else:
                        left_parent, right_parent = hub, mate
                else:
                    branch_anchors = [parent for parent in parents if branch_side.get(parent)]
                    if len(branch_anchors) == 1:
                        anchor = branch_anchors[0]
                        mate = second if anchor == first else first
                        if branch_side[anchor] < 0:
                            left_parent, right_parent = mate, anchor
                        else:
                            left_parent, right_parent = anchor, mate

                pair_distance = separation * distance_multiplier
                if not already_placed:
                    positions[left_parent] = (
                        center - (pair_distance / 2.0),
                        positions[left_parent][1],
                    )
                    positions[right_parent] = (
                        center + (pair_distance / 2.0),
                        positions[right_parent][1],
                    )
                elif len(already_placed) == 1:
                    fixed = already_placed[0]
                    moving = second if fixed == first else first
                    direction = 1.0 if moving == right_parent else -1.0
                    positions[moving] = (
                        positions[fixed][0] + (direction * pair_distance),
                        positions[moving][1],
                    )
            else:
                parent_span = sum(widths) + family_gap * max(0, len(parents) - 1)
                parent_cursor = center - (parent_span / 2.0)
                for parent_node, width in zip(parents, widths):
                    if parent_node not in placed:
                        positions[parent_node] = (
                            parent_cursor + (width / 2.0),
                            positions[parent_node][1],
                        )
                    parent_cursor += width + family_gap

            parent_ys = [positions[parent][1] for parent in parents]
            if normalize_partner_y and max(parent_ys) - min(parent_ys) <= 0.55:
                partner_y = sum(parent_ys) / len(parent_ys)
                for parent_node in parents:
                    positions[parent_node] = (positions[parent_node][0], partner_y)
            placed.update(parents)

            junction_center = sum(positions[parent][0] for parent in parents) / len(parents)
            place_siblings(children, junction_center, anchor_child)

            # When a shared origin is first reached through one already placed
            # sibling, reserve the other siblings beside it and then center the
            # still-movable ancestry block over the complete sibling group.
            # This makes the result independent of which descendant branch was
            # visited first (pedigree collapse / sibling mating).
            placed_children = [child for child in children if child in placed]
            if placed_children and movable_parents:
                child_center = sum(positions[child][0] for child in placed_children) / len(
                    placed_children
                )
                fixed_sum = sum(
                    positions[parent][0]
                    for parent in parents
                    if parent not in movable_parents
                )
                movable_sum = sum(positions[parent][0] for parent in movable_parents)
                target_sum = (child_center * len(parents)) - fixed_sum
                delta = (target_sum - movable_sum) / len(movable_parents)
                for parent_node in movable_parents:
                    x, y = positions[parent_node]
                    positions[parent_node] = (x + delta, y)

            for parent_node in parents:
                parent_family = origin_family.get(parent_node)
                if parent_family:
                    shared_children = [
                        child
                        for child in self._children(families.get(parent_family, {}))
                        if child in placed and child in positions
                    ]
                    recursive_center = (
                        sum(positions[child][0] for child in shared_children) / len(shared_children)
                        if shared_children
                        else positions[parent_node][0]
                    )
                    layout_family(
                        parent_family,
                        recursive_center,
                        parent_node,
                        next_stack,
                    )

        for family_id in roots:
            layout_family(family_id, root_centers[family_id], None, set())

        def shift_descendants(seeds: Sequence[str], delta_x: float, blocked: Set[str]) -> None:
            if abs(delta_x) <= _EPSILON:
                return
            pending = list(seeds)
            shifted: Set[str] = set()
            while pending:
                node = pending.pop()
                if node in shifted or node in blocked or node not in positions:
                    continue
                shifted.add(node)
                for descendant_family_id in parent_families.get(node, []):
                    descendant_family = families.get(descendant_family_id, {})
                    pending.extend(self._parents(descendant_family))
                    pending.extend(self._children(descendant_family))
            for node in shifted:
                x, y = positions[node]
                positions[node] = (x + delta_x, y)

        # A founder mate must remain outside the sibling fan it joins.  The
        # greedy recursive pass can otherwise pull that mate back between two
        # siblings while re-centering an already laid descendant.  Move the
        # mate outward and translate its descendant branch by half the delta so
        # every one-child connector remains perpendicular.
        for origin_id in sorted(families, key=str.casefold):
            origin = families[origin_id]
            siblings = [child for child in self._children(origin) if child in positions]
            if len(siblings) < 2:
                continue
            sibling_set = set(siblings)
            origin_parents = set(self._parents(origin))
            sibling_xs = [positions[sibling][0] for sibling in siblings]
            for sibling in sorted(siblings, key=lambda node: (positions[node][0], node.casefold())):
                side = branch_side.get(sibling, 0)
                if not side:
                    continue
                for branch_family_id in parent_families.get(sibling, []):
                    branch_family = families.get(branch_family_id, {})
                    mates = [
                        parent
                        for parent in self._parents(branch_family)
                        if parent in positions and parent != sibling
                    ]
                    if len(mates) != 1:
                        continue
                    mate = mates[0]
                    if (
                        mate in sibling_set
                        or mate in origin_parents
                        or mate in origin_family
                        or len(parent_families.get(mate, [])) > 1
                    ):
                        continue
                    separation = (node_width(sibling) + node_width(mate)) / 2.0 + family_gap
                    desired = (
                        min(sibling_xs) - separation
                        if side < 0
                        else max(sibling_xs) + separation
                    )
                    current = positions[mate][0]
                    if (side < 0 and current <= desired) or (side > 0 and current >= desired):
                        continue
                    delta = desired - current
                    positions[mate] = (desired, positions[mate][1])
                    shift_descendants(
                        [
                            child
                            for child in self._children(branch_family)
                            if child in positions
                        ],
                        delta / 2.0,
                        sibling_set | origin_parents | {sibling, mate},
                    )

        def is_ancestor(ancestor: str, descendant: str) -> bool:
            pending = [descendant]
            seen: Set[str] = set()
            while pending:
                node = pending.pop()
                if node in seen:
                    continue
                seen.add(node)
                family_id = origin_family.get(node)
                family = families.get(family_id, {}) if family_id else {}
                for parent in self._parents(family):
                    if parent == ancestor:
                        return True
                    pending.append(parent)
            return False

        # Multiple mates form a fan of complete family intervals, not merely
        # distinct mate coordinates.  Reserve the full child width on each
        # side of the shared parent so a four-child family cannot engulf the
        # one-child family beside it (the real FinwÃ«/MÃ­riel/Indis case).
        for hub in sorted(parent_families, key=str.casefold):
            family_ids = parent_families[hub]
            if len(family_ids) < 2:
                continue
            usable: List[Tuple[int, str, str, List[str], float]] = []
            for family_id in family_ids:
                family = families.get(family_id, {})
                mates = [
                    parent
                    for parent in self._parents(family)
                    if parent in positions and parent != hub
                ]
                children = [
                    child
                    for child in self._children(family)
                    if child in positions
                ]
                if len(mates) != 1 or not children:
                    continue
                mate = mates[0]
                if is_ancestor(hub, mate) or is_ancestor(mate, hub):
                    usable = []
                    break
                left = min(positions[child][0] - (node_width(child) / 2.0) for child in children)
                right = max(positions[child][0] + (node_width(child) / 2.0) for child in children)
                usable.append(
                    (
                        mate_fan_slots.get((family_id, hub), 0),
                        family_id,
                        mate,
                        children,
                        max(0.72, right - left),
                    )
                )
            if len(usable) < 2:
                continue

            hub_x = positions[hub][0]
            left_cursor = hub_x - (node_width(hub) / 2.0) - family_gap
            right_cursor = hub_x + (node_width(hub) / 2.0) + family_gap
            ordered_fan = sorted(usable, key=lambda item: (abs(item[0]), item[0], item[1].casefold()))
            for slot, _family_id, mate, children, child_width in ordered_fan:
                if slot < 0:
                    desired_center = left_cursor - (child_width / 2.0)
                    left_cursor -= child_width + family_gap
                else:
                    desired_center = right_cursor + (child_width / 2.0)
                    right_cursor += child_width + family_gap
                current_center = sum(positions[child][0] for child in children) / len(children)
                delta = desired_center - current_center
                positions[mate] = (
                    (2.0 * desired_center) - hub_x,
                    positions[mate][1],
                )
                shift_descendants(children, delta, {hub, mate})

    def _restore_single_child_axes(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
    ) -> None:
        """Center every offspring group on its parents, top-down.

        The family junction is defined by the parent midpoint.  Moving a
        partner during recursive packing must therefore move the *children of
        that family* to the new midpoint, not translate the partner's entire
        descendant component (which also drags the other partner away from its
        own ancestry).  Processing families from older to younger propagates
        each corrected midpoint through later generations while preserving the
        spacing within sibling groups.

        Directed parentage cycles are left to the cycle-safe base layout.  A
        finite projection cannot satisfy contradictory parent/child equations
        in a corrupt cycle, and must never oscillate or crash.
        """
        del show_inbreeding
        cycle_nodes = self._parentage_cycle_nodes(positions, families)
        ordered = sorted(
            families,
            key=lambda family_id: (
                min(
                    (
                        positions[parent][1]
                        for parent in self._parents(families[family_id])
                        if parent in positions
                    ),
                    default=0.0,
                ),
                max(
                    (
                        positions[child][1]
                        for child in self._children(families[family_id])
                        if child in positions
                    ),
                    default=0.0,
                ),
                family_id.casefold(),
            ),
        )

        # More than one pass is useful for equal-date families whose stable
        # lexical order is not their ancestry order.  A DAG converges quickly;
        # the hard bound protects unusual consanguineous data.
        for _pass in range(max(2, min(len(ordered) + 1, 64))):
            changed = False
            for family_id in ordered:
                family = families[family_id]
                parents = [node for node in self._parents(family) if node in positions]
                children = [node for node in self._children(family) if node in positions]
                if not parents or not children:
                    continue
                if cycle_nodes.intersection(parents) and cycle_nodes.intersection(children):
                    continue
                parent_center = sum(positions[parent][0] for parent in parents) / len(parents)
                ordered_children = sorted(
                    children,
                    key=lambda child: (positions[child][0], child.casefold()),
                )
                widths = [
                    self._estimated_label_width(str(labels.get(child, child)).strip())
                    for child in ordered_children
                ]
                slot_width = max(widths, default=0.96) + 0.82
                midpoint = (len(ordered_children) - 1) / 2.0
                for index, child in enumerate(ordered_children):
                    target_x = parent_center + ((index - midpoint) * slot_width)
                    x, y = positions[child]
                    if abs(target_x - x) <= _EPSILON:
                        continue
                    positions[child] = (target_x, y)
                    changed = True
            if not changed:
                break

    def _solve_horizontal_constraints(
        self,
        positions: Dict[str, Point],
        families: Mapping[str, Mapping[str, object]],
        labels: Mapping[str, str],
        show_inbreeding: bool,
        *,
        chronological: bool = False,
    ) -> None:
        """Resolve label/route collisions while retaining exact family axes.

        Besides separating complete animal-label rectangles, the projection
        clears the canonical parent entries and offspring corridors around a
        family knot.  This deliberately makes a branch wider before the
        endpoint router is allowed to introduce an additional dogleg or mask
        a line behind a foreign label (the Elrond/Jessica and Arwen/Taylor
        regressions).
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
        equality_rows: List[np.ndarray] = []
        collision_pairs: List[Tuple[int, int, float, float]] = []
        row_pair_requirements: List[Tuple[int, int, float]] = []

        # Preserve the visual left-to-right order while separating any pair
        # whose complete marker/name/detail rectangles share vertical space.
        for left_offset, first in enumerate(nodes):
            for second in nodes[left_offset + 1 :]:
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
        # family equalities below distribute the movement through the related
        # ancestors and descendants instead of detaching just one marker.
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
                for foreign in sorted(obstacles, key=str.casefold):
                    rect = obstacles[foreign]
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
                for foreign in sorted(obstacles, key=str.casefold):
                    if foreign in family_members:
                        continue
                    rect = obstacles[foreign]
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

        # Every sibling group is centered on the exact parent midpoint.  For
        # one child this reduces to the perpendicular rule
        # x(child) == mean(x(parents)); for a fan it constrains the mean of all
        # child X coordinates to the same family knot.
        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [
                parent for parent in self._parents(family)
                if parent in index
            ]
            children = [
                child for child in self._children(family)
                if child in index
            ]
            if not parents or not children:
                continue
            vector = np.zeros(len(nodes), dtype=float)
            child_share = 1.0 / len(children)
            for child in children:
                vector[index[child]] += child_share
            parent_share = -1.0 / len(parents)
            for parent in parents:
                vector[index[parent]] += parent_share
            equality_rows.append(vector)

        if not equality_rows and not collision_pairs and not row_pair_requirements:
            return
        # Constrained projection: family-center equalities remain exact while
        # the row order selected by the block stage supplies the inequality
        # directions.  The resulting matrix projects all later clearance
        # movements into the equality null space.
        metric = np.eye(len(nodes), dtype=float)
        metric_inverse = np.linalg.inv(metric)
        if equality_rows:
            equality_matrix = np.vstack(equality_rows)
            weighted_constraint_gram = (
                equality_matrix @ metric_inverse @ equality_matrix.T
            )
            correction = (
                metric_inverse
                @ equality_matrix.T
                @ np.linalg.pinv(weighted_constraint_gram)
            )
            candidate = initial - correction @ (equality_matrix @ initial)
            projector = metric_inverse - (
                correction @ equality_matrix @ metric_inverse
            )
        else:
            candidate = initial.copy()
            projector = metric_inverse

        # The repeated origin-aware block sweeps supply the meaningful layer
        # order (including extended sibling blocks around multiple mates).
        # Preserve that order while separating complete rendered boxes; the
        # hard midpoint projection may widen it but must not interleave two
        # previously contiguous families.
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

        if projector.shape[1]:
            # Alternating projections keep every family-axis equality exact
            # while pushing colliding label rectangles apart.  The initial
            # direction is stable, but a zero-distance pair can still choose
            # the direction with the greater feasible movement.
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
                    movement = projector @ raw
                    gain = float(np.dot(raw, movement))
                    if gain <= 1e-10:
                        continue
                    candidate += movement * ((deficit * 1.002) / gain)
                    changed = True
                if not changed or worst_deficit <= 1e-7:
                    break
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
        collapsed: List[Tuple[str, Point, bool, Optional[Tuple[float, float]]]] = []

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
                    )
                )
                continue
            parent_x = sum(positions[node][0] for node in parents) / len(parents)
            child_x = sum(positions[node][0] for node in children) / len(children)
            child_ys = [positions[node][1] for node in children]
            child_y = min(child_ys) if chronological else sum(child_ys) / len(child_ys)
            parent_ys = [positions[node][1] for node in parents]
            parent_mid_y = sum(parent_ys) / len(parent_ys)
            # In an ancestor/offspring pairing the parents intentionally occupy
            # different ranks.  Place the junction beyond the parent closest
            # to the children, leaving room for the canonical horizontal rail
            # instead of squeezing it into that parent's marker/label box.
            parent_y = max(parent_ys) if chronological else (
                max(parent_ys)
                if child_y >= parent_mid_y
                else min(parent_ys)
            )
            fixed_between_parents = len(parents) == 2
            # With two parents the semantic family knot is always their exact
            # midpoint.  The layout stages align a sole child to that X and
            # center a multi-child fan around the same X.
            base_x = parent_x if fixed_between_parents else (parent_x + child_x) / 2.0
            key = (round(parent_y, 5), round(child_y, 5))
            grouped.setdefault(key, []).append(
                (
                    family_id,
                    base_x,
                    parent_y,
                    child_y,
                    fixed_between_parents,
                    min(positions[node][0] for node in parents),
                    max(positions[node][0] for node in parents),
                )
            )

        raw: List[Tuple[str, Point, bool, Optional[Tuple[float, float]]]] = list(collapsed)
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
            for family_id, base_x, parent_y, child_y, fixed_x, _left, _right in entries:
                # Only parent intervals that actually overlap receive distinct
                # rails.  Fractions stay in the clear corridor between marker
                # and label boxes; unrelated families remain on one tidy row.
                fraction = (
                    0.38
                    if lane_count == 1
                    else 0.28 + (0.30 * lane_rank[entry_lanes[family_id]] / (lane_count - 1))
                )
                y = parent_y + ((child_y - parent_y) * fraction)
                low_y, high_y = sorted((parent_y, child_y))
                padding = min(0.25, (high_y - low_y) * 0.15)
                raw.append(
                    (
                        family_id,
                        (base_x, y),
                        fixed_x,
                        (low_y + padding, high_y - padding),
                    )
                )

        placed: Dict[str, Point] = {}
        for family_id, base, fixed_x, y_bounds in sorted(
            raw,
            key=lambda item: (item[1][1], item[1][0], item[0].casefold()),
        ):
            placed[family_id] = self._free_junction_point(
                base,
                obstacles,
                placed,
                fixed_x=fixed_x,
                y_bounds=y_bounds,
            )
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
        fixed_x: bool,
        y_bounds: Optional[Tuple[float, float]],
    ) -> Point:
        base_x, base_y = base
        x_candidates = [base_x]
        if not fixed_x:
            for step in range(1, 17):
                offset = step * 0.32
                x_candidates.extend((base_x - offset, base_x + offset))
            for rect in obstacles.values():
                x_candidates.extend(
                    (
                        rect.left - self.junction_clearance,
                        rect.right + self.junction_clearance,
                    )
                )

        y_candidates = [base_y]
        if y_bounds is not None:
            low_y, high_y = y_bounds
            for step in range(1, 9):
                offset = step * 0.12
                for candidate in (base_y - offset, base_y + offset):
                    if low_y <= candidate <= high_y:
                        y_candidates.append(candidate)
            for rect in obstacles.values():
                for candidate in (
                    rect.bottom - self.junction_clearance,
                    rect.top + self.junction_clearance,
                ):
                    if low_y <= candidate <= high_y:
                        y_candidates.append(candidate)

        def score(point: Point) -> Tuple[float, float, float]:
            x, y = point
            blocked = sum(1 for rect in obstacles.values() if rect.contains(point, margin=0.04))
            crowded = sum(
                1
                for other in placed.values()
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
    ) -> Tuple[List[Point], bool, bool]:
        if not parent_entry:
            path = [start, end]
            _score, overlap, obstacle_hit = self._score_path(
                family_id,
                endpoint,
                path,
                obstacles,
                owned_segments,
            )
            return path, overlap, obstacle_hit

        canonical = _simplify_path([start, (end[0], start[1]), end])
        if _has_parent_entry_shape(_path_segments(canonical)):
            canonical_score, overlap, obstacle_hit = self._score_path(
                family_id,
                endpoint,
                canonical,
                obstacles,
                owned_segments,
            )
            base_score = _path_length(canonical) + (
                max(0, len(_path_segments(canonical)) - 1) * 0.30
            )
            if not overlap and not obstacle_hit and canonical_score <= base_score + _EPSILON:
                return canonical, False, False

        preferred_y = start[1] + ((end[1] - start[1]) * 0.58)
        candidates = self._candidate_paths(
            start,
            end,
            preferred_y,
            obstacles.values(),
        )
        scored = self._score_candidates(
            family_id,
            endpoint,
            candidates,
            obstacles,
            owned_segments,
        )
        _key, best_path, overlap, obstacle_hit = min(scored, key=lambda item: item[0])
        if overlap or obstacle_hit:
            expanded = self._candidate_paths(
                start,
                end,
                preferred_y,
                obstacles.values(),
                exhaustive=True,
            )
            scored = self._score_candidates(
                family_id,
                endpoint,
                expanded,
                obstacles,
                owned_segments,
            )
            _key, best_path, overlap, obstacle_hit = min(scored, key=lambda item: item[0])
        return best_path, overlap, obstacle_hit

    def _score_candidates(
        self,
        family_id: str,
        endpoint: str,
        candidates: Sequence[Sequence[Point]],
        obstacles: Mapping[str, Rect],
        owned_segments: Sequence[_OwnedSegment],
    ) -> List[Tuple[Tuple[float, int, Tuple[Point, ...]], List[Point], bool, bool]]:
        scored: List[Tuple[Tuple[float, int, Tuple[Point, ...]], List[Point], bool, bool]] = []
        for candidate in candidates:
            path = list(candidate)
            score, overlap, obstacle_hit = self._score_path(
                family_id,
                endpoint,
                path,
                obstacles,
                owned_segments,
            )
            key = (score, len(path), tuple((round(x, 7), round(y, 7)) for x, y in path))
            scored.append((key, path, overlap, obstacle_hit))
        return scored

    def _candidate_paths(
        self,
        start: Point,
        end: Point,
        preferred_y: float,
        obstacles: Iterable[Rect],
        *,
        exhaustive: bool = False,
    ) -> List[List[Point]]:
        sx, sy = start
        ex, ey = end
        raw: List[List[Point]] = []
        y_candidates = {
            preferred_y,
            (sy + ey) / 2.0,
            sy + ((ey - sy) * 0.32),
            sy + ((ey - sy) * 0.68),
            min(sy, ey) - self.route_clearance,
            max(sy, ey) + self.route_clearance,
        }
        x_candidates = {
            (sx + ex) / 2.0,
            min(sx, ex) - self.route_clearance,
            max(sx, ex) + self.route_clearance,
        }
        for rect in obstacles:
            y_candidates.add(rect.bottom - self.route_clearance)
            y_candidates.add(rect.top + self.route_clearance)
            x_candidates.add(rect.left - self.route_clearance)
            x_candidates.add(rect.right + self.route_clearance)

        if exhaustive:
            y_limit = max(self.max_y_lanes * 2, 10)
            x_limit = max(self.max_x_lanes * 2, 8)
        else:
            y_limit = self.max_y_lanes
            x_limit = self.max_x_lanes
        y_lanes = _nearest_lanes(y_candidates, preferred_y, y_limit)
        x_lanes = _nearest_lanes(x_candidates, (sx + ex) / 2.0, x_limit)

        raw.append([start, (ex, sy), end])
        for x in x_lanes:
            for y in y_lanes:
                raw.append([start, (x, sy), (x, y), (ex, y), end])

        unique: Dict[Tuple[Point, ...], List[Point]] = {}
        for path in raw:
            simplified = _simplify_path(path)
            if len(simplified) < 2:
                continue
            if not _has_parent_entry_shape(_path_segments(simplified)):
                continue
            key = tuple((round(x, 7), round(y, 7)) for x, y in simplified)
            unique[key] = simplified
        return list(unique.values())

    def _score_path(
        self,
        family_id: str,
        endpoint: str,
        path: Sequence[Point],
        obstacles: Mapping[str, Rect],
        owned_segments: Sequence[_OwnedSegment],
    ) -> Tuple[float, bool, bool]:
        segments = _path_segments(path)
        score = _path_length(path) + (max(0, len(segments) - 1) * 0.30)
        overlap = False
        obstacle_hit = False

        for index, segment in enumerate(segments):
            for obstacle_name, rect in obstacles.items():
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
            for other in owned_segments:
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

    def _find_obstacle_gaps(
        self,
        segments: Sequence[_OwnedSegment],
        obstacles: Mapping[str, Rect],
    ) -> Dict[RouteKey, List[Point]]:
        """Mask the unavoidable part of a straight route that passes behind a foreign node."""
        gaps: Dict[RouteKey, List[Point]] = {}
        for owned in segments:
            (x1, y1), (x2, y2) = owned.segment
            dx = x2 - x1
            dy = y2 - y1
            segment_length = math.hypot(dx, dy)
            if segment_length <= _EPSILON:
                continue
            for obstacle_name, rect in obstacles.items():
                if obstacle_name in (owned.endpoint, f"@{owned.family_id}"):
                    continue
                clipped = self._segment_rect_interval(owned.segment, rect, padding=0.035)
                if clipped is None:
                    continue
                start, end = clipped
                if end - start <= _EPSILON:
                    continue
                covered_length = (end - start) * segment_length
                sample_count = max(1, int(math.ceil(covered_length / 0.14)))
                key = (owned.family_id, owned.endpoint, owned.index)
                for index in range(sample_count):
                    fraction = start + ((index + 0.5) * (end - start) / sample_count)
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
        for index, first in enumerate(segments):
            for second in segments[index + 1 :]:
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
        return [str(value).strip() for value in raw if str(value).strip()]

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


def _nearest_lanes(values: Iterable[float], anchor: float, limit: int) -> List[float]:
    unique = sorted(set(round(float(value), 7) for value in values))
    selected = sorted(unique, key=lambda value: (abs(value - anchor), value))[:limit]
    return sorted(selected)


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
