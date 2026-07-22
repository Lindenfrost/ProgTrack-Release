# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track pedigree layout pipeline.

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from statistics import median
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .pedigree_engine import PedigreeEngine

# Layout constants
DEFAULT_NODE_SPACING = 1.25
DEFAULT_GROUP_GAP = 0.90
DEFAULT_CLUSTER_GAP = 1.40
DEFAULT_COMPONENT_GAP = 3.80
DEFAULT_LEVEL_SPACING = 2.0
DEFAULT_BIRTHDATE_ROW_OFFSET_FACTOR = 0.28
DEFAULT_PARTNER_LINE_NUDGE = 0.35

VERTICAL_LAYOUT_PARTNER_NORMALIZED = "partner_normalized"
VERTICAL_LAYOUT_CHRONOLOGICAL = "chronological"


def birth_ordinal_to_month_y(ordinal: Optional[int]) -> Optional[float]:
    """Return an absolute, month-snapped calendar coordinate.

    Integer years mark January.  Every following month advances by 1/12,
    providing one stable linear transform for the complete graph while exact
    stored days remain available as labels/tooltips.
    """
    if ordinal is None:
        return None
    try:
        value = datetime.fromordinal(int(ordinal))
    except (OverflowError, TypeError, ValueError):
        return None
    return float(value.year) + ((int(value.month) - 1) / 12.0)


def compute_chronological_positions(
    base_positions: Dict[str, Tuple[float, float]],
    families: Dict[str, Dict[str, Any]],
    birth_ordinal_by_node: Dict[str, Optional[int]],
) -> Tuple[Dict[str, Tuple[float, float]], Set[str]]:
    """Keep pedigree X placement and replace Y with absolute birth months.

    Undated nodes are placed deterministically beside their dated family
    members.  The returned undated set lets the renderer draw an explicit
    local scale-break marker so fallback coordinates cannot be mistaken for a
    measured date.
    """
    if not base_positions:
        return {}, set()

    dated_y: Dict[str, float] = {}
    for node in base_positions:
        y = birth_ordinal_to_month_y(birth_ordinal_by_node.get(node))
        if y is not None:
            dated_y[node] = y

    adjacency: Dict[str, Set[str]] = {node: set() for node in base_positions}
    local_peer_dates: Dict[str, List[float]] = defaultdict(list)
    for family in families.values():
        mother = str(family.get("mother", "") or "").strip()
        father = str(family.get("father", "") or "").strip()
        children = [
            str(child or "").strip()
            for child in family.get("children", [])
            if str(child or "").strip() in base_positions
        ]
        members = [
            node
            for node in (
                mother,
                father,
                *children,
            )
            if node in base_positions
        ]
        for node in members:
            adjacency[node].update(member for member in members if member != node)
        for child in children:
            local_peer_dates[child].extend(
                dated_y[sibling]
                for sibling in children
                if sibling != child and sibling in dated_y
            )
        if mother in base_positions and father in dated_y:
            local_peer_dates[mother].append(dated_y[father])
        if father in base_positions and mother in dated_y:
            local_peer_dates[father].append(dated_y[mother])

    undated = set(base_positions) - set(dated_y)
    fallback_y: Dict[str, float] = {}
    global_center = median(dated_y.values()) if dated_y else 0.0
    base_values = [point[1] for point in base_positions.values()]
    base_center = median(base_values) if base_values else 0.0

    for node in sorted(undated, key=str.casefold):
        neighbour_dates = local_peer_dates.get(node) or [
            dated_y[other] for other in adjacency.get(node, set()) if other in dated_y
        ]
        if neighbour_dates:
            anchor = float(median(neighbour_dates))
        else:
            second_hop_dates = [
                dated_y[other]
                for neighbour in adjacency.get(node, set())
                for other in adjacency.get(neighbour, set())
                if other in dated_y
            ]
            if second_hop_dates:
                anchor = float(median(second_hop_dates))
            else:
                anchor = global_center + ((base_positions[node][1] - base_center) * 0.35)

        # Multiple undated members in one local family receive tiny stable
        # offsets so their markers remain individually selectable.
        occupied = [
            fallback_y[other]
            for other in adjacency.get(node, set())
            if other in fallback_y
        ]
        while any(abs(anchor - other_y) < 0.16 for other_y in occupied):
            anchor += 0.18
        fallback_y[node] = anchor

    positions = {
        node: (float(point[0]), dated_y.get(node, fallback_y.get(node, global_center)))
        for node, point in base_positions.items()
    }
    return positions, undated


def parse_complete_birth_date_ordinal(raw_value: Any) -> Optional[int]:
    """Parse complete supported birth-date values to a date ordinal."""
    if raw_value is None:
        return None

    if isinstance(raw_value, datetime):
        return raw_value.date().toordinal()
    if isinstance(raw_value, date):
        return raw_value.toordinal()

    if isinstance(raw_value, (int, float)):
        return None

    text = str(raw_value).strip()
    if not text:
        return None

    normalized = text.split("T", 1)[0].strip()
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(normalized, fmt).date().toordinal()
        except ValueError:
            continue
    return None


