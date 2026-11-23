# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.3.0
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Master Track per-user session persistence.

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Dict

from Plugins.core.backend.errors import PermissionDeniedError

logger = logging.getLogger(__name__)


class SessionManager:
    """Load/save per-user session records in the configured backend."""

    def __init__(self, plugin_dir: str, backend=None, *, session=None):
        if backend is None:
            raise RuntimeError("Master Track sessions require the configured ProgTrack backend.")
        self.backend = backend
        self.session = session or (lambda: None)

    @contextmanager
    def _scope(self, username, *, write=False):
        if getattr(getattr(self.backend, "adapter", None), "dialect", "") != "postgresql":
            yield username
            return
        with self.backend.protected_request(self.session(), write=write) as (_, account, _):
            if account["username"].casefold() != username.casefold():
                raise PermissionDeniedError("These preferences belong to a different account.")
            yield account["username"]

    def exists(self, username: str) -> bool:
        marker = object()
        with self._scope(username) as canonical:
            return self.backend.records.get("sessions", canonical, default=marker) is not marker

    def load(self, username: str) -> Dict[str, Any]:
        with self._scope(username) as canonical:
            value = self.backend.records.get("sessions", canonical, default=self._defaults(canonical))
            return value if isinstance(value, dict) else self._defaults(canonical)

    def save(self, username: str, data: Dict[str, Any]) -> None:
        with self._scope(username, write=True) as canonical:
            self.backend.records.put("sessions", canonical, {**data, "username": canonical})

    def update(self, username: str, updates: Dict[str, Any]) -> None:
        with self._scope(username, write=True) as canonical:
            getter = getattr(self.backend.records, "get_with_revision", None)
            if callable(getter):
                current, revision = getter("sessions", canonical, default=self._defaults(canonical))
            else:
                current = self.backend.records.get("sessions", canonical, default=self._defaults(canonical))
                revision = None
            if not isinstance(current, dict):
                current = self._defaults(canonical)
            updated = {**current, **updates, "username": canonical}
            if revision is None:
                self.backend.records.put("sessions", canonical, updated)
            else:
                self.backend.records.put("sessions", canonical, updated, expected_revision=revision)

    def delete(self, username: str) -> None:
        with self._scope(username, write=True) as canonical:
            self.backend.records.delete("sessions", canonical)

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
