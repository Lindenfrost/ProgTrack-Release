# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Sample Track plugin bootstrap.

from __future__ import annotations


def initialize(app):
    """Entry point called by ProgTrackApp._init_sample_track_plugin()."""
    try:
        from .sample_track_widget import SampleTrackPlugin
        return SampleTrackPlugin(app)
    except Exception as exc:  # pragma: no cover
        import logging
        logging.getLogger(__name__).error(
            "Sample_Track plugin failed to initialize: %s", exc, exc_info=True)
        return None
