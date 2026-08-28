# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Master Track per-user session persistence.

from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


class SessionManager:
    """Load/save per-user session records in the configured backend."""

    def __init__(self, plugin_dir: str, backend=None):
        if backend is None:
            raise RuntimeError("Master Track sessions require the configured ProgTrack backend.")
        self.backend = backend

    def exists(self, username: str) -> bool:
        marker = object()
        return self.backend.records.get(
            "sessions", username, default=marker
        ) is not marker

    def load(self, username: str) -> Dict[str, Any]:
        value = self.backend.records.get(
            "sessions", username, default=self._defaults(username)
        )
        return value if isinstance(value, dict) else self._defaults(username)

    def save(self, username: str, data: Dict[str, Any]) -> None:
        data["username"] = username
        self.backend.records.put("sessions", username, data)

    def delete(self, username: str) -> None:
        self.backend.records.delete("sessions", username)

    @staticmethod
    def _defaults(username: str) -> Dict[str, Any]:
        return {
            "username": username,
            "disabled_plugins": [],
            "last_active_tab": "tab.plots",
            "last_animal_group": "",
            "last_category_index": 0,
            "window_geometry": None,
            "active_species": None,
            "active_project": "All",
            "species_cache": [],
            "max_parent_generations": 3,
            "projects_sidebar_visible": True,
            "species_sidebar_visible": True,
            "style_settings": {},
            "language": None,
            "display_chk_prog": True,
            "display_chk_weight": True,
            "display_chk_events": True,
            "display_chk_events_offspring": True,
            "display_chk_events_breeding": True,
            "display_chk_events_experimental": True,
        }
