# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Master Track authentication helpers.

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import uuid
from datetime import date
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base64 obfuscation (replaces cryptography package)
# ---------------------------------------------------------------------------

def _derive_key() -> bytes:
    """Derive an obfuscation key from a fixed app secret + machine id."""
    secret = b"ProgTrack_Master_Track_v1"
    machine = str(uuid.getnode()).encode()
    # Simple xor-style obfuscation key (not secure, just hiding)
    raw = hashlib.sha256(secret + machine).digest()
    return base64.urlsafe_b64encode(raw)


def _encrypt(data: bytes) -> bytes:
    """Encrypt data using base64 obfuscation."""
    key = _derive_key()
    return base64.urlsafe_b64encode(key + data)


def _decrypt(token: bytes) -> bytes:
    """Decrypt data from base64 obfuscation."""
    raw = base64.urlsafe_b64decode(token)
    key = _derive_key()
    return raw[len(key):]


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """Return (hex-hash, hex-salt) for *password*."""
    if salt is None:
        salt = os.urandom(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
    return h.hex(), salt.hex()


def verify_password(password: str, stored_hash: str, stored_salt: str) -> bool:
    salt = bytes.fromhex(stored_salt)
    h = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 260_000)
    return h.hex() == stored_hash


# ---------------------------------------------------------------------------
# User database I/O
# ---------------------------------------------------------------------------

class UserDB:
    """Encrypted JSON user database stored in *users.enc*."""

    def __init__(self, plugin_dir: str):
        self.path = os.path.join(plugin_dir, "users.enc")
        self._users: List[Dict[str, Any]] = []
        self._loaded = False

    # -- persistence --------------------------------------------------------

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def load(self) -> List[Dict[str, Any]]:
        if not self.exists():
            self._users = []
            self._loaded = True
            return self._users
        try:
            with open(self.path, "rb") as f:
                raw = _decrypt(f.read())
            self._users = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            logger.error("Failed to load user database: %s", exc)
            self._users = []
        self._loaded = True
        return self._users

    def save(self) -> None:
        raw = json.dumps(self._users, indent=2).encode("utf-8")
        enc = _encrypt(raw)
        with open(self.path, "wb") as f:
            f.write(enc)

    @property
    def users(self) -> List[Dict[str, Any]]:
        if not self._loaded:
            self.load()
        return self._users

    # -- queries ------------------------------------------------------------

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        for u in self.users:
            if u["username"].lower() == username.lower():
                return self._ensure_user_defaults(u)
        return None

    def lord_exists(self) -> bool:
        return any(u["role"] == "lord" for u in self.users)

    def lord_count(self) -> int:
        return sum(1 for u in self.users if u["role"] == "lord")

    @staticmethod
    def _ensure_user_defaults(user: Dict[str, Any]) -> Dict[str, Any]:
        """Backfill missing jobs/permissions fields for records pre-dating this schema."""
        user.setdefault("jobs", [])
        perms = user.setdefault("permissions", {})
        if not isinstance(perms, dict):
            user["permissions"] = {"granted": [], "revoked": []}
        else:
            perms.setdefault("granted", [])
            perms.setdefault("revoked", [])
        return user

    # -- mutations ----------------------------------------------------------

    def add_user(self, username: str, password: str, role: str = "user",
                 display_name: str = "",
                 pronouns: str = "", email: str = "", phone: str = "",
                 mobile: str = "", unit: str = "", profession: str = "") -> Dict[str, Any]:
        pw_hash, salt = hash_password(password)
        record = {
            "username": username,
            "display_name": display_name or username,
            "role": role,
            "jobs": [],
            "permissions": {"granted": [], "revoked": []},
            "password_hash": pw_hash,
            "salt": salt,
            "created_at": date.today().isoformat(),
            "last_login": None,
            "must_change_password": role != "lord",
            "pronouns":   pronouns,
            "email":      email,
            "phone":      phone,
            "mobile":     mobile,
            "unit":       unit,
            "profession": profession,
        }
        self._users.append(record)
        self.save()
        return record

    def update_user_profile(self, username: str,
                             display_name: Optional[str] = None,
                             pronouns: Optional[str] = None,
                             email: Optional[str] = None,
                             phone: Optional[str] = None,
                             mobile: Optional[str] = None,
                             unit: Optional[str] = None,
                             profession: Optional[str] = None) -> bool:
        """Update profile fields for *username*. Only non-None arguments are changed."""
        user = self.get_user(username)
        if not user:
            return False
        if display_name is not None:
            user["display_name"] = display_name
        for key, val in [("pronouns", pronouns), ("email", email), ("phone", phone),
                         ("mobile", mobile), ("unit", unit), ("profession", profession)]:
            if val is not None:
                user[key] = val
        self.save()
        return True

    def delete_user(self, username: str) -> bool:
        target = self.get_user(username)
        if not target:
            return False
        if target["role"] == "lord" and self.lord_count() <= 1:
            logger.warning("Refused to delete last lord account '%s'.", username)
            return False
        before = len(self._users)
        self._users = [u for u in self._users if u["username"].lower() != username.lower()]
        if len(self._users) < before:
            self.save()
            return True
        return False

    def set_password(self, username: str, new_password: str,
                     must_change: bool = False) -> bool:
        user = self.get_user(username)
        if not user:
            return False
        pw_hash, salt = hash_password(new_password)
        user["password_hash"] = pw_hash
        user["salt"] = salt
        user["must_change_password"] = must_change
        self.save()
        return True

    def set_role(self, username: str, new_role: str) -> bool:
        user = self.get_user(username)
        if not user:
            return False
        if user["role"] == "lord" and new_role != "lord" and self.lord_count() <= 1:
            logger.warning("Refused to demote last lord account '%s'.", username)
            return False
        user["role"] = new_role
        self.save()
        return True

    def set_jobs(self, username: str, jobs: List[str]) -> bool:
        """Replace the job list for *username*. Returns True on success."""
        user = self.get_user(username)
        if not user:
            return False
        user["jobs"] = list(jobs)
        self.save()
        return True

    def set_direct_permissions(self, username: str,
                                granted: List[str], revoked: List[str]) -> bool:
        """Replace the direct permission grants/revocations for *username*."""
        user = self.get_user(username)
        if not user:
            return False
        user["permissions"] = {"granted": list(granted), "revoked": list(revoked)}
        self.save()
        return True

    def reset_permissions_for_job(self, job_name: str) -> int:
        """Clear custom granted/revoked overrides for every user that has *job_name*
        assigned.  Returns the number of affected users."""
        count = 0
        for user in self.users:
            self._ensure_user_defaults(user)
            if job_name in user.get("jobs", []):
                user["permissions"] = {"granted": [], "revoked": []}
                count += 1
        if count:
            self.save()
        return count

    def record_login(self, username: str) -> None:
        user = self.get_user(username)
        if user:
            user["last_login"] = date.today().isoformat()
            self.save()

    def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        user = self.get_user(username)
        if not user:
            return None
        if verify_password(password, user["password_hash"], user["salt"]):
            return user
        return None
