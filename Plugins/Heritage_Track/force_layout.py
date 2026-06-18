# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track force-directed layout helpers.

from __future__ import annotations

import math
from collections import defaultdict
from itertools import combinations
from typing import Any, Dict, List, Optional, Set, Tuple


class ForceDirectedLayout:
    """Physics-based layout with pedigree constraints.
    
    This layout algorithm uses a force-directed simulation to resolve
    node overlaps while maintaining the pedigree structure. It's especially
    effective for large, dense pedigree graphs where simple de-overlap
    passes produce poor results.
    
    Key features:
    - Repulsion between all nodes (prevents overlap)
    - Attraction along parent-child edges (keeps families together)
    - Hard level constraints (maintains generational hierarchy)
    - Locked position constraints (preserves user preferences)
    """
    
    def __init__(
        self,
        node_spacing: float = 1.25,
        level_spacing: float = 2.0,
        repulsion_force: float = 100.0,
        attraction_force: float = 0.1,
        level_constraint_strength: float = 10.0,
        max_iterations: int = 100,
        convergence_threshold: float = 0.01,
    ):
        """Initialize the force-directed layout.
        
        Args:
            node_spacing: Minimum desired spacing between node centers
            level_spacing: Vertical spacing between generation levels
            repulsion_force: Strength of node-node repulsion
            attraction_force: Strength of parent-child attraction
            level_constraint_strength: How strongly to enforce level constraints
            max_iterations: Maximum simulation iterations
            convergence_threshold: Stop when movement falls below this
        """
        self.node_spacing = node_spacing
        self.level_spacing = level_spacing
        self.repulsion_force = repulsion_force
        self.attraction_force = attraction_force
        self.level_constraint_strength = level_constraint_strength
        self.max_iterations = max_iterations
        self.convergence_threshold = convergence_threshold
    
    def compute_positions(
        self,
        nodes: Set[str],
        levels: Dict[str, int],
        families: Dict[str, Dict[str, Any]],
        locked_positions: Optional[Dict[str, Tuple[float, float]]] = None,
        initial_positions: Optional[Dict[str, Tuple[float, float]]] = None,
    ) -> Dict[str, Tuple[float, float]]:
        """Compute positions using force-directed simulation.
        
        Args:
            nodes: Set of all nodes to position
            levels: Generation level for each node (0 = founders)
            families: Family structures with mother/father/children
            locked_positions: Nodes that shouldn't move (user-dragged)
            initial_positions: Starting positions (if available)
            
        Returns:
            Dictionary mapping node names to (x, y) positions
        """
        locked = locked_positions or {}
        
        # Initialize positions
        positions = self._initialize_positions(nodes, levels, initial_positions)
        
        # Apply locked positions immediately
        for node, pos in locked.items():
            if node in positions:
                positions[node] = pos
        
        # Build edge list for attractions
        edges = self._build_edges(families)
        
        # Run simulation
        for iteration in range(self.max_iterations):
            forces: Dict[str, Tuple[float, float]] = defaultdict(lambda: (0.0, 0.0))
            
            # 1. Repulsion forces between all node pairs
            for node1, node2 in combinations(nodes, 2):
                if node1 in locked and node2 in locked:
                    continue  # Both locked, skip
                    
                x1, y1 = positions[node1]
                x2, y2 = positions[node2]
                
                dx = x1 - x2
                dy = y1 - y2
                dist_sq = dx * dx + dy * dy
                
                if dist_sq < 0.001:  # Avoid division by zero
                    dx, dy = 0.1, 0.1
                    dist_sq = 0.02
                
                # Repulsion force (inverse square law)
                force = self.repulsion_force / dist_sq
                dist = math.sqrt(dist_sq)
                
                fx = (dx / dist) * force
                fy = (dy / dist) * force
                
                if node1 not in locked:
                    f1 = forces[node1]
                    forces[node1] = (f1[0] + fx, f1[1] + fy)
                
                if node2 not in locked:
                    f2 = forces[node2]
                    forces[node2] = (f2[0] - fx, f2[1] - fy)
            
            # 2. Attraction forces along parent-child edges
            for parent, child in edges:
                if parent not in nodes or child not in nodes:
                    continue
                if parent in locked and child in locked:
                    continue
                
                px, py = positions[parent]
                cx, cy = positions[child]
                
                dx = cx - px
                dy = cy - py
                
                # Attraction force (Hooke's law)
                fx = dx * self.attraction_force
                fy = dy * self.attraction_force
                
                if parent not in locked:
                    p_f = forces[parent]
                    forces[parent] = (p_f[0] + fx, p_f[1] + fy)
                
                if child not in locked:
                    c_f = forces[child]
                    forces[child] = (c_f[0] - fx, c_f[1] - fy)
            
            # 3. Apply forces and level constraints
            max_movement = 0.0
            
            for node in nodes:
                if node in locked:
                    continue
                
                x, y = positions[node]
                fx, fy = forces[node]
                
                # Apply movement (with damping)
                new_x = x + fx * 0.1
                new_y = y + fy * 0.1
                
                # Enforce level constraint (hard constraint)
                level = levels.get(node, 0)
                target_y = -level * self.level_spacing  # Negative for display
                new_y = new_y + (target_y - new_y) * self.level_constraint_strength
                
                # Track movement for convergence
                movement = math.sqrt((new_x - x) ** 2 + (new_y - y) ** 2)
                max_movement = max(max_movement, movement)
                
                positions[node] = (new_x, new_y)
            
            # Check for convergence
            if max_movement < self.convergence_threshold:
                break
        
        return positions
    
    def _initialize_positions(
        self,
        nodes: Set[str],
        levels: Dict[str, int],
        initial_positions: Optional[Dict[str, Tuple[float, float]]]
    ) -> Dict[str, Tuple[float, float]]:
        """Create initial positions for all nodes."""
        positions: Dict[str, Tuple[float, float]] = {}
        
        # Group by level
        level_groups: Dict[int, List[str]] = defaultdict(list)
        for node in nodes:
            lvl = levels.get(node, 0)
            level_groups[lvl].append(node)
        
        # Sort each level
        for lvl in level_groups:
            level_groups[lvl].sort(key=str.lower)
        
        # Position nodes by level
        x_pos = 0.0
        for lvl in sorted(level_groups.keys(), reverse=True):
            nodes_at_level = level_groups[lvl]
            y = -lvl * self.level_spacing
            
            for i, node in enumerate(nodes_at_level):
                if initial_positions and node in initial_positions:
                    positions[node] = initial_positions[node]
                else:
                    # Spread horizontally with spacing
                    x = x_pos + i * self.node_spacing
                    positions[node] = (x, y)
            
            # Update x_pos for next level (staggered)
            x_pos += self.node_spacing * 0.5
        
        return positions
    
    def _build_edges(
        self,
        families: Dict[str, Dict[str, Any]]
    ) -> List[Tuple[str, str]]:
        """Build list of parent-child edges."""
        edges: List[Tuple[str, str]] = []
        
        for family in families.values():
            mother = family.get("mother")
            father = family.get("father")
            children = family.get("children", [])
            
            for child in children:
                if mother:
                    edges.append((mother, child))
                if father:
                    edges.append((father, child))
        
        return edges


class OverlapDetector:
    """Detects and reports overlapping nodes in a layout."""
    
    def __init__(self, node_radius: float = 0.5):
        self.node_radius = node_radius
    
    def find_overlaps(
        self,
        positions: Dict[str, Tuple[float, float]]
    ) -> List[Tuple[str, str]]:
        """Find all pairs of overlapping nodes."""
        overlaps = []
        nodes = list(positions.keys())
        
        for i, n1 in enumerate(nodes):
            for n2 in nodes[i+1:]:
                x1, y1 = positions[n1]
                x2, y2 = positions[n2]
                
                dist_sq = (x1 - x2) ** 2 + (y1 - y2) ** 2
                min_dist = 2 * self.node_radius
                
                if dist_sq < min_dist * min_dist:
                    overlaps.append((n1, n2))
        
        return overlaps
    
    def count_overlaps(self, positions: Dict[str, Tuple[float, float]]) -> int:
        """Count total number of overlapping node pairs."""
        return len(self.find_overlaps(positions))
