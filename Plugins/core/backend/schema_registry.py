"""Central ordered schema-revision registry for optional plugins."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .repositories import _execute, _fetchone, _placeholder, now_text


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
        """Apply adapter-specific, ordered, idempotent component revisions."""
        current = self.current_revision(component)
        for revision in sorted(revisions):
            if revision <= current:
                continue
            sql = str(revisions[revision].get(self.adapter.dialect, "")).strip()
            if not sql:
                raise ValueError(
                    f"{component} revision {revision} has no "
                    f"{self.adapter.dialect} implementation."
                )
            mark = _placeholder(self.adapter)
            with self.adapter.transaction(write=True) as connection:
                _execute(connection, sql)
                if self.adapter.dialect == "sqlite":
                    _execute(
                        connection,
                        "INSERT INTO schema_revisions(component,revision,applied_at) "
                        "VALUES(?,?,?) ON CONFLICT(component) DO UPDATE SET "
                        "revision=excluded.revision,applied_at=excluded.applied_at",
                        (component, revision, now_text()),
                    )
                else:
                    _execute(
                        connection,
                        "INSERT INTO schema_revisions(component,revision,applied_at) "
                        "VALUES(%s,%s,%s) ON CONFLICT(component) DO UPDATE SET "
                        "revision=EXCLUDED.revision,applied_at=EXCLUDED.applied_at",
                        (component, revision, now_text()),
                    )
            current = revision
        return current
