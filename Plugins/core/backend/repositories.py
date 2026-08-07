"""Shared repositories for normalized and plugin-owned records."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .errors import ConflictError, ImmutableIdentityError, LockConflictError
from .json_codec import dumps, loads


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_text() -> str:
    return now_utc().isoformat()


def _placeholder(adapter: Any) -> str:
    return "?" if adapter.dialect == "sqlite" else "%s"


def _json_placeholder(adapter: Any) -> str:
    mark = _placeholder(adapter)
    return mark if adapter.dialect == "sqlite" else f"{mark}::jsonb"


def _execute(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    if hasattr(connection, "execute"):
        return connection.execute(sql, params)
    cursor = connection.cursor()
    cursor.execute(sql, params)
    return cursor


def _fetchone(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    cursor = _execute(connection, sql, params)
    return cursor.fetchone()


def _fetchall(connection: Any, sql: str, params: tuple[Any, ...] = ()) -> list[Any]:
    cursor = _execute(connection, sql, params)
    return list(cursor.fetchall())


class DomainRecordRepository:
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def get(self, namespace: str, record_id: str, default: Any = None) -> Any:
        mark = _placeholder(self.adapter)
        with self.adapter.transaction() as connection:
            row = _fetchone(
                connection,
                f"SELECT payload_json FROM domain_records "
                f"WHERE namespace={mark} AND record_id={mark}",
                (namespace, record_id),
            )
        if row is None:
            return default
        value = row["payload_json"] if isinstance(row, dict) else row[0]
        return value if isinstance(value, (dict, list)) else loads(value, default)

    def get_with_revision(
        self, namespace: str, record_id: str, default: Any = None
    ) -> tuple[Any, int]:
        """Return a record and its optimistic-lock revision in one read."""
        mark = _placeholder(self.adapter)
        with self.adapter.transaction() as connection:
            row = _fetchone(
                connection,
                f"SELECT payload_json,revision FROM domain_records "
                f"WHERE namespace={mark} AND record_id={mark}",
                (namespace, record_id),
            )
        if row is None:
            return default, 0
        value = row["payload_json"] if isinstance(row, dict) else row[0]
        revision = int(row["revision"] if isinstance(row, dict) else row[1])
        payload = value if isinstance(value, (dict, list)) else loads(value, default)
        return payload, revision

    def list(self, namespace: str) -> dict[str, Any]:
        mark = _placeholder(self.adapter)
        with self.adapter.transaction() as connection:
            rows = _fetchall(
                connection,
                f"SELECT record_id, payload_json FROM domain_records "
                f"WHERE namespace={mark} ORDER BY record_id",
                (namespace,),
            )
        result: dict[str, Any] = {}
        for row in rows:
            record_id = row["record_id"] if isinstance(row, dict) else row[0]
            payload = row["payload_json"] if isinstance(row, dict) else row[1]
            result[str(record_id)] = (
                payload if isinstance(payload, (dict, list)) else loads(payload)
            )
        return result

    def namespace_names(self) -> list[str]:
        with self.adapter.transaction() as connection:
            rows = _fetchall(
                connection,
                "SELECT DISTINCT namespace FROM domain_records ORDER BY namespace",
            )
        return [
            str(row["namespace"] if isinstance(row, dict) else row[0])
            for row in rows
        ]

    def list_all(self) -> dict[str, dict[str, Any]]:
        return {namespace: self.list(namespace) for namespace in self.namespace_names()}

    def put(
        self,
        namespace: str,
        record_id: str,
        payload: Any,
        *,
        expected_revision: int | None = None,
    ) -> int:
        mark = _placeholder(self.adapter)
        json_mark = _json_placeholder(self.adapter)
        serialized = dumps(payload)
        timestamp = now_text()
        with self.adapter.transaction(write=True) as connection:
            row = _fetchone(
                connection,
                f"SELECT revision FROM domain_records "
                f"WHERE namespace={mark} AND record_id={mark}",
                (namespace, record_id),
            )
            if row is None:
                if expected_revision not in (None, 0):
                    raise ConflictError("Record does not exist at expected revision.")
                _execute(
                    connection,
                    f"INSERT INTO domain_records("
                    "namespace,record_id,payload_json,revision,created_at,updated_at"
                    f") VALUES({mark},{mark},{json_mark},1,{mark},{mark})",
                    (namespace, record_id, serialized, timestamp, timestamp),
                )
                return 1
            revision = int(row["revision"] if isinstance(row, dict) else row[0])
            if expected_revision is not None and expected_revision != revision:
                raise ConflictError(
                    f"Stale revision {expected_revision}; current revision is {revision}."
                )
            next_revision = revision + 1
            _execute(
                connection,
                f"UPDATE domain_records SET payload_json={json_mark},revision={mark},"
                f"updated_at={mark} WHERE namespace={mark} AND record_id={mark}",
                (serialized, next_revision, timestamp, namespace, record_id),
            )
            return next_revision

    def delete(self, namespace: str, record_id: str) -> bool:
        mark = _placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            cursor = _execute(
                connection,
                f"DELETE FROM domain_records WHERE namespace={mark} "
                f"AND record_id={mark}",
                (namespace, record_id),
            )
            return int(cursor.rowcount or 0) > 0


class LeaseRepository:
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def acquire(
        self,
        entity_type: str,
        entity_id: str,
        *,
        owner_login: str,
        owner_display: str,
        ttl_seconds: int = 120,
    ) -> dict[str, str]:
        token = str(uuid.uuid4())
        acquired = now_utc()
        expires = acquired + timedelta(seconds=max(15, ttl_seconds))
        mark = _placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            row = _fetchone(
                connection,
                f"SELECT owner_login,owner_display,token,expires_at "
                f"FROM entity_leases WHERE entity_type={mark} AND entity_id={mark}",
                (entity_type, entity_id),
            )
            if row is not None:
                current = self.adapter.row_to_dict(row)
                expiry = datetime.fromisoformat(str(current["expires_at"]))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry > acquired and current["owner_login"] != owner_login:
                    raise LockConflictError(
                        f"{entity_type} {entity_id} is being edited by "
                        f"{current['owner_display'] or current['owner_login']}.",
                        owner=str(current["owner_display"] or current["owner_login"]),
                        expires_at=expiry.isoformat(),
                    )
                _execute(
                    connection,
                    f"DELETE FROM entity_leases WHERE entity_type={mark} AND entity_id={mark}",
                    (entity_type, entity_id),
                )
            _execute(
                connection,
                "INSERT INTO entity_leases("
                "entity_type,entity_id,owner_login,owner_display,token,"
                f"acquired_at,heartbeat_at,expires_at) VALUES({','.join([mark] * 8)})",
                (
                    entity_type,
                    entity_id,
                    owner_login,
                    owner_display,
                    token,
                    acquired.isoformat(),
                    acquired.isoformat(),
                    expires.isoformat(),
                ),
            )
        return {
            "token": token,
            "owner_login": owner_login,
            "owner_display": owner_display,
            "expires_at": expires.isoformat(),
        }

    def release(
        self,
        entity_type: str,
        entity_id: str,
        token: str,
        *,
        force: bool = False,
    ) -> bool:
        mark = _placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            if force:
                cursor = _execute(
                    connection,
                    f"DELETE FROM entity_leases WHERE entity_type={mark} AND entity_id={mark}",
                    (entity_type, entity_id),
                )
            else:
                cursor = _execute(
                    connection,
                    f"DELETE FROM entity_leases WHERE entity_type={mark} "
                    f"AND entity_id={mark} AND token={mark}",
                    (entity_type, entity_id, token),
                )
            return int(cursor.rowcount or 0) > 0

    def heartbeat(self, token: str, *, ttl_seconds: int = 120) -> bool:
        now = now_utc()
        expires = now + timedelta(seconds=max(15, ttl_seconds))
        mark = _placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            cursor = _execute(
                connection,
                f"UPDATE entity_leases SET heartbeat_at={mark},expires_at={mark} "
                f"WHERE token={mark}",
                (now.isoformat(), expires.isoformat(), token),
            )
            return int(cursor.rowcount or 0) > 0

    def active(self) -> list[dict[str, Any]]:
        now = now_text()
        mark = _placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            _execute(
                connection,
                f"DELETE FROM entity_leases WHERE expires_at<={mark}",
                (now,),
            )
            rows = _fetchall(
                connection,
                "SELECT entity_type,entity_id,owner_login,owner_display,"
                "token,acquired_at,heartbeat_at,expires_at "
                "FROM entity_leases ORDER BY entity_type,entity_id",
            )
        return [self.adapter.row_to_dict(row) for row in rows]

    def force_release_as_lord(
        self,
        entity_type: str,
        entity_id: str,
        *,
        actor_role: str,
        reason: str,
        audit: "AuditRepository",
        actor_login: str,
    ) -> bool:
        if str(actor_role).casefold() != "lord":
            raise PermissionError("Only Lord may force-release entity locks.")
        if not str(reason).strip():
            raise ValueError("A force-release reason is required.")
        released = self.release(entity_type, entity_id, "", force=True)
        if released:
            audit.append(
                actor_login=actor_login,
                category="lock",
                action="force_release",
                entity_type=entity_type,
                entity_id=entity_id,
                payload={"reason": str(reason).strip()},
            )
        return released


class AuditRepository:
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def append(
        self,
        *,
        actor_login: str,
        category: str,
        action: str,
        entity_type: str,
        entity_id: str,
        payload: Any,
        correlation_id: str = "",
    ) -> str:
        event_id = str(uuid.uuid4())
        mark = _placeholder(self.adapter)
        json_mark = _json_placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            _execute(
                connection,
                "INSERT INTO audit_events("
                "event_id,occurred_at,actor_login,category,action,entity_type,"
                "entity_id,correlation_id,payload_json) VALUES("
                + ",".join([mark] * 8 + [json_mark])
                + ")",
                (
                    event_id,
                    now_text(),
                    actor_login,
                    category,
                    action,
                    entity_type,
                    entity_id,
                    correlation_id or event_id,
                    dumps(payload),
                ),
            )
        return event_id


def deterministic_record_id(*parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
