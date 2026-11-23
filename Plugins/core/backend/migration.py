# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Atomic Core schema initialization and structural validation."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from uuid import uuid4

from .errors import BackendSchemaError, BackendUnavailableError
from .repositories import _fetchall, _fetchone, _json_placeholder, _placeholder, now_text
from .schema import (
    BACKEND_CONTRACT,
    POSTGRESQL_MIGRATIONS,
    SCHEMA_LOCK,
    SCHEMA_VERSION,
    SQLITE_ADD_LEASE_OWNER_SESSION,
    SQLITE_MIGRATIONS,
)


_COLUMNS = {
    "schema_revisions": "component revision applied_at",
    "installation": "key value_json revision updated_at",
    "animals": "ipid name species birth_date origin archived role_id record_json revision created_at updated_at",
    "measurements": "measurement_id animal_ipid kind measured_at value_json sample_id source_id created_at",
    "animal_events": "event_id animal_ipid event_type occurred_at payload_json created_at",
    "domain_records": "namespace record_id payload_json revision created_at updated_at",
    "entity_leases": "entity_type entity_id owner_login owner_display token acquired_at heartbeat_at expires_at owner_session_id",
    "audit_events": "event_id occurred_at actor_login category action entity_type entity_id correlation_id payload_json",
    "managed_files": "document_id owner_type owner_id category original_name relative_path media_type byte_size sha256 state created_at created_by updated_at revision",
    "document_operations": "operation_id document_id operation_kind actor_login request_sha256 intent_json state result_json retired_path retired_sha256 retired_byte_size cleanup_pending created_at updated_at",
    "document_publish_attempts": "attempt_id operation_id category staging_path final_path verified_sha256 verified_byte_size created_at",
    "active_sessions": "username_key username session_id token_hash generation client_type client_context issued_at heartbeat_at expires_at revoked_at",
}
_INDEXES = {
    "idx_measurements_animal_kind_date",
    "idx_animal_events_animal_date",
    "idx_audit_entity",
    "idx_entity_leases_owner_session",
    "idx_document_operations_pending",
    "idx_document_publish_attempts_operation",
}
_TRIGGER = "animals_identity_immutable"
_CONTRACT_KEY = "backend-contract"
_REQUIRED_KEYS = {
    "entity_leases": ("entity_type,entity_id", ("token",)),
    "active_sessions": ("username_key", ("session_id",)),
    "managed_files": ("document_id", ("relative_path",)),
    "document_operations": ("operation_id", ()),
    "document_publish_attempts": ("attempt_id", ("staging_path", "final_path")),
}


# Explicitly registered package-owned plugin migrations. No plugin currently
# requires a separate relational schema. Registration is deployment code, never
# an ad-hoc request from a loaded plugin or a client setting.
PLUGIN_MIGRATIONS = {}
# Additive upgrades allowed by maintenance. Revisions with destructive or
# nontransactional work require an explicit backup/recovery policy first.
MAINTENANCE_ADDITIVE_REVISIONS = frozenset({3, 4})


def _ordered_plugins(registry):
    remaining = dict(registry)
    completed = {"core"}
    while remaining:
        ready = sorted(name for name, spec in remaining.items() if set(spec[0]) <= completed)
        if not ready:
            raise BackendSchemaError("Plugin migration dependencies are missing or cyclic.")
        for name in ready:
            _, revisions, verify = remaining.pop(name)
            if not revisions or sorted(revisions) != list(range(1, len(revisions) + 1)):
                raise BackendSchemaError(f"Invalid schema registration for {name}.")
            completed.add(name)
            yield name, revisions, verify


