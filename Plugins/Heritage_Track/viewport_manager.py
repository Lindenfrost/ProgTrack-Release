# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Module: Heritage Track viewport and lazy-rendering helpers.

from __future__ import annotations

from typing import Dict, Set, Tuple


class ViewportManager:
    """Manages visible node subset for large graphs.
    
    For large pedigree datasets (1000+ nodes), computing positions for
    all nodes can be expensive. This class provides viewport culling to
    only process nodes that are currently visible or near the viewport.
    """
    
    def __init__(self, margin: float = 5.0):
        """Initialize the viewport manager.
        
        Args:
            margin: Extra margin around viewport to include (in graph units)
        """
        self.margin = margin
    
    def get_visible_nodes(
        self,
        all_nodes: Set[str],
        positions: Dict[str, Tuple[float, float]],
        viewport: Tuple[float, float, float, float]
    ) -> Set[str]:
        """Return only nodes within viewport + margin.
        
        Args:
            all_nodes: Set of all node names
            positions: Current positions for all nodes
            viewport: (x_min, x_max, y_min, y_max) of visible area
            
        Returns:
            Set of node names that should be visible
        """
        x_min, x_max, y_min, y_max = viewport
        
        # Expand by margin
        x_min -= self.margin
        x_max += self.margin
        y_min -= self.margin
        y_max += self.margin
        
        visible: Set[str] = set()
        
        for node in all_nodes:
            if node not in positions:
                continue
            
            x, y = positions[node]
            
            if x_min <= x <= x_max and y_min <= y <= y_max:
                visible.add(node)
        
        return visible
    
    def should_render_node(
        self,
        node: str,
        positions: Dict[str, Tuple[float, float]],
        viewport: Tuple[float, float, float, float]
    ) -> bool:
        """Check if a single node should be rendered.
        
        Args:
            node: Node name to check
            positions: Position dictionary
            viewport: (x_min, x_max, y_min, y_max) of visible area
            
        Returns:
            True if node is in or near viewport
        """
        if node not in positions:
            return False
        
        x, y = positions[node]
        x_min, x_max, y_min, y_max = viewport
        
        return (
            x_min - self.margin <= x <= x_max + self.margin and
            y_min - self.margin <= y <= y_max + self.margin
        )
    
    def compute_viewport_bounds(
        self,
        ax_xlim: Tuple[float, float],
        ax_ylim: Tuple[float, float]
    ) -> Tuple[float, float, float, float]:
        """Convert matplotlib axis limits to viewport tuple.
        
        Args:
            ax_xlim: (x_min, x_max) from matplotlib ax.get_xlim()
            ax_ylim: (y_min, y_max) from matplotlib ax.get_ylim()
            
        Returns:
            (x_min, x_max, y_min, y_max) viewport tuple
        """
        return (ax_xlim[0], ax_xlim[1], ax_ylim[0], ax_ylim[1])
    
    def estimate_visible_count(
        self,
        total_nodes: int,
        all_positions: Dict[str, Tuple[float, float]],
        viewport: Tuple[float, float, float, float]
    ) -> int:
        """Estimate how many nodes will be visible.
        
        This is useful for deciding whether to use viewport culling.
        
        Args:
            total_nodes: Total number of nodes in graph
            all_positions: All node positions (sample)
            viewport: Current viewport
            
        Returns:
            Estimated count of visible nodes
        """
        if not all_positions:
            return 0
        
        visible = self.get_visible_nodes(set(all_positions.keys()), all_positions, viewport)
        
        # Extrapolate to total
        if all_positions:
            ratio = len(visible) / len(all_positions)
            return int(total_nodes * ratio)
        
        return total_nodes
