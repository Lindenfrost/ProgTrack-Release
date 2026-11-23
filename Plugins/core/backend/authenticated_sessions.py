# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Database authority for one active authentication session per account."""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .errors import (
    AuthenticationError,
    BackendSchemaError,
    FirstAccountUnavailableError,
    SessionRevokedError,
    ValidationError,
)
from .json_codec import dumps, loads
from .repositories import AuditRepository, _execute, _fetchone, _json_placeholder, _placeholder, now_utc
from .schema import SCHEMA_LOCK


@dataclass(frozen=True, slots=True)
class SessionContext:
    username: str
    session_id: str
    token: str = field(repr=False)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthenticatedSessionService:
    def __init__(self, adapter: Any, *, ttl_seconds: int | None = None) -> None:
        from .session_timing import session_timeout_seconds

        self.adapter = adapter
        self.ttl_seconds = session_timeout_seconds() if ttl_seconds is None else max(60, min(ttl_seconds, 600))
        self.on_revoked = None
        self.account_commands = None

    def _revoked(self, context, *, reason=""):
        error = SessionRevokedError(reason=reason)
        notify = self.on_revoked
        if notify is not None and context is not None:
            try:
                notify(context, error)
            except RuntimeError:
                # A GUI subscriber may already have been destroyed at exit.
                pass
        return error

    def _catalogue(self, connection: Any, *, lock: str = "") -> tuple[list[dict], int]:
        sql = "SELECT payload_json, revision FROM domain_records WHERE namespace='security' AND record_id='users'"
        if self.adapter.dialect == "postgresql":
            sql += lock
        row = _fetchone(connection, sql)
        if row is None:
            return [], 0
        payload = row["payload_json"]
        if self.adapter.dialect == "sqlite":
            payload = loads(payload)
        if not isinstance(payload, list) or any(
            not isinstance(account, dict)
            or not isinstance(account.get("username"), str)
            or not account["username"]
            for account in payload
        ):
            raise ValidationError("The account catalogue is invalid.")
        names = [account["username"].casefold() for account in payload]
        if len(set(names)) != len(names):
            raise ValidationError("The account catalogue contains duplicate usernames.")
        return payload, int(row["revision"])

    def require_available_schema(self, connection: Any) -> None:
        if self.adapter.dialect == "postgresql":
            row = _fetchone(
                connection, "SELECT pg_try_advisory_xact_lock_shared(%s,%s) AS acquired", SCHEMA_LOCK
            )
            if not row["acquired"]:
                raise BackendSchemaError("The Shared database is undergoing maintenance.")

    def first_account_available(self) -> bool:
        with self.adapter.transaction() as connection:
            self.require_available_schema(connection)
            accounts, _ = self._catalogue(connection)
            return not accounts and not self._first_account_completed(connection)

    @staticmethod
    def _first_account_completed(connection: Any) -> bool:
        return _fetchone(
            connection, "SELECT key FROM installation WHERE key='initial-lord-created'"
        ) is not None

    def _now(self, connection: Any):
        if self.adapter.dialect == "postgresql":
            return _fetchone(connection, "SELECT clock_timestamp() AS current_time")["current_time"]
        return now_utc()

    def _write_accounts(self, connection: Any, accounts: list[dict], revision: int, timestamp: str) -> None:
        mark = _placeholder(self.adapter)
        data = _json_placeholder(self.adapter)
        if revision == 0:
            result = _fetchone(
                connection,
                "INSERT INTO domain_records(namespace,record_id,payload_json,revision,created_at,updated_at) "
                f"VALUES('security','users',{data},1,{mark},{mark}) "
                "ON CONFLICT(namespace,record_id) DO NOTHING RETURNING revision",
                (dumps(accounts), timestamp, timestamp),
            )
        else:
            result = _fetchone(
                connection,
                f"UPDATE domain_records SET payload_json={data}, revision=revision+1, updated_at={mark} "
                f"WHERE namespace='security' AND record_id='users' AND revision={mark} RETURNING revision",
                (dumps(accounts), timestamp, revision),
            )
        if result is None:
            raise FirstAccountUnavailableError("The account catalogue was changed.")

    def create_first_account(self, username: str, password: str, display_name: str = "") -> SessionContext:
        from Plugins.Master_Track.auth import build_user_record

        username = username.strip()
        if not username or len(password) < 6:
            raise ValidationError("A username and a password of at least six characters are required.")
        account = build_user_record(username, password, role="lord", display_name=display_name.strip())
        with self.adapter.transaction(write=True) as connection:
            self.require_available_schema(connection)
            accounts, revision = self._catalogue(connection, lock=" FOR UPDATE")
            if accounts or self._first_account_completed(connection):
                raise FirstAccountUnavailableError("The first account has already been created.")
            now = self._now(connection)
            account["last_login"] = now.date().isoformat()
            self._write_accounts(connection, [account], revision, now.isoformat())
            mark, payload = _placeholder(self.adapter), _json_placeholder(self.adapter)
            _execute(
                connection, "INSERT INTO installation(key,value_json,updated_at) "
                f"VALUES('initial-lord-created',{payload},{mark})", ("true", now.isoformat()),
            )
            context = self._issue(connection, account, now, "desktop")
        return context

    def login(self, username: str, password: str, *, client_type: str = "desktop") -> SessionContext:
        from Plugins.Master_Track.auth import verify_password

        if client_type not in {"desktop", "companion"}:
            raise ValidationError("Unknown authentication client type.")
        with self.adapter.transaction(write=True) as connection:
            self.require_available_schema(connection)
            accounts, revision = self._catalogue(connection, lock=" FOR UPDATE")
            account = self._account(accounts, username)
            try:
                valid = account is not None and verify_password(password, account["password_hash"], account["salt"])
            except (KeyError, TypeError, ValueError):
                valid = False
            if not valid:
                raise AuthenticationError("Invalid username or password.")
            now = self._now(connection)
            account["last_login"] = now.date().isoformat()
            self._write_accounts(connection, accounts, revision, now.isoformat())
            context = self._issue(connection, account, now, client_type)
        return context

    def change_password(self, context: SessionContext, previous: str, replacement: str):
        return self.account_commands.change_own_password(context, previous, replacement)

    @staticmethod
    def _account(accounts: list[dict], username: str) -> dict | None:
        for account in accounts:
            if account["username"].casefold() == username.casefold():
                if account.get("active") is False or any(account.get(flag) for flag in ("disabled", "archived", "blocked")):
                    return None
                return account
        return None

    def _issue(self, connection: Any, account: dict, now, client_type: str) -> SessionContext:
        username = account["username"]
        context = SessionContext(username, str(uuid.uuid4()), secrets.token_urlsafe(32))
        key, mark = username.casefold(), _placeholder(self.adapter)
        previous = _fetchone(connection, f"SELECT session_id FROM active_sessions WHERE username_key={mark}", (key,))
        _execute(
            connection,
            "INSERT INTO active_sessions(username_key,username,session_id,token_hash,client_type,"
            f"issued_at,heartbeat_at,expires_at) VALUES({','.join([mark] * 8)}) "
            "ON CONFLICT(username_key) DO UPDATE SET username=excluded.username, "
            "session_id=excluded.session_id,token_hash=excluded.token_hash, "
            "generation=active_sessions.generation+1,client_type=excluded.client_type, "
            "client_context=excluded.client_context,issued_at=excluded.issued_at, "
            "heartbeat_at=excluded.heartbeat_at,expires_at=excluded.expires_at,revoked_at=NULL",
            (key, username, context.session_id, _digest(context.token), client_type, now.isoformat(),
             now.isoformat(), (now + timedelta(seconds=self.ttl_seconds)).isoformat()),
        )
        if previous is not None:
            _execute(connection, f"DELETE FROM entity_leases WHERE owner_session_id={mark}", (previous["session_id"],))
        AuditRepository(self.adapter)._append_in_transaction(
            connection, actor_login=username, category="session",
            action="replace" if previous is not None else "login",
            entity_type="account", entity_id=username,
            payload={"session_id": context.session_id, "client_type": client_type},
        )
        return context

    def require(
        self, connection: Any, context: SessionContext | None, *,
        mutate_session: bool = False, mutate_accounts: bool = False, allow_password_change: bool = False,
    ) -> dict:
        """Pin the current account and session until the enclosing request commits."""
        if context is None:
            raise self._revoked(context)
        self.require_available_schema(connection)
        accounts, _ = self._catalogue(connection, lock=" FOR UPDATE" if mutate_accounts else " FOR SHARE")
        account = self._account(accounts, context.username)
        self.require_current_token(connection, context, mutate_session=mutate_session)
        if account is None:
            raise self._revoked(context, reason=self._account_revocation_reason(connection, context))
        if account.get("must_change_password") and not allow_password_change:
            raise AuthenticationError("The temporary password must be changed before continuing.")
        return account

    def require_current_token(self, connection, context, *, mutate_session=False):
        """Validate the pinned token independently of intentional self edits."""
        if context is None:
            raise self._revoked(context)
        mark = _placeholder(self.adapter)
        postgres = self.adapter.dialect == "postgresql"
        clock = "clock_timestamp()" if postgres else mark
        lock = (" FOR UPDATE" if mutate_session else " FOR SHARE") if postgres else ""
        args = (context.username.casefold(),) if postgres else (context.username.casefold(), now_utc().isoformat())
        row = _fetchone(
            connection,
            f"SELECT session_id, token_hash FROM active_sessions WHERE username_key={mark} "
            f"AND revoked_at IS NULL AND expires_at>{clock}{lock}", args,
        )
        if row is None or row["session_id"] != context.session_id or not hmac.compare_digest(
            row["token_hash"], _digest(context.token)
        ):
            raise self._revoked(context, reason=self._account_revocation_reason(connection, context))

    def _account_revocation_reason(self, connection, context):
        mark = _placeholder(self.adapter)
        old_session = ("payload_json->>'revoked_session_id'" if self.adapter.dialect == "postgresql"
                       else "json_extract(payload_json, '$.revoked_session_id')")
        row = _fetchone(
            connection, "SELECT 1 AS revoked FROM audit_events WHERE category='security' AND entity_type='account' "
            f"AND entity_id={mark} AND {old_session}={mark} LIMIT 1", (context.username, context.session_id),
        )
        return "user_account_changed" if row is not None else ""

    def renew(self, connection: Any, context: SessionContext) -> dict:
        account = self.require(connection, context, mutate_session=True)
        mark = _placeholder(self.adapter)
        now = self._now(connection)
        result = _execute(
            connection,
            f"UPDATE active_sessions SET heartbeat_at={mark},expires_at={mark} WHERE session_id={mark} AND expires_at>{mark}",
            (now.isoformat(), (now + timedelta(seconds=self.ttl_seconds)).isoformat(), context.session_id, now.isoformat()),
        )
        if result.rowcount != 1:
            raise self._revoked(context)
        return account

    def effective_permissions(self, connection: Any, account: dict) -> set[str]:
        from Plugins.Master_Track.permissions import DEFAULT_JOB_BUNDLES, resolve_effective_permissions

        row = _fetchone(
            connection,
            "SELECT payload_json FROM domain_records "
            "WHERE namespace='configuration' AND record_id='job-bundles'"
            + (" FOR SHARE" if self.adapter.dialect == "postgresql" else ""),
        )
        overrides = row["payload_json"] if row else {}
        if isinstance(overrides, str):
            overrides = loads(overrides)
        if not isinstance(overrides, dict):
            raise ValidationError("The job permissions catalogue is invalid.")
        bundles = dict(DEFAULT_JOB_BUNDLES)
        for name, permissions in overrides.items():
            if not isinstance(permissions, list) or any(not isinstance(item, str) for item in permissions):
                raise ValidationError("A job permissions bundle is invalid.")
            bundles[name] = set(permissions)
        direct = account.get("permissions") or {}
        return resolve_effective_permissions(
            account["role"], account.get("jobs") or [],
            direct.get("granted") or [], direct.get("revoked") or [],
            job_bundles=bundles,
        )

    def animal_scope(self, connection: Any, account: dict, allowed: set[str]) -> tuple[bool, set[str]]:
        from Plugins.core.project_visibility import is_unrestricted_project_role, visible_projects_for_user

        if "*" in allowed or "project.view_all" in allowed or is_unrestricted_project_role(account["role"]):
            return True, set()
        row = _fetchone(
            connection,
            "SELECT payload_json FROM domain_records WHERE namespace='projects' AND record_id='catalog'",
        )
        payload = row["payload_json"] if row else {}
        if isinstance(payload, str):
            payload = loads(payload)
        projects = payload.get("projects") if isinstance(payload, dict) else None
        if not isinstance(projects, dict):
            return False, set()
        return visible_projects_for_user(projects, account["username"], account["role"])

    def require_visible_animal(self, connection: Any, account: dict, allowed: set[str], ipid: str) -> None:
        from Plugins.core.authorization import organizational_owner_id
        from Plugins.core.project_visibility import animal_visible_by_project_scope
        from .errors import PermissionDeniedError

        scope = self.animal_scope(connection, account, allowed)
        row = _fetchone(connection, f"SELECT record_json FROM animals WHERE ipid={_placeholder(self.adapter)}", (ipid,))
        record = row["record_json"] if row else None
        if isinstance(record, str):
            record = loads(record)
        if not isinstance(record, dict) or not animal_visible_by_project_scope(record, *scope):
            raise PermissionDeniedError("This account cannot access the requested animal.")
        owner = organizational_owner_id(record)
        if owner and owner.casefold() != self.active_unit(connection, account):
            raise PermissionDeniedError("This account cannot access the animal's organizational Unit.")

    def require_visible_project(self, connection, account, allowed, project_id):
        from .errors import PermissionDeniedError

        catalogue = _fetchone(connection, """
            SELECT payload_json FROM domain_records WHERE namespace='projects' AND record_id='catalog'
        """)
        snapshot = catalogue["payload_json"] if catalogue else {}
        if isinstance(snapshot, str):
            snapshot = loads(snapshot)
        projects = snapshot.get("projects", {}) if isinstance(snapshot, dict) else {}
        unrestricted, visible = self.animal_scope(connection, account, allowed)
        if not any(isinstance(project, dict) and project.get("project_id") == project_id
                   and (unrestricted or name in visible) for name, project in projects.items()):
            raise PermissionDeniedError("This account cannot access the selected project.")

    def active_unit(self, connection: Any, account: dict) -> str:
        unit_id = str(account.get("unit_id") or "").strip().casefold()
        if not unit_id:
            return ""
        row = _fetchone(
            connection,
            "SELECT payload_json FROM domain_records "
            "WHERE namespace='security' AND record_id='organization-units'"
            + (" FOR SHARE" if self.adapter.dialect == "postgresql" else ""),
        )
        catalogue = row["payload_json"] if row else {}
        if isinstance(catalogue, str):
            catalogue = loads(catalogue)
        units = catalogue.get("units", ()) if isinstance(catalogue, dict) else ()
        if any(
            isinstance(item, dict) and item.get("id", item.get("unit_id")) == unit_id
            and item.get("active", True) and not item.get("archived", False)
            for item in units
        ):
            return unit_id
        return ""

    def logout(self, context: SessionContext) -> None:
        with self.adapter.transaction(write=True) as connection:
            self.require(connection, context, mutate_session=True, allow_password_change=True)
            self._revoke(connection, context.session_id)

    def revoke_account_session(self, connection, canonical_username):
        """Revoke the target while the account command holds the users lock."""
        mark = _placeholder(self.adapter)
        postgres = self.adapter.dialect == "postgresql"
        clock = "clock_timestamp()" if postgres else mark
        args = (canonical_username.casefold(),)
        if not postgres:
            args += (self._now(connection).isoformat(),)
        row = _fetchone(
            connection, f"SELECT session_id FROM active_sessions WHERE username_key={mark} "
            f"AND revoked_at IS NULL AND expires_at>{clock}" + (" FOR UPDATE" if postgres else ""), args,
        )
        if row is None:
            return None
        self._revoke(connection, row["session_id"])
        return row["session_id"]

    def _revoke(self, connection: Any, session_id: str) -> None:
        mark = _placeholder(self.adapter)
        _execute(connection, f"UPDATE active_sessions SET revoked_at={mark} WHERE session_id={mark}",
                 (self._now(connection).isoformat(), session_id))
        _execute(connection, f"DELETE FROM entity_leases WHERE owner_session_id={mark}", (session_id,))