def nudge_nodes_off_child_line_segments(
    animal_positions: Dict[str, Tuple[float, float]],
    families: Dict[str, Dict[str, Any]],
    family_positions: Dict[str, Tuple[float, float]],
    protected_nodes: Optional[Set[str]] = None,
    *,
    nudge: float = DEFAULT_PARTNER_LINE_NUDGE,
    vertical_threshold: float = 0.18,
    horizontal_margin: float = 0.25,
) -> Dict[str, Tuple[float, float]]:
    """Move automatic non-family nodes away from horizontal child connector lines."""
    protected = set(protected_nodes or set())
    segments: List[Tuple[float, float, float, Set[str]]] = []

    for family_id, family in families.items():
        family_pos = family_positions.get(family_id)
        if family_pos is None:
            continue
        fx, _fy = family_pos
        mother = str(family.get("mother", "")).strip()
        father = str(family.get("father", "")).strip()
        excluded = {name for name in (mother, father) if name}
        for child in family.get("children", []):
            if child not in animal_positions:
                continue
            cx, cy = animal_positions[child]
            x_min = min(fx, cx) - horizontal_margin
            x_max = max(fx, cx) + horizontal_margin
            segments.append((cy, x_min, x_max, excluded | {child}))

    if not segments:
        return animal_positions

    adjusted = dict(animal_positions)
    for node in sorted(adjusted, key=str.lower):
        if node in protected:
            continue
        x, y = adjusted[node]
        shift = 0.0
        for seg_y, x_min, x_max, excluded in segments:
            if node in excluded:
                continue
            if x_min <= x <= x_max and abs((y + shift) - seg_y) <= vertical_threshold:
                shift -= nudge
        if shift:
            adjusted[node] = (x, y + shift)

    return adjusted


class GroupGrouper:
    """Groups nodes into sibling groups and singletons."""

    def __init__(self, node_set: Set[str], engine: PedigreeEngine):
        self.node_set = node_set
        self.engine = engine

    def group(self) -> Dict[str, Dict[str, Any]]:
        """Create full-sibling groups and singleton groups."""
        groups: Dict[str, Dict[str, Any]] = {}
        group_by_node: Dict[str, str] = {}
        temp_groups: Dict[Tuple[str, str], List[str]] = defaultdict(list)
        singleton_groups: List[str] = []

        for node in sorted(self.node_set, key=str.lower):
            mother, father = self._parents_of(node)
            if mother and father and mother != father and mother in self.node_set and father in self.node_set:
                temp_groups[(mother, father)].append(node)
            else:
                singleton_groups.append(node)

        # Create full-sibling groups
        for (mother, father), members in sorted(temp_groups.items(), key=lambda x: (x[0][0].lower(), x[0][1].lower())):
            gid = f"sib::{mother}::{father}"
            groups[gid] = {
                "id": gid,
                "members": sorted(members, key=str.lower),
                "parent_pair": (mother, father),
                "parent_set": {p for p in (mother, father) if p},
                "child_families": set(),
                "origin_family": self._family_node_id(mother, father),
            }
            for member in members:
                group_by_node[member] = gid

        # Create singleton groups
        for node in singleton_groups:
            mother, father = self._parents_of(node)
            known_parents = {p for p in (mother, father) if p and p in self.node_set}
            gid = f"solo::{node}"
            groups[gid] = {
                "id": gid,
                "members": [node],
                "parent_pair": (mother if mother in self.node_set else "", father if father in self.node_set else ""),
                "parent_set": known_parents,
                "child_families": set(),
                "origin_family": "",
            }
            group_by_node[node] = gid

        return groups, group_by_node

    def _parents_of(self, node: str) -> Tuple[str, str]:
        """Get genetic parents of a node."""
        parent_values = self.engine.child_to_parents.get(node, {})
        mother = str(parent_values.get("egg_donor", "")).strip()
        father = str(parent_values.get("sperm_donor", "")).strip()
        return mother, father

    @staticmethod
    def _family_node_id(mother: str, father: str) -> str:
        """Generate family node ID from parents."""
        m = mother or ""
        f = father or ""
        return f"fam::{m}::{f}"


class ComponentAnalyzer:
    """Analyzes connected components in the pedigree graph."""

    def __init__(self, node_set: Set[str], engine: PedigreeEngine):
        self.node_set = node_set
        self.engine = engine

    def analyze(self, groups: Dict[str, Dict[str, Any]]) -> Tuple[Dict[str, int], Dict[int, Set[str]]]:
        """Compute connected components using undirected parent/child edges."""
        undirected_adj: Dict[str, Set[str]] = defaultdict(set)

        for node in self.node_set:
            parent_values = self.engine.child_to_parents.get(node, {})
            mother = str(parent_values.get("egg_donor", "")).strip()
            father = str(parent_values.get("sperm_donor", "")).strip()
            for parent in (mother, father):
                if parent and parent in self.node_set and parent != node:
                    undirected_adj[node].add(parent)
                    undirected_adj[parent].add(node)

        component_by_node: Dict[str, int] = {}
        component_nodes: Dict[int, Set[str]] = defaultdict(set)
        component_idx = 0

        for seed in sorted(self.node_set, key=str.lower):
            if seed in component_by_node:
                continue
            stack = [seed]
            while stack:
                cur = stack.pop()
                if cur in component_by_node:
                    continue
                component_by_node[cur] = component_idx
                component_nodes[component_idx].add(cur)
                for nxt in undirected_adj.get(cur, set()):
                    if nxt not in component_by_node:
                        stack.append(nxt)
            component_idx += 1

        # Assign component to groups
        for gid, group in groups.items():
            comp_ids = [component_by_node.get(n, 0) for n in group["members"]]
            group["component"] = min(comp_ids) if comp_ids else 0

        return component_by_node, component_nodes


