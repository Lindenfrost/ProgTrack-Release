# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Lord-only administration helpers for a Shared PostgreSQL deployment.

The application never performs these operations from a plugin.  This module
keeps database discovery, creation, archive/delete safeguards, and the
server-side backup envelope behind one small service so the Qt dialog and
future administration clients use the same rules.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from ..backend_configuration import (
    BackendConfigurationService,
    BackendConfigurationValidationError,
    PostgreSQLSettings,
)
from .errors import BackendConfigurationError, BackendError, ConflictError, PermissionDeniedError, ValidationError
from .managed_files import ManagedFileService, safe_relative_path, sha256_file
from .schema import SCHEMA_LOCK, SCHEMA_VERSION
from .catalog_revision import catalog_mutation, read_catalog_revision


_DATABASE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
_LEGACY_BACKUP_SCHEMA = "progtrack-postgresql-backup/1"
_BACKUP_SCHEMA = "progtrack-postgresql-backup/2"
_BACKUP_FRESHNESS = timedelta(hours=24)
_ACTIONS = {
    "test": "database_connection_tested",
    "save": "database_profile_validated",
    "select": "database_selected",
    "create": "database_created",
    "archive": "database_archive_status_changed",
    "backup": "database_backup",
    "restore": "database_restore",
    "delete": "database_deleted",
    "transfer": "database_interchange_import",
}


@dataclass(frozen=True)
class VerifiedBackup:
    path: Path
    digest: str
    database: str
    server_context: tuple
    database_oid: int
    core_revision: int
    created_at: datetime


