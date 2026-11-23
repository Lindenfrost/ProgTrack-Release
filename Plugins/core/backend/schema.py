# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Versioned shared schema.

The initial schema keeps domain records inspectable and adapter-neutral while
the application UI is cut over incrementally. Animal identity and measurement
records are normalized; plugin-owned records use stable typed namespaces.
"""

SCHEMA_VERSION = 4
BACKEND_CONTRACT = 1
# Stable per-database namespace for the migration owner and compatibility gate.
SCHEMA_LOCK = (942637, 1)
SQLITE_ADD_LEASE_OWNER_SESSION = "ALTER TABLE entity_leases ADD COLUMN owner_session_id TEXT"

DOCUMENT_OPERATION_MIGRATION = """
    ALTER TABLE managed_files ADD COLUMN revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0);
    CREATE TABLE document_operations (
        operation_id TEXT PRIMARY KEY,
        document_id TEXT NOT NULL,
        operation_kind TEXT NOT NULL CHECK (operation_kind IN ('create','replace','retire')),
        actor_login TEXT NOT NULL,
        request_sha256 TEXT NOT NULL,
        intent_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK (state IN ('prepared','committed','abandoned')),
        result_json TEXT NOT NULL DEFAULT '',
        retired_path TEXT,
        retired_sha256 TEXT,
        retired_byte_size BIGINT,
        cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK (cleanup_pending IN (0,1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE UNIQUE INDEX idx_document_operations_pending
        ON document_operations(document_id) WHERE state = 'prepared';
    CREATE TABLE document_publish_attempts (
        attempt_id TEXT PRIMARY KEY,
        operation_id TEXT NOT NULL REFERENCES document_operations(operation_id),
        category TEXT NOT NULL,
        staging_path TEXT NOT NULL UNIQUE,
        final_path TEXT NOT NULL UNIQUE,
        verified_sha256 TEXT,
        verified_byte_size BIGINT,
        created_at TEXT NOT NULL,
        CHECK ((verified_sha256 IS NULL AND verified_byte_size IS NULL)
            OR (verified_sha256 IS NOT NULL AND verified_byte_size IS NOT NULL AND verified_byte_size >= 0))
    );
    CREATE INDEX idx_document_publish_attempts_operation ON document_publish_attempts(operation_id);
"""

SQLITE_MIGRATIONS = {
    1: """
    CREATE TABLE IF NOT EXISTS schema_revisions (
        component TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        applied_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS installation (
        key TEXT PRIMARY KEY,
        value_json TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS animals (
        ipid TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        species TEXT NOT NULL,
        birth_date TEXT NOT NULL,
        origin TEXT NOT NULL,
        archived INTEGER NOT NULL DEFAULT 0,
        role_id TEXT NOT NULL,
        record_json TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (length(trim(ipid)) > 0),
        CHECK (length(trim(name)) > 0),
        CHECK (length(trim(species)) > 0),
        CHECK (length(trim(birth_date)) = 10),
        CHECK (length(trim(origin)) > 0)
    );
    CREATE TRIGGER IF NOT EXISTS animals_identity_immutable
    BEFORE UPDATE OF ipid, name, species, birth_date, origin ON animals
    WHEN OLD.ipid <> NEW.ipid
      OR OLD.name <> NEW.name
      OR OLD.species <> NEW.species
      OR OLD.birth_date <> NEW.birth_date
      OR OLD.origin <> NEW.origin
    BEGIN
      SELECT RAISE(ABORT, 'animal identity is immutable');
    END;
    CREATE TABLE IF NOT EXISTS measurements (
        measurement_id TEXT PRIMARY KEY,
        animal_ipid TEXT NOT NULL REFERENCES animals(ipid) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        measured_at TEXT NOT NULL,
        value_json TEXT NOT NULL,
        sample_id TEXT NOT NULL DEFAULT '',
        source_id TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(animal_ipid, kind, measured_at, value_json, sample_id)
    );
    CREATE INDEX IF NOT EXISTS idx_measurements_animal_kind_date
        ON measurements(animal_ipid, kind, measured_at);
    CREATE TABLE IF NOT EXISTS animal_events (
        event_id TEXT PRIMARY KEY,
        animal_ipid TEXT NOT NULL REFERENCES animals(ipid) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_animal_events_animal_date
        ON animal_events(animal_ipid, occurred_at);
    CREATE TABLE IF NOT EXISTS domain_records (
        namespace TEXT NOT NULL,
        record_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY(namespace, record_id)
    );
    CREATE TABLE IF NOT EXISTS entity_leases (
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        owner_login TEXT NOT NULL,
        owner_display TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE,
        acquired_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        PRIMARY KEY(entity_type, entity_id)
    );
    CREATE TABLE IF NOT EXISTS audit_events (
        event_id TEXT PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        actor_login TEXT NOT NULL,
        category TEXT NOT NULL,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        payload_json TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_audit_entity
        ON audit_events(entity_type, entity_id, occurred_at);
    CREATE TABLE IF NOT EXISTS managed_files (
        document_id TEXT PRIMARY KEY,
        owner_type TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        category TEXT NOT NULL,
        original_name TEXT NOT NULL,
        relative_path TEXT NOT NULL UNIQUE,
        media_type TEXT NOT NULL,
        byte_size INTEGER NOT NULL,
        sha256 TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK (state IN ('staged','pending','active','quarantined','deleted'))
    );
    """,
    2: """
    -- Animal-event payloads were historically stored with the runtime alias
    -- ``typ``.  The normalized event_type column is already authoritative;
    -- make the JSON payload use the same canonical key as well.
    UPDATE animal_events
    SET payload_json = json_remove(
        json_set(payload_json, '$.event_type', json_extract(payload_json, '$.typ')),
        '$.typ'
    )
    WHERE json_type(payload_json, '$.typ') IS NOT NULL
      AND json_type(payload_json, '$.event_type') IS NULL;
    UPDATE animal_events
    SET payload_json = json_remove(payload_json, '$.typ')
    WHERE json_type(payload_json, '$.typ') IS NOT NULL
      AND json_type(payload_json, '$.event_type') IS NOT NULL;
    """,
    3: """
    CREATE TABLE IF NOT EXISTS active_sessions (
        username_key TEXT PRIMARY KEY NOT NULL,
        username TEXT NOT NULL,
        session_id TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL,
        generation INTEGER NOT NULL DEFAULT 1 CHECK (generation > 0),
        client_type TEXT NOT NULL,
        client_context TEXT NOT NULL DEFAULT '{}',
        issued_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        revoked_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_entity_leases_owner_session
        ON entity_leases(owner_session_id);
    """,
    4: DOCUMENT_OPERATION_MIGRATION,
}


POSTGRESQL_MIGRATIONS = {
    1: """
    CREATE TABLE IF NOT EXISTS schema_revisions (
        component TEXT PRIMARY KEY,
        revision INTEGER NOT NULL,
        applied_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE IF NOT EXISTS installation (
        key TEXT PRIMARY KEY,
        value_json JSONB NOT NULL,
        revision BIGINT NOT NULL DEFAULT 1,
        updated_at TIMESTAMPTZ NOT NULL
    );
    CREATE TABLE IF NOT EXISTS animals (
        ipid TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        species TEXT NOT NULL,
        birth_date TEXT NOT NULL,
        origin TEXT NOT NULL,
        archived BOOLEAN NOT NULL DEFAULT FALSE,
        role_id TEXT NOT NULL,
        record_json JSONB NOT NULL,
        revision BIGINT NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CHECK (char_length(trim(birth_date)) = 10)
    );
    CREATE OR REPLACE FUNCTION reject_animal_identity_update()
    RETURNS trigger AS $$
    BEGIN
      IF OLD.ipid IS DISTINCT FROM NEW.ipid
         OR OLD.name IS DISTINCT FROM NEW.name
         OR OLD.species IS DISTINCT FROM NEW.species
         OR OLD.birth_date IS DISTINCT FROM NEW.birth_date
         OR OLD.origin IS DISTINCT FROM NEW.origin THEN
        RAISE EXCEPTION 'animal identity is immutable';
      END IF;
      RETURN NEW;
    END;
    $$ LANGUAGE plpgsql;
    DROP TRIGGER IF EXISTS animals_identity_immutable ON animals;
    CREATE TRIGGER animals_identity_immutable
      BEFORE UPDATE ON animals FOR EACH ROW
      EXECUTE FUNCTION reject_animal_identity_update();
    CREATE TABLE IF NOT EXISTS measurements (
        measurement_id TEXT PRIMARY KEY,
        animal_ipid TEXT NOT NULL REFERENCES animals(ipid) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        measured_at TEXT NOT NULL,
        value_json JSONB NOT NULL,
        sample_id TEXT NOT NULL DEFAULT '',
        source_id TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMPTZ NOT NULL,
        UNIQUE(animal_ipid, kind, measured_at, value_json, sample_id)
    );
    CREATE INDEX IF NOT EXISTS idx_measurements_animal_kind_date
        ON measurements(animal_ipid, kind, measured_at);
    CREATE TABLE IF NOT EXISTS animal_events (
        event_id TEXT PRIMARY KEY,
        animal_ipid TEXT NOT NULL REFERENCES animals(ipid) ON DELETE CASCADE,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        payload_json JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_animal_events_animal_date
        ON animal_events(animal_ipid, occurred_at);
    CREATE TABLE IF NOT EXISTS domain_records (
        namespace TEXT NOT NULL,
        record_id TEXT NOT NULL,
        payload_json JSONB NOT NULL,
        revision BIGINT NOT NULL DEFAULT 1,
        created_at TIMESTAMPTZ NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY(namespace, record_id)
    );
    CREATE TABLE IF NOT EXISTS entity_leases (
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        owner_login TEXT NOT NULL,
        owner_display TEXT NOT NULL,
        token TEXT NOT NULL UNIQUE,
        acquired_at TIMESTAMPTZ NOT NULL,
        heartbeat_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        PRIMARY KEY(entity_type, entity_id)
    );
    CREATE TABLE IF NOT EXISTS audit_events (
        event_id TEXT PRIMARY KEY,
        occurred_at TIMESTAMPTZ NOT NULL,
        actor_login TEXT NOT NULL,
        category TEXT NOT NULL,
        action TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        payload_json JSONB NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_audit_entity
        ON audit_events(entity_type, entity_id, occurred_at);
    CREATE TABLE IF NOT EXISTS managed_files (
        document_id TEXT PRIMARY KEY,
        owner_type TEXT NOT NULL,
        owner_id TEXT NOT NULL,
        category TEXT NOT NULL,
        original_name TEXT NOT NULL,
        relative_path TEXT NOT NULL UNIQUE,
        media_type TEXT NOT NULL,
        byte_size BIGINT NOT NULL,
        sha256 TEXT NOT NULL,
        state TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        created_by TEXT NOT NULL,
        updated_at TIMESTAMPTZ NOT NULL,
        CHECK (state IN ('staged','pending','active','quarantined','deleted'))
    );
    """,
    2: """
    -- Animal-event payloads were historically stored with the runtime alias
    -- ``typ``.  The normalized event_type column is already authoritative;
    -- make the JSON payload use the same canonical key as well.
    UPDATE animal_events
    SET payload_json = (payload_json - 'typ')
                       || jsonb_build_object('event_type', payload_json->'typ')
    WHERE payload_json ? 'typ'
      AND NOT (payload_json ? 'event_type');
    UPDATE animal_events
    SET payload_json = payload_json - 'typ'
    WHERE payload_json ? 'typ'
      AND payload_json ? 'event_type';
    """,
    3: """
    CREATE TABLE IF NOT EXISTS active_sessions (
        username_key TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        session_id TEXT NOT NULL UNIQUE,
        token_hash TEXT NOT NULL,
        generation BIGINT NOT NULL DEFAULT 1 CHECK (generation > 0),
        client_type TEXT NOT NULL,
        client_context JSONB NOT NULL DEFAULT '{}'::jsonb,
        issued_at TIMESTAMPTZ NOT NULL,
        heartbeat_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ
    );
    ALTER TABLE entity_leases ADD COLUMN IF NOT EXISTS owner_session_id TEXT;
    CREATE INDEX IF NOT EXISTS idx_entity_leases_owner_session
        ON entity_leases(owner_session_id);
    """,
    4: DOCUMENT_OPERATION_MIGRATION,
}
