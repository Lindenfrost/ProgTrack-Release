# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Network Track plugin bootstrap.

from .network_track import NetworkTrackWidget

__version__ = "1.0.0"
__all__ = ['NetworkTrackWidget']
