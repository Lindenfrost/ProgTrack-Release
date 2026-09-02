# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Embryo Track plugin bootstrap.

from .embryo_track import EmbryoTrackerWidget, show_embryo_tracker

__version__ = "1.0.0"
__all__ = ['EmbryoTrackerWidget', 'show_embryo_tracker']