def _apply_plugin_migrations(connection, adapter):
    mark = _placeholder(adapter)
    for name, revisions, verify in _ordered_plugins(PLUGIN_MIGRATIONS):
        row = _fetchone(connection, f"SELECT revision FROM schema_revisions WHERE component={mark}", (name,))
        current = row["revision"] if row else 0
        if type(current) is not int or not 0 <= current <= max(revisions):
            raise BackendSchemaError(f"Incompatible schema revision for {name}.")
        for revision in sorted(revisions):
            if revision <= current:
                continue
            script = revisions[revision].get(adapter.dialect)
            if not script:
                raise BackendSchemaError(f"Missing {adapter.dialect} migration for {name}.")
            if adapter.dialect == "sqlite":
                for statement in _sqlite_statements(script):
                    connection.execute(statement)
            else:
                connection.execute(script)
        verify(connection, adapter.dialect)
        if current < max(revisions):
            connection.execute(
                f"""INSERT INTO schema_revisions(component,revision,applied_at)
                VALUES({mark},{mark},{mark}) ON CONFLICT(component) DO UPDATE SET
                revision=excluded.revision, applied_at=excluded.applied_at""",
                (name, max(revisions), now_text()),
            )


def _sqlite_statements(script):
    """Split the canonical SQLite SQL without an implicit transaction commit."""
    pending = ""
    for line in script.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            continue
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending
            pending = ""
    if pending.strip():
        raise BackendSchemaError("The Core SQLite migration contains incomplete SQL.")


def _columns(connection, dialect):
    columns = {}
    if dialect == "sqlite":
        rows = connection.execute(
            "SELECT m.name AS table_name, p.name AS column_name "
            "FROM sqlite_master m JOIN pragma_table_info(m.name) p WHERE m.type='table'"
        )
    else:
        rows = _fetchall(connection, """
            SELECT c.relname AS table_name, a.attname AS column_name
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid
            WHERE n.nspname=current_schema() AND c.relkind IN ('r','p')
              AND a.attnum > 0 AND NOT a.attisdropped
        """)
    for row in rows:
        columns.setdefault(row["table_name"], set()).add(row["column_name"])
    return columns


def _schema_objects(connection, dialect):
    if dialect == "sqlite":
        return {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('index','trigger')"
        )}
    return {row["name"] for row in _fetchall(connection, """
        SELECT indexname AS name FROM pg_indexes WHERE schemaname=current_schema()
        UNION ALL
        SELECT t.tgname AS name FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema() AND c.relname='animals'
          AND NOT t.tgisinternal AND t.tgenabled IN ('O','A')
    """)}


def _verify_required_keys(connection, dialect):
    for table, (primary, unique) in _REQUIRED_KEYS.items():
        if dialect == "sqlite":
            info = connection.execute(f"PRAGMA table_info({table})").fetchall()
            actual_primary = ",".join(row["name"] for row in sorted(
                (row for row in info if row["pk"]), key=lambda row: row["pk"]
            ))
            indexes = connection.execute(f"PRAGMA index_list({table})").fetchall()
            actual_unique = {
                row[0] for index in indexes if index["unique"] and not index["partial"]
                for row in connection.execute(
                    "SELECT group_concat(name, ',') FROM pragma_index_info(?)", (index["name"],)
                )
            }
            matching = actual_primary == primary and set(unique) <= actual_unique
        else:
            keys = {row["definition"].replace(" ", "") for row in _fetchall(connection, """
                SELECT pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k
                JOIN pg_class c ON c.oid=k.conrelid
                JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() AND c.relname=%s
                  AND k.contype IN ('p','u') AND NOT k.condeferrable
            """, (table,))}
            matching = {f"PRIMARYKEY({primary})", *(f"UNIQUE({column})" for column in unique)} <= keys
        if not matching:
            raise BackendSchemaError(f"Core table {table} lacks its required primary/unique keys.")