class RowAssigner:
    """Assigns rows to groups based on descendant depth."""

    def __init__(
        self,
        groups: Dict[str, Dict[str, Any]],
        usable_families: Dict[str, Dict[str, Any]],
        family_to_child_group: Dict[str, str],
    ):
        self.groups = groups
        self.usable_families = usable_families
        self.family_to_child_group = family_to_child_group

    def assign(self) -> Dict[str, int]:
        """Compute base rows from descendant depth with multi-pass alignment."""
        base_row_memo: Dict[str, int] = {}
        visiting: Set[str] = set()

        def _base_row(gid: str) -> int:
            if gid in base_row_memo:
                return base_row_memo[gid]
            if gid in visiting:
                return 0
            visiting.add(gid)
            child_rows: List[int] = []
            for family_id in self.groups[gid].get("child_families", set()):
                child_gid = self.family_to_child_group.get(family_id)
                if child_gid and child_gid != gid:
                    child_rows.append(_base_row(child_gid))
            visiting.remove(gid)
            row = (max(child_rows) + 1) if child_rows else 0
            base_row_memo[gid] = row
            return row

        group_row = {gid: _base_row(gid) for gid in self.groups}

        # Extract nodes elevated beyond their natural breeding row
        self._extract_elevated_nodes(group_row)

        # Recompute after extractions
        base_row_memo.clear()
        visiting.clear()
        group_row = {gid: _base_row(gid) for gid in self.groups}

        # Build half-sibling adjacency
        halfsib_adj = self._build_halfsib_adjacency()

        # Cluster and align rows
        self._align_cluster_rows(group_row, halfsib_adj)

        return group_row

    def _extract_elevated_nodes(self, group_row: Dict[str, int]) -> None:
        """Extract nodes that are elevated beyond their natural breeding row."""
        nodes_to_extract: List[Tuple[str, str]] = []

        for gid, group in list(self.groups.items()):
            if len(group["members"]) < 2:
                continue
            current_row = group_row.get(gid, 0)
            for node in list(group["members"]):
                node_child_rows: List[int] = []
                for family_id in list(group["child_families"]):
                    family = self.usable_families.get(family_id)
                    if not family:
                        continue
                    f_mother = str(family.get("mother", "")).strip()
                    f_father = str(family.get("father", "")).strip()
                    if node not in (f_mother, f_father):
                        continue
                    child_gid = self.family_to_child_group.get(family_id)
                    if child_gid and child_gid != gid:
                        node_child_rows.append(group_row.get(child_gid, 0))
                natural_row = (max(node_child_rows) + 1) if node_child_rows else 0
                if natural_row < current_row:
                    nodes_to_extract.append((node, gid))

        for node, old_gid in nodes_to_extract:
            self._extract_node_to_solo(node, old_gid)

    def _extract_node_to_solo(self, node: str, old_gid: str) -> None:
        """Extract a node from its group to a new solo group."""
        old_group = self.groups.get(old_gid)
        if not old_group or node not in old_group["members"]:
            return

        old_group["members"] = [m for m in old_group["members"] if m != node]

        families_to_move: Set[str] = set()
        for family_id in list(old_group["child_families"]):
            family = self.usable_families.get(family_id)
            if not family:
                continue
            f_mother = str(family.get("mother", "")).strip()
            f_father = str(family.get("father", "")).strip()
            if node in (f_mother, f_father):
                other = f_father if node == f_mother else f_mother
                if other not in old_group["members"]:
                    families_to_move.add(family_id)
        old_group["child_families"] -= families_to_move

        new_gid = f"solo::{node}"
        self.groups[new_gid] = {
            "id": new_gid,
            "members": [node],
            "parent_pair": old_group["parent_pair"],
            "parent_set": old_group["parent_set"],
            "component": old_group["component"],
            "child_families": families_to_move,
            "origin_family": old_group.get("origin_family", ""),
        }

    def _build_halfsib_adjacency(self) -> Dict[str, Set[str]]:
        """Build half-sibling adjacency between groups."""
        halfsib_adj: Dict[str, Set[str]] = defaultdict(set)
        group_ids = list(self.groups.keys())

        def _is_group_ancestor_of(ancestor_gid: str, descendant_gid: str) -> bool:
            visited: Set[str] = set()
            stack: List[str] = [ancestor_gid]
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                for fid in self.groups.get(cur, {}).get("child_families", set()):
                    cg = self.family_to_child_group.get(fid)
                    if cg == descendant_gid:
                        return True
                    if cg and cg not in visited:
                        stack.append(cg)
            return False

        for idx, gid_a in enumerate(group_ids):
            parents_a = set(self.groups[gid_a].get("parent_set", set()))
            if not parents_a:
                continue
            for gid_b in group_ids[idx + 1:]:
                parents_b = set(self.groups[gid_b].get("parent_set", set()))
                if not parents_b:
                    continue
                if len(parents_a & parents_b) == 1:
                    if not _is_group_ancestor_of(gid_a, gid_b) and not _is_group_ancestor_of(gid_b, gid_a):
                        halfsib_adj[gid_a].add(gid_b)
                        halfsib_adj[gid_b].add(gid_a)

        return halfsib_adj

    def _align_cluster_rows(self, group_row: Dict[str, int], halfsib_adj: Dict[str, Set[str]]) -> None:
        """Align rows within half-sibling clusters."""
        # Build clusters
        sibling_clusters: List[List[str]] = []
        assigned_groups: Set[str] = set()

        def _cluster_conflicts(candidate_gid: str, cluster_gids: List[str]) -> bool:
            for other_gid in cluster_gids:
                if self._is_group_ancestor_of(candidate_gid, other_gid):
                    return True
                if self._is_group_ancestor_of(other_gid, candidate_gid):
                    return True
            return False

        for seed_gid in sorted(self.groups.keys(), key=str.lower):
            if seed_gid in assigned_groups:
                continue

            cluster: List[str] = []
            queue: List[str] = [seed_gid]
            queued: Set[str] = {seed_gid}

            while queue:
                cur_gid = queue.pop(0)
                if cur_gid in assigned_groups:
                    continue
                if _cluster_conflicts(cur_gid, cluster):
                    continue

                cluster.append(cur_gid)
                assigned_groups.add(cur_gid)

                for nxt_gid in sorted(halfsib_adj.get(cur_gid, set()), key=str.lower):
                    if nxt_gid not in assigned_groups and nxt_gid not in queued:
                        queue.append(nxt_gid)
                        queued.add(nxt_gid)

            sibling_clusters.append(cluster)

        # Multi-pass row alignment
        changed = True
        safety = 0
        max_passes = len(self.groups) * 4 + 10

        while changed and safety < max_passes:
            safety += 1
            changed = False

            for cluster in sibling_clusters:
                target = max(group_row.get(gid, 0) for gid in cluster)
                for gid in cluster:
                    if group_row.get(gid, 0) < target:
                        group_row[gid] = target
                        changed = True

            for gid in self.groups:
                child_rows = [
                    group_row.get(child_gid, 0)
                    for family_id in self.groups[gid].get("child_families", set())
                    for child_gid in [self.family_to_child_group.get(family_id)]
                    if child_gid and child_gid != gid
                ]
                desired = (max(child_rows) + 1) if child_rows else 0
                if desired > group_row.get(gid, 0):
                    group_row[gid] = desired
                    changed = True

        if safety >= max_passes:
            # Reset to base rows on failure
            base_row_memo: Dict[str, int] = {}
            visiting: Set[str] = set()

            def _base_row_simple(gid: str) -> int:
                if gid in base_row_memo:
                    return base_row_memo[gid]
                if gid in visiting:
                    return 0
                visiting.add(gid)
                child_rows: List[int] = []
                for family_id in self.groups[gid].get("child_families", set()):
                    child_gid = self.family_to_child_group.get(family_id)
                    if child_gid and child_gid != gid:
                        child_rows.append(_base_row_simple(child_gid))
                visiting.remove(gid)
                row = (max(child_rows) + 1) if child_rows else 0
                base_row_memo[gid] = row
                return row

            for gid in self.groups:
                group_row[gid] = _base_row_simple(gid)

    def _is_group_ancestor_of(self, ancestor_gid: str, descendant_gid: str) -> bool:
        """Check if one group is an ancestor of another."""
        visited: Set[str] = set()
        stack: List[str] = [ancestor_gid]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            for fid in self.groups.get(cur, {}).get("child_families", set()):
                cg = self.family_to_child_group.get(fid)
                if cg == descendant_gid:
                    return True
                if cg and cg not in visited:
                    stack.append(cg)
        return False


