# Managed-document, interchange, and external source adapter contract

## Managed-document contract

Large files remain outside either profile database. PostgreSQL or SQLite stores
the same authoritative metadata and stable ownership links; payloads live
under the configured managed root for that deployment profile.

### Required metadata

Each document has:

- immutable facility-owned `document_id`;
- `owner_type` and typed `owner_id`: animal IPID or facility-owned non-animal
  record ID;
- semantic `document_kind`;
- original filename and optional title;
- normalized MIME type;
- exact byte size;
- lowercase SHA-256 checksum;
- managed-storage-relative payload path;
- created time and actor;
- state (`staged`, `pending`, `active`, `quarantined`, `deleted`);
- optional source-system/source-record/path provenance.

Paths are always relative POSIX-style package/storage paths. Absolute paths,
drive letters, `..`, symlinks escaping the root, and owner names embedded as
identity are rejected.

### Suggested managed layout

`documents/<document_id>/<sanitized-original-name>`

The path is payload organization only. Ownership is held in typed metadata:
animal owners use immutable IPID; other durable owners use facility-owned
record IDs.

Deduplication by checksum may be an internal optimization, but it must not
merge document metadata or ownership. Two owners may deliberately have
separate document records with identical bytes.

### Write lifecycle

1. copy upload to managed staging;
2. stream and validate size, MIME policy, and SHA-256;
3. commit `pending` metadata and typed ownership links;
4. atomically move or idempotently place the final payload;
5. re-read and verify the final payload;
6. transition `pending` to `active` in a second database transaction;
7. publish success only after activation.

The system must not claim success when only the file or only the metadata was
written.

Failure before pending commit removes abandoned staging. Pending metadata
without a final payload retries activation or quarantines. A final payload with
pending metadata is verified and activated idempotently or quarantined on
mismatch. Active metadata with missing/mismatched bytes is removed from normal
visibility and quarantined. Startup/scheduled reconciliation detects abandoned
staging, pending rows, unregistered final payloads, and checksum mismatches.

### Current-file reconciliation

Before the example package is generated:

- match Medi JSON metadata to actual files;
- create review candidates for folder-only files;
- reject or quarantine zero-byte payloads unless explicitly accepted;
- resolve project folder names to exact stable projects;
- verify that the already removed orphan `A` project/SOP test folders are not
  recreated or guessed as “Anode”;
- retain identical payload hashes as separate owner records;
- produce a readable reconciliation report.

No files are changed during Phase 2A.

## Canonical full-dataset interchange package

The package is backend-neutral and versioned. It is not the Phase 5 `.pta`
single-animal transfer format.

### Container

A ZIP-compatible container with deterministic safe relative paths:

```text
manifest.json
records/
  reference_data.jsonl
  users_or_security_seed.jsonl          # optional/protected by decision
  animals.jsonl
  animal_identifiers.jsonl
  animal_roles.jsonl
  animal_relationships.jsonl
  animal_events.jsonl
  measurements.jsonl
  projects.jsonl
  project_assignments.jsonl
  housing_nodes.jsonl
  housing_occupancies.jsonl
  housing_inspections.jsonl
  medical_issues.jsonl
  medical_entries.jsonl
  samples.jsonl
  aliquots.jsonl
  flow_records.jsonl
  surgery_plans.jsonl
  report_overrides.jsonl
  analysis_models.jsonl
  network_messages.jsonl                # if retention approved
  documents.jsonl
payload/
  documents/<document_id>/<sanitized-original-name>
  config-assets/<asset_id>/<sanitized-original-name>
configuration/                              # optional/protected
  institution_branding.json
reports/
  validation.json
  source-reconciliation.json
```

JSON Lines allows streaming and record-level errors. Record types and paths are
declared in the manifest; absent optional record sets are explicit.

### Manifest

The manifest contains:

- package format ID and semantic version;
- exporter/app/schema versions and source commit;
- facility-owned package ID, creation time, manager-defined source
  facility tag/name,
  source system, non-production/example marker;
- record-set descriptors, schema version, count, byte size, and SHA-256;
- document-payload descriptors/count/total size;
- dependency/import order;
- supported/required features;
- source adapter/version;
- validation status and error/warning counts;
- no machine-specific absolute paths or credentials.

The package declares its source deployment profile (`standalone_sqlite` or
`shared_postgresql`) for diagnostics only. Record semantics are
backend-neutral. Importing a validated Standalone package into empty
PostgreSQL is the supported small-facility-to-server transition.

The manifest itself receives a package checksum/signature mechanism when
production-grade transfer is introduced. The Phase 2 example cutover at least
checks every record file and payload SHA-256.

### Inclusion

Include normalized authoritative domain records, reviewed authoritative
derived results, approved reference/config records, and reconciled managed
documents. Include stable source provenance and immutable animal IPIDs for
diagnostics and linkage.

### Exclusion

Exclude:

- project/sidebar caches;
- generated Heritage core projections and in-memory graph caches;
- generated Animal Report aggregate rows;
- per-user geometry, active tab/filter/expanded-node state by default;
- entity locks and live sessions;
- `.bak`, `.tmp`, `.lock`, corrupt/fallback files;
- generated PDFs/XLSX/PNG exports unless deliberately registered as documents;
- packaged icons, sounds, translations, example import templates, and code;
- raw database credentials;
- password hashes from an ordinary domain package;
- unreviewed orphan/zero-byte files.

