# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track family-node placement optimization.

from __future__ import annotations

from collections import defaultdict
import statistics
from typing import Any, Dict, Optional, Tuple


class FamilyPositionOptimizer:
    """Optimizes family node positions to minimize edge crossings.
    
    Family nodes are the intermediate nodes that connect parents to
    children in the pedigree visualization. Placing them optimally
    reduces edge crossings and improves readability.
    
    The optimizer places each family node at the median x of its
    connected animals, which minimizes the total horizontal edge length.
    """
    
    def __init__(self, level_spacing: float = 2.0):
        """Initialize the family position optimizer.
        
        Args:
            level_spacing: Vertical spacing between generation levels
        """
        self.level_spacing = level_spacing
    
    def optimize_family_positions(
        self,
        families: Dict[str, Dict[str, Any]],
        animal_positions: Dict[str, Tuple[float, float]],
        levels: Optional[Dict[str, int]] = None
    ) -> Dict[str, Tuple[float, float]]:
        """Compute optimal positions for family nodes.
        
        Args:
            families: Family structures with mother/father/children
            animal_positions: Positions of animal nodes
            levels: Generation levels (optional, for y-positioning)
            
        Returns:
            Dictionary mapping family_id to (x, y) position
        """
        family_positions: Dict[str, Tuple[float, float]] = {}
        
        for family_id, family in families.items():
            mother = family.get("mother")
            father = family.get("father")
            children = family.get("children", [])
            
            # Collect x positions of all connected animals
            x_positions = []
            y_positions = []
            
            for node in [mother, father] + list(children):
                if node and node in animal_positions:
                    x, y = animal_positions[node]
                    x_positions.append(x)
                    y_positions.append(y)
            
            if not x_positions:
                continue
            
            # Use median x to minimize total edge length
            median_x = statistics.median(x_positions)
            
            # Compute y position
            if y_positions:
                # Place at median y of connected nodes, offset slightly
                median_y = statistics.median(y_positions)
                family_y = median_y
            else:
                # Fallback to level-based positioning
                parent_level = self._get_parent_level(family, animal_positions, levels)
                child_level = self._get_child_level(family, animal_positions, levels)
                if parent_level is not None and child_level is not None:
                    family_y = -(parent_level + child_level) / 2 * self.level_spacing
                else:
                    family_y = 0.0
            
            family_positions[family_id] = (median_x, family_y)
        
        return family_positions
    
    def _get_parent_level(
        self,
        family: Dict[str, Any],
        animal_positions: Dict[str, Tuple[float, float]],
        levels: Optional[Dict[str, int]]
    ) -> Optional[int]:
        """Get the generation level of parents in a family."""
        if levels is None:
            return None
        
        mother = family.get("mother")
        father = family.get("father")
        
        parent_levels = []
        for parent in [mother, father]:
            if parent and parent in levels:
                parent_levels.append(levels[parent])
        
        if parent_levels:
            return max(parent_levels)  # Use higher parent level
        return None
    
    def _get_child_level(
        self,
        family: Dict[str, Any],
        animal_positions: Dict[str, Tuple[float, float]],
        levels: Optional[Dict[str, int]]
    ) -> Optional[int]:
        """Get the generation level of children in a family."""
        if levels is None:
            return None
        
        children = family.get("children", [])
        
        child_levels = []
        for child in children:
            if child and child in levels:
                child_levels.append(levels[child])
        
        if child_levels:
            return min(child_levels)  # Use lower child level
        return None
    
    def optimize_with_constraints(
        self,
        families: Dict[str, Dict[str, Any]],
        animal_positions: Dict[str, Tuple[float, float]],
        levels: Dict[str, int],
        min_gap: float = 0.5
    ) -> Dict[str, Tuple[float, float]]:
        """Optimize family positions while maintaining minimum spacing.
        
        This version ensures family nodes don't overlap by maintaining
        a minimum gap between adjacent family nodes at the same level.
        
        Args:
            families: Family structures
            animal_positions: Animal node positions
            levels: Generation levels
            min_gap: Minimum horizontal gap between family nodes
            
        Returns:
            Optimized family positions with spacing constraints
        """
        # Get base positions
        positions = self.optimize_family_positions(families, animal_positions, levels)
        
        # Group by y-level
        level_groups: Dict[float, list] = defaultdict(list)
        for fid, (x, y) in positions.items():
            level_groups[y].append((fid, x))
        
        # Sort each level by x and enforce minimum gap
        adjusted_positions = dict(positions)
        
        for y_level, family_list in level_groups.items():
            if len(family_list) <= 1:
                continue
            
            # Sort by current x position
            family_list.sort(key=lambda item: item[1])
            
            # Enforce minimum gap
            for i in range(1, len(family_list)):
                prev_fid, prev_x = family_list[i - 1]
                curr_fid, curr_x = family_list[i]
                
                gap = curr_x - prev_x
                if gap < min_gap:
                    # Shift current and all subsequent families
                    shift = min_gap - gap
                    for j in range(i, len(family_list)):
                        fid, old_x = family_list[j]
                        _, old_y = adjusted_positions[fid]
                        adjusted_positions[fid] = (old_x + shift, old_y)
                        family_list[j] = (fid, old_x + shift)
        
        return adjusted_positions