def _verify_document_journal_constraints(connection, dialect):
    name = "idx_document_operations_pending"
    if dialect == "sqlite":
        index = _fetchone(connection, "SELECT * FROM pragma_index_list('document_operations') WHERE name=?", (name,))
        columns = _fetchall(connection, "SELECT name FROM pragma_index_info(?)", (name,))
        definition = _fetchone(connection, "SELECT sql FROM sqlite_master WHERE name=?", (name,))
        predicate = re.split(r"\bwhere\b", definition["sql"], maxsplit=1, flags=re.I)[-1]
        matching = (index is not None and index["unique"] and index["partial"]
                    and [column["name"] for column in columns] == ["document_id"])
        foreign = any((entry["table"], entry["from"], entry["to"]) ==
                      ("document_operations", "operation_id", "operation_id")
                      for entry in _fetchall(connection, "PRAGMA foreign_key_list(document_publish_attempts)"))
    else:
        index = _fetchone(connection, """
            SELECT i.indisunique AND i.indisvalid AND i.indisready AS usable, i.indnkeyatts AS key_count,
                   pg_get_indexdef(i.indexrelid, 1, true) AS key,
                   pg_get_expr(i.indpred, i.indrelid) AS predicate
            FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
            JOIN pg_class idx ON idx.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relname='document_operations' AND idx.relname=%s
        """, (name,))
        matching = index is not None and index["usable"] and index["key_count"] == 1 and index["key"] == "document_id"
        predicate = index["predicate"] if matching else ""
        foreign = any(re.sub(r'\s|"', '', entry["definition"]) ==
                      "FOREIGNKEY(operation_id)REFERENCESdocument_operations(operation_id)"
                      for entry in _fetchall(connection, """
            SELECT pg_get_constraintdef(k.oid) AS definition FROM pg_constraint k
            JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relname='document_publish_attempts'
              AND k.contype='f' AND k.convalidated AND NOT k.condeferrable
        """))
    if not matching or not foreign or re.sub(r"[\s()]|::text", "", predicate or "") != "state='prepared'":
        raise BackendSchemaError("The managed-document journal lacks its operation identity constraints.")


def _validate_current(connection, adapter, revision):
    if revision != SCHEMA_VERSION:
        raise BackendSchemaError(
            f"Database core revision {revision or 'uninitialized'} is incompatible with "
            f"required revision {SCHEMA_VERSION}. Use the Backend administration workflow.",
            reason="newer" if revision > SCHEMA_VERSION else "incompatible",
        )
    actual = _columns(connection, adapter.dialect)
    missing = [f"{table}.{column}" for table, columns in _COLUMNS.items()
               for column in columns.split() if column not in actual.get(table, ())]
    absent = (_INDEXES | {_TRIGGER}) - _schema_objects(connection, adapter.dialect)
    if missing or absent:
        items = ", ".join(missing + sorted(absent))
        raise BackendSchemaError(f"Database Core schema is incomplete: {items}.")
    _verify_required_keys(connection, adapter.dialect)
    _verify_document_journal_constraints(connection, adapter.dialect)
    _validate_contract(connection, adapter)
    _validate_plugin_revisions(connection)


def _plugin_revisions_match(connection):
    rows = _fetchall(connection, "SELECT component, revision FROM schema_revisions WHERE component <> 'core'")
    actual = {row["component"]: row["revision"] for row in rows}
    expected = {name: max(revisions) for name, revisions, _ in _ordered_plugins(PLUGIN_MIGRATIONS)}
    return actual == expected


def _validate_plugin_revisions(connection):
    if not _plugin_revisions_match(connection):
        raise BackendSchemaError("Database plugin schema revisions are incompatible.")


def _validate_contract(connection, adapter):
    row = _fetchone(connection, f"SELECT value_json FROM installation WHERE key={_placeholder(adapter)}",
                    (_CONTRACT_KEY,))
    metadata = row["value_json"] if row else None
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except ValueError:
            metadata = None
    minimum = metadata.get("min_contract") if isinstance(metadata, dict) else None
    maximum = metadata.get("max_contract") if isinstance(metadata, dict) else None
    if (not isinstance(metadata, dict) or metadata.get("state") != "ready"
            or type(metadata.get("core_revision")) is not int
            or metadata["core_revision"] != SCHEMA_VERSION
            or type(minimum) is not int or type(maximum) is not int
            or not 1 <= minimum <= BACKEND_CONTRACT <= maximum):
        raise BackendSchemaError("Database backend-contract metadata is missing or incompatible.")


def _core_revision(connection, columns):
    if "schema_revisions" not in columns:
        return 0
    row = _fetchone(connection, "SELECT revision FROM schema_revisions WHERE component='core'")
    if row is None:
        return 0
    revision = row["revision"]
    if type(revision) is not int or revision < 1:
        raise BackendSchemaError("Database has an invalid Core revision marker.")
    return revision


