"""Deployment-profile aware runtime paths for ProgTrack.

Packaged application resources remain below ``application_root`` and are
read-only. Every mutable path is resolved below an explicit data/config/cache
root. Tiny/Standalone SQLite is local-only; Shared PostgreSQL never falls back
to SQLite.
"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import socket
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping


class BackendProfile(str, Enum):
    STANDALONE_SQLITE = "standalone_sqlite"
    SHARED_POSTGRESQL = "shared_postgresql"


class RuntimePathError(RuntimeError):
    """Raised when a deployment profile resolves to an unsafe topology."""


@dataclass(frozen=True)
class RuntimePaths:
    application_root: Path
    profile: BackendProfile
    data_root: Path
    config_root: Path
    cache_root: Path
    state_root: Path
    database_path: Path | None
    managed_root: Path
    managed_documents: Path
    managed_config_assets: Path
    logs: Path
    runtime: Path
    exports: Path
    preferences: Path
    profile_file: Path

    def create_mutable_roots(self) -> None:
        roots = {
            self.data_root,
            self.config_root,
            self.cache_root,
            self.state_root,
            self.managed_root,
            self.managed_documents,
            self.managed_config_assets,
            self.logs,
            self.runtime,
            self.exports,
            self.preferences,
        }
        if self.database_path is not None:
            roots.add(self.database_path.parent)
        for path in roots:
            path.mkdir(parents=True, exist_ok=True)

    def as_dict(self) -> dict[str, str | None]:
        return {
            "application_root": str(self.application_root),
            "profile": self.profile.value,
            "data_root": str(self.data_root),
            "config_root": str(self.config_root),
            "cache_root": str(self.cache_root),
            "state_root": str(self.state_root),
            "database_path": str(self.database_path) if self.database_path else None,
            "managed_root": str(self.managed_root),
            "managed_documents": str(self.managed_documents),
            "managed_config_assets": str(self.managed_config_assets),
            "logs": str(self.logs),
            "runtime": str(self.runtime),
            "exports": str(self.exports),
            "preferences": str(self.preferences),
            "profile_file": str(self.profile_file),
        }


def _windows_roots(environ: Mapping[str, str]) -> tuple[Path, Path, Path, Path]:
    local = Path(environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    roaming = Path(environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    data = local / "ProgTrack"
    return data, roaming / "ProgTrack", data / "cache", data / "state"


def _linux_roots(environ: Mapping[str, str]) -> tuple[Path, Path, Path, Path]:
    user = Path.home()
    data = Path(environ.get("XDG_DATA_HOME") or user / ".local" / "share") / "ProgTrack"
    config = Path(environ.get("XDG_CONFIG_HOME") or user / ".config") / "ProgTrack"
    cache = Path(environ.get("XDG_CACHE_HOME") or user / ".cache") / "ProgTrack"
    state = Path(environ.get("XDG_STATE_HOME") or user / ".local" / "state") / "ProgTrack"
    return data, config, cache, state


def _portable_roots(application_root: Path) -> tuple[Path, Path, Path, Path]:
    root = application_root / "ProgTrackData"
    return root, root / "config", root / "cache", root / "state"


def application_directory_is_writable(application_root: Path) -> bool:
    """Probe write access without leaving a file behind."""
    try:
        application_root.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix=".progtrack-write-probe-", dir=application_root)
        os.close(handle)
        Path(name).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def _load_profile_file(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimePathError(f"Invalid backend profile file: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimePathError(f"Backend profile must be a JSON object: {path}")
    return raw


def _profile_value(
    environ: Mapping[str, str], config: Mapping[str, Any]
) -> BackendProfile:
    value = str(
        environ.get("PROGTRACK_BACKEND_PROFILE")
        or config.get("profile")
        or BackendProfile.STANDALONE_SQLITE.value
    ).strip().lower()
    try:
        return BackendProfile(value)
    except ValueError as exc:
        allowed = ", ".join(profile.value for profile in BackendProfile)
        raise RuntimePathError(
            f"Unsupported backend profile {value!r}; expected one of: {allowed}"
        ) from exc


def resolve_runtime_paths(
    application_root: str | Path,
    *,
    environ: Mapping[str, str] | None = None,
    create: bool = True,
) -> RuntimePaths:
    env = dict(os.environ if environ is None else environ)
    app_root = Path(application_root).resolve()
    portable_requested = str(env.get("PROGTRACK_PORTABLE", "")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    # A read-only application folder always selects installed/user-profile
    # roots, even if portable mode was requested.
    portable = portable_requested and application_directory_is_writable(app_root)

    if portable:
        data_root, config_root, cache_root, state_root = _portable_roots(app_root)
    elif platform.system() == "Windows":
        data_root, config_root, cache_root, state_root = _windows_roots(env)
    else:
        data_root, config_root, cache_root, state_root = _linux_roots(env)

    profile_file = config_root / "backend.json"
    profile_config = _load_profile_file(profile_file)
    profile = _profile_value(env, profile_config)

    managed_override = env.get("PROGTRACK_MANAGED_ROOT") or profile_config.get(
        "managed_root"
    )
    managed_root = (
        Path(str(managed_override)).expanduser()
        if managed_override
        else data_root / "managed"
    )

    database_path: Path | None = None
    if profile is BackendProfile.STANDALONE_SQLITE:
        configured_db = env.get("PROGTRACK_SQLITE_PATH") or profile_config.get(
            "sqlite_path"
        )
        database_path = (
            Path(str(configured_db)).expanduser()
            if configured_db
            else data_root / "database" / "progtrack.sqlite3"
        )
        validate_standalone_sqlite_path(database_path)

    paths = RuntimePaths(
        application_root=app_root,
        profile=profile,
        data_root=data_root,
        config_root=config_root,
        cache_root=cache_root,
        state_root=state_root,
        database_path=database_path,
        managed_root=managed_root,
        managed_documents=managed_root / "documents",
        managed_config_assets=managed_root / "config-assets",
        logs=state_root / "logs",
        runtime=state_root / "runtime",
        exports=data_root / "exports",
        preferences=config_root / "preferences",
        profile_file=profile_file,
    )
    if create:
        paths.create_mutable_roots()
    return paths


def _windows_drive_type(path: Path) -> int | None:
    if platform.system() != "Windows":
        return None
    anchor = path.resolve().anchor
    if not anchor:
        return None
    try:
        return int(ctypes.windll.kernel32.GetDriveTypeW(str(anchor)))
    except (AttributeError, OSError):
        return None


def validate_standalone_sqlite_path(path: str | Path) -> Path:
    """Reject obvious shared/synchronized locations for writable SQLite."""
    resolved = Path(path).expanduser().resolve()
    text = str(resolved)
    if text.startswith("\\\\") or text.startswith("//"):
        raise RuntimePathError("Standalone SQLite cannot be stored on a UNC/network path.")
    # DRIVE_REMOTE=4. DRIVE_UNKNOWN/NO_ROOT are left to the writable-path test.
    if _windows_drive_type(resolved) == 4:
        raise RuntimePathError("Standalone SQLite cannot be stored on a network drive.")
    folded = text.casefold()
    synchronized_markers = (
        "\\onedrive\\",
        "/onedrive/",
        "\\dropbox\\",
        "/dropbox/",
        "\\google drive\\",
        "/google drive/",
        "\\nextcloud\\",
        "/nextcloud/",
    )
    if any(marker in folded for marker in synchronized_markers):
        raise RuntimePathError(
            "Standalone SQLite cannot be stored in a synchronized/cloud folder."
        )
    return resolved


def backend_diagnostics(paths: RuntimePaths) -> dict[str, Any]:
    return {
        **paths.as_dict(),
        "host": socket.gethostname(),
        "platform": platform.platform(),
        "application_directory_writable": application_directory_is_writable(
            paths.application_root
        ),
    }
