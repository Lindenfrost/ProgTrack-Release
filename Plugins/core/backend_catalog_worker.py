# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""One bounded read-only catalog request, with no access to dialog widgets."""

from dataclasses import dataclass
from math import ceil
from threading import Event
from time import monotonic

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from .backend.catalog_cache import CatalogSnapshot, discover_snapshot
from .backend.postgresql_admin import PostgreSQLAdministrationService


class BoundedCatalogReader(PostgreSQLAdministrationService):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._deadline = None
        self._cancel = None

    def bind_budget(self, cancel):
        self._deadline = monotonic() + 30
        self._cancel = cancel

    def verify_budget(self):
        if self._cancel.is_set() or monotonic() >= self._deadline:
            raise TimeoutError("The catalog request was cancelled or timed out.")

    def _connect(self, database, *, operation=None, autocommit=False):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        if operation is not None or autocommit:
            raise ValueError("A catalog reader may only open read-only connections.")
        self.verify_budget()
        remaining = self._deadline - monotonic()
        options = conninfo_to_dict(self._dsn(database))
        options['connect_timeout'] = min(self.settings.connect_timeout, max(2, ceil(remaining)))
        milliseconds = max(1, int(remaining * 1000))
        options['options'] = (
            f'-c default_transaction_read_only=on -c statement_timeout={milliseconds} '
            f'-c lock_timeout={milliseconds}'
        )
        return psycopg.connect(make_conninfo(**options))


@dataclass(frozen=True)
class CatalogReadResult:
    snapshot: CatalogSnapshot | None = None
    usable: bool = False
    status: str = 'discovery_failed'
    persist: bool = False


class _ReadSignals(QObject):
    finished = pyqtSignal(int, object)


class CatalogReadWorker(QRunnable):
    def __init__(self, generation, reader, cached):
        super().__init__()
        self.generation = generation
        self.reader = reader
        self.cached = cached
        self.signals = _ReadSignals()
        self.cancelled = Event()

    def run(self):
        if self.cancelled.is_set():
            return
        bounded = isinstance(self.reader, BoundedCatalogReader)
        if bounded:
            self.reader.bind_budget(self.cancelled)
        try:
            result = self._read()
            if bounded:
                self.reader.verify_budget()
        except TimeoutError:
            result = (CatalogReadResult(self.cached, status='stale') if self.cached is not None else CatalogReadResult())
        except Exception:
            result = CatalogReadResult()
        finally:
            if bounded:
                self.reader._discovery_password = ''
                self.reader._client_key_passphrase = ''
        if not self.cancelled.is_set():
            self.signals.finished.emit(self.generation, result)

    def _read(self):
        before = self.reader.catalog_revision()
        if self.cancelled.is_set():
            return CatalogReadResult()
        if self.cached is not None:
            if before is None:
                return CatalogReadResult(self.cached, status='stale')
            if before == self.cached.revision and not before.dirty:
                return CatalogReadResult(self.cached, True, 'current')
        snapshot, coherent = discover_snapshot(self.reader, before, cancelled=self.cancelled.is_set)
        if not coherent:
            return CatalogReadResult(snapshot, status='dirty')
        return CatalogReadResult(snapshot, True, 'current' if snapshot.revision else 'stale', True)
