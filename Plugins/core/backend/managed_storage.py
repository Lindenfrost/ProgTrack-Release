# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Client-local access to a managed-document root (#226)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath

from .errors import BackendConfigurationError


class ManagedStorageUnavailable(BackendConfigurationError):
    """Sanitized failure suitable for a localized storage diagnostic."""

    def __init__(self, reason: str):
        super().__init__("Managed document storage is unavailable.")
        self.reason = reason


@dataclass(frozen=True)
class StorageCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)


def resolve_storage_credentials(
    runtime_dsn: str, *, username: str = "", password: str = ""
) -> StorageCredentials:
    """Resolve each override independently against the ordinary runtime DSN."""
    from psycopg.conninfo import conninfo_to_dict

    try:
        options = conninfo_to_dict(runtime_dsn)
    except Exception:
        raise ManagedStorageUnavailable("invalid_credentials") from None
    return StorageCredentials(
        username.strip() or options.get("user", ""),
        password or options.get("password", ""),
    )


def _unc_share(value: str) -> str | None:
    normalized = value.replace("/", "\\")
    if not normalized.startswith("\\\\"):
        return None
    path = PureWindowsPath(normalized)
    share = path.drive
    if (share.startswith(("\\\\?\\", "\\\\.\\")) or not path.is_absolute()
            or len(share[2:].split("\\")) != 2 or ".." in path.parts):
        raise ManagedStorageUnavailable("invalid_path")
    return share


def _temporary_windows_connection(share: str, credentials: StorageCredentials) -> None:
    import ctypes
    from ctypes import wintypes

    if "\0" in credentials.username or "\0" in credentials.password:
        raise ManagedStorageUnavailable("invalid_credentials")

    class NetResource(ctypes.Structure):
        _fields_ = [
            ("scope", wintypes.DWORD), ("type", wintypes.DWORD),
            ("display_type", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("local_name", wintypes.LPWSTR), ("remote_name", wintypes.LPWSTR),
            ("comment", wintypes.LPWSTR), ("provider", wintypes.LPWSTR),
        ]

    fn = ctypes.WinDLL("mpr").WNetAddConnection2W
    fn.argtypes = (ctypes.POINTER(NetResource), wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD)
    fn.restype = wintypes.DWORD
    resource = NetResource(0, 1, 0, 0, None, share, None, None)
    code = fn(ctypes.byref(resource), credentials.password, credentials.username, 4)
    if code == 1219:
        raise ManagedStorageUnavailable("credential_conflict")
    if code in (5, 86, 1326):
        raise ManagedStorageUnavailable("authentication_failed")
    if code != 0:
        raise ManagedStorageUnavailable("not_accessible")


def require_storage_access(
    root: str | Path,
    *,
    credentials: StorageCredentials | None = None,
    writable: bool = False,
    mount_anchor: Path | None = None,
) -> Path:
    """Check a root under the actual OS account; the caller bounds blocking I/O.

    An optional IT-provided mount anchor detects a lost mount even when its
    underlying directory still exists. Local roots have no mount anchor.
    """
    value = os.fspath(root)
    if not value.strip() or any(ord(char) < 32 for char in value):
        raise ManagedStorageUnavailable("invalid_path")
    share = _unc_share(value) if sys.platform == "win32" else None
    try:
        path = Path(value).expanduser()
    except (OSError, RuntimeError, ValueError):
        raise ManagedStorageUnavailable("invalid_path") from None
    if not path.is_absolute() or ".." in path.parts:
        raise ManagedStorageUnavailable("invalid_path")
    try:
        if mount_anchor is not None:
            anchor = Path(mount_anchor)
            if not anchor.is_absolute() or not path.is_relative_to(anchor) or not os.path.ismount(anchor):
                raise ManagedStorageUnavailable("mount_unavailable")
        if share is not None and credentials is not None and (credentials.username or credentials.password):
            _temporary_windows_connection(share, credentials)
        # Query real access under the process account without creating test files.
        with os.scandir(path) as entries:
            next(entries, None)
        if writable and not os.access(path, os.W_OK):
            raise ManagedStorageUnavailable("read_only")
        if writable and hasattr(os, "statvfs") and os.statvfs(path).f_flag & os.ST_RDONLY:
            raise ManagedStorageUnavailable("read_only")
    except (FileNotFoundError, NotADirectoryError):
        raise ManagedStorageUnavailable("not_available") from None
    except (OSError, ValueError):
        raise ManagedStorageUnavailable("not_accessible") from None
    return path
