# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track display-set strategies.

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import defaultdict
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Set

from .pedigree_engine import PedigreeEngine

if TYPE_CHECKING:
    from .scope_provider import ScopeProvider


def _norm_name(value: Any) -> str:
    """Normalize a name value to a string."""
    if value is None:
        return ""
    return str(value).strip()


def _iter_genetic_parents(parent_values: Dict[str, str]) -> Iterable[str]:
    """Yield genetic parent names from parent values dict."""
    for key in ("egg_donor", "sperm_donor"):
        val = parent_values.get(key, "")
        if val:
            yield _norm_name(val)


class DisplaySetStrategy(ABC):
    """Abstract strategy for computing which nodes to display."""

    @abstractmethod
    def compute(
        self,
        engine: PedigreeEngine,
        selected: List[str],
        max_generations: int = 999,
        exclude_archived: bool = False,
        archived_set: Optional[Set[str]] = None,
    ) -> Set[str]:
        """Compute the set of nodes to display.

        Args:
            engine: The pedigree engine with lineage data
            selected: Currently selected animal names
            max_generations: Maximum ancestor generations to show
            exclude_archived: Whether to stop at archived ancestors
            archived_set: Set of archived animal names

        Returns:
            Set of node names to display
        """
        pass


class SelectedAnimalsStrategy(DisplaySetStrategy):
    """Display strategy when specific animals are selected.

    Includes:
    - All selected animals (always shown, even if disconnected)
    - All ancestors up to max_generations (solid nodes)
    - Note: Offspring, partners, and siblings are shown as GHOSTS (handled by ghost strategy)
    """

    def compute(
        self,
        engine: PedigreeEngine,
        selected: List[str],
        max_generations: int = 999,
        exclude_archived: bool = False,
        archived_set: Optional[Set[str]] = None,
    ) -> Set[str]:
        selected_names = [_norm_name(name) for name in selected if _norm_name(name)]

        if not selected_names:
            return set(engine.all_nodes)

        archived = archived_set or set()
        # Start with selected animals - these are ALWAYS shown even if disconnected
        display: Set[str] = set(selected_names)

        # Phase: Collect ancestors of selected animals only (not descendants)
        # Descendants will be shown as ghosts by the ghost strategy
        for seed in selected_names:
            # Each selected animal owns its generation budget.  A shared
            # global visited set made ``[descendant, ancestor]`` stop at a
            # different boundary than ``[ancestor, descendant]``: the first
            # traversal could mark the second seed visited before that seed
            # received its full ``max_generations`` walk.  Keep cycle guards
            # local to a seed and union the results into the display set.
            visited_ancestors: Set[str] = {seed}
            current_level: List[tuple[str, int]] = [(seed, 0)]

            while current_level:
                node, gen = current_level.pop(0)
                if gen >= max_generations:
                    continue

                parents = engine.child_to_parents.get(node, {})
                for parent in _iter_genetic_parents(parents):
                    if not parent:
                        continue
                    if exclude_archived and parent in archived:
                        # Stop traversing at archived ancestors, don't add to display
                        # (they will appear as ghosts if connected to displayed animals)
                        continue
                    if parent not in visited_ancestors:
                        visited_ancestors.add(parent)
                        display.add(parent)
                        current_level.append((parent, gen + 1))

        return display


