# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track pedigree graph engine.

from __future__ import annotations

from collections import defaultdict, deque
import heapq
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple


ParentMap = Dict[str, Dict[str, str]]
GENETIC_PARENT_KEYS = ("egg_donor", "sperm_donor")


def _norm_name(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _iter_genetic_parent_names(parent_values: Dict[str, str]) -> Iterable[str]:
    for key in GENETIC_PARENT_KEYS:
        yield _norm_name(parent_values.get(key, ""))


class PedigreeEngine:
    """Builds lineage graph structures from app animals + Heritage store."""

    def __init__(
        self,
        animals: Dict[str, Dict[str, Any]],
        parent_lookup: Callable[[str, Optional[Dict[str, Any]]], Dict[str, str]],
        heritage_entries: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self.animals = animals if isinstance(animals, dict) else {}
        self.parent_lookup = parent_lookup
        self.heritage_entries = heritage_entries or {}

        self.child_to_parents: ParentMap = {}
        self.parent_to_children: Dict[str, Set[str]] = defaultdict(set)
        self.all_nodes: Set[str] = set()
        self.father_like_nodes: Set[str] = set()
        self.mother_like_nodes: Set[str] = set()
        # Diagnostics are scoped by the exact node set used for a layout.  A
        # refresh computes levels for the full graph and for the current
        # display set; keeping both prevents the latter from overwriting the
        # former and makes unresolved topology explicit to callers.
        self._level_diagnostics_by_scope: Dict[Tuple[str, ...], Tuple[str, ...]] = {}
        self.level_diagnostics: Tuple[str, ...] = ()

    def build(self) -> None:
        """Build core lookup maps from available records."""
        self.child_to_parents.clear()
        self.parent_to_children.clear()
        self.all_nodes.clear()
        self.father_like_nodes.clear()
        self.mother_like_nodes.clear()
        self._level_diagnostics_by_scope.clear()
        self.level_diagnostics = ()

        # Start with app animals.
        for animal_name, record in self.animals.items():
            name = _norm_name(animal_name)
            if not name:
                continue
            self.all_nodes.add(name)

            merged = self.parent_lookup(name, record)
            parents = {
                "egg_donor": _norm_name(merged.get("egg_donor", "")),
                "sperm_donor": _norm_name(merged.get("sperm_donor", "")),
                "surrogate_mother": _norm_name(merged.get("surrogate_mother", "")),
                "surrogate_father": _norm_name(merged.get("surrogate_father", "")),
            }
            self.child_to_parents[name] = parents

        # Include heritage-only nodes, if any.
        for animal_name, entry in self.heritage_entries.items():
            name = _norm_name(animal_name)
            if not name:
                continue
            if name not in self.child_to_parents:
                self.child_to_parents[name] = {
                    "egg_donor": _norm_name(entry.get("egg_donor", "")),
                    "sperm_donor": _norm_name(entry.get("sperm_donor", "")),
                    "surrogate_mother": _norm_name(entry.get("surrogate_mother", "")),
                    "surrogate_father": _norm_name(entry.get("surrogate_father", "")),
                }
            self.all_nodes.add(name)

        # Build reverse maps and role-like markers.
        for child, parent_values in self.child_to_parents.items():
            for parent_key in GENETIC_PARENT_KEYS:
                parent = _norm_name(parent_values.get(parent_key, ""))
                if not parent:
                    continue
                self.all_nodes.add(parent)
                self.parent_to_children[parent].add(child)

                if parent_key == "sperm_donor":
                    self.father_like_nodes.add(parent)
                elif parent_key == "egg_donor":
                    self.mother_like_nodes.add(parent)

    def iter_edges(self, nodes_subset: Optional[Set[str]] = None) -> Iterable[Tuple[str, str]]:
        """Yield parent->child edges, optionally restricted to subset."""
        subset = nodes_subset
        for child, parent_values in self.child_to_parents.items():
            if subset is not None and child not in subset:
                continue
            for parent in _iter_genetic_parent_names(parent_values):
                if not parent:
                    continue
                if subset is not None and parent not in subset:
                    continue
                yield parent, child

    def get_display_nodes(
        self,
        selected: List[str],
        max_generations: int = 999,
        exclude_archived: bool = False,
        archived_set: Optional[Set[str]] = None,
    ) -> Set[str]:
        """Resolve display set for selected-connected mode.

        When animals are selected:
        - Include all descendants (children, grandchildren, etc.) of selected animals
        - Include all ancestors of selected animals AND their descendants,
          going back up to max_generations levels
        - If exclude_archived is True, stop at first archived ancestor level
        - Converging lineages (shared ancestors) are naturally handled
        """
        selected_names = [_norm_name(name) for name in selected if _norm_name(name)]

        # No selection -> show all known nodes.
        if not selected_names:
            return set(self.all_nodes)

        archived = archived_set or set()
        display: Set[str] = set(selected_names)

        # Phase 1: Collect all descendants of selected animals (no limit on descendants)
        queue = deque(selected_names)
        visited_descendants = set(selected_names)
        while queue:
            current = queue.popleft()
            for child in self.parent_to_children.get(current, set()):
                if child not in visited_descendants:
                    visited_descendants.add(child)
                    display.add(child)
                    queue.append(child)

        # Phase 2: Collect ancestors of ALL nodes in the display set
        # (selected animals + their descendants)
        # This ensures we show the complete pedigree for the entire descendant tree
        seeds_for_ancestors = list(display)  # Include selected + all descendants
        for seed in seeds_for_ancestors:
            # BFS from this seed up to max_generations
            current_level: deque[Tuple[str, int]] = deque([(seed, 0)])  # (node, generation_from_seed)
            # Generation depth is relative to *each* seed.  Sharing one
            # visited set between seeds made the returned scope depend on the
            # order of the selected animals whenever one was an ancestor of
            # another.  A local set keeps cycles bounded while preserving the
            # complete budget for every selected/descendant seed.
            visited_ancestors: Set[str] = {seed}

            while current_level:
                node, gen = current_level.popleft()
                if gen >= max_generations:
                    continue

                parents = self.child_to_parents.get(node, {})
                for parent in _iter_genetic_parent_names(parents):
                    if not parent:
                        continue
                    # If excluding archived, add the archived parent but don't traverse further
                    if exclude_archived and parent in archived:
                        display.add(parent)
                        continue
                    if parent not in visited_ancestors:
                        visited_ancestors.add(parent)
                        display.add(parent)
                        current_level.append((parent, gen + 1))

        return display

    def compute_levels(self, nodes_subset: Set[str]) -> Dict[str, int]:
        """Compute deterministic hard generation levels for layered plotting.

        Genetic parent edges are the only hard vertical constraint.  A
        topological pass therefore cannot be pushed backwards by an indirect
        partner chain.  Partner alignment remains a responsibility of the
        horizontal layout/router, where it can be relaxed without violating
        ancestry.  Cyclic or contradictory records receive deterministic
        fallback levels plus an explicit diagnostic instead of being treated
        as a valid layout.
        """
        nodes = {
            _norm_name(node)
            for node in (nodes_subset or set())
            if _norm_name(node)
        }
        scope = tuple(sorted(nodes, key=str.casefold))
        if not nodes:
            self._record_level_diagnostics(scope, ())
            return {}

        parents_by_child: Dict[str, Set[str]] = {}
        children_by_parent: Dict[str, Set[str]] = defaultdict(set)
        indegree: Dict[str, int] = {node: 0 for node in nodes}
        for child in nodes:
            parents = {
                parent
                for parent in _iter_genetic_parent_names(
                    self.child_to_parents.get(child, {})
                )
                if parent and parent in nodes
            }
            parents_by_child[child] = parents
            indegree[child] = len(parents)
            for parent in parents:
                children_by_parent[parent].add(child)

        # A heap makes both the queue and every propagated level independent
        # of dictionary/set insertion order.
        ready = [node for node, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        levels: Dict[str, int] = {node: 0 for node in nodes}
        processed: Set[str] = set()
        while ready:
            parent = heapq.heappop(ready)
            processed.add(parent)
            for child in sorted(children_by_parent.get(parent, ()), key=str.casefold):
                levels[child] = max(levels.get(child, 0), levels[parent] + 1)
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, child)

        # The remaining nodes are in (or downstream of) a cycle.  Calculate a
        # bounded, stable fallback only so the renderer can still report the
        # bad record; the diagnostic makes the result ineligible for caching.
        remaining = nodes - processed
        if remaining:
            memo = {node: levels[node] for node in processed}
            visiting: Set[str] = set()

            def fallback_level(node: str) -> int:
                if node in memo:
                    return memo[node]
                if node in visiting:
                    return 0
                visiting.add(node)
                parent_levels = [
                    fallback_level(parent)
                    for parent in sorted(parents_by_child.get(node, ()), key=str.casefold)
                ]
                visiting.remove(node)
                memo[node] = min(max(parent_levels, default=-1) + 1, len(nodes) - 1)
                return memo[node]

            for node in sorted(remaining, key=str.casefold):
                levels[node] = fallback_level(node)

        # Same-generation partner placement is useful for compact pedigree
        # rows, but it is only a preference.  Raise a lower partner when all
        # of that partner's direct children already remain strictly below the
        # proposed row.  An ancestor/descendant pairing consequently stays
        # separated, because its direct edge would fail this safety check.
        partner_pairs: Set[Tuple[str, str]] = set()
        for child in sorted(nodes, key=str.casefold):
            parent_values = self.child_to_parents.get(child, {})
            mother = _norm_name(parent_values.get("egg_donor", ""))
            father = _norm_name(parent_values.get("sperm_donor", ""))
            if (
                mother
                and father
                and mother != father
                and mother in nodes
                and father in nodes
            ):
                partner_pairs.add(tuple(sorted((mother, father), key=str.casefold)))
        if partner_pairs:
            # Components are intentionally not forced as a whole.  A local
            # safe raise preserves as many partner alignments as ancestry
            # permits without reintroducing the old chain-wide push.
            for first, second in sorted(
                partner_pairs,
                key=lambda pair: (pair[0].casefold(), pair[1].casefold()),
            ):
                target = max(levels[first], levels[second])
                for partner in (first, second):
                    if levels[partner] >= target:
                        continue
                    children = children_by_parent.get(partner, set()) & nodes
                    if all(levels.get(child, 0) > target for child in children):
                        levels[partner] = target

        diagnostics = self.generation_diagnostics(nodes, levels)
        self._record_level_diagnostics(scope, diagnostics)
        return levels

    def generation_diagnostics(
        self,
        nodes_subset: Set[str],
        levels: Optional[Dict[str, int]] = None,
    ) -> Tuple[str, ...]:
        """Return deterministic diagnostics for generation constraints."""
        nodes = {
            _norm_name(node)
            for node in (nodes_subset or set())
            if _norm_name(node)
        }
        if not nodes:
            return ()

        parents_by_child: Dict[str, Set[str]] = {}
        children_by_parent: Dict[str, Set[str]] = defaultdict(set)
        indegree: Dict[str, int] = {node: 0 for node in nodes}
        edges: List[Tuple[str, str]] = []
        for child in sorted(nodes, key=str.casefold):
            parents = {
                parent
                for parent in _iter_genetic_parent_names(
                    self.child_to_parents.get(child, {})
                )
                if parent and parent in nodes
            }
            parents_by_child[child] = parents
            indegree[child] = len(parents)
            for parent in sorted(parents, key=str.casefold):
                children_by_parent[parent].add(child)
                edges.append((parent, child))

        ready = [node for node, degree in indegree.items() if degree == 0]
        heapq.heapify(ready)
        processed: Set[str] = set()
        while ready:
            parent = heapq.heappop(ready)
            processed.add(parent)
            for child in sorted(children_by_parent.get(parent, ()), key=str.casefold):
                indegree[child] -= 1
                if indegree[child] == 0:
                    heapq.heappush(ready, child)

        diagnostics: List[str] = []
        remaining = nodes - processed
        if remaining:
            diagnostics.append(
                "unresolved generation order: cyclic parentage involving "
                + ", ".join(sorted(remaining, key=str.casefold))
            )

        if levels is not None:
            violations = [
                f"{parent}->{child}"
                for parent, child in sorted(
                    edges, key=lambda edge: (edge[0].casefold(), edge[1].casefold())
                )
                if levels.get(parent, 0) >= levels.get(child, 0)
            ]
            if violations:
                diagnostics.append(
                    "unresolved generation order: parent-before-child violated for "
                    + ", ".join(violations)
                )
        return tuple(sorted(set(diagnostics), key=str.casefold))

    def _record_level_diagnostics(
        self,
        scope: Tuple[str, ...],
        diagnostics: Iterable[str],
    ) -> None:
        values = tuple(sorted({str(item) for item in diagnostics if str(item)}, key=str.casefold))
        self._level_diagnostics_by_scope[scope] = values
        self.level_diagnostics = values

    def get_level_diagnostics(self, nodes_subset: Set[str]) -> Tuple[str, ...]:
        """Return diagnostics for one exact node scope, if it was computed."""
        scope = tuple(
            sorted(
                {
                    _norm_name(node)
                    for node in (nodes_subset or set())
                    if _norm_name(node)
                },
                key=str.casefold,
            )
        )
        return self._level_diagnostics_by_scope.get(scope, ())

    def set_level_diagnostics(
        self,
        nodes_subset: Set[str],
        diagnostics: Iterable[str],
    ) -> None:
        """Store diagnostics after a display builder modifies levels."""
        scope = tuple(
            sorted(
                {
                    _norm_name(node)
                    for node in (nodes_subset or set())
                    if _norm_name(node)
                },
                key=str.casefold,
            )
        )
        self._record_level_diagnostics(scope, diagnostics)


    def get_genetic_parent_map(self) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
        """Return map used for kinship/inbreeding calculations.

        Uses egg_donor and sperm_donor as the genetic parent pair.
        Surrogates are intentionally excluded from coefficient calculations.
        """
        result: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        for child, parent_values in self.child_to_parents.items():
            mother = _norm_name(parent_values.get("egg_donor", "")) or None
            father = _norm_name(parent_values.get("sperm_donor", "")) or None
            result[child] = (mother, father)
        return result