def _record_contract(connection, adapter, from_revision, timestamp, *, correlation_id=None, backend_admin=None, application_actor=None):
    mark = _placeholder(adapter)
    metadata = {
        "state": "ready",
        "core_revision": SCHEMA_VERSION,
        "min_contract": BACKEND_CONTRACT,
        "max_contract": BACKEND_CONTRACT,
        "last_migration": {
            "id": f"core/{SCHEMA_VERSION}",
            "previous_revision": from_revision,
            "applied_by_contract": BACKEND_CONTRACT,
            "applied_at": timestamp,
            "result": "applied",
            "correlation_id": correlation_id or uuid4().hex,
            "backend_admin": backend_admin,
            "application_actor": application_actor,
        },
    }
    connection.execute(
        f"""INSERT INTO installation(key,value_json,updated_at)
            VALUES({mark},{_json_placeholder(adapter)},{mark})
            ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
            revision=installation.revision+1, updated_at=excluded.updated_at""",
        (_CONTRACT_KEY, json.dumps(metadata), timestamp),
    )


def migrate_core_schema(adapter, *, admin_actor=None, application_actor=None, correlation_id=None, check_target=None):
    """Initialize empty databases and upgrade older Standalone databases."""
    dialect = adapter.dialect
    migrations = SQLITE_MIGRATIONS if dialect == "sqlite" else POSTGRESQL_MIGRATIONS
    attempted = "inspect"
    transaction = adapter._schema_transaction if dialect == "postgresql" else adapter.transaction
    try:
        with transaction(write=True) as connection:
            if dialect == "postgresql":
                acquired = _fetchone(connection, "SELECT pg_try_advisory_xact_lock(%s,%s) AS acquired", SCHEMA_LOCK)
                if not acquired["acquired"]:
                    raise BackendSchemaError("Another operation holds the database schema lock. Try again.")
            if check_target is not None:
                check_target(connection)
            columns = _columns(connection, dialect)
            current = _core_revision(connection, columns)
            if current > SCHEMA_VERSION or (dialect == "postgresql" and current and not admin_actor):
                _validate_current(connection, adapter, current)
                return
            if dialect == "postgresql" and admin_actor and current:
                pending_core = {rev for rev in migrations if current < rev <= SCHEMA_VERSION}
                if pending_core - MAINTENANCE_ADDITIVE_REVISIONS:
                    raise BackendSchemaError("A verified backup and an approved recovery workflow are required for this upgrade.")
            if current == SCHEMA_VERSION:
                _validate_contract(connection, adapter)
                if _plugin_revisions_match(connection):
                    _validate_current(connection, adapter, current)
                    return
            if current == 0:
                unknown = columns.keys() - _COLUMNS.keys()
                if dialect == "postgresql" and unknown:
                    raise BackendSchemaError("Unrecognized tables exist in the target schema. Use a dedicated ProgTrack database.")
                # A manually prepared, empty entity_leases table is supported.
                # A missing revision marker in a populated database needs repair.
                for table in _COLUMNS.keys() & columns.keys():
                    if _fetchone(connection, f"SELECT 1 FROM {table} LIMIT 1") is not None:
                        raise BackendSchemaError(
                            f"Found existing rows in {table} without a Core revision. "
                            "Review the database before initializing it."
                        )
            mark = _placeholder(adapter)
            started_at = (_fetchone(connection, "SELECT clock_timestamp() AS started_at")["started_at"]
                          if dialect == "postgresql" else now_text())
            if admin_actor:
                logging.getLogger(__name__).info(
                    "Schema migration %s started by %s: Core %s to %s",
                    correlation_id, admin_actor, current, SCHEMA_VERSION,
                )
            for revision, script in sorted(migrations.items()):
                if revision <= current:
                    continue
                attempted = f"apply Core revision {revision}"
                if dialect == "sqlite":
                    if revision == 3 and "owner_session_id" not in columns.get("entity_leases", ()):
                        connection.execute(SQLITE_ADD_LEASE_OWNER_SESSION)
                    for statement in _sqlite_statements(script):
                        connection.execute(statement)
                else:
                    connection.execute(script)
                timestamp = started_at
                connection.execute(f"""
                    INSERT INTO schema_revisions(component,revision,applied_at)
                    VALUES('core',{mark},{mark}) ON CONFLICT(component) DO UPDATE SET
                        revision=excluded.revision,applied_at=excluded.applied_at
                """, (revision, timestamp))
            _apply_plugin_migrations(connection, adapter)
            attempted = "record and verify the backend contract"
            applied_at = (_fetchone(connection, "SELECT clock_timestamp() AS applied_at")["applied_at"]
                          if dialect == "postgresql" else now_text())
            _record_contract(connection, adapter, current,
                             applied_at.isoformat() if dialect == "postgresql" else applied_at,
                             correlation_id=correlation_id, backend_admin=admin_actor,
                             application_actor=application_actor)
            _validate_current(connection, adapter, SCHEMA_VERSION)
            if admin_actor:
                details = json.dumps({
                    "source_revision": current, "target_revision": SCHEMA_VERSION,
                    "application_actor": application_actor,
                    "started_at": started_at.isoformat(),
                })
                connection.execute(
                    """INSERT INTO audit_events(event_id,occurred_at,actor_login,category,
                        action,entity_type,entity_id,correlation_id,payload_json)
                    VALUES(%s,%s,%s,'backend','schema_migrated','database',current_database(),%s,%s::jsonb)""",
                    (uuid4().hex, applied_at, admin_actor, correlation_id, details),
                )
    except BackendSchemaError:
        raise
    except Exception as exc:
        raise BackendSchemaError(
            f"Could not {attempted} in the {dialect} database. "
            "Check database schema and migration privileges before retrying."
        ) from exc


