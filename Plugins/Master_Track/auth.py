# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Master Track authentication helpers.

from __future__ import annotations

import hashlib
import logging
import os
from datetime import date
from typing import Any, Dict, List, Optional

from .permissions import (
    JOB_ANIMAL_WELFARE,
    JOB_VET,
)

logger = logging.getLogger(__name__)


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
    """Backend-owned user database with PBKDF2 password hashes.

    User records are never read from or written to a file.  The backend is
    mandatory so an installation cannot silently fall back to the archived
    The former users.enc example store is archived; runtime users are loaded
    exclusively from the backend security namespace.
    """

    def __init__(self, plugin_dir: str, backend=None):
        if backend is None:
            raise RuntimeError("Master Track requires the configured ProgTrack backend.")
        self.backend = backend
        self._users: List[Dict[str, Any]] = []
        self._loaded = False

    # -- persistence --------------------------------------------------------

    def exists(self) -> bool:
        return bool(self.backend.records.get("security", "users", default=[]))

    def load(self) -> List[Dict[str, Any]]:
        value = self.backend.records.get("security", "users", default=[])
        self._users = value if isinstance(value, list) else []
        self._loaded = True
        changed = False
        for user in self._users:
            before = (user.get("role"), tuple(user.get("jobs") or []))
            self._ensure_user_defaults(user)
            after = (user.get("role"), tuple(user.get("jobs") or []))
            changed = changed or before != after
        if changed:
            self.save()
        return self._users

    def save(self) -> None:
        for user in self._users:
            self._ensure_user_defaults(user)
        self.backend.records.put("security", "users", self._users)

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
        """Backfill fields and enforce the Vet-bound AWO job invariant."""
        jobs = user.setdefault("jobs", [])
        if not isinstance(jobs, list):
            jobs = []
        cleaned_jobs = []
        seen = set()
        for raw in jobs:
            value = str(raw or "").strip()
            if value and value not in seen:
                cleaned_jobs.append(value)
                seen.add(value)
        if JOB_ANIMAL_WELFARE in seen and JOB_VET not in seen:
            # Never grant AWO access from malformed or hand-edited records.
            cleaned_jobs = [job for job in cleaned_jobs if job != JOB_ANIMAL_WELFARE]
        user["jobs"] = cleaned_jobs
        unit_id = str(user.get("unit_id") or "").strip()
        user["unit_id"] = unit_id.casefold()
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
                 mobile: str = "", unit: str = "", profession: str = "",
                 unit_id: str = "") -> Dict[str, Any]:
        pw_hash, salt = hash_password(password)
        initial_jobs: list[str] = []
        record = {
            "username": username,
            "display_name": display_name or username,
            "role": role,
            "jobs": initial_jobs,
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
            "unit_id":     unit_id.strip().casefold(),
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
                             profession: Optional[str] = None,
                             unit_id: Optional[str] = None) -> bool:
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
        if unit_id is not None:
            user["unit_id"] = str(unit_id).strip().casefold()
        elif unit is not None and "unit_id" not in user:
            user["unit_id"] = ""
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
        """Replace jobs, rejecting AWO unless Vet is also assigned."""
        user = self.get_user(username)
        if not user:
            return False
        cleaned = []
        seen = set()
        for raw in jobs or []:
            value = str(raw or "").strip()
            if value and value not in seen:
                cleaned.append(value)
                seen.add(value)
        if JOB_ANIMAL_WELFARE in seen and JOB_VET not in seen:
            logger.warning("Refused AWO assignment without Vet for '%s'.", username)
            return False
        user["jobs"] = cleaned
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

    def remove_job_from_all_users(self, job_name: str) -> int:
        """Remove a deleted custom job from every account atomically."""
        count = 0
        for user in self.users:
            self._ensure_user_defaults(user)
            jobs = list(user.get("jobs", []))
            retained = [job for job in jobs if job != job_name]
            if retained != jobs:
                user["jobs"] = retained
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
