# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Module: Master Track per-user session persistence.

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionManager:
    """Load / save per-user session files under ``sessions/``."""

    def __init__(self, plugin_dir: str):
        self.sessions_dir = os.path.join(plugin_dir, "sessions")
        os.makedirs(self.sessions_dir, exist_ok=True)

    def _path(self, username: str) -> str:
        safe = "".join(c if c.isalnum() or c in ("_", "-") else "_" for c in username)
        return os.path.join(self.sessions_dir, f"{safe}.json")

    def load(self, username: str) -> Dict[str, Any]:
        path = self._path(username)
        if not os.path.isfile(path):
            return self._defaults(username)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data
        except Exception as exc:
            logger.error("Failed to load session for %s: %s", username, exc)
            return self._defaults(username)

    def save(self, username: str, data: Dict[str, Any]) -> None:
        data["username"] = username
        path = self._path(username)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.error("Failed to save session for %s: %s", username, exc)

    def delete(self, username: str) -> None:
        path = self._path(username)
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError:
                pass

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