class BackendAdminError(BackendConfigurationError):
    """Known administration failure with a localized UI key."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = reason


class _AdminOperation:
    """A service-authenticated, one-use grant for one high-level command."""

    def __init__(self, issuer, action, database, login, password, initiator):
        self.issuer = issuer
        self.action = action
        self.database = database
        self.login = login
        self.password = password
        self.initiator = initiator
        self.correlation_id = uuid.uuid4().hex
        self.audit_action = _ACTIONS[action]
        self.server_login = ""
        self.row = None
        self.used = False
        self.active = True
        self.transfer_backend = None
        self.catalog_touched = False

    def close(self):
        self.active = False
        self.password = ""
        self.transfer_backend = None

    def require_transfer(self, backend):
        if (not self.active or not self.used or self.action != "transfer"
                or self.transfer_backend is not backend or self not in self.issuer._issued_operations):
            raise PermissionDeniedError("Fresh PostgreSQL transfer authorization is required.")
        self.issuer._require_initiator(self.initiator)


@dataclass(frozen=True)
class PostgreSQLDatabaseInfo:
    name: str
    owner: str
    size_bytes: int
    allow_connections: bool
    is_template: bool
    compatible: bool | None

    @property
    def archived(self) -> bool:
        return not self.allow_connections and not self.is_template


class PostgreSQLAdministrationService:
    """Use runtime credentials for discovery and fresh admin credentials for commands."""

    def __init__(
        self,
        configuration: BackendConfigurationService,
        settings: PostgreSQLSettings,
        *,
        discovery_password: str = "",
        client_key_passphrase: str = "",
        authorized: bool,
        validate_initiator: Callable[[], str] | None = None,
        active_database: str = "",
        audit_callback: Callable[[str, str, dict[str, Any]], None] | None = None,
    ):
        configuration.require_lord(authorized)
        self.configuration = configuration
        self.settings = settings
        self._discovery_password = discovery_password
        self._client_key_passphrase = client_key_passphrase
        self._validate_initiator = validate_initiator
        self._active_database = active_database
        self._issued_operations = set()
        self.audit_callback = audit_callback

    @staticmethod
    def validate_database_name(name: str) -> str:
        value = str(name or "").strip()
        if not _DATABASE_NAME.fullmatch(value):
            raise ValidationError(
                "Database names must start with a letter or underscore and contain "
                "only letters, numbers, underscores, and hyphens."
            )
        return value

    def _audit(self, operation: _AdminOperation, result: str) -> None:
        if self.audit_callback is not None:
            try:
                self.audit_callback(operation.audit_action, operation.server_login or "unauthenticated", {
                    "database": operation.database,
                    "initiating_application_user": operation.initiator,
                    "backend_admin": operation.server_login or None,
                    "correlation_id": operation.correlation_id,
                    "occurred_at": datetime.now(timezone.utc).isoformat(),
                    "result": result,
                })
            except Exception:
                raise BackendAdminError("audit_failed", "The operation outcome could not be written to the audit log. Check the target before retrying.") from None

    def _require_initiator(self, expected: str | None = None) -> str:
        try:
            current = self._validate_initiator() if self._validate_initiator is not None else ""
        except PermissionDeniedError:
            current = ""
        if not current or (expected is not None and current != expected):
            raise BackendAdminError("initiator", "The initiating ProgTrack Lord session is no longer valid.")
        return current

    def _dsn(self, database: str, operation: _AdminOperation | None = None) -> str:
        target = replace(self.settings, database=database)
        if operation is not None:
            target = replace(target, user=operation.login)
        return self.configuration.connection_dsn(
            target,
            password=operation.password if operation is not None else self._discovery_password,
            client_key_passphrase=self._client_key_passphrase,
            allow_environment=False,
        )

    def _connect(self, database: str, *, operation: _AdminOperation | None = None,
                 autocommit: bool = False):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - dependency environment
            raise BackendConfigurationError(
                "PostgreSQL administration requires Psycopg 3."
            ) from exc
        return psycopg.connect(self._dsn(database, operation), autocommit=autocommit)

    @contextmanager
    def authorize(self, action: str, database: str, *, login: str, password: str):
        """Authenticate the entered credentials and confine them to this command."""
        if action not in _ACTIONS:
            raise PermissionDeniedError("Unknown PostgreSQL administration operation.")
        database = self.validate_database_name(database)
        initiator = self._require_initiator()
        login = login.strip()
        if not login or not password or login in (self.settings.user, self.configuration.saved_postgresql().user):
            raise BackendAdminError("separate", "Enter credentials for a separate backend administrator account.")
        operation = _AdminOperation(self, action, database, login, password, initiator)
        try:
            self._authenticate(operation)
            self._require_initiator(initiator)
            self._issued_operations.add(operation)
            yield operation
            if not operation.used:
                raise PermissionDeniedError("The authorized PostgreSQL operation was not invoked.")
        except Exception as exc:
            self._audit(operation, "failed")
            if isinstance(exc, (PermissionDeniedError, ConflictError, ValidationError, BackendConfigurationError)):
                raise exc from None
            raise BackendConfigurationError("The PostgreSQL operation failed. Check access, TLS, and server state.") from None
        else:
            self._audit(operation, "success")
        finally:
            self._issued_operations.discard(operation)
            operation.close()

    def _identity(self, cursor, operation):
        cursor.execute("SELECT session_user, current_user, rolcreatedb OR rolsuper "
                       "FROM pg_roles WHERE rolname=current_user")
        row = cursor.fetchone()
        if not row or row[0] != operation.login or row[1] != operation.login:
            raise PermissionDeniedError("The connected PostgreSQL role does not match the backend administrator.")
        operation.server_login = str(row[1])
        return bool(row[2])

    @staticmethod
    def _target(cursor, database):
        cursor.execute("SELECT oid, datallowconn, datistemplate, "
                       "pg_has_role(current_user, datdba, 'USAGE') "
                       "FROM pg_database WHERE datname=%s", (database,))
        return cursor.fetchone()

    def _authenticate(self, operation):
        admin_db = (operation.database if operation.action in ("test", "save", "select", "backup", "restore", "transfer")
                    else "postgres")
        with self._connect(admin_db, operation=operation) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            can_create = self._identity(cursor, operation)
            target = self._target(cursor, operation.database)
            if operation.action == "create":
                if not can_create:
                    raise BackendAdminError("privilege", "The backend administrator cannot create databases.")
                if target is not None:
                    raise ConflictError("The PostgreSQL database already exists.")
            elif not target or target[2] or not target[3]:
                raise BackendAdminError("privilege", "The backend administrator does not own the requested database.")
            operation.row = target

    def _consume(self, operation, action, database):
        if (not isinstance(operation, _AdminOperation) or operation.issuer is not self
                or operation not in self._issued_operations
                or not operation.active or operation.used or not operation.server_login
                or operation.action != action or operation.database != database):
            raise PermissionDeniedError("Fresh authorization for this PostgreSQL operation and target is required.")
        self._require_initiator(operation.initiator)
        operation.used = True

    def _check_current_target(self, cursor, operation):
        self._identity(cursor, operation)
        current = self._target(cursor, operation.database)
        if not current or current[0] != operation.row[0] or current[2] or not current[3]:
            raise PermissionDeniedError("The PostgreSQL target or administrator privilege changed.")
        self._require_initiator(operation.initiator)
        return current

    @staticmethod
    def _compatible(connection: Any) -> bool | None:
        from .migration import probe_shared_compatibility

        return probe_shared_compatibility(connection)

    @classmethod
    def migrate_configured_schema(cls, configuration, *, login, password):
        """Authenticate a deliberate migration against the configured incompatible target."""
        import psycopg
        from psycopg.conninfo import conninfo_to_dict, make_conninfo
        from psycopg.rows import dict_row, tuple_row
        from .migration import migrate_core_schema, probe_shared_compatibility

        options = conninfo_to_dict(configuration.effective_postgres_dsn())
        database = cls.validate_database_name(options.get("dbname"))
        login = str(login).strip()
        if not login or not password or login == options.get("user"):
            raise PermissionDeniedError("Enter credentials for a separate backend administrator account.")
        correlation_id = uuid.uuid4().hex
        connected_actor = None
        try:
            with psycopg.connect(make_conninfo(**options), autocommit=True) as runtime:
                with runtime.transaction():
                    runtime.execute("SET TRANSACTION READ ONLY")
                    if probe_shared_compatibility(runtime) is not False:
                        raise PermissionDeniedError("Maintenance requires an authoritatively incompatible target.")
                    identity = runtime.execute(
                        "SELECT oid FROM pg_database WHERE datname=current_database()"
                    ).fetchone()
            options.update(user=login, password=password)
            with psycopg.connect(make_conninfo(**options), row_factory=dict_row) as connection:
                with connection.cursor(row_factory=tuple_row) as cursor:
                    cursor.execute("SELECT session_user, current_user")
                    if cursor.fetchone() != (login, login):
                        raise PermissionDeniedError("The connected PostgreSQL role does not match the backend administrator.")
                    target = cls._target(cursor, database)
                    if not target or target[0] != identity[0] or not target[1] or target[2] or not target[3]:
                        raise PermissionDeniedError("The PostgreSQL target or administrator authority changed.")
                connected_actor = login
                connection.commit()

                @contextmanager
                def transaction(*, write=False):
                    with connection.transaction():
                        yield connection

                def verify_target(active):
                    with active.cursor(row_factory=tuple_row) as cursor:
                        cursor.execute("SELECT session_user,current_user,current_database()")
                        if cursor.fetchone() != (login, login, database) or cls._target(cursor, database) != target:
                            raise PermissionDeniedError("The PostgreSQL target or administrator authority changed.")

                adapter = SimpleNamespace(dialect="postgresql", _schema_transaction=transaction)
                maintenance = make_conninfo(**{**options, "dbname": "postgres"})
                with psycopg.connect(maintenance, autocommit=True) as clock, catalog_mutation(clock, administrator=login):
                    migrate_core_schema(
                        adapter, admin_actor=login, correlation_id=correlation_id, check_target=verify_target,
                    )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Schema migration %s failed on %s; authenticated administrator %s",
                correlation_id, database, connected_actor,
            )
            if isinstance(exc, (BackendConfigurationError, PermissionDeniedError)):
                raise exc from None
            raise BackendConfigurationError("Schema migration failed. Check server access, TLS and PostgreSQL administrator credentials.") from None

    def catalog_revision(self):
        """One prompt-free read; no enumeration and no per-database probes."""
        try:
            with self._connect("postgres") as connection:
                connection.read_only = True
                return read_catalog_revision(connection)
        except Exception:
            return None

    def list_databases(self) -> list[PostgreSQLDatabaseInfo]:
        """List only databases visible to the configured PostgreSQL account."""
        try:
            with self._connect("postgres") as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(
                        "SELECT datname, pg_get_userbyid(datdba), "
                        "pg_database_size(datname), datallowconn, datistemplate "
                        "FROM pg_database WHERE datistemplate = FALSE "
                        "AND has_database_privilege(current_user, oid, 'CONNECT') ORDER BY datname"
                    )
                    rows = cursor.fetchall()
            result: list[PostgreSQLDatabaseInfo] = []
            for name, owner, size, allow_connections, is_template in rows:
                compatible: bool | None = None
                if allow_connections:
                    try:
                        with self._connect(str(name)) as probe:
                            with probe.cursor() as cursor:
                                cursor.execute("SET TRANSACTION READ ONLY")
                            compatible = self._compatible(probe)
                    except Exception:
                        compatible = None
                result.append(
                    PostgreSQLDatabaseInfo(
                        name=str(name),
                        owner=str(owner or ""),
                        size_bytes=int(size or 0),
                        allow_connections=bool(allow_connections),
                        is_template=bool(is_template),
                        compatible=compatible,
                    )
                )
            return result
        except Exception:
            raise BackendConfigurationError(
                "Unable to enumerate PostgreSQL databases for this account."
            ) from None

    def create_database(self, name: str, *, operation=None) -> None:
        database = self.validate_database_name(name)
        self._consume(operation, "create", database)
        from psycopg import sql

        with self._connect("postgres", operation=operation, autocommit=True) as connection, connection.cursor() as cursor:
            if not self._identity(cursor, operation):
                raise PermissionDeniedError("The backend administrator cannot create databases.")
            if self._target(cursor, database) is not None:
                raise ConflictError("The PostgreSQL database already exists.")
            self._require_initiator(operation.initiator)
            with self._catalog_action(connection, operation):
                cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
                try:
                    self._initialize_target(operation)
                except Exception:
                    raise BackendAdminError("initialization",
                        "PostgreSQL created the database, but schema initialization failed. Review the target before retrying."
                    ) from None

    @contextmanager
    def _catalog_action(self, connection, operation):
        with catalog_mutation(connection, administrator=operation.server_login):
            operation.catalog_touched = True
            yield

    def _initialize_target(self, operation):
        from .adapters import PostgreSQLAdapter

        self._require_initiator(operation.initiator)
        adapter = PostgreSQLAdapter(self._dsn(operation.database, operation),
                                    min_size=self.settings.pool_min, max_size=self.settings.pool_max)
        try:
            adapter.migrate()
        finally:
            adapter.close()

    @classmethod
    def test_recovery_target(cls, configuration, settings, *, login, password,
                             runtime_password, client_key_passphrase):
        """Authorize only a read-only compatibility test before a ProgTrack login."""
        import psycopg

        from ..runtime_paths import BackendProfile

        if configuration.paths.profile is not BackendProfile.SHARED_POSTGRESQL:
            raise PermissionDeniedError("An active Shared profile is required.")
        database = cls.validate_database_name(settings.database)
        login = login.strip()
        if not login or not password or login in (settings.user, configuration.saved_postgresql().user):
            raise BackendAdminError("separate", "Enter credentials for a separate backend administrator account.")
        try:
            admin_dsn = configuration.connection_dsn(
                replace(settings, user=login), password=password,
                client_key_passphrase=client_key_passphrase, allow_environment=False,
            )
            runtime_dsn = configuration.connection_dsn(
                settings, password=runtime_password,
                client_key_passphrase=client_key_passphrase, allow_environment=False,
            )
            with psycopg.connect(admin_dsn) as connection, connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                target = cls._target(cursor, database)
                cursor.execute("SELECT session_user, current_user, current_database()")
                if cursor.fetchone() != (login, login, database) or not target or not target[1] or target[2] or not target[3]:
                    raise BackendAdminError("privilege", "The backend administrator does not own the requested database.")
                if cls._compatible(connection) is not True:
                    raise ValidationError("The selected PostgreSQL database is not available for ProgTrack.")
            with psycopg.connect(runtime_dsn) as connection, connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute("SELECT oid FROM pg_database WHERE datname=current_database()")
                if cursor.fetchone() != (target[0],) or cls._compatible(connection) is not True:
                    raise ValidationError("The configured runtime credentials cannot use this ProgTrack database.")
        except (BackendError, BackendConfigurationValidationError):
            raise
        except psycopg.Error:
            raise BackendConfigurationError("Could not verify the PostgreSQL credentials and target.") from None

    def test_connection(self, *, operation=None, for_save=False) -> None:
        action = "save" if for_save else "test"
        self._consume(operation, action, self.settings.database)
        if for_save:
            with self._connect(operation.database, operation=operation) as connection, connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                self._check_current_target(cursor, operation)
                if self._compatible(connection) is not True:
                    raise ValidationError("The selected PostgreSQL database is not available for ProgTrack.")

    def switch_database(self, name: str, *, operation=None) -> PostgreSQLSettings:
        database = self.validate_database_name(name)
        self._consume(operation, "select", database)
        with self._connect(database, operation=operation) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            self._check_current_target(cursor, operation)
            if self._compatible(connection) is not True:
                raise ValidationError("The selected PostgreSQL database is not available for ProgTrack.")
        return replace(self.settings, database=database)

    def archive_database(self, name: str, *, archived: bool | None = None, operation=None) -> None:
        database = self.validate_database_name(name)
        self._consume(operation, "archive", database)
        if database in (self._active_database, self.settings.database):
            raise ConflictError("The active PostgreSQL database cannot be archived.")
        from psycopg import sql

        with self._connect("postgres", operation=operation, autocommit=True) as connection, connection.cursor() as cursor:
            current = self._check_current_target(cursor, operation)
            if archived is None:
                archived = bool(current[1])
            operation.audit_action = "database_archived" if archived else "database_unarchived"
            allow = sql.SQL("FALSE" if archived else "TRUE")
            with self._catalog_action(connection, operation):
                cursor.execute(sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS {}").format(sql.Identifier(database), allow))

    def delete_database(self, name: str, *, typed_confirmation: str = "", backup=None, operation=None) -> None:
        database = self.validate_database_name(name)
        self._consume(operation, "delete", database)
        if database in (self._active_database, self.settings.database):
            raise ConflictError("The active PostgreSQL database cannot be deleted.")
        if typed_confirmation != database or not self._verify_deletion_backup(backup, operation):
            raise ConflictError("A verified backup is required before deletion.")
        from psycopg import sql

        with self._connect("postgres", operation=operation, autocommit=True) as connection, connection.cursor() as cursor:
            self._check_current_target(cursor, operation)
            with self._catalog_action(connection, operation):
                cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(database)))

    def _backup_context(self, operation):
        s = self.settings
        return (s.host, s.port, s.server_name, s.sslmode, s.ca_file,
                s.client_cert_file, s.client_key_file, s.user, operation.server_login)

    def _verify_deletion_backup(self, backup, operation):
        if (not isinstance(backup, VerifiedBackup) or backup.database != operation.database
                or backup.database_oid != operation.row[0] or backup.core_revision <= 0
                or backup.server_context != self._backup_context(operation)
                or not timedelta() <= datetime.now(timezone.utc) - backup.created_at <= _BACKUP_FRESHNESS
                or not backup.path.is_file() or sha256_file(backup.path) != backup.digest):
            return False
        manifest = self.verify_backup(backup.path)
        return (manifest.get("database") == backup.database
                and manifest.get("database_oid") == backup.database_oid
                and manifest.get("core_revision") == backup.core_revision
                and manifest.get("created_at") == backup.created_at.isoformat())

    def _target_storage_client(self, database, root=None):
        from ..managed_storage_worker import ManagedStorageClient
        from .managed_storage import _unc_share

        if root is None:
            saved_target = self.configuration.saved_postgresql().database
            if database != self.settings.database or (saved_target and database != saved_target):
                raise BackendConfigurationError("Choose this database's client-local managed document folder.")
            root = self.configuration.environ.get("PROGTRACK_MANAGED_ROOT") or self.settings.managed_root
        runtime_dsn = None
        if sys.platform == "win32" and _unc_share(str(root)):
            runtime_dsn = self.configuration.connection_dsn(
                self.settings, password=self._discovery_password or self.configuration.read_password(),
                client_key_passphrase=self._client_key_passphrase,
            )
        return ManagedStorageClient(self.configuration, root, runtime_dsn=runtime_dsn)

    def _target_storage(self, database, root=None, *, writable=False):
        return self._target_storage_client(database, root).check(writable=writable)

    @contextmanager
    def _backup_snapshot(self, operation):
        with self._connect(operation.database, operation=operation) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self._check_current_target(cursor, operation)
            cursor.execute("SELECT pg_try_advisory_xact_lock_shared(%s,%s)", SCHEMA_LOCK)
            if not cursor.fetchone()[0]:
                raise ConflictError("Another operation holds the database maintenance lock.")
            cursor.execute("SELECT revision FROM schema_revisions WHERE component='core'")
            revision = cursor.fetchone()
            if not revision or not 1 <= revision[0] <= SCHEMA_VERSION:
                raise BackendConfigurationError("The selected database has an unsupported Core schema revision.")
            revision = int(revision[0])
            cursor.execute("SELECT count(*) FROM managed_files WHERE state IN ('staged','pending','quarantined')")
            unresolved = cursor.fetchone()[0]
            if revision >= 4:
                cursor.execute("SELECT count(*) FROM document_operations WHERE state='prepared' OR cleanup_pending=1")
                unresolved += cursor.fetchone()[0]
            if unresolved:
                raise BackendAdminError("backup_unresolved_documents",
                                        "Resolve the managed-document records or operations before backing up.")
            fields = ["document_id", "category", "relative_path", "byte_size", "sha256", "state", "revision"]
            revision_column = "revision" if revision >= 4 else "1"
            cursor.execute(
                "SELECT document_id,category,relative_path,byte_size,sha256,state," + revision_column
                + " FROM managed_files WHERE state='active' ORDER BY document_id"
            )
            records = [dict(zip(fields, row)) for row in cursor.fetchall()]
            cursor.execute("SELECT pg_export_snapshot()")
            yield revision, records, cursor.fetchone()[0]

    @contextmanager
    def _tool_connection(self, operation):
        """Keep the SQL password in the child environment and TLS secret in memory."""
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        options = conninfo_to_dict(self._dsn(operation.database, operation))
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
        env["PGPASSWORD"] = options.pop("password")
        passphrase = options.pop("sslpassword", "")
        if not passphrase:
            yield make_conninfo(**options), env, None
            return
        # libpq service syntax keeps everything after the first equals sign,
        # strips trailing ASCII whitespace and limits each line to 1022 bytes.
        secret_line = f"sslpassword={passphrase}".encode("utf-8")
        if (len(secret_line) >= 1022 or passphrase != passphrase.rstrip(" \t\v\f\r\n")
                or any(ch in passphrase for ch in "\r\n\x00\x1a")):
            raise BackendConfigurationError("The PostgreSQL tool credential contains characters unsupported by libpq service files.")
        payload = b"[progtrack-admin]\n" + secret_line + b"\n[progtrack-end]\n"
        conninfo = make_conninfo(service="progtrack-admin", **options)
        if sys.platform == "win32":
            from .windows_pg_service_pipe import WindowsPgServicePipe

            try:
                transport = WindowsPgServicePipe(payload)
            except (OSError, AttributeError):
                raise self._tool_transport_unavailable() from None
            with transport:
                env["PGSERVICEFILE"] = transport.name
                yield conninfo, env, transport
            return
        # libpq has no sslpassword environment variable. Use a private Linux
        # tmpfs directory if the mount is available, with no disk fallback.
        if sys.platform != "linux" or not self._private_ram_available():
            raise self._tool_transport_unavailable()
        with tempfile.TemporaryDirectory(prefix="progtrack-pg-", dir="/dev/shm") as folder:
            path = Path(folder) / "pg_service.conf"
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as file:
                file.write(payload)
            env["PGSERVICEFILE"] = str(path)
            yield conninfo, env, None

    @staticmethod
    def _tool_transport_unavailable():
        return BackendAdminError("encrypted_key", "Encrypted client keys need protected PostgreSQL tool credential transport on this host.")

    @staticmethod
    def _private_ram_available():
        try:
            return any(len(row) > 2 and row[1:3] == ["/dev/shm", "tmpfs"]
                       for row in (line.split() for line in Path("/proc/mounts").read_text().splitlines()))
        except OSError:
            return False

    def _run_tool(self, command, arguments, operation):
        tool = shutil.which(command)
        if not tool:
            raise BackendConfigurationError(f"{command} is required for this PostgreSQL operation.")
        with self._connect(operation.database, operation=operation) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            self._check_current_target(cursor, operation)
        with self._tool_connection(operation) as (conninfo, env, transport):
            argv = [tool, "--no-password", "--dbname", conninfo, *arguments]
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            if transport is None:
                result = subprocess.run(argv, env=env, capture_output=True, text=True,
                                        check=False, timeout=3600, creationflags=flags)
                returncode, stderr = result.returncode, getattr(result, "stderr", "") or ""
            else:
                returncode, stderr = self._run_with_service_pipe(argv, env, transport, flags)
        if returncode:
            if command == "psql":
                self._check_restore_verification_failure(stderr)
            raise BackendConfigurationError(f"{command} failed (exit code {returncode}). Check server privileges and tool compatibility.")

    @staticmethod
    def _check_restore_verification_failure(stderr):
        for line in stderr.splitlines():
            if "verify.sql:" not in line:
                continue
            if "PROGTRACK_RESTORE_DOCUMENT_INVENTORY_MISMATCH" in line:
                raise BackendAdminError("backup_document_inventory", "The database dump and verified managed payload inventory disagree.")
            if "PROGTRACK_RESTORE_CORE_SCHEMA_MISMATCH" in line:
                raise BackendAdminError("backup_core_schema", "The database dump and backup Core schema metadata disagree.")
            if "PROGTRACK_RESTORE_UNRESOLVED_DOCUMENTS" in line:
                raise BackendAdminError("backup_unresolved_documents", "The database dump contains unresolved managed-document records or operations.")

    def _run_with_service_pipe(self, argv, env, transport, flags):
        with subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              text=True, close_fds=True, creationflags=flags) as child:
            try:
                transport.serve_process(child.pid)
                for _ in range(300):
                    if transport.wait_for_delivery(0.1):
                        break
                    if child.poll() is not None:
                        if child.returncode:
                            return child.returncode, ""
                        raise self._tool_transport_unavailable()
                else:
                    raise self._tool_transport_unavailable()
                _, stderr = child.communicate(timeout=3600)
            except BaseException:
                if child.poll() is None:
                    child.kill()
                child.communicate()
                raise
            return child.returncode, stderr or ""

    def backup_database(self, destination: str | Path, *, database: str, operation=None, storage_root=None) -> VerifiedBackup:
        self._consume(operation, "backup", database)
        storage = self._target_storage_client(database, storage_root)
        root = storage.check()
        documents = ManagedFileService(None, root / "documents", root / "config-assets",
                                       create_roots=False, storage_client=storage)
        target = Path(destination).resolve()
        with tempfile.TemporaryDirectory(prefix="progtrack-backup-") as temp:
            staging = Path(temp) / "managed"
            dump_path = Path(temp) / "database.dump"
            with self._backup_snapshot(operation) as (revision, records, snapshot_id):
                self._run_tool("pg_dump", [
                    "--format=custom", "--snapshot", snapshot_id, "--file", str(dump_path),
                ], operation)
            manifest_files = []
            for record in records:
                child = "config-assets" if record["category"] == "config-asset" else "documents"
                relative = (Path(child) / safe_relative_path(record["relative_path"])).as_posix()
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                documents.copy_verified_payload(record, destination)
                manifest_files.append({
                    "document_id": record["document_id"], "revision": record["revision"],
                    "path": relative, "size": record["byte_size"], "sha256": record["sha256"],
                })
            created_at = datetime.now(timezone.utc)
            manifest = {
                "schema": _BACKUP_SCHEMA,
                "created_at": created_at.isoformat(),
                "database": operation.database,
                "database_oid": operation.row[0],
                "core_revision": revision,
                "host": self.settings.host,
                "dump_sha256": sha256_file(dump_path),
                "managed_files": manifest_files,
            }
            target.parent.mkdir(parents=True, exist_ok=True)
            descriptor, name = tempfile.mkstemp(prefix=".progtrack-backup-", dir=target.parent)
            os.close(descriptor)
            temporary = Path(name)
            try:
                with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    archive.write(dump_path, "database.dump")
                    for entry in manifest_files:
                        archive.write(staging / Path(entry["path"]), "managed/" + entry["path"])
                self.verify_backup(temporary)
                digest = sha256_file(temporary)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)
        return VerifiedBackup(target, digest, database, self._backup_context(operation),
                              operation.row[0], revision, created_at)

    @contextmanager
    def _restore_guard(self, operation):
        with self._connect(operation.database, operation=operation, autocommit=True) as connection, connection.cursor() as cursor:
            self._check_current_target(cursor, operation)
            cursor.execute("SELECT pg_try_advisory_lock(%s,%s)", SCHEMA_LOCK)
            if not cursor.fetchone()[0]:
                raise ConflictError("Another operation holds the database maintenance lock.")
            yield cursor

    @staticmethod
    def _check_restore_cleanup_fence(cursor, entries):
        cursor.execute("SELECT to_regclass('public.document_operations')")
        row = cursor.fetchone()
        if not row or row[0] is None:
            return
        cursor.execute("SELECT retired_path, result_json FROM public.document_operations WHERE cleanup_pending=1")
        paths = {entry["path"].casefold() for entry in entries}
        for retired_path, result in cursor.fetchall():
            child = "config-assets" if json.loads(result)["category"] == "config-asset" else "documents"
            if f"{child}/{retired_path}".casefold() in paths:
                raise BackendAdminError("restore_cleanup_pending", "A restore path still has pending retired-file cleanup.")

    def restore_backup(self, backup: str | Path, *, database: str, confirmed: bool = False,
                       operation=None, storage_root=None) -> dict[str, Any]:
        """Verify and publish payloads before atomically restoring the metadata that references them."""
        self._consume(operation, "restore", database)
        if not confirmed:
            raise ConflictError("Explicit restore confirmation is required.")
        storage = self._target_storage_client(database, storage_root)
        root = storage.check(writable=True)
        with tempfile.TemporaryDirectory(prefix="progtrack-restore-") as folder:
            temporary = Path(folder)
            package = temporary / "backup"
            shutil.copyfile(backup, package)
            manifest = self.verify_backup(package)
            legacy = manifest["schema"] == _LEGACY_BACKUP_SCHEMA
            dump_path = temporary / "database.dump"
            staging = temporary / "managed"
            with zipfile.ZipFile(package) as archive:
                with archive.open("database.dump") as source, dump_path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                for entry in manifest["managed_files"]:
                    destination = staging / safe_relative_path(entry["path"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open("managed/" + entry["path"]) as reader, destination.open("xb") as writer:
                        shutil.copyfileobj(reader, writer)
            restore_sql, verify_sql = self._restore_scripts(dump_path, manifest)
            restore_args = ["--no-psqlrc", "--set", "ON_ERROR_STOP=1", "--single-transaction",
                            "--file", str(restore_sql), "--file", str(verify_sql)]
            with (self._connect("postgres", operation=operation, autocommit=True) as clock,
                  self._catalog_action(clock, operation), self._restore_guard(operation) as restore_cursor):
                self._check_restore_cleanup_fence(restore_cursor, manifest["managed_files"])
                if legacy:
                    # The v1 writer included the entire tree. Prove exact dump/path agreement
                    # in a rolled-back trial before carrying any of its files into the live share.
                    self._run_tool("psql", [*restore_args, "--command", "ROLLBACK"], operation)
                absent = []
                for entry in manifest["managed_files"]:
                    target = root / safe_relative_path(entry["path"])
                    size = storage.file("stat", target)
                    if size is None:
                        absent.append((entry, target))
                    elif not self._matches_storage_entry(storage, entry, target, size=size):
                        raise ConflictError("A managed restore path already contains different data.")
                for entry, target in absent:
                    storage.file("restore_new", target, source=staging / safe_relative_path(entry["path"]))
                for entry in manifest["managed_files"]:
                    target = root / safe_relative_path(entry["path"])
                    if not self._matches_storage_entry(storage, entry, target):
                        raise ValidationError("A restored managed payload did not pass final verification.")
                self._run_tool("psql", restore_args, operation)
        return manifest

    @staticmethod
    def _matches_storage_entry(storage, entry, path, *, size=None):
        if size is None:
            size = storage.file("stat", path)
        return size == entry["size"] and storage.file("digest", path) == entry["sha256"]

    @staticmethod
    def _restore_scripts(dump_path, manifest):
        tool = shutil.which("pg_restore")
        if not tool:
            raise BackendConfigurationError("pg_restore is required for this PostgreSQL operation.")
        if not shutil.which("psql"):
            raise BackendConfigurationError("psql is required for this PostgreSQL operation.")
        restore_sql = dump_path.with_suffix(".sql")
        verify_sql = dump_path.with_name("verify.sql")
        result = subprocess.run(
            [tool, "--clean", "--if-exists", "--file", str(restore_sql), str(dump_path)],
            env={key: value for key, value in os.environ.items() if not key.upper().startswith("PG")},
            capture_output=True, check=False, timeout=3600,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        if result.returncode:
            raise ValidationError("The PostgreSQL backup dump could not be read.")
        legacy = manifest["schema"] == _LEGACY_BACKUP_SCHEMA
        key = "path" if legacy else "document_id"
        expected = {entry[key]: entry for entry in manifest["managed_files"]}
        expected_hex = json.dumps(expected, separators=(",", ":")).encode("utf-8").hex()
        revision = manifest.get("core_revision")
        revision_check = f"actual_revision = {revision}" if revision else f"actual_revision BETWEEN 1 AND {SCHEMA_VERSION}"
        relative_path = "(CASE WHEN category='config-asset' THEN 'config-assets/' ELSE 'documents/' END) || relative_path"
        inventory_key = relative_path if legacy else "document_id"
        identity = ""
        if not legacy:
            document_revision = "revision" if revision >= 4 else "1"
            identity = f"'document_id', document_id, 'revision', {document_revision}, "
        verify_sql.write_text(f"""
