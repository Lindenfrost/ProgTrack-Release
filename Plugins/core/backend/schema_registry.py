# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Central ordered schema-revision registry for optional plugins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import BackendSchemaError
from .repositories import _fetchone, _placeholder


class SchemaRegistry:
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def current_revision(self, component: str) -> int:
        mark = _placeholder(self.adapter)
        with self.adapter.transaction() as connection:
            row = _fetchone(
                connection,
                f"SELECT revision FROM schema_revisions WHERE component={mark}",
                (component,),
            )
        if row is None:
            return 0
        return int(row["revision"] if isinstance(row, dict) else row[0])

    def apply(
        self,
        component: str,
        revisions: Mapping[int, Mapping[str, str]],
    ) -> int:
        """Require an installed revision from the central deployment registry."""
        from .migration import PLUGIN_MIGRATIONS

        registration = PLUGIN_MIGRATIONS.get(component)
        if registration is None or revisions != registration[1]:
            raise BackendSchemaError(f"Schema migrations for {component} must be registered centrally.")
        current = self.current_revision(component)
        if current != max(revisions):
            raise BackendSchemaError(f"Schema for {component} requires administrative migration.")
        return current
