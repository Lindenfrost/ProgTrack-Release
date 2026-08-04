# Ordered Phase 2B implementation and validation plan

Phase 2B starts only after explicit approval of this Phase 2A package.

## Delivery order

### 0. Freeze architecture and synchronize specifications

- Resolve document 06 and record approval.
- Correct Issues #50, #15, #34, and #48 as described in documents 08/09.
- Distill only the approved target into versioned repository architecture docs.
- Freeze a fresh source/example snapshot and execute the read-only evidence
  contract.
- Keep Issue #48 blocking #49–#53 until this gate passes.

Gate: no unresolved decision changes schema, identity, deployment profile,
permissions, interchange, or managed payload semantics.

### 1. Runtime paths and profile configuration — Issue #49

- Implement explicit `standalone_sqlite` and `shared_postgresql` profiles.
- Add typed paths for local database, configuration, preferences, caches,
  logs, runtime locks/temp, exports, managed documents, and managed config
  assets.
- Reject network/NAS/synchronized SQLite and obtain exclusive local process
  ownership.
- Keep legacy stores read-only until their registered cutover.
- Ensure packaged/read-only code directories receive no runtime writes.

Gate: profile selection is unmistakable; every registered path has one owner;
SQLite cannot start writable in an unsupported topology.

### 2. Service interfaces and adapter parity — Issue #50

- Implement repository/service protocols before plugin cutover.
- Implement SQLite and PostgreSQL adapters against identical domain tests.
- Use animal IPID as the only animal primary/foreign key.
- Use facility-owned IDs for installation-created durable non-animal records.
- Keep global semantic IDs untagged and freeze the exception catalogue.
- Add transactions, revisions, typed references, domain/security/operation
  audit interfaces, and configuration/asset services.
- Make Standalone a supported one-process profile and Shared PostgreSQL the
  concurrent profile; no fallback between adapters.

Gate: both pass functional/validation/audit/package contract tests;
PostgreSQL passes concurrent transaction tests; SQLite passes local-path and
exclusive-process tests.

### 3. Security, sessions, permissions, and locks

- Cut users/jobs/grants into SecurityService with versioned password hashes.
- Add every grantable permission/label and keep internal-only IDs non-grantable.
- Enforce permission/project scope at command boundary.
- Implement PostgreSQL visible leases and Standalone local lock API/process
  ownership.
- Add optimistic revision conflicts and audited force release.

Gate: UI and direct-service outcomes agree; stale writes fail; shared lock
contention and Standalone second-process rejection pass.

### 4. Canonical interchange and managed payload foundation

- Implement source registry, versioned JSONL package, validation, quarantine,
  typed IDs, provenance, checksums, and idempotency.
- Implement DocumentService/config-asset `staged → pending → active` lifecycle
  with quarantine/delete and reconciliation.
- Use `payload/documents/<document_id>/<sanitized-original-name>` and separate
  `payload/config-assets/...`.
- Add package-profile metadata but keep record semantics backend-neutral.
- Prove empty Standalone and Shared round-trips.

Gate: counts/hashes/references match; crash injection at every payload
transition never exposes inconsistent active data.

### 5. Core AnimalService and MeasurementService cutover

- Implement immutable four-block IPID and DB constraints for IPID/name/species/
  full birth date/origin.
- Implement delete-and-recreate for wrong identity with no automatic transfer.
- Validate the already completed 2026-07-28 example-origin cleaning and use
  each stored value when constructing the four-block IPID. Do not recalculate
  or silently change origin/species during cutover.
- Normalize animals, identifiers, roles, relationships, lifecycle/status,
  measurements, events, and heritage-only animals.
- Preserve global built-in role IDs; create facility-owned custom-role records.

Gate: every role including Lord is unable to update identity; all references
resolve by IPID; cleaned origins validate without further data mutation.

### 6. Existing-animal-only measurement imports — Issue #53

- Build one non-mutating full-file plan for blood, urine, weight, and sperm.
- Keep existing, unknown, archived, ambiguous, invalid, duplicate, and
  permission-rejected decisions distinct.
- Display unknown rows as skipped; table may cap at 200 but counts/identifiers
  cover the complete file.
- Show localized singular/plural warning and final summary.
- Revalidate transactionally; insert only existing-animal rows.
- Never call AnimalService creation for Researcher, Keeper, Manager, Master, or
  Lord.
- Preserve Animal ID, IPID, public animal ID, Sample ID, and sample number as
  separate concepts.
- Apply deterministic duplicate behavior and safe retry after Manager-led New
  Animal creation.
- Write operation-level aggregate outcome; unknown rows cause no animal/domain
  side effect.

Gate: mixed-file, >200-row, role, direct-service, race, cancel,
partial-success, and retry tests pass for all four types with unchanged animal
count.

### 7. Project and housing cutover

- Normalize projects/protocols/users/animal assignments/history.
- Resolve `Zeta-1` and project-name conflicts by reviewed correction/quarantine.
- Normalize four-level housing, occupancy/movement, inspections, and scope.
- Remove caches as authority and regenerate them through services.

Gate: project visibility/species/history and Cage workflows pass in both
profiles; concurrent Shared edits conflict safely.

### 8. Medical, documents, samples, and institution branding

