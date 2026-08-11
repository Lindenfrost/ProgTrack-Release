# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.1
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track ghost-node detection strategies.

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, Set

from .pedigree_engine import PedigreeEngine


class GhostNodeStrategy(ABC):
    """Abstract strategy for finding ghost nodes.

    Ghost nodes are out-of-scope animals that are connected to the
    display set (as parents or children) and should be shown greyed-out
    to keep pedigree lines visually intact.
    """

    @abstractmethod
    def find_ghosts(
        self,
        display_nodes: Set[str],
        engine: PedigreeEngine,
        archived_animals: Optional[Set[str]] = None,
    ) -> Set[str]:
        """Find ghost nodes for the current display set.

        Args:
            display_nodes: Currently selected nodes for display
            engine: The pedigree engine with lineage data
            archived_animals: Set of archived animal names

        Returns:
            Set of ghost node names
        """
        pass


class VisibleFamilyCompletenessGhostStrategy(GhostNodeStrategy):
    """Add only the missing co-parent of an already visible relationship.

    Generation cut-offs and selected-animal ghost expansion can expose a child
    together with one genetic parent while omitting the other.  Without the
    co-parent the visible child is rendered like a founder even though its
    ancestry is already present elsewhere in the graph.  This one-step rule
    completes that family without recursively expanding another generation.
    """

    def __init__(self, families: Optional[Dict[str, Dict[str, Any]]] = None):
        self.families = families or {}

    def find_ghosts(
        self,
        display_nodes: Set[str],
        engine: PedigreeEngine,
        archived_animals: Optional[Set[str]] = None,
    ) -> Set[str]:
        ghost_nodes: Set[str] = set()
        for family in self.families.values():
            parents = {
                str(family.get(key, "")).strip()
                for key in ("mother", "father")
                if str(family.get(key, "")).strip()
            }
            children = {
                str(child).strip()
                for child in family.get("children", [])
                if str(child).strip()
            }
            if not (children & display_nodes) or not (parents & display_nodes):
                continue
            ghost_nodes.update(parents - display_nodes)
        return ghost_nodes


class ArchivedGhostStrategy(GhostNodeStrategy):
    """Ghosts from archived animal boundaries.

    When exclude_archived is enabled, archived animals that are parents
    or children of displayed animals become ghost nodes.
    """

    def find_ghosts(
        self,
        display_nodes: Set[str],
        engine: PedigreeEngine,
        archived_animals: Optional[Set[str]] = None,
    ) -> Set[str]:
        if not archived_animals:
            return set()

        ghost_nodes: Set[str] = set()

        for node in list(display_nodes):
            # Check for archived parents
            parents = engine.child_to_parents.get(node, {})
            for parent_key in ("egg_donor", "sperm_donor"):
                parent = parents.get(parent_key, "")
                if parent and parent in archived_animals and parent not in display_nodes:
                    ghost_nodes.add(parent)

            # Check for archived children
            children = engine.parent_to_children.get(node, set())
            for child in children:
                if child in archived_animals and child not in display_nodes:
                    ghost_nodes.add(child)

        return ghost_nodes


class CompositeGhostStrategy(GhostNodeStrategy):
    """Combines multiple ghost detection strategies."""

    def __init__(self, strategies: list[GhostNodeStrategy]):
        self.strategies = strategies

    def find_ghosts(
        self,
        display_nodes: Set[str],
        engine: PedigreeEngine,
        archived_animals: Optional[Set[str]] = None,
    ) -> Set[str]:
        """Combine results from all strategies."""
        ghost_nodes: Set[str] = set()

        for strategy in self.strategies:
            ghosts = strategy.find_ghosts(display_nodes, engine, archived_animals)
            ghost_nodes.update(ghosts)

        return ghost_nodes


class NoGhostStrategy(GhostNodeStrategy):
    """No-op strategy that never finds ghosts."""

    def find_ghosts(
        self,
        display_nodes: Set[str],
        engine: PedigreeEngine,
        archived_animals: Optional[Set[str]] = None,
    ) -> Set[str]:
        return set()


class OffspringAndSiblingsGhostStrategy(GhostNodeStrategy):
    """Ghosts for offspring, partners, and siblings of selected animals.

    When animals are selected, this strategy finds:
    1. All partners of selected animals (as ghosts)
    2. All offspring of selected animals and each partner (as ghosts)
    3. Siblings of selected animals (as ghosts), but NOT their partners/offspring
       unless the sibling is also selected
    """

    def __init__(self, selected_animals: Optional[Set[str]] = None):
        self.selected_animals = selected_animals or set()

    def find_ghosts(
        self,
        display_nodes: Set[str],
        engine: PedigreeEngine,
        archived_animals: Optional[Set[str]] = None,
    ) -> Set[str]:
        ghost_nodes: Set[str] = set()

        # Collect all partners of selected animals first
        all_partners: Set[str] = set()
        for selected in self.selected_animals:
            # Get all children of this selected animal
            children = engine.parent_to_children.get(selected, set())
            for child in children:
                # Add child as ghost if not already a display node
                if child not in display_nodes:
                    ghost_nodes.add(child)

                # Find the other parent (partner) of this child
                parents = engine.child_to_parents.get(child, {})
                for parent_key in ("egg_donor", "sperm_donor"):
                    parent = parents.get(parent_key, "")
                    if parent and parent != selected and parent not in display_nodes:
                        all_partners.add(parent)

        # Add all partners as ghosts
        for partner in all_partners:
            if partner not in display_nodes and partner not in self.selected_animals:
                ghost_nodes.add(partner)

            # Add all children of partners as ghosts (offspring with other mates)
            partner_children = engine.parent_to_children.get(partner, set())
            for child in partner_children:
                if child not in display_nodes and child not in self.selected_animals:
                    ghost_nodes.add(child)

        # Find siblings of selected animals (but NOT their partners/offspring)
        for selected in self.selected_animals:
            # Get parents of the selected animal
            parents = engine.child_to_parents.get(selected, {})
            for parent_key in ("egg_donor", "sperm_donor"):
                parent = parents.get(parent_key, "")
                if not parent:
                    continue

                # Get all children of this parent (siblings)
                siblings = engine.parent_to_children.get(parent, set())
                for sibling in siblings:
                    # Add sibling as ghost if not in display and not selected
                    if sibling != selected and sibling not in display_nodes:
                        ghost_nodes.add(sibling)
                    # Note: We explicitly do NOT add the siblings' partners or offspring
                    # unless those siblings are also selected (handled in next iteration)

        return ghost_nodes
