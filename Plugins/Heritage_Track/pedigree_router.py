# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track semantic pedigree connector router.

from __future__ import annotations

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
        raise ValueError("Pedigree routes must be orthogonal")


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
        node_gap: float = 0.28,
        route_clearance: float = 0.16,
        junction_clearance: float = 0.30,
    ):
        self.automatic_x_scale = max(1.0, float(automatic_x_scale))
        self.node_gap = max(0.05, float(node_gap))
        self.route_clearance = max(0.05, float(route_clearance))
        self.junction_clearance = max(0.15, float(junction_clearance))

    def plan(
        self,
        animal_positions: Mapping[str, Point],
        families: Mapping[str, Mapping[str, object]],
        *,
        labels: Optional[Mapping[str, str]] = None,
        protected_nodes: Optional[Set[str]] = None,
        show_inbreeding: bool = True,
    ) -> RoutePlan:
        labels = labels or {}
        protected = set(protected_nodes or set())
        adjusted = self._spread_nodes(animal_positions, labels, protected, show_inbreeding)
        animal_obstacles = self.node_obstacles(adjusted, labels, show_inbreeding)
        family_positions = self._place_junctions(
            adjusted,
            families,
            animal_obstacles,
        )
        family_members = self._family_members(adjusted, families)

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

        ordered_families = sorted(
            family_positions,
            key=lambda fid: (
                round(family_positions[fid][1], 6),
                round(family_positions[fid][0], 6),
                fid.casefold(),
            ),
        )
        for family_id in ordered_families:
            family = families.get(family_id, {})
            endpoint_routes: Dict[str, List[Point]] = {}
            endpoints = self._ordered_endpoints(family, adjusted)
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
                )
                endpoint_routes[endpoint] = path
                if path_has_overlap:
                    unresolved.append(
                        f"{family_id}: route to {endpoint} shares a segment with another family"
                    )
                if path_hits_obstacle:
                    unresolved.append(
                        f"{family_id}: route to {endpoint} intersects a foreign render obstacle"
                    )
                for index, segment in enumerate(_path_segments(path)):
                    new_owned.append(_OwnedSegment(family_id, endpoint, index, segment))

            routes[family_id] = endpoint_routes
            owned_segments.extend(new_owned)

        crossing_gaps, crossing_problems = self._find_crossing_gaps(
            owned_segments,
            adjusted,
        )
        unresolved.extend(crossing_problems)

        plan = RoutePlan(
            animal_positions=adjusted,
            family_positions=family_positions,
            family_members=family_members,
            routes=routes,
            crossing_gaps=crossing_gaps,
            unresolved=sorted(set(unresolved)),
        )
        plan.unresolved.extend(
            problem
            for problem in self.validate_plan(
                plan,
                families,
                labels=labels,
                show_inbreeding=show_inbreeding,
            )
            if problem not in plan.unresolved
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
            label_half_width = max(0.36, (len(label) * 0.048) + 0.12)
            bottom_offset = 0.78 if show_inbreeding else 0.56
            obstacles[node] = Rect(
                x - label_half_width,
                x + label_half_width,
                y - bottom_offset,
                y + 0.34,
            )
        return obstacles

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

        for family_id, endpoint_routes in plan.routes.items():
            expected = set(self._ordered_endpoints(families.get(family_id, {}), plan.animal_positions))
            if set(endpoint_routes) != expected:
                problems.append(f"{family_id}: routed endpoints do not match semantic family members")
            junction = plan.family_positions.get(family_id)
            if junction is None:
                problems.append(f"{family_id}: missing family junction")
                continue
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
                for index, segment in enumerate(segments):
                    for node, rect in animal_obstacles.items():
                        if node == endpoint and index == len(segments) - 1:
                            continue
                        if rect.intersects(segment, margin=0.01):
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

    def _spread_nodes(
        self,
        positions: Mapping[str, Point],
        labels: Mapping[str, str],
        protected: Set[str],
        show_inbreeding: bool,
    ) -> Dict[str, Point]:
        adjusted = {node: (float(point[0]), float(point[1])) for node, point in positions.items()}
        if not adjusted:
            return adjusted

        automatic = [node for node in adjusted if node not in protected]
        if automatic and not protected:
            center = sum(adjusted[node][0] for node in automatic) / len(automatic)
            adjusted = {
                node: (center + ((x - center) * self.automatic_x_scale), y)
                for node, (x, y) in adjusted.items()
            }

        rows = self._cluster_rows(adjusted)
        for row in rows:
            self._deoverlap_row(adjusted, row, labels, protected, show_inbreeding)
        return adjusted

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
            return max(0.36, (len(label) * 0.048) + 0.12)

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
    ) -> Dict[str, Point]:
        grouped: Dict[Tuple[float, float], List[Tuple[str, float, float, float]]] = {}
        collapsed: List[Tuple[str, Point]] = []

        for family_id in sorted(families, key=str.casefold):
            family = families[family_id]
            parents = [node for node in self._parents(family) if node in positions]
            children = [node for node in self._children(family) if node in positions]
            if not parents:
                continue
            if not children:
                collapsed.append((family_id, self._collapsed_junction(parents, positions)))
                continue
            parent_x = sum(positions[node][0] for node in parents) / len(parents)
            parent_y = sum(positions[node][1] for node in parents) / len(parents)
            child_x = sum(positions[node][0] for node in children) / len(children)
            child_y = sum(positions[node][1] for node in children) / len(children)
            base_x = (parent_x + child_x) / 2.0
            key = (round(parent_y, 5), round(child_y, 5))
            grouped.setdefault(key, []).append((family_id, base_x, parent_y, child_y))

        raw: List[Tuple[str, Point]] = list(collapsed)
        for entries in grouped.values():
            entries.sort(key=lambda item: (item[1], item[0].casefold()))
            count = len(entries)
            for index, (family_id, base_x, parent_y, child_y) in enumerate(entries):
                fraction = 0.50 if count == 1 else 0.36 + (0.28 * index / (count - 1))
                y = parent_y + ((child_y - parent_y) * fraction)
                raw.append((family_id, (base_x, y)))

        placed: Dict[str, Point] = {}
        for family_id, base in sorted(raw, key=lambda item: (item[1][1], item[1][0], item[0].casefold())):
            placed[family_id] = self._free_junction_point(base, obstacles, placed)
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
    ) -> Point:
        base_x, base_y = base
        x_candidates = [base_x]
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

        def score(x: float) -> Tuple[float, float]:
            point = (x, base_y)
            blocked = sum(1 for rect in obstacles.values() if rect.contains(point, margin=0.04))
            crowded = sum(
                1
                for other in placed.values()
                if abs(other[0] - x) < (self.junction_clearance * 2.0)
                and abs(other[1] - base_y) < (self.junction_clearance * 2.0)
            )
            return (blocked * 10000.0) + (crowded * 1500.0) + abs(x - base_x), x

        best_x = min(sorted(set(round(value, 7) for value in x_candidates)), key=score)
        return float(best_x), float(base_y)

    def _route_endpoint(
        self,
        family_id: str,
        endpoint: str,
        start: Point,
        end: Point,
        obstacles: Mapping[str, Rect],
        owned_segments: Sequence[_OwnedSegment],
    ) -> Tuple[List[Point], bool, bool]:
        preferred_y = start[1] + ((end[1] - start[1]) * 0.58)
        candidates = self._candidate_paths(start, end, preferred_y, obstacles.values())
        scored: List[Tuple[Tuple[float, int, Tuple[Point, ...]], List[Point], bool, bool]] = []
        for path in candidates:
            score, overlap, obstacle_hit = self._score_path(
                family_id,
                endpoint,
                path,
                obstacles,
                owned_segments,
            )
            key = (score, len(path), tuple((round(x, 7), round(y, 7)) for x, y in path))
            scored.append((key, path, overlap, obstacle_hit))
        _key, best_path, overlap, obstacle_hit = min(scored, key=lambda item: item[0])
        return best_path, overlap, obstacle_hit

    def _candidate_paths(
        self,
        start: Point,
        end: Point,
        preferred_y: float,
        obstacles: Iterable[Rect],
    ) -> List[List[Point]]:
        sx, sy = start
        ex, ey = end
        raw: List[List[Point]] = [
            [start, (sx, ey), end],
            [start, (ex, sy), end],
        ]
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

        for y in sorted(y_candidates):
            raw.append([start, (sx, y), (ex, y), end])
        for x in sorted(x_candidates):
            raw.append([start, (x, sy), (x, ey), end])

        unique: Dict[Tuple[Point, ...], List[Point]] = {}
        for path in raw:
            simplified = _simplify_path(path)
            if len(simplified) < 2:
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
            for other in owned_segments:
                relation, point = _segment_relation(segment, other.segment)
                if relation == "none":
                    continue
                if other.endpoint == endpoint and point is not None and _points_equal(point, path[-1]):
                    continue
                if relation == "overlap":
                    overlap = True
                    score += 8000.0
                else:
                    score += 850.0
        return score, overlap, obstacle_hit

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
        if point is None or first.endpoint != second.endpoint:
            return False
        animal_point = animal_positions.get(first.endpoint)
        return animal_point is not None and _points_equal(point, animal_point)

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
    return sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(path, path[1:]))


