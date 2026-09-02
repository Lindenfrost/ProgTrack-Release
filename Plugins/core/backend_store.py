"""Small compatibility repository for plugin-owned structured records.

This is intentionally not a JSON-file fallback. It lets existing plugin
models keep their in-memory dictionaries while persistence is owned by the
shared backend and stable namespace/record IDs.
"""

from __future__ import annotations

import copy
from typing import Any


class BackendJsonStore:
    def __init__(
        self,
        backend: Any,
        namespace: str,
        record_id: str = "root",
    ):
        if backend is None or not hasattr(backend, "records"):
            raise RuntimeError(
                f"BackendJsonStore {namespace}/{record_id} requires ProgTrackBackend."
            )
        self.backend = backend
        self.namespace = namespace
        self.record_id = record_id

    def load(self, default: Any) -> Any:
        value = self.backend.records.get(
            self.namespace, self.record_id, default=None
        )
        return copy.deepcopy(default if value is None else value)

    def load_with_revision(self, default: Any = None) -> tuple[Any, int]:
        """Load one record together with its backend revision when available.

        The in-memory test backends used by plugins predate optimistic
        revisions, so the small fallback deliberately reports revision zero.
        Production repositories expose ``get_with_revision`` and therefore
        retain the normal conflict detection contract.
        """
        getter = getattr(self.backend.records, "get_with_revision", None)
        if callable(getter):
            value, revision = getter(self.namespace, self.record_id, default=None)
            return copy.deepcopy(default if value is None else value), int(revision or 0)
        return self.load(default), 0

    def save(self, value: Any, *, expected_revision: int | None = None) -> int:
        payload = copy.deepcopy(value)
        try:
            return self.backend.records.put(
                self.namespace,
                self.record_id,
                payload,
                expected_revision=expected_revision,
            )
        except TypeError:
            # Compatibility with the deliberately tiny in-memory stores used
            # by legacy plugin tests.
            return self.backend.records.put(self.namespace, self.record_id, payload)
