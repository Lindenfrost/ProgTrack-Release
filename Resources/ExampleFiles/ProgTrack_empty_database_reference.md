<!-- Part of: ProgTrack 0.3.0. Required ProgTrack version: 0.3.0 or newer. -->
# ProgTrack empty database schema reference

Core schema version `4`. Backend contract generation `1`.

This file is the schema-only reference for an empty ProgTrack database. It is
the mapping baseline for future migrations and adapter implementations. It
describes the current relational schema, backend type mapping, constraints,
triggers, indexes, and generic record keys. It contains no test procedure,
deployment instruction, seed data, UI behavior, or acceptance checklist.

An empty database has this schema and no domain data. Initialization records
the Core revision and the backend contract in installation metadata.

## SQLite schema

```sql
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
    owner_session_id TEXT,
    PRIMARY KEY(entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_leases_owner_session
    ON entity_leases(owner_session_id);
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
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    CHECK (state IN ('staged','pending','active','quarantined','deleted'))
);
```

## PostgreSQL schema

```sql
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
    occurred_at TIMESTAMPTZ NOT NULL,
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
    owner_session_id TEXT,
    PRIMARY KEY(entity_type, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_entity_leases_owner_session
    ON entity_leases(owner_session_id);
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
    revision BIGINT NOT NULL DEFAULT 1 CHECK (revision > 0),
    CHECK (state IN ('staged','pending','active','quarantined','deleted'))
);
```

## Managed-document journal (both backends)

```sql
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
```

## Backend type mapping

| Logical value | SQLite | PostgreSQL |
|---|---|---|
| JSON payload | `TEXT` containing JSON | `JSONB` |
| Boolean | `INTEGER` (`0`/`1`) | `BOOLEAN` |
| Revision | `INTEGER` | `BIGINT` |
| Timestamp | `TEXT` | `TIMESTAMPTZ` |
| File size | `INTEGER` | `BIGINT` |

## Relational object map

| Table | Primary key | References / uniqueness | Logical role |
|---|---|---|---|
| `schema_revisions` | `component` | one row per registered schema component | migration revision state |
| `installation` | `key` | one row per installation setting | backend/deployment settings |
| `animals` | `ipid` | immutable identity fields; `archived` is lifecycle state | canonical animal records |
| `measurements` | `measurement_id` | `animal_ipid → animals.ipid`; unique animal/kind/time/value/sample tuple | normalized measurements |
| `animal_events` | `event_id` | `animal_ipid → animals.ipid` | normalized animal events |
| `domain_records` | `(namespace, record_id)` | one JSON payload per typed namespace/key | plugin and service records |
| `entity_leases` | `(entity_type, entity_id)` | `token` unique, indexed `owner_session_id` | transient edit/open leases |
| `active_sessions` | `username_key` | `session_id` unique, positive generation | current server-authoritative session slot per account |
| `audit_events` | `event_id` | index on entity and occurrence time | immutable audit records |
| `managed_files` | `document_id` | `relative_path` unique; state check and positive revision | metadata for external managed payloads |
| `document_operations` | `operation_id` | one prepared operation per document ID | request identity, committed result, and retired path |
| `document_publish_attempts` | `attempt_id` | `operation_id → document_operations.operation_id`; unique staging and final paths | operation-owned candidates and verified content |

## Generic domain-record key map

`domain_records.payload_json` is the adapter-neutral payload container. The
following namespace/key pairs are the current logical mapping surface; dynamic
keys are shown in angle brackets.

| Namespace | Record ID | Current logical record |
|---|---|---|
| `security` | `users` | Master Track user catalogue and credential metadata |
| `security` | `organization-units` | organization/unit membership and scope |
| `sessions` | `<username>` | compatibility session record keyed by username |
| `configuration` | `animal-roles` | configurable animal roles and schema version |
| `configuration` | `role-block-presets` | role block presets and schema version |
| `configuration` | `institution-branding` | institution branding configuration |
| `configuration` | `master-track` | Master Track settings |
| `configuration` | `job-bundles` | job-to-permission bundle overrides |
| `configuration` | `flow-track` | Flow Track settings |
| `configuration` | `network-track` | Network Track settings |
| `housing` | `cage` | Cage Track housing/cage records |
| `housing` | `inspections` | Cage Track inspection records |
| `preferences` | `cage-track:inspection-sort:<identity>` | user-specific inspection sorting |
| `reproduction` | `flow` | Flow Track reproduction data |
| `medical` | `history` | Medi Track medical history |
| `measurements` | `pdg-models` | PdG conversion models |
| `samples` | `organs` | Sample Track organ samples |
| `samples` | `other` | Sample Track other samples |
| `embryo-track` | `cranimetry-reference` | Embryo Track cranimetry reference |
| `animal-reports` | `report-data` | Animal Reports data |
| `animal-reports` | `locked-entries` | Animal Reports locked entries |
| `heritage` | `graph` | Heritage Track graph and settings |
| `projects` | `catalog` | Projects Track project catalogue |
| `projects` | `history` | Projects Track project history |
| `project-cache` | `<identity>` | user/identity-scoped project cache |
| `surgery-planner` | `schedule` | committed Surgery Planner schedule |
| `surgery-planner` | `draft-schedule` | Surgery Planner draft schedule |
| `surgery-planner` | `block-days` | Surgery Planner blocked-day records |
| `surgery-planner` | `settings` | Surgery Planner settings |
| `network-track` | `chat` | Network Track chat payload |

## Migration invariants

The canonical migration source is
`Plugins/core/backend/schema.py`. Any later migration or adapter must preserve
the logical objects, keys, constraints, identity immutability, foreign-key
relationships, and backend type mapping defined here. A changed schema version
must update this reference as part of the same change.

`schema_revisions.core` and `installation['backend-contract']` are written in
the same transaction as Core schema changes. The contract payload contains
`state`, `core_revision`, `min_contract`, `max_contract`, and `last_migration`,
which identifies the previous revision, applied migration, applying contract,
result, UTC timestamp and correlation ID. Contract generation is independent of
the application release version. PostgreSQL bootstrap coordinates on
transaction advisory lock `(942637, 1)`. Initialization leaves session and
lease tables empty for a new database. Revision 3 adds nullable session
ownership to earlier leases and retains existing domain rows. A Shared runtime
probe obtains the shared schema lock before checking the revision and required
objects. Opening an older Shared database requires controlled administration.

Revision 4 initializes existing managed rows at document revision 1, preserving
their state and relative path. The managed-document journal uses text for
intent and result JSON and UTC timestamps on both backends. Journal paths are
relative to the configured client-local managed-storage category.

`username_key` represents the normalized immutable account username. The
stored canonical `username` retains its original spelling. Session credentials
are represented by a token hash. `client_context` contains nonsecret metadata.
Shared lease expiry and authenticated requests use server time under the
session/lease service contract. Owner-session enforcement and revocation
belong to that service, including removal of old lease rows when it activates.
