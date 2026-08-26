"""Lord-only administration helpers for a Shared PostgreSQL deployment.

The application never performs these operations from a plugin.  This module
keeps database discovery, creation, archive/delete safeguards, and the
server-side backup envelope behind one small service so the Qt dialog and
future administration clients use the same rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from ..backend_configuration import (
    BackendConfigurationPermissionError,
    BackendConfigurationService,
    BackendConfigurationValidationError,
    PostgreSQLSettings,
)
from .errors import BackendConfigurationError, ConflictError, ValidationError


_DATABASE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$")
_BACKUP_SCHEMA = "progtrack-postgresql-backup/1"


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
    """Perform guarded PostgreSQL deployment operations as Lord only."""

    def __init__(
        self,
        configuration: BackendConfigurationService,
        settings: PostgreSQLSettings,
        *,
        password: str = "",
        authorized: bool,
        actor_login: str = "",
        audit_callback: Callable[[str, str, dict[str, Any]], None] | None = None,
    ):
        configuration.require_lord(authorized)
        self.configuration = configuration
        self.settings = settings
        self.password = password or configuration.read_password()
        self.actor_login = actor_login or "lord"
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

    def _audit(self, action: str, payload: dict[str, Any]) -> None:
        if self.audit_callback is not None:
            self.audit_callback(action, self.actor_login, payload)

    def _connect(self, database: str | None = None, *, autocommit: bool = False):
        try:
            import psycopg
        except ImportError as exc:  # pragma: no cover - dependency environment
            raise BackendConfigurationError(
                "PostgreSQL administration requires Psycopg 3."
            ) from exc
        target = replace(self.settings, database=database or self.settings.database)
        dsn = self.configuration.connection_dsn(
            target, password=self.password, allow_environment=False
        )
        connection = psycopg.connect(dsn, autocommit=autocommit)
        return connection

    @staticmethod
    def _compatible(connection: Any) -> bool | None:
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT EXISTS (SELECT 1 FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name='schema_revisions')"
                )
                row = cursor.fetchone()
            return bool(row[0] if not isinstance(row, dict) else next(iter(row.values())))
        except Exception:
            return None

    def list_databases(self) -> list[PostgreSQLDatabaseInfo]:
        """List only databases visible to the configured PostgreSQL account."""
        try:
            with self._connect("postgres") as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT datname, pg_get_userbyid(datdba), "
                        "pg_database_size(datname), datallowconn, datistemplate "
                        "FROM pg_database WHERE datistemplate = FALSE ORDER BY datname"
                    )
                    rows = cursor.fetchall()
            result: list[PostgreSQLDatabaseInfo] = []
            for name, owner, size, allow_connections, is_template in rows:
                compatible: bool | None = None
                if allow_connections:
                    try:
                        with self._connect(str(name)) as probe:
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
        except Exception as exc:
            raise BackendConfigurationError(
                "Unable to enumerate PostgreSQL databases for this account."
            ) from exc

    def create_database(self, name: str, *, initialize: Callable[[str], None] | None = None) -> PostgreSQLDatabaseInfo:
        database = self.validate_database_name(name)
        if any(row.name == database for row in self.list_databases()):
            raise ConflictError(f"PostgreSQL database already exists: {database}")
        try:
            with self._connect("postgres", autocommit=True) as connection:
                from psycopg import sql

                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("CREATE DATABASE {}\n").format(sql.Identifier(database)))
            if initialize is not None:
                initialize(database)
            self._audit("database_created", {"database": database})
            return next(row for row in self.list_databases() if row.name == database)
        except StopIteration as exc:
            raise BackendConfigurationError(
                f"PostgreSQL database was created but could not be rediscovered: {database}"
            ) from exc
        except Exception as exc:
            raise BackendConfigurationError(
                f"Unable to create PostgreSQL database {database}."
            ) from exc

    def switch_database(self, name: str) -> PostgreSQLSettings:
        database = self.validate_database_name(name)
        if not any(row.name == database for row in self.list_databases()):
            raise ValidationError(f"PostgreSQL database is not available: {database}")
        self._audit("database_selected", {"database": database})
        return replace(self.settings, database=database)

    def archive_database(self, name: str, *, archived: bool = True) -> PostgreSQLDatabaseInfo:
        database = self.validate_database_name(name)
        if database == self.settings.database:
            raise ConflictError("The active PostgreSQL database cannot be archived.")
        try:
            with self._connect("postgres", autocommit=True) as connection:
                from psycopg import sql

                with connection.cursor() as cursor:
                    if archived:
                        cursor.execute(
                            sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS FALSE")
                            .format(sql.Identifier(database))
                        )
                    else:
                        cursor.execute(
                            sql.SQL("ALTER DATABASE {} WITH ALLOW_CONNECTIONS TRUE")
                            .format(sql.Identifier(database))
                        )
            self._audit("database_archived" if archived else "database_unarchived", {"database": database})
            return next(row for row in self.list_databases() if row.name == database)
        except StopIteration as exc:
            raise BackendConfigurationError("Archived database could not be rediscovered.") from exc
        except Exception as exc:
            raise BackendConfigurationError(
                f"Unable to {'archive' if archived else 'unarchive'} PostgreSQL database {database}."
            ) from exc

    def delete_database(self, name: str, *, backup_verified: bool) -> None:
        database = self.validate_database_name(name)
        if database == self.settings.database:
            raise ConflictError("The active PostgreSQL database cannot be deleted.")
        if not backup_verified:
            raise ConflictError("A verified backup is required before deletion.")
        try:
            with self._connect("postgres", autocommit=True) as connection:
                from psycopg import sql

                with connection.cursor() as cursor:
                    cursor.execute(sql.SQL("DROP DATABASE {}\n").format(sql.Identifier(database)))
            self._audit("database_deleted", {"database": database, "backup_verified": True})
        except Exception as exc:
            raise BackendConfigurationError(
                f"Unable to delete PostgreSQL database {database}; active connections may remain."
            ) from exc

    def _write_pg_service_file(self) -> Path:
        service_file = Path(tempfile.mkstemp(prefix="progtrack-", suffix=".pg_service.conf")[1])
        host = self.settings.server_name.strip() or self.settings.host
        lines = [
            "[progtrack-admin]",
            f"host={host}",
            f"port={self.settings.port}",
            f"dbname={self.settings.database}",
            f"user={self.settings.user}",
            f"sslmode={self.settings.sslmode}",
        ]
        if self.settings.server_name.strip():
            lines.append(f"hostaddr={self.settings.host}")
        if self.settings.ca_file.strip():
            lines.append(f"sslrootcert={self.settings.ca_file}")
        if self.settings.client_cert_file.strip():
            lines.append(f"sslcert={self.settings.client_cert_file}")
            lines.append(f"sslkey={self.settings.client_key_file}")
            key_passphrase = self.configuration.read_client_key_passphrase()
            if key_passphrase:
                lines.append(f"sslpassword={key_passphrase}")
        service_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.chmod(service_file, 0o600)
        return service_file

    @staticmethod
    def _file_manifest(root: Path) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        if not root.is_dir():
            return entries
        for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.name.endswith(".pending")):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            entries.append({
                "path": path.relative_to(root).as_posix(),
                "size": path.stat().st_size,
                "sha256": digest,
            })
        return entries

    def backup_database(
        self,
        destination: str | Path,
        *,
        managed_root: str | Path,
    ) -> Path:
        """Create a pg_dump + managed-file envelope without exposing secrets."""
        pg_dump = shutil.which("pg_dump")
        if not pg_dump:
            raise BackendConfigurationError(
                "pg_dump is required for a complete PostgreSQL backup."
            )
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        root = Path(managed_root).expanduser()
        manifest_files = self._file_manifest(root)
        dump_path = Path(tempfile.mkstemp(prefix="progtrack-", suffix=".dump")[1])
        passfile = Path(tempfile.mkstemp(prefix="progtrack-", suffix=".pgpass")[1])
        service_file = self._write_pg_service_file()
        try:
            pg_host = self.settings.server_name.strip() or self.settings.host
            passfile.write_text(
                f"{pg_host}:{self.settings.port}:{self.settings.database}:"
                f"{self.settings.user}:{self.password}\n",
                encoding="utf-8",
            )
            os.chmod(passfile, 0o600)
            env = dict(os.environ)
            env["PGPASSFILE"] = str(passfile)
            env["PGSERVICEFILE"] = str(service_file)
            completed = subprocess.run(
                [pg_dump, "--format=custom", "--file", str(dump_path), "--dbname", "progtrack-admin"],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise BackendConfigurationError(
                    "pg_dump failed: " + (completed.stderr or "unknown error").strip()
                )
            manifest = {
                "schema": _BACKUP_SCHEMA,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "database": self.settings.database,
                "host": self.settings.host,
                "managed_files": manifest_files,
            }
            temporary = target.with_suffix(target.suffix + ".tmp")
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                archive.write(dump_path, "database.dump")
                for entry in manifest_files:
                    path = root / Path(entry["path"])
                    archive.write(path, "managed/" + entry["path"])
            os.replace(temporary, target)
            self._audit("database_backup", {"database": self.settings.database, "path": str(target)})
            return target
        finally:
            dump_path.unlink(missing_ok=True)
            passfile.unlink(missing_ok=True)
            service_file.unlink(missing_ok=True)

    def restore_backup(
        self,
        backup: str | Path,
        *,
        managed_root: str | Path,
        confirmed: bool = False,
    ) -> dict[str, Any]:
        """Restore a verified pg_dump and managed payload set after confirmation."""
        if not confirmed:
            raise ConflictError("Explicit restore confirmation is required.")
        manifest = self.verify_backup(backup)
        pg_restore = shutil.which("pg_restore")
        if not pg_restore:
            raise BackendConfigurationError(
                "pg_restore is required to restore a complete PostgreSQL backup."
            )
        root = Path(managed_root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        dump_path = Path(tempfile.mkstemp(prefix="progtrack-restore-", suffix=".dump")[1])
        passfile = Path(tempfile.mkstemp(prefix="progtrack-restore-", suffix=".pgpass")[1])
        service_file = self._write_pg_service_file()
        staging = Path(tempfile.mkdtemp(prefix="progtrack-managed-restore-"))
        try:
            with zipfile.ZipFile(backup) as archive:
                dump_path.write_bytes(archive.read("database.dump"))
                for entry in manifest.get("managed_files", []):
                    destination = staging / Path(entry["path"])
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(archive.read("managed/" + entry["path"]))
            pg_host = self.settings.server_name.strip() or self.settings.host
            passfile.write_text(
                f"{pg_host}:{self.settings.port}:{self.settings.database}:"
                f"{self.settings.user}:{self.password}\n",
                encoding="utf-8",
            )
            os.chmod(passfile, 0o600)
            env = dict(os.environ)
            env["PGPASSFILE"] = str(passfile)
            env["PGSERVICEFILE"] = str(service_file)
            completed = subprocess.run(
                [
                    pg_restore,
                    "--exit-on-error",
                    "--clean",
                    "--if-exists",
                    "--dbname",
                    "progtrack-admin",
                    str(dump_path),
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode:
                raise BackendConfigurationError(
                    "pg_restore failed: " + (completed.stderr or "unknown error").strip()
                )
            for staged in sorted(p for p in staging.rglob("*") if p.is_file()):
                relative = staged.relative_to(staging)
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(staged, target)
            self._audit(
                "database_restore",
                {"database": self.settings.database, "path": str(backup)},
            )
            return manifest
        finally:
            dump_path.unlink(missing_ok=True)
            passfile.unlink(missing_ok=True)
            service_file.unlink(missing_ok=True)
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def verify_backup(path: str | Path) -> dict[str, Any]:
        backup = Path(path)
        try:
            with zipfile.ZipFile(backup) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("schema") != _BACKUP_SCHEMA:
                    raise ValidationError("Unsupported PostgreSQL backup schema.")
                for entry in manifest.get("managed_files", []):
                    data = archive.read("managed/" + entry["path"])
                    if len(data) != int(entry["size"]):
                        raise ValidationError("Managed backup file size mismatch.")
                    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                        raise ValidationError("Managed backup file checksum mismatch.")
                if "database.dump" not in archive.namelist():
                    raise ValidationError("PostgreSQL dump is missing from backup.")
                return manifest
        except (OSError, zipfile.BadZipFile, KeyError, json.JSONDecodeError) as exc:
            raise ValidationError(f"Invalid PostgreSQL backup: {exc}") from exc

    def migrate_interchange(
        self,
        source_backend: Any,
        target_backend: Any,
        package_path: str | Path,
    ) -> Any:
        """Transfer a complete local canonical package into an empty target."""
        source_backend.interchange.export_package(package_path)
        imported = target_backend.interchange.import_package(package_path, require_empty=True)
        self._audit("database_interchange_import", {"package": str(package_path)})
        return imported
