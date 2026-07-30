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

    def save(self, value: Any) -> int:
        return self.backend.records.put(
            self.namespace, self.record_id, copy.deepcopy(value)
        )