- Normalize Medi issues/entries/status and typed document ownership.
- Reconcile remaining Medi folder-only/zero-byte payloads and verify that the
  already removed orphan Project `A` test paths are not recreated.
- Normalize samples/aliquots/storage with typed IDs and quarantine.
- Implement Issue #52 branding configuration, grantable permission, managed
  config asset, optional protected package section, and shared PDF header.

Gate: payload recovery/round-trip passes; branding works with absent/corrupt
logo and representative portrait/landscape/multi-page PDFs.

### 9. Flow, Heritage, PdG, Reports, Embryo, and Surgery

- Replace embedded/name references with IPID/facility-owned typed IDs.
- Remove Heritage core projection and report aggregate as stored truth.
- Split report override, shared scientific config, and user preferences.
- Version Embryo cranimetry reference under a configuration permission.
- Resolve Surgery staging/published path/schema conflict.

Gate: domain regressions and package round-trip pass with no direct legacy
write.

### 10. Network and remaining configuration/preferences

- Apply reviewed chat-retention decision.
- Version remaining scientific/reference configuration.
- Move per-user state to preference service.
- Eliminate remaining application/plugin-folder writes.

Gate: static scan finds no unregistered runtime write.

### 11. UI icon registry — Issue #51

- Replace platform-dependent UI symbols with packaged semantic assets/fallbacks.
- Keep packaged UI icons outside document/config-asset/interchange payloads.

Gate: Windows/Linux packaged rendering and missing-asset fallback pass.

### 12. Release cutover verification

- Correct only approved disposable example data.
- Export the frozen example dataset canonically.
- Import into empty Standalone SQLite and Shared PostgreSQL.
- Run functional, adapter, concurrency/profile, permission, import/export,
  document, visual, and manual tests.
- Update technical architecture, README, and manuals with deployment-profile
  selection, SQLite restrictions, PostgreSQL setup expectations, backup, and
  canonical transition workflow.
- Remove legacy live fallback writes; retain explicit read-only diagnostic/
  import tooling only where approved.

Gate: both profiles are honestly documented and supported, all plugins use
services, and release artifacts contain no development/audit clutter.

## Automated validation

### Architecture/static

- manifests/entry points parse;
- every write path maps to runtime/config/cache/resource/managed/output or is
  forbidden;
- no plugin database driver/SQL;
- all animal FKs use IPID;
- facility-owned/global semantic taxonomy is enforced;
- no direct identity update path;
- no measurement importer uses a create-missing animal path;
- action permission catalogue and translations agree;
- disabled-plugin behavior and packaged resources resolve;
- clean tree before/after read-only audit.

### Adapter/profile parity

- CRUD/query/validation and audit outcomes match;
- package export/import results match;
- deterministic fixture reset;
- Standalone local-path/exclusive-process enforcement;
- Shared multi-client contention, lease expiry, stale revision, deadlock order,
  transaction isolation, and connection-loss behavior;
- no Shared-to-Standalone fallback.

### Measurement imports

- all four formats: existing + multiple unknown rows + invalid + duplicate;
- >200 displayed rows with complete-file counts;
- archived, ambiguous, similar-name/unknown-ID, Sample-ID distinction;
- cancel/confirm/direct-service/permission/race cases;
- Manager creates animal normally, retry imports only formerly skipped data;
- exact operation/domain/security event expectations.

### Managed payload/interchange

- path safety, schema/version/hash/count/provenance;
- failure at each staged/pending/final/active transition;
- startup reconciliation and quarantine;
- identical bytes under distinct owners;
- config asset distinct from document and packaged icon;
- Standalone ↔ empty Shared package transition;
- External source adapters produce package/staging only, never SQL.

### Domain regression

- animal lifecycle/roles/relationships/measurements;
- project visibility/species/history;
- housing hierarchy/occupancy/inspection;
- medical/status/document/report-to-Medi;
- samples/aliquots/storage;
- Flow/Heritage/PdG/Reports/Embryo/Surgery/Network;
- report exports and branding;
- localization and permission labels.

## Manual/developer verification

- profile selection and warnings are understandable;
- unsupported SQLite paths/second instance fail clearly;
- lock/conflict screens preserve unsaved input;
- mixed measurement-import preview matches committed result;
- managed-document reconciliation is reviewable;
- full portable Standalone workflow works without a server;
- multi-workstation Shared workflow shows locks and refreshes;
- README/manual profile instructions match actual setup;
- representative UI/PDF/graph rendering passes on Windows and Linux.

## Traceability

| Finding/requirement | Primary step |
| --- | --- |
| F01, F04, F19; A01/A02 | 5 |
| F02, F03, F12–F14, F22 | 1–3 |
| F05 | 9 |
| F06 | 7 |
| F07 | 8 |
| F08, F18; document state machine | 4 and 8 |
| F09 | 9 |
| F10 | 9 |
| F11 | 1 and 4 |
| F15 | 10 |
| F16–F17 | 3 and 9 |
| F20; Issue #53 | 6 |
| F21; Issue #52 | 1, 4, and 8 |
| typed identifiers | 2 and every domain cutover |
| published issue synchronization | 0 |
| evidence completeness | 0 and final static suite |
