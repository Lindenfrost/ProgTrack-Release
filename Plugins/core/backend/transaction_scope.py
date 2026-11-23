# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Share one request transaction across repositories and derived writes."""

from contextlib import contextmanager
from contextvars import ContextVar
from itertools import count


class TransactionScopeAdapter:
    def __init__(self, adapter):
        self._adapter = adapter
        self._current = ContextVar("progtrack_request_transaction", default=None)
        self._savepoints = count()

    def __getattr__(self, name):
        return getattr(self._adapter, name)

    @property
    def in_transaction(self):
        return self._current.get() is not None

    @contextmanager
    def transaction(self, *, write=False, snapshot=False):
        active = self._current.get()
        if active is None:
            options = {"snapshot": True} if snapshot and self.dialect == "postgresql" else {}
            with self._adapter.transaction(write=write, **options) as connection:
                marker = self._current.set((connection, write))
                try:
                    yield connection
                finally:
                    self._current.reset(marker)
            return

        connection, writable = active
        if write and not writable:
            raise RuntimeError("A read-only request cannot be upgraded to a write request.")
        if not write:
            yield connection
            return
        # A repository may encounter a handled conflict. Roll its work back
        # before returning control to the enclosing request.
        name = f"progtrack_nested_{next(self._savepoints)}"
        connection.execute(f"SAVEPOINT {name}")
        try:
            yield connection
        except BaseException:
            connection.execute(f"ROLLBACK TO SAVEPOINT {name}")
            raise
        finally:
            connection.execute(f"RELEASE SAVEPOINT {name}")
