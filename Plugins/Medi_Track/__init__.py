# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Medi Track plugin bootstrap.

from __future__ import annotations


def initialize(app):
    """Entry point called by ProgTrackApp to load the plugin."""
    try:
        from .medi_track_widget import MediTrackPlugin
        return MediTrackPlugin(app)
    except Exception as exc:  # pragma: no cover
        import logging
        logging.getLogger(__name__).error(
            "Medi_Track plugin failed to initialize: %s", exc, exc_info=True)
        return None
