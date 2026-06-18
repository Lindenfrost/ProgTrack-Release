# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track plugin bootstrap.

from .adaptive_spacer import AdaptiveSpacer
from .display_context import DisplayContext, DisplayContextBuilder
from .display_strategies import (
    AllAnimalsStrategy,
    CompositeDisplayStrategy,
    DisplaySetStrategy,
    SelectedAnimalsStrategy,
)
from .edge_bundler import BundledEdge, EdgeBundler
from .engine_cache import PedigreeEngineCache
from .family_optimizer import FamilyPositionOptimizer
from .force_layout import ForceDirectedLayout, OverlapDetector
from .ghost_strategies import (
    ArchivedGhostStrategy,
    CompositeGhostStrategy,
    GhostNodeStrategy,
    NoGhostStrategy,
    ScopeGhostStrategy,
)
from .heritage_track_widget import HeritageTrackPlugin
from .layout_pipeline import LayoutPipeline
from .scope_provider import (
    ExplicitScopeProvider,
    NullScopeProvider,
    ProjectsTrackScopeProvider,
    ScopeFilter,
    ScopeProvider,
)
from .viewport_manager import ViewportManager

__all__ = [
    # Main plugin
    "HeritageTrackPlugin",
    "initialize",
    # Display context
    "DisplayContext",
    "DisplayContextBuilder",
    # Strategies
    "DisplaySetStrategy",
    "SelectedAnimalsStrategy",
    "AllAnimalsStrategy",
    "CompositeDisplayStrategy",
    "GhostNodeStrategy",
    "ScopeGhostStrategy",
    "ArchivedGhostStrategy",
    "CompositeGhostStrategy",
    "NoGhostStrategy",
    # Scope
    "ScopeProvider",
    "NullScopeProvider",
    "ProjectsTrackScopeProvider",
    "ExplicitScopeProvider",
    "ScopeFilter",
    # Layout
    "LayoutPipeline",
    # Performance & Layout Improvements
    "PedigreeEngineCache",
    "ForceDirectedLayout",
    "OverlapDetector",
    "EdgeBundler",
    "BundledEdge",
    "FamilyPositionOptimizer",
    "AdaptiveSpacer",
    "ViewportManager",
]


def initialize(app):
    """Initialize Heritage_Track plugin and return plugin instance."""
    try:
        return HeritageTrackPlugin(app)
    except Exception:
        import traceback

        traceback.print_exc()
        return None