class XPlacer:
    """Computes X coordinates for nodes within rows."""

    def __init__(
        self,
        groups: Dict[str, Dict[str, Any]],
        group_row: Dict[str, int],
        usable_families: Dict[str, Dict[str, Any]],
        family_to_child_group: Dict[str, str],
        locked_x: Dict[str, float],
        node_spacing: float = DEFAULT_NODE_SPACING,
        group_gap: float = DEFAULT_GROUP_GAP,
        cluster_gap: float = DEFAULT_CLUSTER_GAP,
    ):
        self.groups = groups
        self.group_row = group_row
        self.usable_families = usable_families
        self.family_to_child_group = family_to_child_group
        self.locked_x = locked_x
        self.node_spacing = node_spacing
        self.group_gap = group_gap
        self.cluster_gap = cluster_gap
        self.min_node_spacing = node_spacing * 0.98

    def place(
        self,
        row_groups_by_component: Dict[int, Dict[int, List[str]]],
        component_nodes: Dict[int, Set[str]],
        get_tie_key: Callable[[str], Tuple[int, int, str]],
    ) -> Dict[int, Dict[str, float]]:
        """Compute X positions for all nodes."""
        component_xmaps: Dict[int, Dict[str, float]] = {cid: {} for cid in component_nodes}

        for comp_id in sorted(component_nodes.keys()):
            rows = sorted(row_groups_by_component.get(comp_id, {}).keys())
            if not rows:
                continue

            xmap = component_xmaps[comp_id]

            # Initial placement
            for row in rows:
                self._place_row(comp_id, row, xmap, row_groups_by_component, get_tie_key)

            # Refinement passes
            for _ in range(8):
                for row in range(1, max(rows) + 1):
                    if row in row_groups_by_component.get(comp_id, {}):
                        self._place_row(comp_id, row, xmap, row_groups_by_component, get_tie_key)
                for row in range(max(rows) - 1, -1, -1):
                    if row in row_groups_by_component.get(comp_id, {}):
                        self._place_row(comp_id, row, xmap, row_groups_by_component, get_tie_key)

        return component_xmaps

    def _place_row(
        self,
        comp_id: int,
        row: int,
        xmap: Dict[str, float],
        row_groups_by_component: Dict[int, Dict[int, List[str]]],
        get_tie_key: Callable[[str], Tuple[int, int, str]],
    ) -> None:
        """Place all groups in a single row."""
        row_group_ids = list(row_groups_by_component.get(comp_id, {}).get(row, []))
        if not row_group_ids:
            return

        clusters = self._clusters_for_row(comp_id, row, row_groups_by_component)

        # Sort clusters
        ordered_clusters: List[Tuple[float, Tuple[int, int, str], List[str]]] = []
        for cluster in clusters:
            ordered_cluster = sorted(
                cluster,
                key=lambda gid: (
                    self._group_target(gid, xmap) is None,
                    self._group_target(gid, xmap) if self._group_target(gid, xmap) is not None else 0.0,
                    min((get_tie_key(n) for n in self.groups[gid]["members"]), default=(1, 9999, gid.lower())),
                )
            )
            cluster_targets = [self._group_target(gid, xmap) for gid in ordered_cluster if self._group_target(gid, xmap) is not None]
            cluster_anchor = self._median(cluster_targets)
            cluster_key = min((get_tie_key(n) for gid in ordered_cluster for n in self.groups[gid]["members"]), default=(1, 9999, ""))
            ordered_clusters.append((cluster_anchor if cluster_anchor is not None else 0.0, cluster_key, ordered_cluster))

        ordered_clusters.sort(key=lambda item: (item[0], item[1]))

        # Place clusters
        current_x = 0.0
        first_cluster = True

        for _cluster_anchor, _cluster_key, cluster_groups in ordered_clusters:
            cluster_members: List[Tuple[str, List[str], float]] = []
            cluster_width = 0.0

            for idx, gid in enumerate(cluster_groups):
                ordered_members = self._order_members_in_group(gid, xmap, get_tie_key)
                self.groups[gid]["members"] = ordered_members
                width = self._group_width(gid)
                cluster_members.append((gid, ordered_members, width))
                cluster_width += width
                if idx > 0:
                    cluster_width += self.group_gap

            cluster_target_vals = [
                self._group_target(gid, xmap)
                for gid, _members, _width in cluster_members
                if self._group_target(gid, xmap) is not None
            ]
            cluster_target = self._median(cluster_target_vals)
            if cluster_target is None:
                cluster_target = current_x + (cluster_width / 2.0)

            if first_cluster:
                cluster_left = cluster_target - (cluster_width / 2.0)
                first_cluster = False
            else:
                cluster_left = max(cluster_target - (cluster_width / 2.0), current_x + self.cluster_gap)

            cursor = cluster_left
            for gid, ordered_members, width in cluster_members:
                center = cursor + (width / 2.0)

                # Respect manual x locks
                locked_centres: List[float] = []
                for idx, node in enumerate(ordered_members):
                    slot_offset = (idx - (len(ordered_members) - 1) / 2.0) * self.node_spacing
                    if node in self.locked_x:
                        locked_centres.append(self.locked_x[node] - slot_offset)
                if locked_centres:
                    center = self._median(locked_centres) or center

                base_slots = [
                    center + (idx - (len(ordered_members) - 1) / 2.0) * self.node_spacing
                    for idx in range(len(ordered_members))
                ]

                xs: List[float] = []
                for idx, node in enumerate(ordered_members):
                    xs.append(self.locked_x[node] if node in self.locked_x else base_slots[idx])

                for idx in range(1, len(xs)):
                    if ordered_members[idx] in self.locked_x:
                        continue
                    xs[idx] = max(xs[idx], xs[idx - 1] + self.min_node_spacing)

                for idx in range(len(xs) - 2, -1, -1):
                    if ordered_members[idx] in self.locked_x:
                        continue
                    xs[idx] = min(xs[idx], xs[idx + 1] - self.min_node_spacing)

                for idx, node in enumerate(ordered_members):
                    xmap[node] = xs[idx]

                cursor += width + self.group_gap

            current_x = max(current_x, cluster_left + cluster_width)

        # Final de-overlap pass
        self._deoverlap_row(row_group_ids, xmap)

    def _clusters_for_row(
        self,
        comp_id: int,
        row: int,
        row_groups_by_component: Dict[int, Dict[int, List[str]]],
    ) -> List[List[str]]:
        """Find connected clusters within a row."""
        row_group_ids = list(row_groups_by_component.get(comp_id, {}).get(row, []))
        pending = set(row_group_ids)
        clusters: List[List[str]] = []

        for gid in row_group_ids:
            if gid not in pending:
                continue

            stack = [gid]
            cluster: List[str] = []

            while stack:
                cur = stack.pop()
                if cur not in pending:
                    continue
                pending.remove(cur)
                cluster.append(cur)

                # Add half-siblings in same row
                for nxt in self._get_halfsibs(cur):
                    if nxt in pending and self.group_row.get(nxt, -1) == row:
                        if self.groups.get(nxt, {}).get("component") == comp_id:
                            stack.append(nxt)

            clusters.append(cluster)

        return clusters

    def _get_halfsibs(self, gid: str) -> Set[str]:
        """Get half-sibling groups for a group."""
        parents = set(self.groups[gid].get("parent_set", set()))
        if not parents:
            return set()

        result: Set[str] = set()
        for other_gid, other_group in self.groups.items():
            if other_gid == gid:
                continue
            other_parents = set(other_group.get("parent_set", set()))
            if len(parents & other_parents) == 1:
                result.add(other_gid)

        return result

    def _order_members_in_group(
        self,
        gid: str,
        xmap: Dict[str, float],
        get_tie_key: Callable[[str], Tuple[int, int, str]],
    ) -> List[str]:
        """Sort members within a group by target position and tie key."""
        members = list(self.groups[gid]["members"])

        def _member_target(node: str) -> Optional[float]:
            if node in self.locked_x:
                return self.locked_x[node]

            vals: List[float] = []

            for family_id in self.groups[gid].get("child_families", set()):
                family = self.usable_families.get(family_id)
                if not family:
                    continue
                mother = str(family.get("mother", "")).strip()
                father = str(family.get("father", "")).strip()
                if node not in {mother, father}:
                    continue
                center = self._family_center(family_id, xmap)
                if center is not None:
                    vals.extend([center, center])

            origin_family = self.groups[gid].get("origin_family", "")
            if origin_family in self.usable_families:
                center = self._family_center(origin_family, xmap)
                if center is not None:
                    vals.append(center)

            return self._median(vals)

        members.sort(
            key=lambda node: (
                _member_target(node) is None,
                _member_target(node) if _member_target(node) is not None else 0.0,
                get_tie_key(node),
            )
        )
        return members

    def _group_target(self, gid: str, xmap: Dict[str, float]) -> Optional[float]:
        """Compute target X position for a group."""
        vals: List[float] = []

        for family_id in self.groups[gid].get("child_families", set()):
            center = self._family_center(family_id, xmap)
            if center is not None:
                vals.extend([center, center])

        origin_family = self.groups[gid].get("origin_family", "")
        if origin_family in self.usable_families:
            center = self._family_center(origin_family, xmap)
            if center is not None:
                vals.append(center)

        member_center = self._group_center(gid, xmap)
        if member_center is not None:
            vals.append(member_center)

        return self._median(vals)

    def _family_center(self, family_id: str, xmap: Dict[str, float]) -> Optional[float]:
        """Compute center X position for a family node."""
        family = self.usable_families.get(family_id)
        if not family:
            return None

        child_gid = self.family_to_child_group.get(family_id)
        child_center = self._group_center(child_gid, xmap) if child_gid else None

        mother = str(family.get("mother", "")).strip()
        father = str(family.get("father", "")).strip()
        parent_xs = [xmap[p] for p in (mother, father) if p in xmap]
        parent_mid = self._mean(parent_xs)

        if child_center is not None and parent_mid is not None:
            return (child_center + parent_mid) / 2.0
        if child_center is not None:
            return child_center
        if parent_mid is not None:
            return parent_mid
        return None

    def _group_center(self, gid: str, xmap: Dict[str, float]) -> Optional[float]:
        """Compute center X position for a group."""
        xs = [xmap[n] for n in self.groups[gid]["members"] if n in xmap]
        return self._mean(xs)

    def _group_width(self, gid: str) -> float:
        """Compute width of a group."""
        members = self.groups[gid]["members"]
        return (len(members) - 1) * self.node_spacing if len(members) > 1 else 0.0

    def _deoverlap_row(self, row_group_ids: List[str], xmap: Dict[str, float]) -> None:
        """Final de-overlap pass across a row."""
        ordered_nodes = [
            node
            for gid in sorted(row_group_ids, key=lambda _gid: self._group_center(_gid, xmap) or 0.0)
            for node in self.groups[gid]["members"]
            if node in xmap
        ]

        # Left-to-right pass
        prev_x = -float("inf")
        for node in ordered_nodes:
            x = self.locked_x[node] if node in self.locked_x else xmap[node]
            if node not in self.locked_x:
                x = max(x, prev_x + self.min_node_spacing)
                xmap[node] = x
            prev_x = xmap[node]

        # Right-to-left pass
        next_x = float("inf")
        for node in reversed(ordered_nodes):
            x = self.locked_x[node] if node in self.locked_x else xmap[node]
            if node not in self.locked_x:
                x = min(x, next_x - self.min_node_spacing)
                xmap[node] = x
            next_x = xmap[node]

        # Restore locked positions
        for node in ordered_nodes:
            if node in self.locked_x:
                xmap[node] = self.locked_x[node]

    @staticmethod
    def _median(values: List[float]) -> Optional[float]:
        """Compute median of a list of values."""
        cleaned = sorted(float(v) for v in values if v is not None)
        if not cleaned:
            return None
        mid = len(cleaned) // 2
        if len(cleaned) % 2 == 1:
            return cleaned[mid]
        return (cleaned[mid - 1] + cleaned[mid]) / 2.0

    @staticmethod
    def _mean(values: List[float]) -> Optional[float]:
        """Compute mean of a list of values."""
        cleaned = [float(v) for v in values if v is not None]
        if not cleaned:
            return None
        return sum(cleaned) / len(cleaned)


