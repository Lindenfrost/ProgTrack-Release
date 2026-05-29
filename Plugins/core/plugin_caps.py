# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Compatibility: bundled with ProgTrack 0.1.0 RC.
# Module: Shared plugin capability helpers.

from __future__ import annotations

from typing import Any


def has_master_track(app: Any) -> bool:
    """Return True if Master_Track is installed and not disabled."""
    mt = getattr(app, 'master_track', None)
    if mt is None:
        return False
    disabled = getattr(app, '_disabled_plugins', set())
    return "master_track" not in disabled


def has_project_track(app: Any) -> bool:
    """Return True if Project_Track (Projects_Track) is installed and active."""
    plugin = getattr(app, 'projects_track_plugin', None)
    if plugin is None:
        return False
    disabled = getattr(app, '_disabled_plugins', set())
    return "projects_track" not in disabled


def has_heritage_track(app: Any) -> bool:
    """Return True if Heritage_Track is installed and its window is available."""
    plugin = getattr(app, 'heritage_plugin', None)
    if plugin is None:
        return False
    disabled = getattr(app, '_disabled_plugins', set())
    return "heritage_track" not in disabled


def has_network_track(app: Any) -> bool:
    """Return True if Network_Track is installed and active."""
    window = getattr(app, 'network_track_window', None)
    if window is None:
        return False
    disabled = getattr(app, '_disabled_plugins', set())
    return "network_track" not in disabled


def has_medi_track(app: Any) -> bool:
    """Return True if Medi_Track is installed and active."""
    plugin = getattr(app, 'medi_track_plugin', None)
    if plugin is None:
        return False
    disabled = getattr(app, '_disabled_plugins', set())
    return "medi_track" not in disabled


def has_cage_track(app: Any) -> bool:
    """Return True if Cage_Track is installed and active."""
    plugin = getattr(app, 'cage_track_plugin', None)
    if plugin is None:
        return False
    disabled = getattr(app, '_disabled_plugins', set())
    return "cage_track" not in disabled


def has_steroid_track(app: Any) -> bool:
    """Return True if Steroid_track is installed and active."""
    enabled = getattr(app, 'steroid_track_enabled', False)
    return bool(enabled)
