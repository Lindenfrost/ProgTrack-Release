# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Shared repositories for normalized and plugin-owned records."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .errors import ConflictError, ImmutableIdentityError, LockConflictError, PermissionDeniedError
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


def _read_json_payload(adapter: Any, value: Any, default: Any = None) -> Any:
    """Psycopg decodes JSONB values, while SQLite returns stored JSON text."""
    return value if adapter.dialect == "postgresql" else loads(value, default)


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
    def __init__(self, adapter: Any, authorize=None, *, authorize_write=None, authorize_read=None):
        self.adapter = adapter
        self.authorize = authorize
        self.authorize_write = authorize_write
        self.authorize_read = authorize_read

    def _require_access(self, namespace: str):
        if self.authorize is not None:
            self.authorize(namespace)

    def get(self, namespace: str, record_id: str, default: Any = None) -> Any:
        self._require_access(namespace)
        if self.authorize_read is not None:
            self.authorize_read(namespace, record_id)
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
        return _read_json_payload(self.adapter, value, default)

    def get_with_revision(
        self, namespace: str, record_id: str, default: Any = None
    ) -> tuple[Any, int]:
        """Return a record and its optimistic-lock revision in one read."""
        self._require_access(namespace)
        if self.authorize_read is not None:
            self.authorize_read(namespace, record_id)
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
        return _read_json_payload(self.adapter, value, default), revision

    def revisions(self, namespace: str, record_ids: tuple[str, ...], *, lock=False) -> dict[str, int]:
        """Read revision hints without transferring record payloads."""
        self._require_access(namespace)
        if not record_ids:
            return {}
        if self.authorize_read is not None:
            for record_id in record_ids:
                self.authorize_read(namespace, record_id)
        mark = _placeholder(self.adapter)
        marks = ",".join([mark] * len(record_ids))
        lock_clause = " FOR UPDATE" if lock and self.adapter.dialect == "postgresql" else ""
        with self.adapter.transaction() as connection:
            rows = _fetchall(
                connection,
                f"SELECT record_id,revision FROM domain_records WHERE namespace={mark} "
                f"AND record_id IN ({marks}) ORDER BY record_id{lock_clause}",
                (namespace, *record_ids),
            )
        result = dict.fromkeys(record_ids, 0)
        for row in rows:
            record_id, revision = (row["record_id"], row["revision"]) if isinstance(row, dict) else row
            result[record_id] = int(revision)
        return result

    def list(self, namespace: str) -> dict[str, Any]:
        self._require_access(namespace)
        if self.authorize_read is not None:
            self.authorize_read(namespace, None)
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
            result[str(record_id)] = _read_json_payload(self.adapter, payload)
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
        self._require_access(namespace)
        with self.adapter.transaction(write=True) as connection:
            return self._put(
                connection,
                namespace, record_id, dumps(payload), expected_revision, now_text(),
            )

    def _put(self, connection, namespace, record_id, serialized, expected, timestamp) -> int:
        if self.authorize_write is not None:
            self.authorize_write(namespace, record_id)
        mark = _placeholder(self.adapter)
        payload = _json_placeholder(self.adapter)
        if expected is not None and expected != 0:
            row = _fetchone(
                connection,
                f"UPDATE domain_records SET payload_json={payload},revision=revision+1,updated_at={mark} "
                f"WHERE namespace={mark} AND record_id={mark} AND revision={mark} RETURNING revision",
                (serialized, timestamp, namespace, record_id, expected),
            )
        else:
            on_conflict = "DO NOTHING" if expected == 0 else (
                "DO UPDATE SET payload_json=excluded.payload_json,"
                "revision=domain_records.revision+1,updated_at=excluded.updated_at"
            )
            row = _fetchone(
                connection,
                "INSERT INTO domain_records(namespace,record_id,payload_json,revision,created_at,updated_at) "
                f"VALUES({mark},{mark},{payload},1,{mark},{mark}) "
                f"ON CONFLICT(namespace,record_id) {on_conflict} RETURNING revision",
                (namespace, record_id, serialized, timestamp, timestamp),
            )
        if row is None:
            raise ConflictError(f"Stale revision {expected} for {namespace}/{record_id}.")
        return int(row["revision"] if isinstance(row, dict) else row[0])

    def put_many(
        self,
        records: Iterable[tuple[str, str, Any]],
        *,
        expected_revisions: dict[tuple[str, str], int | None] | None = None,
        audit_events: Iterable[dict[str, Any]] = (),
    ) -> dict[tuple[str, str], int]:
        """Replace several domain records in one adapter transaction.

        Each optimistic-lock check is part of its SQL write. The
        adapter transaction commits the complete batch together; a
        conflict or write error rolls the batch back instead of leaving a
        partially updated coordinated configuration.
        """
        batch = list(records)
        events = list(audit_events)
        if not batch:
            return {}
        expected = expected_revisions or {}
        prepared: list[tuple[str, str, str]] = []
        seen: set[tuple[str, str]] = set()
        for namespace, record_id, payload in batch:
            self._require_access(namespace)
            key = (str(namespace), str(record_id))
            if key in seen:
                raise ValueError(f"Duplicate domain record in batch: {key!r}")
            seen.add(key)
            prepared.append((key[0], key[1], dumps(payload)))

        timestamp = now_text()
        revisions: dict[tuple[str, str], int] = {}
        with self.adapter.transaction(write=True) as connection:
            for namespace, record_id, serialized in sorted(prepared):
                key = namespace, record_id
                revisions[key] = self._put(
                    connection, namespace, record_id, serialized, expected.get(key), timestamp
                )
            for event in events:
                AuditRepository(self.adapter)._append_in_transaction(connection, **event)
        return revisions

    def put_many_audited(self, records, *, expected_revisions, audit_events):
        """Commit coordinated records and their audit evidence, or neither."""
        return self.put_many(
            records, expected_revisions=expected_revisions, audit_events=audit_events
        )

    def delete(self, namespace: str, record_id: str) -> bool:
        self._require_access(namespace)
        if self.authorize_write is not None:
            self.authorize_write(namespace, record_id)
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
    def __init__(self, adapter: Any, authenticated_sessions=None):
        self.adapter = adapter
        self.authenticated_sessions = authenticated_sessions

    def acquire(
        self,
        entity_type: str,
        entity_id: str,
        *,
        owner_login: str = "",
        owner_display: str = "",
        ttl_seconds: int | None = None,
        session=None,
    ) -> dict[str, str]:
        if self.adapter.dialect == "postgresql":
            return self._acquire_shared(entity_type, entity_id, session, ttl_seconds)
        token = str(uuid.uuid4())
        acquired = now_utc()
        expires = acquired + timedelta(seconds=max(15, ttl_seconds if ttl_seconds is not None else 120))
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

    def _acquire_shared(self, entity_type, entity_id, session, ttl_seconds):
        if self.authenticated_sessions is None:
            raise PermissionDeniedError("Shared lease acquisition requires an authenticated session.")
        with self.adapter.transaction(write=True) as connection:
            account = self.authenticated_sessions.require(connection, session)
            if entity_type == "animal":
                allowed = self.authenticated_sessions.effective_permissions(connection, account)
                self.authenticated_sessions.require_visible_animal(connection, account, allowed, entity_id)
            elif entity_type == "project":
                allowed = self.authenticated_sessions.effective_permissions(connection, account)
                self.authenticated_sessions.require_visible_project(connection, account, allowed, entity_id)
            elif entity_type == "housing" and entity_id == "cage":
                allowed = self.authenticated_sessions.effective_permissions(connection, account)
                if "*" not in allowed and not {"cage.manage_rooms_buildings", "cage.edit"} & allowed:
                    raise PermissionDeniedError("This account cannot edit housing structures.")
            elif entity_type == "housing" and entity_id == "inspections":
                allowed = self.authenticated_sessions.effective_permissions(connection, account)
                if "*" not in allowed and "cage.record_inspection" not in allowed:
                    raise PermissionDeniedError("This account cannot record housing inspections.")
            elif entity_type == "surgery" and entity_id in {"schedule", "draft-schedule", "settings", "block-days"}:
                allowed = self.authenticated_sessions.effective_permissions(connection, account)
                if "*" not in allowed and "op_scheduler.use" not in allowed:
                    raise PermissionDeniedError("This account cannot edit surgery plans.")
            else:
                raise PermissionDeniedError("No Shared edit scope is defined for this entity.")
            username = account["username"]
            display = str(account.get("display_name") or username)
            result = _fetchone(connection, """
                INSERT INTO entity_leases(entity_type,entity_id,owner_login,owner_display,
                    token,owner_session_id,acquired_at,heartbeat_at,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,clock_timestamp(),clock_timestamp(),
                    clock_timestamp() + %s * interval '1 second')
                ON CONFLICT(entity_type,entity_id) DO UPDATE
                SET owner_login=excluded.owner_login, owner_display=excluded.owner_display,
                    owner_session_id=excluded.owner_session_id,
                    token=CASE WHEN entity_leases.expires_at > clock_timestamp()
                        THEN entity_leases.token ELSE excluded.token END,
                    acquired_at=CASE WHEN entity_leases.expires_at > clock_timestamp()
                        THEN entity_leases.acquired_at ELSE clock_timestamp() END,
                    heartbeat_at=clock_timestamp(),
                    expires_at=clock_timestamp() + %s * interval '1 second'
                WHERE entity_leases.expires_at <= clock_timestamp()
                    OR entity_leases.owner_session_id=excluded.owner_session_id
                RETURNING token,owner_login,owner_display,expires_at
            """, (entity_type, entity_id, username, display, str(uuid.uuid4()), session.session_id,
                  self._ttl(ttl_seconds), self._ttl(ttl_seconds)))
            if result is not None:
                return {key: str(value) for key, value in result.items()}
            owner = _fetchone(connection, """
                SELECT owner_login,owner_display,expires_at FROM entity_leases
                WHERE entity_type=%s AND entity_id=%s
            """, (entity_type, entity_id))
            display = str(owner["owner_display"] or owner["owner_login"]) if owner else ""
            raise LockConflictError(
                f"{entity_type} {entity_id} has an active edit lease.",
                owner=display, expires_at=str(owner["expires_at"]) if owner else "",
            )

    @staticmethod
    def _ttl(seconds):
        from .session_timing import lease_timeout_seconds

        return lease_timeout_seconds() if seconds is None else max(15, min(int(seconds), 600))

    def release(
        self,
        entity_type: str,
        entity_id: str,
        token: str,
        *,
        force: bool = False,
        session=None,
    ) -> bool:
        if self.adapter.dialect == "postgresql":
            if force or self.authenticated_sessions is None:
                raise PermissionDeniedError("Shared lease release requires the owning session.")
            with self.adapter.transaction(write=True) as connection:
                self.authenticated_sessions.require(connection, session)
                cursor = _execute(connection, """
                    DELETE FROM entity_leases WHERE entity_type=%s AND entity_id=%s
                    AND token=%s AND owner_session_id=%s AND expires_at>clock_timestamp()
                """, (entity_type, entity_id, token, session.session_id))
                return cursor.rowcount == 1
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

    def heartbeat(self, token: str, *, ttl_seconds: int = 120, session=None) -> bool:
        if self.adapter.dialect == "postgresql":
            if self.authenticated_sessions is None:
                raise PermissionDeniedError("Shared lease renewal requires the owning session.")
            with self.adapter.transaction(write=True) as connection:
                self.authenticated_sessions.require(connection, session)
                return token in self.renew_tokens(connection, session, (token,), ttl_seconds)
        now = now_utc()
        expires = now + timedelta(seconds=max(15, ttl_seconds))
        mark = _placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            cursor = _execute(
                connection,
                f"UPDATE entity_leases SET heartbeat_at={mark},expires_at={mark} "
                f"WHERE token={mark} AND expires_at>{mark}",
                (now.isoformat(), expires.isoformat(), token, now.isoformat()),
            )
            return int(cursor.rowcount or 0) > 0

    def renew_tokens(self, connection, session, tokens, ttl_seconds=None):
        """Renew the authenticated client's lease set in one server request."""
        rows = _fetchall(connection, """
            UPDATE entity_leases SET heartbeat_at=clock_timestamp(),
                expires_at=clock_timestamp() + %s * interval '1 second'
            WHERE owner_session_id=%s AND token=ANY(%s) AND expires_at>clock_timestamp()
            RETURNING token
        """, (self._ttl(ttl_seconds), session.session_id, list(tokens)))
        return {row["token"] for row in rows}

    def require_shared_lease(self, connection, session, entity_type, entity_id, token):
        row = _fetchone(connection, """
            SELECT entity_id FROM entity_leases WHERE entity_type=%s AND entity_id=%s
            AND token=%s AND owner_session_id=%s AND expires_at>clock_timestamp() FOR SHARE
        """, (entity_type, entity_id, token, session.session_id))
        if row is None:
            raise LockConflictError("A current edit lease is required.")

    def shared_snapshot(self, connection, context, account):
        allowed = self.authenticated_sessions.effective_permissions(connection, account)
        unrestricted, visible_projects = self.authenticated_sessions.animal_scope(connection, account, allowed)
        unit_id = self.authenticated_sessions.active_unit(connection, account)
        return _fetchall(connection, """
            SELECT l.entity_type,l.entity_id,l.owner_login,l.owner_display,l.owner_session_id,
                l.acquired_at,l.heartbeat_at,l.expires_at
            FROM entity_leases l
            LEFT JOIN animals a ON l.entity_type='animal' AND a.ipid=l.entity_id
            WHERE l.expires_at>clock_timestamp() AND l.owner_session_id IS NOT NULL
              AND (l.owner_session_id=%s
                   OR (l.entity_type='animal' AND a.ipid IS NOT NULL
                       AND (%s OR btrim(a.record_json->>'project')=ANY(%s))
                       AND COALESCE(NULLIF(lower(btrim(a.record_json->>'organization_unit_id')), ''),
                                    NULLIF(lower(btrim(a.record_json->>'organizational_unit_id')), ''),
                                    NULLIF(lower(btrim(a.record_json->>'workgroup_id')), ''), '') IN ('', %s))
                   OR (l.entity_type='project' AND %s AND EXISTS (
                       SELECT 1 FROM domain_records p
                       CROSS JOIN LATERAL jsonb_each(p.payload_json::jsonb->'projects') j
                       WHERE p.namespace='projects' AND p.record_id='catalog'
                         AND j.value->>'project_id'=l.entity_id AND (%s OR j.key=ANY(%s))))
                   OR (l.entity_type='housing' AND l.entity_id IN ('cage','inspections') AND %s)
                   OR (l.entity_type='surgery' AND %s))
            ORDER BY l.entity_type,l.entity_id
        """, (
            context.session_id, unrestricted, sorted(visible_projects), unit_id,
            bool({"*", "project.view"} & allowed), unrestricted, sorted(visible_projects),
            bool({"*", "cage.view"} & allowed), bool({"*", "op_scheduler.view"} & allowed),
        ))

    def active(self, *, session=None) -> list[dict[str, Any]]:
        if self.adapter.dialect == "postgresql":
            if self.authenticated_sessions is None:
                raise PermissionDeniedError("Shared lease status requires an authenticated session.")
            with self.adapter.transaction() as connection:
                account = self.authenticated_sessions.require(connection, session)
                return self.shared_snapshot(connection, session, account)
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
        session=None,
    ) -> bool:
        if self.adapter.dialect == "postgresql":
            if self.authenticated_sessions is None:
                raise PermissionDeniedError("Force-release requires an authenticated Lord.")
            if not str(reason).strip():
                raise ValueError("A force-release reason is required.")
            with self.adapter.transaction(write=True) as connection:
                account = self.authenticated_sessions.require(connection, session)
                if account["role"] != "lord":
                    raise PermissionDeniedError("Force-release requires an authenticated Lord.")
                cursor = _execute(connection, """
                    DELETE FROM entity_leases WHERE entity_type=%s AND entity_id=%s
                """, (entity_type, entity_id))
                if cursor.rowcount != 1:
                    return False
                audit._append_in_transaction(
                    connection, actor_login=account["username"], category="lock",
                    action="force_release", entity_type=entity_type, entity_id=entity_id,
                    payload={"reason": str(reason).strip()},
                )
                return True
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
        with self.adapter.transaction(write=True) as connection:
            return self._append_in_transaction(
                connection, actor_login=actor_login, category=category, action=action,
                entity_type=entity_type, entity_id=entity_id, payload=payload,
                correlation_id=correlation_id,
            )

    def _append_in_transaction(
        self, connection: Any, *, actor_login: str, category: str, action: str,
        entity_type: str, entity_id: str, payload: Any, correlation_id: str = "",
    ) -> str:
        """Use the caller's transaction; never commit an audit separately."""
        event_id = str(uuid.uuid4())
        mark = _placeholder(self.adapter)
        json_mark = _json_placeholder(self.adapter)
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

    def list_events(self) -> list[dict[str, Any]]:
        """Return the canonical backend audit events in reverse time order.

        Master Track still has a legacy text-log reader for older installations,
        but backend-owned events must be read from ``audit_events`` directly so
        database administration, branding, and lock operations are visible in
        the same audit viewer.  The adapter abstraction keeps this portable for
        SQLite and PostgreSQL (including PostgreSQL JSONB rows).
        """
        with self.adapter.transaction() as connection:
            rows = _fetchall(
                connection,
                "SELECT event_id,occurred_at,actor_login,category,action,"
                "entity_type,entity_id,correlation_id,payload_json "
                "FROM audit_events "
                "ORDER BY occurred_at DESC,event_id DESC",
            )

        events: list[dict[str, Any]] = []
        fields = (
            "event_id",
            "occurred_at",
            "actor_login",
            "category",
            "action",
            "entity_type",
            "entity_id",
            "correlation_id",
            "payload_json",
        )
        for row in rows:
            if isinstance(row, dict):
                values = {field: row.get(field) for field in fields}
            else:
                values = {
                    field: row[index] if index < len(row) else None
                    for index, field in enumerate(fields)
                }

            try:
                payload = _read_json_payload(self.adapter, values.get("payload_json"), {})
            except (TypeError, ValueError):
                payload = {}

            occurred_at = values.get("occurred_at")
            if isinstance(occurred_at, datetime):
                occurred_text = occurred_at.isoformat()
            else:
                occurred_text = str(occurred_at or "")

            events.append(
                {
                    "event_id": str(values.get("event_id") or ""),
                    "occurred_at": occurred_text,
                    "actor_login": str(values.get("actor_login") or ""),
                    "category": str(values.get("category") or ""),
                    "action": str(values.get("action") or ""),
                    "entity_type": str(values.get("entity_type") or ""),
                    "entity_id": str(values.get("entity_id") or ""),
                    "correlation_id": str(values.get("correlation_id") or ""),
                    "payload": payload if isinstance(payload, (dict, list)) else {},
                }
            )
        return events


def deterministic_record_id(*parts: Any) -> str:
    material = "\x1f".join(str(part) for part in parts)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