def require_shared_transaction(connection, adapter, *, structural=False):
    """Validate while pinning the generation until this transaction ends."""
    locked = _fetchone(
        connection, "SELECT pg_try_advisory_xact_lock_shared(%s,%s) AS acquired", SCHEMA_LOCK
    )
    if not locked["acquired"]:
        raise BackendSchemaError("The Shared database is undergoing maintenance. Try again.", reason="maintenance")
    if structural:
        columns = _columns(connection, adapter.dialect)
        revision = _core_revision(connection, columns)
        if revision == SCHEMA_VERSION:
            visible = _fetchone(connection, """
                SELECT bool_and(has_table_privilege(c.oid, 'SELECT')) AS readable
                FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname=current_schema() AND c.relname = ANY(%s)
                  AND c.relkind IN ('r','p')
            """, (list(columns.keys() & _COLUMNS.keys()),))
            if not visible["readable"]:
                raise BackendSchemaError("Schema probe requires runtime SELECT privileges.", reason="unknown")
        _validate_current(connection, adapter, revision)
    else:
        try:
            row = _fetchone(connection, "SELECT revision FROM schema_revisions WHERE component='core'")
            if row is None or row["revision"] != SCHEMA_VERSION:
                raise BackendSchemaError("Database Core revision is incompatible.")
            _validate_contract(connection, adapter)
            _validate_plugin_revisions(connection)
        except BackendSchemaError:
            raise
        except Exception as exc:
            raise BackendSchemaError("Could not verify the database contract.", reason="unknown") from exc


def require_shared_schema(adapter):
    """Probe Shared schema without DDL before exposing runtime repositories."""
    try:
        with adapter._schema_transaction() as connection:
            connection.execute("SET TRANSACTION READ ONLY")
            require_shared_transaction(connection, adapter, structural=True)
    except (BackendSchemaError, BackendUnavailableError):
        raise
    except Exception as exc:
        raise BackendSchemaError(
            "Could not verify the Shared database schema. Check the database and runtime account privileges.",
            reason="unknown"
        ) from exc


def probe_shared_compatibility(connection):
    """Return true, false, or unknown without relying on information_schema ACL filtering."""
    from types import SimpleNamespace
    from psycopg.rows import dict_row

    original = connection.row_factory
    try:
        connection.row_factory = dict_row
        with connection.transaction():
            require_shared_transaction(
                connection, SimpleNamespace(dialect="postgresql"), structural=True
            )
        return True
    except BackendSchemaError as exc:
        return None if exc.reason in ("maintenance", "unknown") else False
    except Exception:
        return None
    finally:
        connection.row_factory = original