def _segment_relation(first: Segment, second: Segment) -> Tuple[str, Optional[Point]]:
    (ax1, ay1), (ax2, ay2) = first
    (bx1, by1), (bx2, by2) = second
    first_vertical = abs(ax1 - ax2) <= _EPSILON
    second_vertical = abs(bx1 - bx2) <= _EPSILON

    if first_vertical and second_vertical:
        if abs(ax1 - bx1) > _EPSILON or not _ranges_overlap(ay1, ay2, by1, by2):
            return "none", None
        overlap_low = max(min(ay1, ay2), min(by1, by2))
        overlap_high = min(max(ay1, ay2), max(by1, by2))
        if overlap_high - overlap_low > _EPSILON:
            return "overlap", None
        return "cross", (ax1, overlap_low)

    if not first_vertical and not second_vertical:
        if abs(ay1 - by1) > _EPSILON or not _ranges_overlap(ax1, ax2, bx1, bx2):
            return "none", None
        overlap_low = max(min(ax1, ax2), min(bx1, bx2))
        overlap_high = min(max(ax1, ax2), max(bx1, bx2))
        if overlap_high - overlap_low > _EPSILON:
            return "overlap", None
        return "cross", (overlap_low, ay1)

    vertical = first if first_vertical else second
    horizontal = second if first_vertical else first
    vx = vertical[0][0]
    hy = horizontal[0][1]
    if _ranges_overlap(vertical[0][1], vertical[1][1], hy, hy) and _ranges_overlap(
        horizontal[0][0], horizontal[1][0], vx, vx
    ):
        return "cross", (vx, hy)
    return "none", None


def _point_is_interior(segment: Segment, point: Point) -> bool:
    return not _points_equal(segment[0], point) and not _points_equal(segment[1], point)


def _split_segment_at_gaps(segment: Segment, gaps: Sequence[Point], radius: float) -> List[Segment]:
    if not gaps:
        return [segment]
    (x1, y1), (x2, y2) = segment
    horizontal = abs(y1 - y2) <= _EPSILON
    direction = 1.0 if (x2 >= x1 if horizontal else y2 >= y1) else -1.0
    start_value = x1 if horizontal else y1
    end_value = x2 if horizontal else y2
    values = sorted(
        (point[0] if horizontal else point[1] for point in gaps),
        reverse=direction < 0,
    )
    cursor = start_value
    output: List[Segment] = []

    def point_at(value: float) -> Point:
        return (value, y1) if horizontal else (x1, value)

    for value in values:
        before = value - (direction * radius)
        after = value + (direction * radius)
        if direction * (before - cursor) > _EPSILON:
            output.append((point_at(cursor), point_at(before)))
        cursor = after
    if direction * (end_value - cursor) > _EPSILON:
        output.append((point_at(cursor), point_at(end_value)))
    return output
