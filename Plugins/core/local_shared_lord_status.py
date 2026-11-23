# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.3.0
"""Per-user, last server-confirmed Lord status for locked connection recovery.

This record contains no password, password verifier, session token, or animal
data. It is only a local UI capability marker; Shared PostgreSQL remains the
authority for every application login and server operation.
"""

from __future__ import annotations

import json
import os
import secrets
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


def local_user_state_root(
    *, environ: Mapping[str, str] | None = None, home: str | Path | None = None,
    platform: str | None = None,
) -> Path:
    """Return an OS-user-specific state root, independent of portable installs.

    The Lord recovery marker must not live beside a portable installation,
    where multiple OS accounts could otherwise share or replace it.
    """
    values = os.environ if environ is None else environ
    user_home = Path(home) if home is not None else Path.home()
    current_platform = os.name if platform is None else platform
    if current_platform == "nt":
        base = values.get("LOCALAPPDATA") or str(user_home / "AppData" / "Local")
        return Path(base) / "ProgTrack" / "state"
    base = values.get("XDG_STATE_HOME") or str(user_home / ".local" / "state")
    return Path(base) / "ProgTrack"


class SharedLordStatusStore:
    FORMAT_VERSION = 1
    _FILENAME = "shared_lord_status.json"

    def __init__(self, state_root: str | Path):
        self.directory = Path(state_root) / "security"
        self.path = self.directory / self._FILENAME

    @staticmethod
    def _is_posix() -> bool:
        return os.name == "posix" and hasattr(os, "getuid")

    def _prepare_directory(self, *, create: bool) -> bool:
        try:
            if create:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            info = self.directory.lstat()
            if not stat.S_ISDIR(info.st_mode):
                return False
            if self._is_posix():
                if info.st_uid != os.getuid():
                    return False
                if stat.S_IMODE(info.st_mode) != 0o700:
                    if not create:
                        return False
                    self.directory.chmod(0o700)
            return True
        except OSError:
            return False

    def load(self) -> dict[str, Any] | None:
        if not self._prepare_directory(create=False):
            return None
        try:
            info = self.path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_size > 4096:
                return None
            if self._is_posix() and (
                info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600
            ):
                return None
            record = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
            return None
        if not isinstance(record, dict):
            return None
        username = str(record.get("username") or "").strip()
        if (
            record.get("format_version") != self.FORMAT_VERSION
            or record.get("authority") != "shared_postgresql"
            or str(record.get("role") or "").casefold() != "lord"
            or record.get("account_status") != "active"
            or not username
        ):
            return None
        return record

    def sync_from_server_account(self, account: Mapping[str, Any]) -> bool:
        """Replace the marker from an account row just verified by the server.

        A successful non-Lord login clears any older Lord marker for this OS
        profile, so a normal user cannot inherit a previous user's recovery UI.
        """
        username = str(account.get("username") or "").strip()
        is_active = account.get("active", True) is not False and not any(
            bool(account.get(flag)) for flag in ("disabled", "archived", "blocked")
        )
        is_lord = str(account.get("role") or "").casefold() == "lord"
        if not username or not is_active or not is_lord:
            self.clear()
            return False

        record = {
            "format_version": self.FORMAT_VERSION,
            "authority": "shared_postgresql",
            "username": username,
            "role": "lord",
            "account_status": "active",
            "server_confirmed_at": datetime.now(timezone.utc).isoformat(),
        }
        if not self._prepare_directory(create=True):
            return False
        temporary = self.directory / f".{self._FILENAME}.{secrets.token_hex(8)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = None
        try:
            fd = os.open(temporary, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                fd = None
                json.dump(record, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if self._is_posix():
                self.path.chmod(0o600)
            return True
        except OSError:
            return False
        finally:
            if fd is not None:
                os.close(fd)
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> None:
        try:
            info = self.path.lstat()
            if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                self.path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            return
