# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track adaptive graph spacing.

from __future__ import annotations

from typing import Dict, List, Set, Tuple


class AdaptiveSpacer:
    """Adjusts spacing based on node content and local density.
    
    Fixed spacing doesn't work well for all label lengths. Long names
    can overlap with adjacent nodes. This class dynamically adjusts
    horizontal spacing to ensure labels don't overlap.
    """
    
    def __init__(
        self,
        min_gap: float = 0.5,
        char_width: float = 0.1,
        padding: float = 0.2
    ):
        """Initialize the adaptive spacer.
        
        Args:
            min_gap: Minimum gap between node edges
            char_width: Estimated width per character
            padding: Extra padding around labels
        """
        self.min_gap = min_gap
        self.char_width = char_width
        self.padding = padding
    
    def compute_adaptive_positions(
        self,
        nodes: Set[str],
        base_positions: Dict[str, Tuple[float, float]],
        node_labels: Dict[str, str],
        level: Optional[int] = None
    ) -> Dict[str, Tuple[float, float]]:
        """Adjust horizontal spacing based on label lengths.
        
        Args:
            nodes: Nodes to adjust (typically same level)
            base_positions: Starting positions
            node_labels: Labels for computing widths
            level: Generation level (for y-position, if needed)
            
        Returns:
            Adjusted positions with proper spacing
        """
        if not nodes:
            return {}
        
        positions = dict(base_positions)
        
        # Compute required widths
        node_widths: Dict[str, float] = {}
        for node in nodes:
            label = node_labels.get(node, node)
            # Estimate width based on label length
            width = len(label) * self.char_width + self.padding * 2
            node_widths[node] = width
        
        # Sort by x position
        sorted_nodes = sorted(nodes, key=lambda n: positions.get(n, (0, 0))[0])
        
        # Adjust positions to ensure minimum spacing
        adjusted = dict(positions)
        
        for i in range(1, len(sorted_nodes)):
            prev_node = sorted_nodes[i - 1]
            curr_node = sorted_nodes[i]
            
            prev_x = adjusted[prev_node][0]
            curr_x = adjusted[curr_node][0]
            
            # Required gap is based on half-widths of both nodes plus min_gap
            prev_half_width = node_widths.get(prev_node, 0.5) / 2
            curr_half_width = node_widths.get(curr_node, 0.5) / 2
            required_gap = prev_half_width + curr_half_width + self.min_gap
            
            actual_gap = curr_x - prev_x
            
            if actual_gap < required_gap:
                # Shift current node and all to the right
                shift = required_gap - actual_gap
                
                for j in range(i, len(sorted_nodes)):
                    node = sorted_nodes[j]
                    x, y = adjusted[node]
                    adjusted[node] = (x + shift, y)
        
        return adjusted
    
    def compute_all_levels(
        self,
        all_nodes: Set[str],
        base_positions: Dict[str, Tuple[float, float]],
        node_labels: Dict[str, str],
        levels: Dict[str, int]
    ) -> Dict[str, Tuple[float, float]]:
        """Adjust spacing for all generation levels.
        
        Args:
            all_nodes: All nodes in the graph
            base_positions: Starting positions
            node_labels: Labels for all nodes
            levels: Generation level for each node
            
        Returns:
            Adjusted positions for all nodes
        """
        # Group by level
        level_groups: Dict[int, Set[str]] = defaultdict(set)
        for node in all_nodes:
            lvl = levels.get(node, 0)
            level_groups[lvl].add(node)
        
        # Adjust each level independently
        adjusted: Dict[str, Tuple[float, float]] = {}
        
        for lvl in sorted(level_groups.keys()):
            nodes_at_level = level_groups[lvl]
            level_positions = {
                n: base_positions[n] 
                for n in nodes_at_level 
                if n in base_positions
            }
            
            adjusted_level = self.compute_adaptive_positions(
                nodes_at_level,
                level_positions,
                node_labels,
                lvl
            )
            
            adjusted.update(adjusted_level)
        
        return adjusted
    
    def estimate_label_width(self, label: str) -> float:
        """Estimate the display width of a label.
        
        Args:
            label: Text label to measure
            
        Returns:
            Estimated width in graph units
        """
        return len(label) * self.char_width + self.padding * 2


from collections import defaultdict
