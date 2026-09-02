# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Master Track plugin bootstrap.

from .plugin import MasterTrackPlugin


def initialize(app):
    """Initialize Master_Track plugin and return plugin instance."""
    try:
        return MasterTrackPlugin(app)
    except Exception:
        import traceback
        traceback.print_exc()
        return None
