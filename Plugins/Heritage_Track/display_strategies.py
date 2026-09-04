# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track display-set strategies.

from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from typing import Any, Dict, Iterable, List, Optional, Set

from .pedigree_engine import PedigreeEngine


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
    - All selected animals (unless archived exclusion is enabled)
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
        archived = archived_set or set()
        selected_names = [
            _norm_name(name)
            for name in selected
            if _norm_name(name)
            and not (exclude_archived and _norm_name(name) in archived)
        ]

        if not selected_names:
            # Empty selection is the splash/empty state; never substitute a
            # hidden all-animals scope here.
            return set()

        # Start with selected animals.  Explicit archived selections remain in
        # the canonical selection so toggling the filter back off can restore
        # them, but they are omitted from this display set while exclusion is
        # enabled.
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
            current_level: deque[tuple[str, int]] = deque([(seed, 0)])

            while current_level:
                node, gen = current_level.popleft()
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
        return set()