class AllAnimalsStrategy(DisplaySetStrategy):
    """Display strategy when no animals are selected (show all animals mode).

    This strategy incorporates:
    - Scope filtering (project/species)
    - Generation level cutoff
    - Sibling level alignment
    """

    def __init__(self, scope_provider: Optional["ScopeProvider"] = None):
        self.scope_provider = scope_provider

    def compute(
        self,
        engine: PedigreeEngine,
        selected: List[str],
        max_generations: int = 999,
        exclude_archived: bool = False,
        archived_set: Optional[Set[str]] = None,
    ) -> Set[str]:
        # Get scope filter if available
        scope_animals: Optional[Set[str]] = None
        if self.scope_provider:
            scope_animals = self._get_scoped_animals(engine)

        # If no scope filter active, return empty set (signals "no specific selection")
        if scope_animals is None:
            return set()

        # Start with all nodes in scope
        candidates: Set[str] = set()
        for node in engine.all_nodes:
            if scope_animals is not None and node not in scope_animals:
                continue
            candidates.add(node)

        if not candidates:
            return set(engine.all_nodes)

        # Compute generation levels
        levels = engine.compute_levels(candidates)
        if not levels:
            return candidates

        max_level = max(levels.values(), default=0)

        # Apply sibling level alignment
        candidates, levels = self._align_sibling_levels(
            candidates, levels, engine, max_level
        )

        # Apply generation cutoff
        cutoff_level = max(0, max_level - max_generations)
        result: Set[str] = set()
        archived = archived_set or set()
        for node in candidates:
            node_level = levels.get(node, 0)
            if node_level >= cutoff_level:
                # Skip archived animals if exclude_archived is enabled
                if exclude_archived and node in archived:
                    continue
                result.add(node)

        return result

    def _get_scoped_animals(self, engine: PedigreeEngine) -> Optional[Set[str]]:
        """Get animals matching the current scope."""
        if not self.scope_provider:
            return None

        scope = self.scope_provider.get_scope()
        if not scope.is_active:
            return None

        # Need access to app animals - this is handled via the scope provider
        # which has access to the app
        return self.scope_provider.get_scoped_animals({}, {})

    def _align_sibling_levels(
        self,
        candidates: Set[str],
        levels: Dict[str, int],
        engine: PedigreeEngine,
        max_level: int,
    ) -> tuple[Set[str], Dict[str, int]]:
        """Align sibling nodes to consistent levels."""

        def _get_siblings(n: str) -> Set[str]:
            pvals = engine.child_to_parents.get(n, {})
            parents = {v for k, v in pvals.items()
                       if k in ("egg_donor", "sperm_donor") and v}
            siblings: Set[str] = set()
            for p in parents:
                siblings.update(engine.parent_to_children.get(p, set()))
            siblings.discard(n)
            return siblings & candidates

        # Build parent -> children map
        parent_to_children_local: Dict[str, Set[str]] = defaultdict(set)
        for c in candidates:
            pvals = engine.child_to_parents.get(c, {})
            for k, v in pvals.items():
                if k in ("egg_donor", "sperm_donor") and v:
                    parent_to_children_local[v].add(c)

        def _has_children_in_candidates(n: str) -> bool:
            return bool(parent_to_children_local.get(n, set()) & candidates)

        # Multi-pass alignment
        for _ in range(10):
            changed = False
            for node in list(candidates):
                if _has_children_in_candidates(node):
                    continue

                siblings = _get_siblings(node)
                if not siblings:
                    continue

                sibling_levels = [levels.get(s, 0) for s in siblings]
                if not sibling_levels:
                    continue

                max_sibling_level = max(sibling_levels)
                current_level = levels.get(node, 0)

                if max_sibling_level > current_level:
                    levels[node] = max_sibling_level
                    changed = True

            if not changed:
                break

        return candidates, levels


class CompositeDisplayStrategy(DisplaySetStrategy):
    """Combines multiple strategies with fallback logic."""

    def __init__(self, strategies: List[DisplaySetStrategy]):
        self.strategies = strategies

    def compute(
        self,
        engine: PedigreeEngine,
        selected: List[str],
        max_generations: int = 999,
        exclude_archived: bool = False,
        archived_set: Optional[Set[str]] = None,
    ) -> Set[str]:
        """Try strategies in order until one returns non-empty result."""
        for strategy in self.strategies:
            result = strategy.compute(engine, selected, max_generations, exclude_archived, archived_set)
            if result:
                return result
        return set(engine.all_nodes)
