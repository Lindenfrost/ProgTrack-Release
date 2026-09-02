# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Projects Track plugin bootstrap.

from .ProjectsTrack_plugin import ProjectsTrackPlugin


def initialize(app):
    """Initialize the ProjectsTrack plugin.
    
    Called by ProgTrack's plugin loader during startup.
    
    Args:
        app: The main ProgTrackApp instance
        
    Returns:
        ProjectsTrackPlugin instance or None if initialization fails
    """
    try:
        plugin = ProjectsTrackPlugin(app)
        return plugin
    except Exception as e:
        import traceback
        traceback.print_exc()
        return None
