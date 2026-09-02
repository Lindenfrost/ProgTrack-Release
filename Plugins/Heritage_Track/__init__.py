# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track plugin bootstrap.

import logging

from .display_context import (
    DisplayContext,
    DisplayContextBuilder,
    FrozenRoutePlan,
    RenderCacheEntry,
    RenderCacheKey,
    RenderCacheRegistry,
)
from .display_strategies import (
    CompositeDisplayStrategy,
    DisplaySetStrategy,
    SelectedAnimalsStrategy,
)
from .engine_cache import PedigreeEngineCache
from .ghost_strategies import (
    ArchivedGhostStrategy,
    CompositeGhostStrategy,
    GhostNodeStrategy,
    NoGhostStrategy,
)
from .heritage_track_widget import HeritageTrackPlugin
from .layout_pipeline import LayoutPipeline
from .pedigree_router import PedigreeRouter, RoutePlan

__all__ = [
    # Main plugin
    "HeritageTrackPlugin",
    "initialize",
    # Display context
    "DisplayContext",
    "DisplayContextBuilder",
    "FrozenRoutePlan",
    "RenderCacheEntry",
    "RenderCacheKey",
    "RenderCacheRegistry",
    # Strategies
    "DisplaySetStrategy",
    "SelectedAnimalsStrategy",
    "CompositeDisplayStrategy",
    "GhostNodeStrategy",
    "ArchivedGhostStrategy",
    "CompositeGhostStrategy",
    "NoGhostStrategy",
    # Layout
    "LayoutPipeline",
    "PedigreeRouter",
    "RoutePlan",
    # Performance & Layout Improvements
    "PedigreeEngineCache",
]


def initialize(app):
    """Initialize Heritage_Track plugin and return plugin instance."""
    try:
        return HeritageTrackPlugin(app)
    except Exception:
        logging.getLogger(__name__).exception("HeritageTrack plugin initialization failed")
        return None
