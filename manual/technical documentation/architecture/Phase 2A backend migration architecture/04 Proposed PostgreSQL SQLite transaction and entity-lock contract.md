# PostgreSQL, SQLite, transaction, and entity-lock contract

## One application, two supported deployment profiles

ProgTrack uses one domain/service API and two durable adapters:

| Profile | Intended use | Supported topology | Not supported |
| --- | --- | --- | --- |
| **Standalone SQLite** | very small facility, portable installation, one local workstation | one active ProgTrack process; database/configuration/managed files on local storage; local backup/export | network share, NAS, cloud-synchronized database, concurrent clients, remote server claims |
| **Shared PostgreSQL** | facility with concurrent users/workstations and central operations | PostgreSQL server, multiple clients, server-authoritative revisions/leases, central backup and managed-payload configuration | silent fallback to JSON or SQLite on connection loss |

Standalone is a supported production profile, not merely a demo adapter.
Shared PostgreSQL is recommended whenever two people may enter data
concurrently, more than one workstation is used, central credential/database
administration is required, or server-side backup/availability is expected.
Standalone still enforces ProgTrack users/permissions inside the application,
but anyone with direct write access to its local database/payload files can
bypass that application boundary. The facility must therefore restrict OS
file access and protect local backups. Choose Shared PostgreSQL when centralized
database access control or separation from workstation administrators is a
requirement.

Both profiles provide the same domain features, schema semantics, validation,
permissions, audit event types, interchange package, and document state
machine. No plugin may branch into a reduced “SQLite edition.” Differences are
limited to deployment and concurrency capabilities. A canonical package is the
supported route from Standalone SQLite to Shared PostgreSQL and back into an
empty compatible installation when allowed by package policy.

The startup profile is explicit and visible. ProgTrack validates local SQLite
path safety and exclusive-process ownership before opening the database.
SQLite paths resolving to mapped/UNC/network drives or synchronized roots are
rejected.

## Adapter boundary

UI and plugin code depend on typed service/repository protocols. The selected
adapter is injected at startup.

Forbidden:

- UI/plugin imports of SQLite or PostgreSQL drivers;
- plugin SQL or database-path construction;
- different domain rules in the two adapters;
- repository decisions about UI labels/visibility;
- folder scanning as discovery of authoritative export records;
- automatic backend fallback after an availability failure.

Adapter parity tests are normative. PostgreSQL-specific concurrency tests and
SQLite exclusive-process/path tests supplement the shared contract.

## Unit of work

A successful mutation:

1. authenticates session/actor;
2. checks semantic permission and project/entity scope;
3. verifies the required editor lock or short operation ownership;
4. loads expected revisions;
5. validates domain invariants and typed foreign keys;
6. writes all related domain records;
7. writes the minimum immutable domain audit event in the same transaction;
8. commits once;
9. publishes refresh/success only after commit.

Any error before commit rolls back domain records and their transaction-aligned
audit event. A denied command performs no domain write, but a
security-sensitive denial may write an independent immutable security event.
Failure of that security-event path never turns denial into permission.

### Isolation and revisions

- Every mutable aggregate has a monotonically increasing `revision`.
- Update/delete includes the expected revision; zero rows affected is conflict.
- PostgreSQL normally uses `READ COMMITTED` plus revision checks. Allocation or
  high-contention invariants may use ordered row locks or `SERIALIZABLE` with a
  bounded documented retry.
- SQLite uses explicit transactions and the same revision predicates. One
  active application process prevents false multi-client guarantees.
- Locks coordinate people; revisions prevent stale overwrite. Neither replaces
  the other.
- Multi-entity lock acquisition uses canonical `(entity_type, typed_id)` order.

## Visible entity-edit locks

### Shared record

`entity_lock` contains controlled entity type, typed entity ID (animal IPID or
facility-owned record ID), mode, owner user/session/client, acquisition and
heartbeat times, expiry, token, and non-sensitive context. Exclusive edit has
a unique `(entity_type, entity_id, mode)` key.

### Shared PostgreSQL behavior

- Acquire/renew/replace-expired is atomic and uses database/server time.
- Another live owner returns a filtered owner display and expiry/context.
- Active editors heartbeat; save, cancel, close, logout, revoke, or clean exit
  releases immediately.
- Crash or lost client expires by lease.
- Force release is a separate permissioned, audited administrator action.
- Recommended test values are a 120-second lease and 30-second heartbeat;
  deployed values are configuration.

A table is preferred over advisory locks because lock state must be queryable.
Short PostgreSQL advisory locks may serialize acquisition internally but are
not user-visible truth.

### Standalone SQLite behavior

Standalone obtains an installation/process lock before database access. A
second ProgTrack process is rejected or offered read-only diagnostic access;
it never becomes a concurrent writer.

The same LockService API still records editor ownership for consistent UI,
reentrant forms, crash recovery, testing, and a future package move to
PostgreSQL. Lease expiry uses a trusted local monotonic/wall-clock strategy
documented by the adapter. It is not advertised as cross-workstation
coordination.

### UI behavior in both profiles

- Entities remain viewable while another editor owns a lock.
- Mutation is disabled and the owner is shown where policy allows.
- Loss of lock while dirty blocks save but preserves text for copy/reload/
  reacquire; nothing is silently discarded.
