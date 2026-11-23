# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Lord-managed backend profile configuration without persisted secrets."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import sys
import platform
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Protocol

from .runtime_paths import BackendProfile, RuntimePaths, _linux_roots, _windows_roots, validate_standalone_sqlite_path


class BackendConfigurationPermissionError(PermissionError):
    pass


class BackendConfigurationValidationError(ValueError):
    pass


class CredentialStore(Protocol):
    def available(self) -> bool: ...
    def read(self, target: str) -> str: ...
    def write(self, target: str, secret: str) -> None: ...
    def delete(self, target: str) -> None: ...


class WindowsCredentialStore:
    """Small ctypes wrapper around Windows Credential Manager."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2

    class _CREDENTIALW(ctypes.Structure):
        _fields_ = [
            ("Flags", ctypes.c_uint32),
            ("Type", ctypes.c_uint32),
            ("TargetName", ctypes.c_wchar_p),
            ("Comment", ctypes.c_wchar_p),
            ("LastWrittenLow", ctypes.c_uint32),
            ("LastWrittenHigh", ctypes.c_uint32),
            ("CredentialBlobSize", ctypes.c_uint32),
            ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
            ("Persist", ctypes.c_uint32),
            ("AttributeCount", ctypes.c_uint32),
            ("Attributes", ctypes.c_void_p),
            ("TargetAlias", ctypes.c_wchar_p),
            ("UserName", ctypes.c_wchar_p),
        ]

    def available(self) -> bool:
        return platform.system() == "Windows" and hasattr(ctypes, "windll")

    def read(self, target: str) -> str:
        if not self.available():
            return ""
        pointer = ctypes.POINTER(self._CREDENTIALW)()
        if not ctypes.windll.advapi32.CredReadW(
            str(target), self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            return ""
        try:
            credential = pointer.contents
            size = int(credential.CredentialBlobSize)
            if not size or not credential.CredentialBlob:
                return ""
            raw = ctypes.string_at(credential.CredentialBlob, size)
            return raw.decode("utf-16-le")
        finally:
            ctypes.windll.advapi32.CredFree(pointer)

    def generation(self, target: str) -> int:
        """Credential Manager last-write identity, without copying its blob."""
        if not self.available():
            return 0
        pointer = ctypes.POINTER(self._CREDENTIALW)()
        if not ctypes.windll.advapi32.CredReadW(
            str(target), self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)
        ):
            return 0
        try:
            entry = pointer.contents
            return (entry.LastWrittenHigh << 32) | entry.LastWrittenLow
        finally:
            ctypes.windll.advapi32.CredFree(pointer)

    def write(self, target: str, secret: str) -> None:
        if not self.available():
            raise BackendConfigurationValidationError(
                "Secure operating-system credential storage is unavailable."
            )
        raw = str(secret).encode("utf-16-le")
        blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw) if raw else None
        credential = self._CREDENTIALW()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = str(target)
        credential.CredentialBlobSize = len(raw)
        credential.CredentialBlob = (
            ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte)) if blob is not None else None
        )
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "ProgTrack"
        if not ctypes.windll.advapi32.CredWriteW(ctypes.byref(credential), 0):
            raise OSError(ctypes.get_last_error(), "Could not store backend credential.")

    def delete(self, target: str) -> None:
        if self.available():
            ctypes.windll.advapi32.CredDeleteW(
                str(target), self.CRED_TYPE_GENERIC, 0
            )


@dataclass(frozen=True)
class PostgreSQLSettings:
    host: str = ""
    port: int = 5432
    database: str = ""
    user: str = ""
    sslmode: str = "require"
    server_name: str = ""
    ca_file: str = ""
    client_cert_file: str = ""
    client_key_file: str = ""
    connect_timeout: int = 10
    managed_root: str = ""
    pool_min: int = 1
    pool_max: int = 4


class BackendConfigurationService:
    SSL_MODES = ("disable", "allow", "prefer", "require", "verify-ca", "verify-full")

    def __init__(
        self,
        paths: RuntimePaths,
        *,
        environ: Mapping[str, str] | None = None,
        credential_store: CredentialStore | None = None,
    ):
        self.paths = paths
        self.environ = dict(os.environ if environ is None else environ)
        self.credential_store = credential_store or WindowsCredentialStore()
        identity = hashlib.sha256(
            str(paths.config_root.resolve()).casefold().encode("utf-8")
        ).hexdigest()[:20]
        self.credential_target = f"ProgTrack/backend/{identity}"

    @staticmethod
    def require_lord(authorized: bool) -> None:
        if not authorized:
            raise BackendConfigurationPermissionError(
                "Only a Lord account may configure the backend."
            )

    def load_document(self) -> dict[str, Any]:
        try:
            value = json.loads(self.paths.profile_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            raise BackendConfigurationValidationError(
                f"Invalid backend profile document: {exc}"
            ) from exc
        if not isinstance(value, dict):
            raise BackendConfigurationValidationError(
                "Backend profile document must contain one JSON object."
            )
        return value

    def saved_postgresql(self) -> PostgreSQLSettings:
        raw = self.load_document().get("postgresql", {})
        if not isinstance(raw, dict):
            raw = {}
        return PostgreSQLSettings(
            host=str(raw.get("host") or ""),
            port=int(raw.get("port") or 5432),
            database=str(raw.get("database") or ""),
            user=str(raw.get("user") or ""),
            sslmode=str(raw.get("sslmode") or "require"),
            server_name=str(raw.get("server_name") or ""),
            ca_file=str(raw.get("ca_file") or ""),
            client_cert_file=str(raw.get("client_cert_file") or ""),
            client_key_file=str(raw.get("client_key_file") or ""),
            connect_timeout=int(raw.get("connect_timeout") or 10),
            managed_root=str(raw.get(
                "managed_root", self.load_document().get("managed_root", self.paths.managed_root)
            ) or ""),
            pool_min=int(raw.get("pool_min") or 1),
            pool_max=int(raw.get("pool_max") or 4),
        )

    def read_password(self) -> str:
        return self.credential_store.read(self.credential_target)


    def client_key_passphrase_target(self) -> str:
        return f"{self.credential_target}/client-key-passphrase"

    def read_client_key_passphrase(self) -> str:
        return self.credential_store.read(self.client_key_passphrase_target())

    def catalog_cache_path(self) -> Path:
        # Portable installations can share their release directory. Catalog
        # visibility is per OS user and belongs under local application cache.
        roots = _windows_roots(self.environ) if platform.system() == "Windows" else _linux_roots(self.environ)
        profile_key = self.credential_target.rsplit("/", 1)[1]
        return roots[2] / "database-catalogs" / f"{profile_key}.json"

    def catalog_credential_generation(self) -> tuple:
        version = self.load_document().get("credential_generation", "legacy")
        generation = getattr(self.credential_store, "generation", lambda _target: 0)
        return version, generation(self.credential_target), generation(self.client_key_passphrase_target())

    @staticmethod
    def _validate_certificate_file(value: str, label: str) -> str:
        path = Path(str(value or "")).expanduser()
        if not path.is_file() or not os.access(path, os.R_OK):
            raise BackendConfigurationValidationError(
                f"{label} must point to a readable certificate/key file."
            )
        return str(path.resolve())

    def environment_overrides(self) -> list[str]:
        keys = (
            "PROGTRACK_BACKEND_PROFILE",
            "PROGTRACK_SQLITE_PATH",
            "PROGTRACK_MANAGED_ROOT",
            "PROGTRACK_POSTGRES_DSN",
            "PROGTRACK_POSTGRES_POOL_MIN",
            "PROGTRACK_POSTGRES_POOL_MAX",
        )
        return [key for key in keys if str(self.environ.get(key) or "").strip()]

    def validate_sqlite_filename(self, filename: str) -> Path:
        return self.validate_sqlite_location(
            self.paths.data_root / "database", filename
        )

    def validate_sqlite_location(
        self, folder: str | Path, filename: str
    ) -> Path:
        value = str(filename or "").strip()
        if not value or Path(value).name != value:
            raise BackendConfigurationValidationError(
                "Enter one SQLite file name without a folder."
            )
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._ -]*\.sqlite3", value):
            raise BackendConfigurationValidationError(
                "SQLite file name must end in .sqlite3 and contain safe characters only."
            )
        directory = Path(str(folder or "")).expanduser()
        if not directory.is_absolute():
            raise BackendConfigurationValidationError(
                "SQLite storage folder must be an absolute local path."
            )
        try:
            return validate_standalone_sqlite_path(directory / value)
        except Exception as exc:
            raise BackendConfigurationValidationError(str(exc)) from exc

    def validate_postgresql(
        self, settings: PostgreSQLSettings, *, validate_storage=True
    ) -> PostgreSQLSettings:
        if not settings.host.strip():
            raise BackendConfigurationValidationError("PostgreSQL host is required.")
        if not 1 <= int(settings.port) <= 65535:
            raise BackendConfigurationValidationError("PostgreSQL port is invalid.")
        if not settings.database.strip():
            raise BackendConfigurationValidationError("PostgreSQL database is required.")
        if not settings.user.strip():
            raise BackendConfigurationValidationError("PostgreSQL user is required.")
        if settings.sslmode not in self.SSL_MODES:
            raise BackendConfigurationValidationError("PostgreSQL SSL mode is invalid.")
        if settings.sslmode in {"verify-ca", "verify-full"} and not settings.ca_file.strip():
            raise BackendConfigurationValidationError(
                "A CA certificate/bundle is required for certificate verification."
            )
        ca_file = settings.ca_file.strip()
        client_cert = settings.client_cert_file.strip()
        client_key = settings.client_key_file.strip()
        if ca_file:
            self._validate_certificate_file(ca_file, "CA certificate/bundle")
        if bool(client_cert) != bool(client_key):
            raise BackendConfigurationValidationError(
                "Client certificate and private key must be provided together."
            )
        if client_cert:
            self._validate_certificate_file(client_cert, "Client certificate")
            self._validate_certificate_file(client_key, "Client private key")
        if settings.sslmode == "verify-full" and not (
            settings.server_name.strip() or settings.host.strip()
        ):
            raise BackendConfigurationValidationError(
                "A server name is required for verify-full TLS validation."
            )
        if not 1 <= int(settings.connect_timeout) <= 300:
            raise BackendConfigurationValidationError(
                "Connection timeout must be between 1 and 300 seconds."
            )
        if not 1 <= int(settings.pool_min) <= int(settings.pool_max) <= 64:
            raise BackendConfigurationValidationError(
                "Pool sizes must satisfy 1 <= minimum <= maximum <= 64."
            )
        if validate_storage:
            try:
                managed = Path(settings.managed_root).expanduser()
            except (OSError, RuntimeError, ValueError):
                raise BackendConfigurationValidationError("Managed document storage path is invalid.") from None
            if not managed.is_absolute():
                raise BackendConfigurationValidationError(
                    "Managed document storage must be an absolute path."
                )
        return settings

    def connection_dsn(
        self,
        settings: PostgreSQLSettings,
        *,
        password: str = "",
        client_key_passphrase: str = "",
        allow_environment: bool = True,
    ) -> str:
        if allow_environment:
            override = str(self.environ.get("PROGTRACK_POSTGRES_DSN") or "").strip()
            if override:
                return override
        validated = self.validate_postgresql(settings, validate_storage=False)
        try:
            from psycopg.conninfo import make_conninfo
        except ImportError as exc:
            raise BackendConfigurationValidationError(
                "Psycopg 3 is not installed."
            ) from exc
        options = {
            "host": validated.host,
            "port": validated.port,
            "dbname": validated.database,
            "user": validated.user,
            "password": password,
            "sslmode": validated.sslmode,
            "connect_timeout": validated.connect_timeout,
        }
        if validated.server_name.strip():
            options["hostaddr"] = validated.host
            options["host"] = validated.server_name.strip()
        if validated.ca_file.strip():
            options["sslrootcert"] = validated.ca_file
        if validated.client_cert_file.strip():
            options["sslcert"] = validated.client_cert_file
            options["sslkey"] = validated.client_key_file
        if client_key_passphrase:
            options["sslpassword"] = client_key_passphrase
        return make_conninfo(**options)

    def effective_postgres_dsn(self) -> str:
        return self.connection_dsn(
            self.saved_postgresql(),
            password=self.read_password(),
            client_key_passphrase=self.read_client_key_passphrase(),
            allow_environment=True,
        )

    def storage_secret_target(self) -> str:
        return self.credential_target + "/managed-storage-password"

    def read_storage_password(self) -> str:
        return self.credential_store.read(self.storage_secret_target())

    def saved_storage_username(self) -> str:
        storage = self.load_document().get("storage", {})
        return str(storage.get("username") or "") if isinstance(storage, dict) else ""

    def storage_credentials(self, *, runtime_dsn=None, username=None, password=None):
        """Use ordinary connection identity only, independently per field."""
        from .backend.managed_storage import resolve_storage_credentials

        return resolve_storage_credentials(
            self.effective_postgres_dsn() if runtime_dsn is None else runtime_dsn,
            username=self.saved_storage_username() if username is None else username,
            password=self.read_storage_password() if password is None else password,
        )

    def check_storage(self, root, *, runtime_dsn=None, username=None, password=None, writable=False):
        from .backend.managed_storage import _unc_share, require_storage_access

        credentials = None
        if sys.platform == "win32" and _unc_share(str(root)):
            credentials = self.storage_credentials(runtime_dsn=runtime_dsn, username=username, password=password)
        mount_anchor = self.environ.get("PROGTRACK_STORAGE_MOUNT_ANCHOR") if sys.platform != "win32" else None
        return require_storage_access(root, credentials=credentials, writable=writable,
                                      mount_anchor=Path(mount_anchor) if mount_anchor else None)

    def effective_pool_sizes(self) -> tuple[int, int]:
        settings = self.saved_postgresql()
        return (
            int(self.environ.get("PROGTRACK_POSTGRES_POOL_MIN") or settings.pool_min),
            int(self.environ.get("PROGTRACK_POSTGRES_POOL_MAX") or settings.pool_max),
        )

    def save_disconnected_shared(self, *, postgresql, password, client_key_passphrase):
        """Save this client's Shared target during recovery, without contacting it."""
        saved = self.load_document().get("profile", self.paths.profile.value)
        if self.paths.profile is not BackendProfile.SHARED_POSTGRESQL or saved != self.paths.profile.value:
            raise BackendConfigurationPermissionError("Only a Shared profile can be corrected here.")
        same_endpoint, same_client_key = self.recovery_secret_identity(postgresql)
        if not same_endpoint and not password and self.read_password():
            raise BackendConfigurationValidationError("Enter a new password for the corrected PostgreSQL target.")
        previous = self.saved_postgresql()
        postgresql = replace(postgresql, managed_root=previous.managed_root if same_endpoint else "")
        return self._save_profile(
            profile=BackendProfile.SHARED_POSTGRESQL,
            sqlite_filename="",
            postgresql=postgresql,
            password=password,
            client_key_passphrase=client_key_passphrase,
            storage_username=None if same_endpoint else "",
            storage_password=None if same_endpoint else "",
            clear_client_key_passphrase=not same_client_key and not client_key_passphrase,
            validate_storage=False,
        )

    def recovery_secret_identity(self, staged):
        """Match credential scope to the saved endpoint and its TLS identity."""
        saved = self.saved_postgresql()
        endpoint_fields = ("host", "port", "database", "user", "sslmode", "server_name", "ca_file")
        same_endpoint = all(getattr(staged, field) == getattr(saved, field) for field in endpoint_fields)
        same_client_key = same_endpoint and (
            staged.client_cert_file == saved.client_cert_file and staged.client_key_file == saved.client_key_file
        )
        return same_endpoint, same_client_key

    def save(self, *, authorized: bool, **settings) -> dict[str, Any]:
        self.require_lord(authorized)
        return self._save_profile(**settings)

    def _save_profile(
        self,
        *,
        profile: BackendProfile,
        sqlite_filename: str,
        sqlite_folder: str | Path | None = None,
        postgresql: PostgreSQLSettings,
        password: str,
        client_key_passphrase: str = "",
        storage_username: str | None = None,
        storage_password: str | None = None,
        clear_client_key_passphrase: bool = False,
        validate_storage: bool = True,
    ) -> dict[str, Any]:
        previous = self.load_document()
        if profile is BackendProfile.STANDALONE_SQLITE:
            sqlite_path = self.validate_sqlite_location(
                sqlite_folder or self.paths.data_root / "database",
                sqlite_filename,
            )
        else:
            sqlite_path = Path(
                str(
                    previous.get("sqlite_path")
                    or self.paths.database_path
                    or self.paths.data_root / "database" / "progtrack.sqlite3"
                )
            )

        secret_changes = {}
        storage_user = self.saved_storage_username()
        pg_document = previous.get("postgresql", {})
        if not isinstance(pg_document, dict):
            pg_document = {}
        managed_root = str(
            previous.get("managed_root") or self.paths.managed_root
        )
        if profile is BackendProfile.SHARED_POSTGRESQL:
            pg = self.validate_postgresql(postgresql, validate_storage=validate_storage)
            if password:
                secret_changes[self.credential_target] = password
            elif not self.read_password() and not str(
                self.environ.get("PROGTRACK_POSTGRES_DSN") or ""
            ).strip():
                raise BackendConfigurationValidationError(
                    "A PostgreSQL password or deployment DSN override is required."
                )
            managed_root = str(Path(pg.managed_root).expanduser()) if validate_storage else pg.managed_root
            if client_key_passphrase:
                secret_changes[self.client_key_passphrase_target()] = client_key_passphrase
            elif clear_client_key_passphrase and self.read_client_key_passphrase():
                secret_changes[self.client_key_passphrase_target()] = ""
            if sys.platform == "win32":
                if storage_username is not None:
                    storage_user = storage_username.strip()
                if storage_password is not None:
                    secret_changes[self.storage_secret_target()] = storage_password
            pg_document = {
                "host": pg.host.strip(),
                "port": int(pg.port),
                "database": pg.database.strip(),
                "user": pg.user.strip(),
                "sslmode": pg.sslmode,
                "server_name": pg.server_name.strip(),
                "ca_file": str(Path(pg.ca_file).expanduser().resolve()) if pg.ca_file.strip() else "",
                "client_cert_file": str(Path(pg.client_cert_file).expanduser().resolve()) if pg.client_cert_file.strip() else "",
                "client_key_file": str(Path(pg.client_key_file).expanduser().resolve()) if pg.client_key_file.strip() else "",
                "connect_timeout": int(pg.connect_timeout),
                "managed_root": managed_root,
                "pool_min": int(pg.pool_min),
                "pool_max": int(pg.pool_max),
            }
        document = {
            "profile": profile.value,
            "sqlite_path": str(sqlite_path),
            "managed_root": managed_root,
            "postgresql": pg_document,
            "storage": {"username": storage_user},
            "credential_generation": (
                uuid.uuid4().hex if profile is BackendProfile.SHARED_POSTGRESQL and (password or client_key_passphrase)
                else previous.get("credential_generation", "legacy")
            ),
        }
        self.paths.profile_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.paths.profile_file.with_suffix(".json.tmp")
        changed = []
        try:
            previous_secrets = {
                target: self.credential_store.read(target) for target in secret_changes
            }
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            for target, value in secret_changes.items():
                changed.append(target)
                if value:
                    self.credential_store.write(target, value)
                else:
                    self.credential_store.delete(target)
            os.replace(temporary, self.paths.profile_file)
        except Exception:
            restored = True
            for target in reversed(changed):
                try:
                    value = previous_secrets[target]
                    if value:
                        self.credential_store.write(target, value)
                    else:
                        self.credential_store.delete(target)
                except Exception:
                    restored = False
            if not restored:
                raise BackendConfigurationValidationError(
                    "Could not save the backend settings or restore the previous credentials."
                ) from None
            raise BackendConfigurationValidationError("Could not save the backend settings.") from None
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        return document


def configured_postgres_dsn(paths: RuntimePaths) -> str:
    """Resolve the secret-bearing DSN for startup without logging it."""
    if paths.profile is not BackendProfile.SHARED_POSTGRESQL:
        return ""
    return BackendConfigurationService(paths).effective_postgres_dsn()
