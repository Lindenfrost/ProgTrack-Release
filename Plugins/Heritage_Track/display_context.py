# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track immutable display context.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Dict, Optional, Set, Tuple

from .pedigree_engine import PedigreeEngine

if TYPE_CHECKING:
    from .display_strategies import DisplaySetStrategy
    from .ghost_strategies import GhostNodeStrategy
    from .scope_provider import ScopeProvider


@dataclass(frozen=True)
class DisplayContext:
    """Immutable context containing all data needed for rendering a pedigree plot.

    This class encapsulates the complete state needed to render a pedigree graph,
    making the rendering process side-effect free and enabling caching/reuse.
    """

    engine: PedigreeEngine
    display_nodes: Set[str]
    levels: Dict[str, int]
    ghost_nodes: Set[str] = field(default_factory=set)
    collapsed_families: Set[str] = field(default_factory=set)
    hidden_nodes: Set[str] = field(default_factory=set)
    family_nodes: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    locked_positions: Dict[str, Tuple[float, float]] = field(default_factory=dict)

    def get_visible_nodes(self) -> Set[str]:
        """Return nodes that should be rendered (display nodes minus hidden)."""
        return self.display_nodes - self.hidden_nodes

    def get_render_nodes(self) -> Set[str]:
        """Return all nodes to render including family nodes."""
        return self.get_visible_nodes() | set(self.family_nodes.keys())

    def is_ghost(self, node: str) -> bool:
        """Check if a node is a ghost node."""
        return node in self.ghost_nodes

    def is_collapsed(self, family_id: str) -> bool:
        """Check if a family is collapsed."""
        return family_id in self.collapsed_families

    def get_node_level(self, node: str) -> int:
        """Get the computed level for a node, defaulting to 0."""
        return self.levels.get(node, 0)

    def get_max_level(self) -> int:
        """Get the maximum level across all nodes."""
        return max(self.levels.values(), default=0)

    def copy_with(
        self,
        display_nodes: Optional[Set[str]] = None,
        levels: Optional[Dict[str, int]] = None,
        ghost_nodes: Optional[Set[str]] = None,
        collapsed_families: Optional[Set[str]] = None,
        hidden_nodes: Optional[Set[str]] = None,
        family_nodes: Optional[Dict[str, Dict[str, Any]]] = None,
        positions: Optional[Dict[str, Tuple[float, float]]] = None,
        locked_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> "DisplayContext":
        """Create a new context with specified fields replaced."""
        return DisplayContext(
            engine=self.engine,
            display_nodes=display_nodes if display_nodes is not None else self.display_nodes,
            levels=levels if levels is not None else self.levels,
            ghost_nodes=ghost_nodes if ghost_nodes is not None else self.ghost_nodes,
            collapsed_families=collapsed_families if collapsed_families is not None else self.collapsed_families,
            hidden_nodes=hidden_nodes if hidden_nodes is not None else self.hidden_nodes,
            family_nodes=family_nodes if family_nodes is not None else self.family_nodes,
            positions=positions if positions is not None else self.positions,
            locked_positions=locked_positions if locked_positions is not None else self.locked_positions,
        )


class DisplayContextBuilder:
    """Builds DisplayContext by orchestrating the pipeline stages.

    This builder separates the concerns of:
    1. Data layer - Building the pedigree engine
    2. Filter layer - Computing which nodes to display
    3. Layout layer - Computing levels and positions
    4. Ghost handling - Finding out-of-scope ghost nodes
    """

    def __init__(
        self,
        engine: PedigreeEngine,
        settings: Dict[str, Any],
        display_strategy: "DisplaySetStrategy",
        ghost_strategy: Optional["GhostNodeStrategy"] = None,
        scope_provider: Optional["ScopeProvider"] = None,
    ):
        self.engine = engine
        self.settings = settings
        self.display_strategy = display_strategy
        self.ghost_strategy = ghost_strategy
        self.scope_provider = scope_provider
        self._max_generations: int = settings.get("max_generations", 999)
        self._exclude_archived: bool = settings.get("exclude_archived", False)

    def build(
        self,
        selected_animals: list[str],
        archived_animals: Optional[Set[str]] = None,
    ) -> DisplayContext:
        """Build a complete DisplayContext from the current state."""
        archived = archived_animals or set()

        # Step 1: Compute display set using strategy
        display_nodes = self.display_strategy.compute(
            self.engine, selected_animals, self._max_generations, self._exclude_archived, archived
        )

        # Step 2: Handle archived exclusion in all-animals mode
        is_all_animals_mode = len(selected_animals) == 0
        if is_all_animals_mode and self._exclude_archived:
            display_nodes = display_nodes - archived

        # Step 3: Find ghost nodes
        ghost_nodes: Set[str] = set()
        if self.ghost_strategy:
            ghost_nodes = self.ghost_strategy.find_ghosts(display_nodes, self.engine, archived)
            display_nodes = display_nodes | ghost_nodes

        # Step 4: Compute levels with modifications
        levels = self._compute_modified_levels(display_nodes, ghost_nodes)

        return DisplayContext(
            engine=self.engine,
            display_nodes=display_nodes,
            levels=levels,
            ghost_nodes=ghost_nodes,
            collapsed_families=set(),
            hidden_nodes=set(),
            family_nodes={},
            positions={},
            locked_positions={},
        )

    def _compute_modified_levels(
        self, display_nodes: Set[str], ghost_nodes: Set[str]
    ) -> Dict[str, int]:
        """Compute levels with leaf promotion and pull-up passes."""
        # Get base levels from engine
        all_graph_nodes = self.engine.all_nodes
        all_graph_levels = self.engine.compute_levels(all_graph_nodes)
        pre_collapse_levels = self.engine.compute_levels(display_nodes)

        if not pre_collapse_levels:
            return {}

        _max_lvl = max(pre_collapse_levels.values(), default=0)

        # Leaf promotion: isolated nodes → max_level
        if _max_lvl > 0:
            for _node in list(display_nodes):
                if pre_collapse_levels.get(_node, 0) < _max_lvl:
                    _has_any_children = bool(self.engine.parent_to_children.get(_node, set()))
                    if not _has_any_children:
                        pre_collapse_levels[_node] = _max_lvl

        # Assign levels to ghost nodes from full-graph level dict
        for _ghost in ghost_nodes:
            if _ghost not in pre_collapse_levels:
                pre_collapse_levels[_ghost] = all_graph_levels.get(_ghost, 0)

        # Pull-up pass: level 0 parents pulled toward children
        for _pull_pass in range(_max_lvl + 2):
            _pull_changed = False
            for _node in list(display_nodes):
                if pre_collapse_levels.get(_node, 0) != 0:
                    continue
                _kids = self.engine.parent_to_children.get(_node, set()) & display_nodes
                if not _kids:
                    continue
                _kid_lvls = [pre_collapse_levels.get(k, 0) for k in _kids]
                _max_kid = max(_kid_lvls)
                _min_kid = min(_kid_lvls)
                if _max_kid <= 1:
                    continue
                _desired = _max_kid - 1
                if _desired >= _min_kid:
                    continue
                pre_collapse_levels[_node] = _desired
                _pull_changed = True
            if not _pull_changed:
                break

        return pre_collapse_levels