Legacy audit text and user/security seed data are controlled optional sections,
not silently mixed with domain data.

Institution branding is an optional protected installation-configuration
section. The logo is a managed configuration asset, not a managed
animal/project/medical document and not a packaged UI icon. Institution name,
logo, and toggle remain separate from animal `origin` and the facility tag.

## Export pipeline

1. Discover from the explicit source registry, not filename extensions.
2. Read all current stores without mutation.
3. Preserve immutable animal IPIDs. Where current facility-owned records lack
   IDs, assign deterministic IDs in the source facility namespace. The
   mapping is stored in the export job so repeated export is idempotent.
4. Normalize dates, role IDs, identifier types, units, and references.
5. Resolve cross-store identities; retain source values for diagnostics.
6. Reconcile current/history duplicates and documents.
7. Validate schemas, uniqueness, foreign keys, controlled values, and checksums.
8. Write package to a staging destination.
9. Re-open and independently verify the package.
10. Publish only a validation-passing package; otherwise retain a failure
    report without labelling it importable.

The disposable example-origin cleaning was completed on 2026-07-28 before
Phase 2B. Export validates the cleaned stored values and preserves the
pre-clean audit evidence, but does not calculate or modify origins again.
Four-block IPID creation uses the cleaned `origin`. This remains distinct from
production migration.

## Import pipeline

1. inspect container/path safety and manifest compatibility;
2. verify hashes and sizes before parsing;
3. parse into a staging model with no target writes;
4. validate record schemas and controlled IDs;
5. resolve all stable foreign keys and source idempotency keys;
6. resolve and validate the immutable controlled origin value in every
   four-block animal IPID. An unknown origin blocks commit until an authorized
   Lord, Master, or Manager adds/approves it. Re-import of the same full IPID is idempotent when
   it represents the same source animal; conflicting content for an equal full
   IPID is quarantined and never resolved by renaming;
7. show preview: creates, updates, skips, conflicts, warnings, documents, and
   errors by record type;
8. show unknown origins, equal-IPID re-imports, and conflicts with record
   provenance;
9. require explicit confirmation and suitable permission;
10. import in declared dependency order through InterchangeService;
11. use one transaction/correlation/audit context for the animal and all
    dependent records;
12. verify post-import counts, relationships, queries, document hashes, and
    representative UI workflows.

Direct table import, plugin-file copying, and “best effort” partial writes are
not allowed.

## Operational measurement workbooks are not dataset interchange

Blood, urine, weight, and sperm XLSX imports are operational measurement
commands governed by Issue #53, not canonical dataset packages. They can
reference existing animal IPIDs/IDs and Sample IDs but cannot create animal
master data, placeholders, owners, or folders. Unknown animals remain visible
in their preview and are skipped while valid rows for existing animals may
commit. Adding a missing animal requires the normal Manager-led New Animal
workflow before safe retry.

## Deployment-profile portability

- Both supported profiles export the same canonical package.
- Standalone SQLite export reads from one exclusively owned local database and
  local managed/configuration roots.
- Shared PostgreSQL export uses a consistent server snapshot plus configured
  managed/configuration roots.
- Import always targets an empty or explicitly compatible installation through
  InterchangeService; database-file copying is not portability.
- PostgreSQL-to-Standalone import is permitted only when package size,
  feature flags, local storage, and one-process operational constraints pass
  validation. It is not a concurrency-preserving downgrade.
- README/manuals must explain profile choice, local-path restrictions, backup,
  and the package-based transition after implementation is finalized.

## External source-adapter boundary

An external source adapter is a source adapter, not a database client.

The adapter:

- reads a documented external-system export/source snapshot;
- never writes ProgTrack PostgreSQL tables directly;
- declares adapter and source-schema versions;
- maps source-system identifiers to canonical source-system/source-record IDs;
- normalizes animals, species, sex, birth/death, public IDs/chips, roles,
  relationships, projects, housing, measurements, samples, medical records,
  and documents only where source semantics are known;
- uses explicit mapping tables for role/species/status/unit vocabularies;
- keeps raw source values/provenance for review;
- produces the same canonical package/staging records as the example exporter;
- supports deterministic reruns and idempotency;
- reports unresolved/ambiguous references rather than matching by display name;
- carries attachment bytes through the same document metadata/checksum rules;
- ends at the common preview/validation/import service.

### Required adapter validation

- required full birth date/species for a canonical animal or an explicit
  reviewed unknown-value policy;
- unique source identity and no conflicting public IDs/chips;
- valid immutable controlled origin in every animal IPID, idempotent
  same-animal re-import, and quarantine of conflicting equal-IPID records;
- valid parent/partner graph without accidental self-links;
- project/user/housing foreign-key resolution;
- unit conversion and specimen/analyte semantics;
- sample ID versus animal ID distinction;
- time-zone/date precision metadata;
- no path traversal or missing document payload;
- per-record error severity and source locator;
- reviewable totals and reconciliation against source counts.

Unknown external-source fields may be preserved in a namespaced provenance extension, but
must not be inserted into arbitrary PostgreSQL columns or opaque plugin JSON.
