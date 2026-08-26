"""Database adapter implementations with one transaction contract."""

from __future__ import annotations

import atexit
import json
import os
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Sequence

from ..runtime_paths import (
    BackendProfile,
    RuntimePaths,
    validate_standalone_sqlite_path,
)
from .errors import BackendConfigurationError, StandaloneLockError
from .schema import POSTGRESQL_MIGRATIONS, SQLITE_MIGRATIONS


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StandaloneProcessLock:
    """Exclusive one-process ownership for a writable Standalone backend."""

    # Older releases created the file before writing its JSON payload.  A
    # crash in that tiny window leaves an empty/invalid lock which otherwise
    # cannot be distinguished from an active owner.
    _INCOMPLETE_LOCK_GRACE_SECONDS = 5.0

    def __init__(self, path: Path):
        self.path = path
        self._owned = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "started_at": utc_now(),
        }
        for attempt in range(2):
            try:
                descriptor = os.open(
                    self.path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o600,
                )
                break
            except FileExistsError as exc:
                owner = _read_lock_owner(self.path)
                stale = _lock_is_stale(self.path, owner)
                if stale and attempt == 0:
                    try:
                        self.path.unlink()
                        continue
                    except OSError:
                        pass
                raise StandaloneLockError(
                    lock_path=str(self.path), owner=owner
                ) from exc
        else:
            descriptor = None
        if descriptor is None:
            raise BackendConfigurationError("Could not acquire Standalone lock.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
        except (OSError, TypeError, ValueError):
            # Do not leave an owner-unknown file behind when metadata writing
            # itself fails before the backend has started.
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        self._owned = True
        atexit.register(self.release)

    def release(self) -> None:
        if self._owned:
            try:
                self.path.unlink(missing_ok=True)
            finally:
                self._owned = False


def _read_lock_owner(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return "unknown"


def _lock_age_seconds(path: Path) -> float:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 0.0


def _lock_is_stale(path: Path, owner: Any) -> bool:
    """Return whether a lock is safe to reclaim after an interrupted start."""
    if isinstance(owner, dict):
        host = str(owner.get("host") or "")
        try:
            pid = int(owner.get("pid") or -1)
        except (TypeError, ValueError):
            pid = -1
        if host == socket.gethostname() and pid > 0:
            return not _pid_is_alive(pid)
        # Missing/invalid owner fields are treated like an interrupted write,
        # but only after the short grace period has elapsed.
        return _lock_age_seconds(path) > StandaloneProcessLock._INCOMPLETE_LOCK_GRACE_SECONDS
    return _lock_age_seconds(path) > StandaloneProcessLock._INCOMPLETE_LOCK_GRACE_SECONDS


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        # ``os.kill(pid, 0)`` is not a safe existence probe on all supported
        # Windows/Python combinations.  Query the process handle instead.
        try:
            import ctypes

            process_query_limited_information = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                process_query_limited_information, False, pid
            )
            if not handle:
                return False
            exit_code = ctypes.c_ulong()
            try:
                if not ctypes.windll.kernel32.GetExitCodeProcess(
                    handle, ctypes.byref(exit_code)
                ):
                    return False
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        except (AttributeError, OSError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class SQLiteAdapter:
    dialect = "sqlite"

    def __init__(self, paths: RuntimePaths, *, acquire_process_lock: bool = True):
        if paths.profile is not BackendProfile.STANDALONE_SQLITE:
            raise BackendConfigurationError("SQLite adapter requires standalone_sqlite.")
        if paths.database_path is None:
            raise BackendConfigurationError("SQLite database path is missing.")
        self.path = validate_standalone_sqlite_path(paths.database_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mutex = threading.RLock()
        self.process_lock = StandaloneProcessLock(paths.runtime / "standalone.lock")
        if acquire_process_lock:
            self.process_lock.acquire()

    def close(self) -> None:
        self.process_lock.release()

    def get_installation_value(self, key: str, default: Any = None) -> Any:
        with self.transaction() as connection:
            row = connection.execute(
                "SELECT value_json FROM installation WHERE key=?", (str(key),)
            ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    def set_installation_value(self, key: str, value: Any) -> None:
        with self.transaction(write=True) as connection:
            connection.execute(
                """
                INSERT INTO installation(key,value_json,revision,updated_at)
                VALUES(?,?,1,?)
                ON CONFLICT(key) DO UPDATE SET
                    value_json=excluded.value_json,
                    revision=installation.revision+1,
                    updated_at=excluded.updated_at
                """,
                (str(key), json.dumps(value, ensure_ascii=False), utc_now()),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=15,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        with self._mutex:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
                yield connection
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()

    def migrate(self) -> None:
        with self._mutex:
            connection = self._connect()
            try:
                for revision in sorted(SQLITE_MIGRATIONS):
                    connection.executescript(SQLITE_MIGRATIONS[revision])
                    connection.execute(
                        """
                        INSERT INTO schema_revisions(component, revision, applied_at)
                        VALUES('core', ?, ?)
                        ON CONFLICT(component) DO UPDATE
                        SET revision=excluded.revision, applied_at=excluded.applied_at
                        """,
                        (revision, utc_now()),
                    )
                connection.commit()
            finally:
                connection.close()

    @staticmethod
    def row_to_dict(row: Any) -> dict[str, Any]:
        return dict(row)


class PostgreSQLAdapter:
    dialect = "postgresql"

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 4,
    ):
        if not dsn.strip():
            raise BackendConfigurationError(
                "PROGTRACK_POSTGRES_DSN is required for shared_postgresql."
            )
        try:
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise BackendConfigurationError(
                "Shared PostgreSQL requires Psycopg 3 and psycopg_pool."
            ) from exc
        self._dict_row = dict_row
        self.pool = ConnectionPool(
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": False, "row_factory": dict_row},
            open=False,
        )
        try:
            self.pool.open(wait=True, timeout=15)
        except Exception as exc:
            self.pool.close()
            raise BackendConfigurationError(
                "Cannot connect to the configured Shared PostgreSQL backend. "
                "Check the server address, database, credentials, TLS policy, "
                "and network availability."
            ) from exc

    def close(self) -> None:
        self.pool.close()

    def get_installation_value(self, key: str, default: Any = None) -> Any:
        with self.transaction() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT value_json FROM installation WHERE key=%s", (str(key),)
                )
                row = cursor.fetchone()
        if row is None:
            return default
        value = row["value_json"] if isinstance(row, dict) else row[0]
        if isinstance(value, (dict, list, str, int, float, bool)):
            return value
        try:
            return json.loads(value)
        except (TypeError, ValueError, json.JSONDecodeError):
            return default

    def set_installation_value(self, key: str, value: Any) -> None:
        with self.transaction(write=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO installation(key,value_json,revision,updated_at)
                    VALUES(%s,%s::jsonb,1,%s)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json=EXCLUDED.value_json,
                        revision=installation.revision+1,
                        updated_at=EXCLUDED.updated_at
                    """,
                    (str(key), json.dumps(value, ensure_ascii=False), utc_now()),
                )

    @contextmanager
    def transaction(self, *, write: bool = False) -> Iterator[Any]:
        del write
        with self.pool.connection() as connection:
            try:
                with connection.transaction():
                    yield connection
            except Exception:
                raise

    def migrate(self) -> None:
        with self.transaction(write=True) as connection:
            with connection.cursor() as cursor:
                for revision in sorted(POSTGRESQL_MIGRATIONS):
                    cursor.execute(POSTGRESQL_MIGRATIONS[revision])
                    cursor.execute(
                        """
                        INSERT INTO schema_revisions(component, revision, applied_at)
                        VALUES('core', %s, %s)
                        ON CONFLICT(component) DO UPDATE
                        SET revision=EXCLUDED.revision, applied_at=EXCLUDED.applied_at
                        """,
                        (revision, utc_now()),
                    )

    @staticmethod
    def row_to_dict(row: Any) -> dict[str, Any]:
        return dict(row)


def create_adapter(
    paths: RuntimePaths,
    *,
    postgres_dsn: str = "",
    postgres_pool_min: int | None = None,
    postgres_pool_max: int | None = None,
    acquire_process_lock: bool = True,
) -> SQLiteAdapter | PostgreSQLAdapter:
    if paths.profile is BackendProfile.STANDALONE_SQLITE:
        return SQLiteAdapter(paths, acquire_process_lock=acquire_process_lock)
    return PostgreSQLAdapter(
        postgres_dsn or os.environ.get("PROGTRACK_POSTGRES_DSN", ""),
        min_size=int(
            os.environ.get(
                "PROGTRACK_POSTGRES_POOL_MIN",
                str(postgres_pool_min if postgres_pool_min is not None else 1),
            )
        ),
        max_size=int(
            os.environ.get(
                "PROGTRACK_POSTGRES_POOL_MAX",
                str(postgres_pool_max if postgres_pool_max is not None else 4),
            )
        ),
    )
