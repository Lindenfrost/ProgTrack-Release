# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track hierarchical edge bundling.

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple


class BundledEdge:
    """Represents a group of edges that are bundled together."""
    
    def __init__(
        self,
        edges: List[Tuple[str, str]],
        control_points: Optional[List[Tuple[float, float]]] = None
    ):
        """Initialize a bundled edge.
        
        Args:
            edges: List of (source, target) node pairs
            control_points: Control points for the bundle path (if bundling)
        """
        self.edges = edges
        self.control_points = control_points
    
    def is_bundled(self) -> bool:
        """Check if this edge group is actually bundled."""
        return self.control_points is not None and len(self.edges) > 1
    
    def __len__(self) -> int:
        return len(self.edges)


class EdgeBundler:
    """Bundles edges to reduce visual clutter in pedigree graphs.
    
    In complex pedigrees with many offspring from the same parents,
    the graph can become cluttered with many parallel lines. This
    class groups edges by source/destination families and routes them
    along common paths to improve readability.
    """
    
    def __init__(self, bundle_threshold: int = 2, curvature: float = 0.3):
        """Initialize the edge bundler.
        
        Args:
            bundle_threshold: Minimum number of edges to form a bundle
            curvature: Control point offset factor (0 = straight, 1 = max curve)
        """
        self.bundle_threshold = bundle_threshold
        self.curvature = curvature
    
    def bundle_edges(
        self,
        families: Dict[str, Dict[str, Any]],
        positions: Dict[str, Tuple[float, float]]
    ) -> List[BundledEdge]:
        """Bundle edges to reduce visual clutter.
        
        Args:
            families: Family structures with mother/father/children
            positions: Node positions for computing bundle paths
            
        Returns:
            List of BundledEdge objects (some bundled, some not)
        """
        # Group edges by (parent_family, child_family) pairs
        edge_groups: Dict[Tuple[str, str], List[Tuple[str, str]]] = defaultdict(list)
        family_of: Dict[str, str] = {}
        
        # Build node -> family mapping
        for family_id, family in families.items():
            mother = family.get("mother")
            father = family.get("father")
            children = family.get("children", [])
            
            for node in [mother, father] + list(children):
                if node:
                    family_of[node] = family_id
        
        # Group edges
        for family_id, family in families.items():
            mother = family.get("mother")
            father = family.get("father")
            children = family.get("children", [])
            
            for child in children:
                for parent in [mother, father]:
                    if parent and parent in positions and child in positions:
                        src_family = family_of.get(parent, "")
                        dst_family = family_of.get(child, "")
                        key = (src_family, dst_family)
                        edge_groups[key].append((parent, child))
        
        # Create bundled edges
        bundled = []
        
        for (src_family, dst_family), edges in edge_groups.items():
            if len(edges) >= self.bundle_threshold and src_family and dst_family:
                # Compute bundle path
                control_points = self._compute_bundle_path(
                    edges, positions, families, src_family, dst_family
                )
                bundled.append(BundledEdge(edges, control_points))
            else:
                # Don't bundle single edges or edges without family info
                for edge in edges:
                    bundled.append(BundledEdge([edge], None))
        
        return bundled
    
    def _compute_bundle_path(
        self,
        edges: List[Tuple[str, str]],
        positions: Dict[str, Tuple[float, float]],
        families: Dict[str, Dict[str, Any]],
        src_family: str,
        dst_family: str
    ) -> List[Tuple[float, float]]:
        """Compute control points for a bundle path.
        
        Args:
            edges: List of edges in the bundle
            positions: Node positions
            families: Family structures
            src_family: Source family ID
            dst_family: Destination family ID
            
        Returns:
            List of control points for the bundle path
        """
        # Compute average start and end points
        start_x = sum(positions[s][0] for s, _ in edges) / len(edges)
        start_y = sum(positions[s][1] for s, _ in edges) / len(edges)
        end_x = sum(positions[t][0] for _, t in edges) / len(edges)
        end_y = sum(positions[t][1] for _, t in edges) / len(edges)
        
        # Compute perpendicular offset for curve
        dx = end_x - start_x
        dy = end_y - start_y
        dist = (dx * dx + dy * dy) ** 0.5
        
        if dist < 0.001:
            return [(start_x, start_y), (end_x, end_y)]
        
        # Perpendicular unit vector
        perp_x = -dy / dist * self.curvature
        perp_y = dx / dist * self.curvature
        
        # Control point at midpoint with offset
        mid_x = (start_x + end_x) / 2 + perp_x
        mid_y = (start_y + end_y) / 2 + perp_y
        
        return [(start_x, start_y), (mid_x, mid_y), (end_x, end_y)]
    
    def render_bundle(
        self,
        bundled: BundledEdge,
        positions: Dict[str, Tuple[float, float]],
        linewidth: float = 1.0,
        color: str = "black"
    ) -> List[Dict[str, Any]]:
        """Generate render commands for a bundled edge group.
        
        Args:
            bundled: BundledEdge to render
            positions: Node positions
            linewidth: Line width for rendering
            color: Color for rendering
            
        Returns:
            List of render command dictionaries
        """
        commands = []
        
        if bundled.is_bundled() and bundled.control_points:
            # Render as a thick bundle line
            commands.append({
                "type": "bundle",
                "points": bundled.control_points,
                "linewidth": linewidth * 2,
                "color": color,
                "alpha": 0.6,
            })
            
            # Render individual connections with thin lines
            for source, target in bundled.edges:
                if source in positions and target in positions:
                    commands.append({
                        "type": "line",
                        "from": positions[source],
                        "to": positions[target],
                        "linewidth": linewidth * 0.5,
                        "color": color,
                        "alpha": 0.4,
                    })
        else:
            # Render as individual lines
            for source, target in bundled.edges:
                if source in positions and target in positions:
                    commands.append({
                        "type": "line",
                        "from": positions[source],
                        "to": positions[target],
                        "linewidth": linewidth,
                        "color": color,
                        "alpha": 1.0,
                    })
        
        return commands