SET LOCAL search_path TO public, pg_catalog;
DO $verify$
DECLARE
    expected jsonb := convert_from(decode('{expected_hex}', 'hex'), 'UTF8')::jsonb;
    actual jsonb;
    actual_revision integer;
    active_count bigint;
BEGIN
    SELECT revision INTO actual_revision FROM schema_revisions WHERE component='core';
    IF actual_revision IS NULL OR NOT ({revision_check}) THEN
        RAISE EXCEPTION 'PROGTRACK_RESTORE_CORE_SCHEMA_MISMATCH';
    END IF;
    IF EXISTS (SELECT 1 FROM managed_files WHERE state IN ('staged','pending','quarantined')) THEN
        RAISE EXCEPTION 'PROGTRACK_RESTORE_UNRESOLVED_DOCUMENTS';
    END IF;
    IF actual_revision >= 4 THEN
        IF to_regclass('public.document_operations') IS NULL THEN
            RAISE EXCEPTION 'PROGTRACK_RESTORE_CORE_SCHEMA_MISMATCH';
        END IF;
        IF EXISTS (SELECT 1 FROM document_operations WHERE state='prepared' OR cleanup_pending=1) THEN
            RAISE EXCEPTION 'PROGTRACK_RESTORE_UNRESOLVED_DOCUMENTS';
        END IF;
    END IF;
    SELECT COUNT(*), COALESCE(jsonb_object_agg({inventory_key}, jsonb_build_object(
        {identity}'size', byte_size, 'sha256', sha256, 'path', {relative_path}
    )), '{{}}'::jsonb) INTO active_count, actual
    FROM managed_files WHERE state='active';
    IF actual IS DISTINCT FROM expected OR active_count <> {len(expected)} THEN
        RAISE EXCEPTION 'PROGTRACK_RESTORE_DOCUMENT_INVENTORY_MISMATCH';
    END IF;
    -- pg_restore --clean only drops objects listed in the source dump. When
    -- restoring a supported earlier schema, remove known newer tables so its
    -- explicit maintenance migration can be applied without stale state.
    IF actual_revision < 4 THEN
        DROP TABLE IF EXISTS document_publish_attempts, document_operations;
    END IF;
    IF actual_revision < 3 THEN
        DROP TABLE IF EXISTS active_sessions;
    END IF;
