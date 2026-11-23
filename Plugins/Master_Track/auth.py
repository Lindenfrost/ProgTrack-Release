# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.3.0
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Master Track authentication helpers.

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from contextlib import nullcontext
from datetime import date
from typing import Any, Dict, List, Optional

from .permissions import (
    JOB_ANIMAL_WELFARE,
    JOB_VET,
)
from Plugins.core.backend.errors import PermissionDeniedError, SessionRevokedError

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
    return hmac.compare_digest(h.hex(), stored_hash)


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

    def __init__(self, plugin_dir: str, backend=None, *, session=None, on_mutation=None):
        if backend is None:
            raise RuntimeError("Master Track requires the configured ProgTrack backend.")
        self.backend = backend
        self.session = session or (lambda: None)
        self.on_mutation = on_mutation
        self._managed = bool(getattr(backend, "account_commands", None) and (session is not None or self._shared()))
        self._users: List[Dict[str, Any]] = []
        self._loaded = False
        self._users_revision: Optional[int] = None
        self._account_baselines = {}

    # -- persistence --------------------------------------------------------

    def exists(self) -> bool:
        return bool(self._read()[0])

    def _shared(self):
        return getattr(getattr(self.backend, "adapter", None), "dialect", "") == "postgresql"

    def _read(self):
        context = self.session()
        if self._shared() and context is None:
            return [], 0
        if self._managed and context is not None:
            try:
                return self.backend.account_commands.directory(context)
            except SessionRevokedError:
                return [], 0
        scope = self.backend.protected_request(self.session()) if self._shared() else nullcontext()
        try:
            with scope:
                getter = getattr(self.backend.records, "get_with_revision", None)
                if callable(getter):
                    value, revision = getter("security", "users", default=[])
                    return value, int(revision or 0)
                return self.backend.records.get("security", "users", default=[]), None
        except SessionRevokedError:
            return [], 0

    def load(self) -> List[Dict[str, Any]]:
        value, self._users_revision = self._read()
        self._users = value if isinstance(value, list) else []
        from copy import deepcopy

        self._account_baselines = {row["username"].casefold(): deepcopy(row) for row in self._users}
        self._loaded = True
        changed = False
        for user in self._users:
            before = (user.get("role"), tuple(user.get("jobs") or []))
            self._ensure_user_defaults(user)
            after = (user.get("role"), tuple(user.get("jobs") or []))
            changed = changed or before != after
        if changed and not self._managed:
            self.save()
        return self._users

    def save(self) -> None:
        if self._managed:
            raise PermissionDeniedError("Use the canonical account command to save account changes.")
        for user in self._users:
            self._ensure_user_defaults(user)
        scope = self.backend.protected_request(self.session(), write=True, mutate_accounts=True) if self._shared() else nullcontext()
        with scope:
            self._users_revision = self.backend.records.put(
                "security", "users", self._users,
                expected_revision=self._users_revision,
            )

    @property
    def users(self) -> List[Dict[str, Any]]:
        if not self._loaded:
            self.load()
        else:
            getter = getattr(self.backend.records, "get_with_revision", None)
            if callable(getter):
                _unused_value, revision = self._read()
                if revision != self._users_revision:
                    self.load()
        return self._users

    def baseline(self, username):
        from copy import deepcopy

        return deepcopy(self._account_baselines.get(username.casefold()))

    def finish_mutation(self, context, result):
        self._loaded = False
        if result.actor_revoked and self.on_mutation is not None:
            self.on_mutation(context)
        return result

    # -- queries ------------------------------------------------------------

    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        for u in self.users:
            if u["username"].casefold() == username.casefold():
                from copy import deepcopy

                return self._ensure_user_defaults(deepcopy(u) if self._managed else u)
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
        if self._managed:
            created = self.backend.account_commands.create(
                self.session(), username, password, role=role, unit_id=unit_id, display_name=display_name,
                pronouns=pronouns, email=email, phone=phone, mobile=mobile, profession=profession,
            )
            self._loaded = False
            return created
        record = build_user_record(
            username, password, role=role, display_name=display_name,
            pronouns=pronouns, email=email, phone=phone, mobile=mobile,
            unit=unit, profession=profession, unit_id=unit_id,
        )
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
        if self._managed:
            self.edit_account(username, {key: value for key, value in {
                "display_name": display_name, "pronouns": pronouns, "email": email,
                "phone": phone, "mobile": mobile, "unit_id": unit_id, "profession": profession,
            }.items() if value is not None})
            return True
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
        if self._managed:
            context = self.session()
            return self.finish_mutation(context, self.backend.account_commands.delete(context, username)).changed
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
        if self._managed:
            context = self.session()
            result = self.backend.account_commands.reset_password(context, username, new_password, must_change=must_change)
            return self.finish_mutation(context, result).changed
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
        if self._managed:
            self.edit_account(username, {"role": new_role})
            return True
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
        if self._managed:
            self.edit_account(username, {"jobs": jobs})
            return True
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
        if self._managed:
            self.edit_account(username, {"permissions": {"granted": list(granted), "revoked": list(revoked)}})
            return True
        user = self.get_user(username)
        if not user:
            return False
        user["permissions"] = {"granted": list(granted), "revoked": list(revoked)}
        self.save()
        return True

    def edit_account(self, username, patch, *, expected=None):
        if self._managed:
            context = self.session()
            result = self.backend.account_commands.edit(context, username, patch, expected=expected)
            return self.finish_mutation(context, result)
        target = self.get_user(username)
        if target is None:
            raise PermissionDeniedError("The account no longer exists.")
        target.update(patch)
        self.save()
        return None

    def reset_permissions_for_job(self, job_name: str) -> int:
        """Clear custom granted/revoked overrides for every user that has *job_name*
        assigned.  Returns the number of affected users."""
        if self._managed:
            raise PermissionDeniedError("Use the canonical job policy command.")
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
        if self._managed:
            raise PermissionDeniedError("Use the canonical job policy command.")
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
        if self._managed:
            raise PermissionDeniedError("Login bookkeeping belongs to authentication.")
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


def build_user_record(
    username: str, password: str, *, role: str, display_name: str = "",
    pronouns: str = "", email: str = "", phone: str = "", mobile: str = "",
    unit: str = "", profession: str = "", unit_id: str = "",
) -> Dict[str, Any]:
    pw_hash, salt = hash_password(password)
    return {
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
        "pronouns": pronouns,
        "email": email,
        "phone": phone,
        "mobile": mobile,
        "unit": unit,
        "unit_id": unit_id.strip().casefold(),
        "profession": profession,
    }
