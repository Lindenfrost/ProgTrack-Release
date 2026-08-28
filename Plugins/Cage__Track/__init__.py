# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Cage Track plugin bootstrap.

from .cage_track_widget import CageTrackPlugin


def initialize(app):
    """Initialize Cage_Track plugin and return plugin instance."""
    try:
        return CageTrackPlugin(app)
    except Exception:
        import traceback

        traceback.print_exc()
        return None