END
$verify$;
""", encoding="utf-8")
        return restore_sql, verify_sql

    @staticmethod
    def verify_backup(path: str | Path) -> dict[str, Any]:
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise ValidationError("PostgreSQL backup contains duplicate members.")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("schema") not in {_BACKUP_SCHEMA, _LEGACY_BACKUP_SCHEMA}:
                    raise ValidationError("Unsupported PostgreSQL backup schema.")
                current = manifest["schema"] == _BACKUP_SCHEMA
                seen_paths = set()
                seen_folded_paths = set()
                seen_ids = set()
                for entry in manifest.get("managed_files", []):
                    relative = safe_relative_path(entry["path"])
                    path = relative.as_posix()
                    if path != entry["path"] or path.casefold() in seen_folded_paths:
                        raise ValidationError("Managed backup contains an invalid or duplicate file path.")
                    seen_paths.add(path)
                    seen_folded_paths.add(path.casefold())
                    if len(relative.parts) < 2 or relative.parts[0] not in {"documents", "config-assets"}:
                        raise ValidationError("Managed backup contains a path outside the managed categories.")
                    if type(entry["size"]) is not int or entry["size"] < 0:
                        raise ValidationError("Managed backup contains an invalid file size.")
                    if current:
                        if (type(entry["revision"]) is not int or entry["revision"] < 1
                                or not isinstance(entry["document_id"], str) or entry["document_id"] in seen_ids):
                            raise ValidationError("Managed backup contains invalid document metadata.")
                        seen_ids.add(entry["document_id"])
                    with archive.open("managed/" + path) as payload:
                        if int(entry["size"]) != archive.getinfo("managed/" + path).file_size:
                            raise ValidationError("Managed backup file size mismatch.")
                        if hashlib.file_digest(payload, "sha256").hexdigest() != entry["sha256"]:
                            raise ValidationError("Managed backup file checksum mismatch.")
                if "database.dump" not in names:
                    raise ValidationError("PostgreSQL dump is missing from backup.")
                expected = {"manifest.json", "database.dump", *("managed/" + path for path in seen_paths)}
                revision = manifest.get("core_revision")
                if (set(names) != expected or (current or revision is not None)
                        and (type(revision) is not int or not 1 <= revision <= SCHEMA_VERSION)):
                    raise ValidationError("PostgreSQL backup has inconsistent document inventory.")
                if current or manifest.get("dump_sha256"):
                    with archive.open("database.dump") as dump:
                        if hashlib.file_digest(dump, "sha256").hexdigest() != manifest["dump_sha256"]:
                            raise ValidationError("PostgreSQL dump checksum mismatch.")
                return manifest
        except (OSError, TypeError, ValueError, zipfile.BadZipFile, KeyError):
            raise ValidationError("The PostgreSQL backup cannot be read or verified.") from None

    def migrate_interchange(
        self,
        package_path: str | Path,
        *,
        database: str,
        operation=None,
        storage_root=None,
    ) -> Any:
        """Transfer a previewed canonical package with this operation's admin credentials."""
        self._consume(operation, "transfer", database)
        root = self._target_storage(database, storage_root, writable=True)
        with self._connect(database, operation=operation) as connection, connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION READ ONLY")
            self._check_current_target(cursor, operation)
        with self._connect("postgres", operation=operation, autocommit=True) as clock, self._catalog_action(clock, operation):
            return self._import_shared_package(package_path, root, operation)

    def _import_shared_package(self, package_path, root, operation):
        from .facade import ProgTrackBackend
        from ..runtime_paths import BackendProfile

        self._initialize_target(operation)
        paths = replace(self.configuration.paths, profile=BackendProfile.SHARED_POSTGRESQL,
                        database_path=None, managed_root=root, managed_documents=root / "documents",
                        managed_config_assets=root / "config-assets")
        backend = ProgTrackBackend(paths, postgres_dsn=self._dsn(operation.database, operation),
                                   postgres_pool_min=self.settings.pool_min,
                                   postgres_pool_max=self.settings.pool_max, acquire_process_lock=False,
                                   storage_client=self._target_storage_client(operation.database, root))
        try:
            operation.transfer_backend = backend
            result = backend.interchange.import_package(package_path, require_empty=True, admin_operation=operation)
            if not result.valid:
                raise ValidationError("The canonical package failed validation. No dataset was imported.")
            return result
        finally:
            operation.transfer_backend = None
            backend.close()