class LayoutPipeline:
    """Orchestrates the layout computation pipeline."""

    def __init__(
        self,
        node_spacing: float = DEFAULT_NODE_SPACING,
        group_gap: float = DEFAULT_GROUP_GAP,
        cluster_gap: float = DEFAULT_CLUSTER_GAP,
        component_gap: float = DEFAULT_COMPONENT_GAP,
        level_spacing: float = DEFAULT_LEVEL_SPACING,
    ):
        self.node_spacing = node_spacing
        self.group_gap = group_gap
        self.cluster_gap = cluster_gap
        self.component_gap = component_gap
        self.level_spacing = level_spacing

    def compute_positions(
        self,
        nodes: Set[str],
        levels: Dict[str, int],
        engine: PedigreeEngine,
        families: Optional[Dict[str, Dict[str, Any]]] = None,
        locked_positions: Optional[Dict[str, Tuple[float, float]]] = None,
        birth_year_by_node: Optional[Dict[str, Optional[int]]] = None,
        birth_ordinal_by_node: Optional[Dict[str, Optional[int]]] = None,
        birthdate_height_layout: bool = False,
    ) -> Dict[str, Tuple[float, float]]:
        """Compute positions through the full pipeline."""
        if not nodes:
            return {}

        families = families or {}
        locked_positions = locked_positions or {}

        # Extract locked X positions
        locked_x: Dict[str, float] = {
            node: float(pos[0])
            for node, pos in locked_positions.items()
            if node in nodes
        }

        # Get tie key function
        def get_tie_key(node: str) -> Tuple[int, int, str]:
            ordinal = birth_ordinal_by_node.get(node) if birth_ordinal_by_node else None
            if ordinal is not None:
                return 0, int(ordinal), node.lower()
            year = birth_year_by_node.get(node) if birth_year_by_node else None
            if year is None:
                return 1, 9999, node.lower()
            return 0, int(year), node.lower()

        # Phase 1: Group nodes
        grouper = GroupGrouper(nodes, engine)
        groups, group_by_node = grouper.group()

        # Phase 2: Analyze components
        component_analyzer = ComponentAnalyzer(nodes, engine)
        component_by_node, component_nodes = component_analyzer.analyze(groups)

        # Phase 3: Build family mappings
        usable_families: Dict[str, Dict[str, Any]] = {}
        family_to_child_group: Dict[str, str] = {}

        for family_id, family in families.items():
            family_children = [c for c in family.get("children", []) if c in nodes]
            if not family_children:
                continue
            usable_families[family_id] = family
            child_gid = group_by_node.get(family_children[0])
            if child_gid:
                family_to_child_group[family_id] = child_gid
            mother = str(family.get("mother", "")).strip()
            father = str(family.get("father", "")).strip()
            for parent in (mother, father):
                pgid = group_by_node.get(parent)
                if pgid:
                    groups[pgid]["child_families"].add(family_id)

        # Phase 4: Assign rows
        row_assigner = RowAssigner(groups, usable_families, family_to_child_group)
        group_row = row_assigner.assign()

        max_row = max(group_row.values(), default=0)

        # Build row groups by component
        row_groups_by_component: Dict[int, Dict[int, List[str]]] = defaultdict(lambda: defaultdict(list))
        for gid, group in groups.items():
            comp_id = int(group.get("component", 0))
            row = group_row.get(gid, 0)
            row_groups_by_component[comp_id][row].append(gid)

        # Sort groups within rows
        for comp_id in list(row_groups_by_component.keys()):
            for row in list(row_groups_by_component[comp_id].keys()):
                row_groups_by_component[comp_id][row].sort(
                    key=lambda gid: min((get_tie_key(n) for n in groups[gid]["members"]), default=(1, 9999, gid.lower()))
                )

        # Phase 5: Place X coordinates
        x_placer = XPlacer(
            groups,
            group_row,
            usable_families,
            family_to_child_group,
            locked_x,
            self.node_spacing,
            self.group_gap,
            self.cluster_gap,
        )
        component_xmaps = x_placer.place(row_groups_by_component, component_nodes, get_tie_key)

        # Phase 6: Pack components
        component_bounds: Dict[int, Tuple[float, float]] = {}
        for comp_id, nodes_in_comp in component_nodes.items():
            xs = [component_xmaps[comp_id][node] for node in nodes_in_comp if node in component_xmaps[comp_id]]
            if not xs:
                xs = [0.0]
            component_bounds[comp_id] = (min(xs), max(xs))

        # Sort components
        def component_sort_key(comp_id: int) -> Tuple[int, int, str]:
            comp_groups = [gid for gid, group in groups.items() if group.get("component") == comp_id]
            top_groups = [gid for gid in comp_groups if group_row.get(gid, 0) == 0]
            seed_groups = top_groups or comp_groups
            seed_nodes = [node for gid in seed_groups for node in groups[gid]["members"]]
            return min((get_tie_key(node) for node in seed_nodes), default=(1, 9999, f"component-{comp_id}"))

        ordered_components = sorted(component_nodes.keys(), key=component_sort_key)
        component_shift: Dict[int, float] = {}
        current_left: Optional[float] = None

        for comp_id in ordered_components:
            left, right = component_bounds[comp_id]
            if current_left is None:
                component_shift[comp_id] = -left
                current_left = right - left
            else:
                shift = (current_left + self.component_gap) - left
                component_shift[comp_id] = shift
                current_left = right + shift

        birthdate_y_offset = self._compute_birthdate_y_offsets(
            groups,
            birth_ordinal_by_node or {},
        ) if birthdate_height_layout else {}

        # Phase 7: Combine into final positions
        positions: Dict[str, Tuple[float, float]] = {}
        for node in nodes:
            gid = group_by_node.get(node)
            row = group_row.get(gid, 0) if gid else 0
            y = float(max_row - row) * self.level_spacing
            y += birthdate_y_offset.get(node, 0.0)
            comp_id = component_by_node.get(node, 0)
            x = component_xmaps.get(comp_id, {}).get(node, 0.0) + component_shift.get(comp_id, 0.0)

            if node in locked_x:
                x = locked_x[node]

            positions[node] = (x, y)

        # A single horizontal strip wastes most of the canvas when a species
        # contains one pedigree plus many unrelated sample animals.  Pack
        # disconnected pedigrees into shelves so each family can use the full
        # viewport width; relationships inside a component remain untouched.
        if len(component_nodes) > 1:
            bounds: Dict[int, Tuple[float, float, float, float]] = {}
            for comp_id, members in component_nodes.items():
                points = [positions[node] for node in members if node in positions]
                if not points:
                    continue
                xs = [point[0] for point in points]
                ys = [point[1] for point in points]
                bounds[comp_id] = (min(xs), max(xs), min(ys), max(ys))
            ordered = sorted(
                bounds,
                key=lambda comp_id: (
                    -len(component_nodes.get(comp_id, set())),
                    -(bounds[comp_id][1] - bounds[comp_id][0]),
                    comp_id,
                ),
            )
            widest = max(
                (bounds[comp_id][1] - bounds[comp_id][0] for comp_id in ordered),
                default=0.0,
            )
            shelf_width = max(18.0, widest * 1.15)
            cursor_x = 0.0
            cursor_y = 0.0
            shelf_height = 0.0
            placements: Dict[int, Tuple[float, float]] = {}
            for comp_id in ordered:
                left, right, bottom, top = bounds[comp_id]
                # Reserve label-sized space even for singletons; otherwise a
                # shelf of unrelated sample animals becomes an unreadable knot.
                width = max(self.node_spacing * 3.1, right - left)
                height = max(self.level_spacing, top - bottom)
                if cursor_x and cursor_x + width > shelf_width:
                    cursor_y += shelf_height + self.component_gap
                    cursor_x = 0.0
                    shelf_height = 0.0
                placements[comp_id] = (cursor_x - left, -cursor_y - bottom)
                cursor_x += width + self.component_gap
                shelf_height = max(shelf_height, height)
            for comp_id, members in component_nodes.items():
                shift_x, shift_y = placements.get(comp_id, (0.0, 0.0))
                for node in members:
                    if node in positions:
                        x, y = positions[node]
                        positions[node] = (x + shift_x, y + shift_y)

        return positions

    def _compute_birthdate_y_offsets(
        self,
        groups: Dict[str, Dict[str, Any]],
        birth_ordinal_by_node: Dict[str, Optional[int]],
    ) -> Dict[str, float]:
        offsets: Dict[str, float] = {}
        max_offset = min(self.level_spacing * DEFAULT_BIRTHDATE_ROW_OFFSET_FACTOR, self.level_spacing * 0.40)

        for group in groups.values():
            members = list(group.get("members", []))
            dated = [
                (node, birth_ordinal_by_node.get(node))
                for node in members
                if birth_ordinal_by_node.get(node) is not None
            ]
            unique_dates = sorted({int(ordinal) for _node, ordinal in dated if ordinal is not None})
            if len(unique_dates) < 2:
                continue

            oldest = unique_dates[0]
            youngest = unique_dates[-1]
            span = youngest - oldest
            if span <= 0:
                continue
            midpoint = oldest + (span / 2.0)

            for node, ordinal in dated:
                if ordinal is None:
                    continue
                normalized = (float(ordinal) - midpoint) / (span / 2.0)
                offsets[node] = normalized * max_offset

        return offsets