- Initial coverage includes animals, projects, housing nodes/inspections,
  medical records/issues, samples, surgery plans, reference/configuration
  records, user accounts/jobs, and other reviewed mutable aggregates.
- Append-only quick entries may use transaction/revision coordination rather
  than a long editor lease.

## Permission boundary

- Every command declares a global semantic permission ID plus derived scope.
- UI availability and launch-handler checks are defensive presentation;
  service authorization is mandatory.
- Project visibility is enforced by service queries and commands, never cache.
- Actor/session comes from authenticated context, never editable signatures.
- Lord/Master defaults and overrides are centralized in SecurityService.
- Disabled-plugin state removes UI/callback capability, grants no permission,
  and changes no durable data.
- Grantable permissions appear in the editable catalogue and every supported
  language. Internal-only permissions remain non-grantable.

## Audit and operation events

Three event contracts remain separate:

1. **Domain audit event:** successful mutation, atomic with its domain
   transaction; actor/session, action, target, before/after revision,
   correlation, time, result.
2. **Security event:** denied or security-sensitive outcome without domain
   mutation; independently durable where possible.
3. **Operation event:** correlated import/export lifecycle such as preview,
   confirmation, commit, completion, failure, or cancellation with counts.

Audit insertion failure rolls back an otherwise successful mutation.
Import/export events use one correlation ID. Routine read logging and full
human-readable audit UI/retention remain Phase 3.

## Animal identity protection

IPID, name, full birth date, species, and stored `origin` are immutable after
creation for every role, including Lord. Database constraints reject updates.
There is no IPID regeneration, reference rewrite, alias, merge, supersede, or
automatic linked-data transfer in Phase 2B.

If finalized identity is wrong, an authorized user deletes the bad animal via
the normal audited workflow and creates a correct animal. Dependent data and
documents are re-entered/re-uploaded manually.

Canonical import preserves complete four-block IPID. Equal IPID is an
idempotent same-animal reference only when identity/content rules agree;
otherwise it is quarantined. Unknown origin blocks creation until an authorized
Lord/Master/Manager approves the catalogue value.

## Existing-animal-only measurement import

Blood progesterone, urine PdG, weight, and sperm follow one command:

1. authorize `core.import` plus the relevant measurement permission;
2. parse and classify the complete file without mutation;
3. create one immutable plan with distinct `existing`, `unknown`, `archived`,
   `ambiguous`, `invalid`, `duplicate`, and `permission_rejected` decisions;
4. display a possibly capped table, but complete-file counts and consolidated
   unknown identifiers;
5. warn that unknown animals are skipped and must be registered by a Manager;
6. on confirmation, transactionally revalidate animal state, visibility,
   permissions, revisions, and duplicates;
7. insert only rows resolved to existing active animals;
8. write correlated operation outcome and transaction-aligned measurement
   events;
9. return imported/skipped/invalid/duplicate/newly-invalid counts.

Manager, Master, and Lord have no animal-creation path inside this command.
Unknown rows create no animal, placeholder, folder, sample owner, measurement,
or per-animal audit event. Animal ID, IPID, public animal ID, Sample ID, and
sample number remain separate.

Prefer one transaction per practical file. If bounded batches are required,
the immutable result records committed batch/row decisions exactly and retry
uses deterministic duplicate keys. Re-import after Manager-led animal creation
must add formerly skipped rows without duplicating rows already committed.

## Managed payload transaction bridge

Database and filesystem are not one atomic transaction. DocumentService uses:

`staged → pending → active`, with `quarantined` and `deleted` terminal/control
states.

1. copy into managed staging;
2. stream/validate MIME policy, size, SHA-256;
3. commit `pending` metadata and typed ownership links;
4. atomically/idempotently place the final payload;
5. re-read and verify final bytes;
6. transition metadata to `active` in a second transaction;
7. publish success only after active.

Recovery:

- pre-pending failure removes abandoned staging;
- pending without final retries activation or quarantines;
- final with pending verifies/activates idempotently or quarantines;
- active with missing/mismatched bytes is removed from active visibility and
  quarantined/reconciled;
- startup/scheduled reconciliation detects staging debris, pending rows,
  unregistered final payloads, and checksum mismatches.

Tests inject failure/crash at every transition in both profiles.

## Cross-service examples

- Project reassignment closes old assignment, writes new assignment/history,
  applies severity/experiment rules, and audits in one transaction.
- Cage move closes occupancy, creates new occupancy, validates hierarchy/
  capacity, records movement, and audits in one transaction.
- Canonical package validation/preview is read-only. Commit uses declared
  dependency order and one correlated import operation.
- Institution-branding change commits configuration metadata and activates the
  managed logo asset through the same recovery protocol.

## Failure semantics

- Database unavailable: no legacy JSON or alternate-adapter write.
- PostgreSQL unavailable: explicit error/read-only transition; never local
  SQLite fallback.
- SQLite process/path safety failure: refuse writable startup.
- Permission denied: no domain write; optional security event only.
- Revision or lock conflict: preserve input and show reload/reacquire.
- Audit failure: successful mutation rolls back.
- Document mismatch: never expose payload as active.
- Notification failure after commit: data stay committed; refresh retries.
- Canonical validation failure: no target write.
