# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track pedigree graph engine.

from __future__ import annotations

from collections import defaultdict, deque
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

    def build(self) -> None:
        """Build core lookup maps from available records."""
        self.child_to_parents.clear()
        self.parent_to_children.clear()
        self.all_nodes.clear()
        self.father_like_nodes.clear()
        self.mother_like_nodes.clear()

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
        queue = list(selected_names)
        visited_descendants = set(selected_names)
        while queue:
            current = queue.pop(0)
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
            current_level: List[Tuple[str, int]] = [(seed, 0)]  # (node, generation_from_seed)
            # Generation depth is relative to *each* seed.  Sharing one
            # visited set between seeds made the returned scope depend on the
            # order of the selected animals whenever one was an ancestor of
            # another.  A local set keeps cycles bounded while preserving the
            # complete budget for every selected/descendant seed.
            visited_ancestors: Set[str] = {seed}

            while current_level:
                node, gen = current_level.pop(0)
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
        """Compute generation-like levels for layered plotting."""
        memo: Dict[str, int] = {}
        visiting: Set[str] = set()

        def level_of(node: str) -> int:
            if node in memo:
                return memo[node]
            if node in visiting:
                # cycle guard
                return 0

            visiting.add(node)
            parent_levels: List[int] = []
            for parent in _iter_genetic_parent_names(self.child_to_parents.get(node, {})):
                if not parent or parent not in nodes_subset:
                    continue
                parent_levels.append(level_of(parent))
            visiting.remove(node)

            lvl = (max(parent_levels) + 1) if parent_levels else 0
            memo[node] = lvl
            return lvl

        for node in nodes_subset:
            level_of(node)

        # Keep genetic mates on the same generation level where possible.
        def _is_ancestor(candidate_ancestor: str, candidate_descendant: str) -> bool:
            if not candidate_ancestor or not candidate_descendant:
                return False
            if candidate_ancestor == candidate_descendant:
                return False

            visited: Set[str] = set()
            stack: List[str] = [candidate_ancestor]
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)

                for child in self.parent_to_children.get(current, set()):
                    if child not in nodes_subset:
                        continue
                    if child == candidate_descendant:
                        return True
                    if child not in visited:
                        stack.append(child)
            return False

        partner_adj: Dict[str, Set[str]] = defaultdict(set)
        for child in nodes_subset:
            parent_values = self.child_to_parents.get(child, {})
            mother = _norm_name(parent_values.get("egg_donor", ""))
            father = _norm_name(parent_values.get("sperm_donor", ""))
            if not mother or not father:
                continue
            if mother not in nodes_subset or father not in nodes_subset:
                continue

            # Do not enforce same-level alignment when one mate is an
            # ancestor of the other (e.g., father-daughter pairings).
            # That constraint is contradictory with parent->child layering.
            if _is_ancestor(mother, father) or _is_ancestor(father, mother):
                continue

            partner_adj[mother].add(father)
            partner_adj[father].add(mother)

        def _align_partner_components(levels: Dict[str, int]) -> bool:
            changed_local = False
            visited: Set[str] = set()
            for seed in list(partner_adj.keys()):
                if seed in visited:
                    continue

                stack = [seed]
                component: List[str] = []
                while stack:
                    cur = stack.pop()
                    if cur in visited:
                        continue
                    visited.add(cur)
                    component.append(cur)
                    for nxt in partner_adj.get(cur, set()):
                        if nxt not in visited:
                            stack.append(nxt)

                if len(component) <= 1:
                    continue

                target_level = max(levels.get(node, 0) for node in component)
                for node in component:
                    if levels.get(node, 0) != target_level:
                        levels[node] = target_level
                        changed_local = True
            return changed_local

        changed = _align_partner_components(memo)
        safety = 0
        max_passes = max(10, len(nodes_subset) * 4)
        max_level = max(0, len(nodes_subset) - 1)
        while changed and safety < max_passes:
            safety += 1
            changed = False

            for child in nodes_subset:
                parent_levels: List[int] = []
                for parent in _iter_genetic_parent_names(self.child_to_parents.get(child, {})):
                    if parent and parent in nodes_subset:
                        parent_levels.append(memo.get(parent, 0))

                if not parent_levels:
                    continue
                desired = min(max(parent_levels) + 1, max_level)
                if memo.get(child, 0) < desired:
                    memo[child] = desired
                    changed = True

            if _align_partner_components(memo):
                changed = True

        return memo

    def compute_levels_fast(self, nodes_subset: Set[str]) -> Dict[str, int]:
        """Compute generation-like levels using Kahn's algorithm (O(V+E)).
        
        This is significantly faster than the recursive DFS approach for large datasets
        (1000+ nodes) because it uses topological sorting instead of repeated traversal.
        
        Args:
            nodes_subset: Set of nodes to compute levels for
            
        Returns:
            Dictionary mapping node names to their level (0 = founders)
        """
        if not nodes_subset:
            return {}
        
        # Build in-degree map (number of parents in the subset)
        in_degree: Dict[str, int] = defaultdict(int)
        for node in nodes_subset:
            for parent in _iter_genetic_parent_names(self.child_to_parents.get(node, {})):
                if parent and parent in nodes_subset:
                    in_degree[node] += 1
        
        # Kahn's algorithm: start with nodes that have no parents in subset
        levels: Dict[str, int] = {}
        queue = deque([n for n in nodes_subset if in_degree[n] == 0])
        
        for node in queue:
            # Compute level based on parents
            parent_levels = [
                levels.get(p, 0) 
                for p in _iter_genetic_parent_names(self.child_to_parents.get(node, {}))
                if p and p in nodes_subset
            ]
            levels[node] = max(parent_levels, default=-1) + 1
            
            # Reduce in-degree for children
            for child in self.parent_to_children.get(node, set()):
                if child in nodes_subset:
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        queue.append(child)
        
        # Handle any remaining nodes (cycles or disconnected)
        for node in nodes_subset:
            if node not in levels:
                parent_levels = [
                    levels.get(p, 0)
                    for p in _iter_genetic_parent_names(self.child_to_parents.get(node, {}))
                    if p and p in nodes_subset
                ]
                levels[node] = max(parent_levels, default=-1) + 1
        
        # Partner alignment (same as original)
        partner_adj: Dict[str, Set[str]] = defaultdict(set)
        for child in nodes_subset:
            parent_values = self.child_to_parents.get(child, {})
            mother = _norm_name(parent_values.get("egg_donor", ""))
            father = _norm_name(parent_values.get("sperm_donor", ""))
            if not mother or not father:
                continue
            if mother not in nodes_subset or father not in nodes_subset:
                continue
            
            # Check for ancestor relationship
            def _is_ancestor(anc: str, desc: str) -> bool:
                visited: Set[str] = set()
                stack = [anc]
                while stack:
                    cur = stack.pop()
                    if cur == desc:
                        return True
                    if cur in visited:
                        continue
                    visited.add(cur)
                    for c in self.parent_to_children.get(cur, set()):
                        if c in nodes_subset and c not in visited:
                            stack.append(c)
                return False
            
            if _is_ancestor(mother, father) or _is_ancestor(father, mother):
                continue
            
            partner_adj[mother].add(father)
            partner_adj[father].add(mother)
        
        # Align partners to same level
        def _align_partners() -> bool:
            changed = False
            visited: Set[str] = set()
            for seed in partner_adj:
                if seed in visited:
                    continue
                component: List[str] = []
                stack = [seed]
                while stack:
                    cur = stack.pop()
                    if cur in visited:
                        continue
                    visited.add(cur)
                    component.append(cur)
                    for nxt in partner_adj.get(cur, set()):
                        if nxt not in visited:
                            stack.append(nxt)
                
                if len(component) > 1:
                    target = max(levels.get(n, 0) for n in component)
                    for n in component:
                        if levels.get(n, 0) != target:
                            levels[n] = target
                            changed = True
            return changed
        
        # Iterative refinement
        for _ in range(10):
            if not _align_partners():
                break
        
        return levels

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
