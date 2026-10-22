# Phase 2B backend, runtime, and plugin contract

## Deployment profiles

- `standalone_sqlite` is supported for exactly one local workstation and for
  tests. The database is rejected on UNC/remote and recognized synchronized
  cloud paths. One exclusive process lock protects the writable database.
- `shared_postgresql` is the network/multi-workstation profile. It uses
  synchronous Psycopg 3 transactions from Qt worker contexts and a bounded
  `psycopg_pool` (`min_size=1`, `max_size=4` by default).
- Both profiles expose the same repository/service contract. Plugin code must
  not import `sqlite3`, Psycopg, or backend-specific SQL.
- `Settings -> Backend` is enabled only for an authenticated Lord. Saving a
  validated profile takes effect on the next clean restart and never transfers
  data or silently opens the other adapter. The packaged runtime includes the
  Psycopg client stack, not a PostgreSQL server.

## Mutable paths

Packaged application resources are read-only. Mutable data, configuration,
cache, state, logs, runtime locks, exports, preferences, managed documents,
and managed configuration assets resolve through
`Plugins.core.runtime_paths`.

Portable mode is explicit and is used only when the application directory is
writable. Installed Windows mode uses LocalAppData/AppData. Installed Linux
mode follows XDG data, config, cache, and state roots. A read-only application
directory automatically selects installed/user-profile roots.

The writable portable layout is `ProgTrackData/database`, `config`, `cache`,
`managed/documents`, `managed/config-assets`, `state/logs`, `state/runtime`,
`exports`, and `config/preferences`. The default SQLite file is
`ProgTrackData/database/progtrack.sqlite3`; its process-owner file is
`ProgTrackData/state/runtime/standalone.lock`.

## Plugin storage

Operational plugin records use stable `namespace`/`record_id` backend records
or normalized domain services. Managed documents are copied through the
managed-file service; database rows retain safe relative paths, ownership,
state, size, media type, and SHA-256. New plugin schemas register ordered,
idempotent revisions before the plugin can use them.

Plugin modules are imported and initialized during stepped startup. A missing
or failing optional plugin remains unavailable and records its exception in the
technical log. A packaged installation is supported with the complete frozen
`_internal` runtime; ad-hoc libraries beside the application are not a runtime
substitute.

Animal identity is the immutable four-block IPID:

`Name | Species | DD.MM.YYYY | Origin`

Name, species, complete birth date, origin, and IPID cannot change after
creation, including for Lord. Other public IDs must follow the configured
facility-qualified convention.

## Core Save acknowledgment and audit

`ProgTrackBackend.save_core_data` returns a detached `CoreSaveReceipt` only
after the adapter transaction commits. `acknowledged_records` maps
`(scope, IPID)` to accepted canonical records, including backend-supplied event
IDs/recording roles even when acknowledgment requires no SQL update.
`audit_before` and `audit_after` contain the transaction's actual affected
existing records. Create/archive/restore handlers retain lifecycle audit ownership.
The receipt is constructed from rows already read for the write, not another
population read or a per-animal query. Import/seed callers without live Core
state may ignore the result; live Core callers must consume it.

Core checks the saving actor/backend identity, updates accepted records and
same-ID event objects in place, restores lossless stored-date views, then
audits that applied delta synchronously once. Never audit the submitted DTO
or defer attribution to a different session. A failed transaction publishes
neither acknowledgment nor success audit. If audit fails after commit, the
acknowledged memory remains committed and the failure is reported without
retrying the data write. This receipt is not a transactional audit outbox or
a substitute for the later shared-database concurrency/durability contracts.
No compatibility layer, schema change or data migration is required.

## Interchange and seed

The canonical ZIP interchange contains a manifest, checksummed JSONL records,
and managed payloads. Import validates paths and hashes and initializes an
empty backend through services. The configured backend remains the only
operational source. `Resources/Seed/progtrack_seed.ptdb` is the single
deterministic, fictional initialization source for both profiles; it is loaded
only when core data and backend record namespaces are both empty.

## Entity leases

Reviewed writes use backend leases plus optimistic revisions where the service
has registered that boundary. Leases expose owner and expiry, support
heartbeat, and are released on save/cancel. Only Lord can force-release another
lease, a reason is mandatory, and the action is audited. Standalone SQLite also
has an exclusive process lock; a live second writer is refused and a confirmed
stale local-process lock is reclaimed.

## Current workflow projections

- Project records validate exactly `draft`, `active`, or `closed`. Authorized
  lifecycle changes are audited and remain independent from archive state.
- Cage Track projects the complete ordered animal selection into one
  deterministically selected building and highlights matching occupants. Its
  inspection table uses typed stable sorting and stores column/direction as a
  signed-in user preference; guest state is not persisted.
- Medi Track multi-animal File-menu export publishes each completed PDF/XLSX
  atomically, reports determinate progress and current item, checks cancellation
  between outputs, and gives localized complete/partial/cancelled summaries.
- The main animal-list filter row combines a case-insensitive short-name prefix
  search with independent female, male, and unknown-sex icon checkboxes. All
  three sex predicates start enabled and compose with authorization, role,
  project, species, archive, and active-plugin filters; they are presentation
  state only and never mutate animal records.
- Institution branding is embedded in
  `Settings -> Conventions -> Institution branding` for Lord, Master, and
  Manager. Its optional PNG/JPEG payload is a managed configuration asset and
  is proportionally scaled into one bounded shared PDF block. The backend-owned
  position convention selects top left or top right (top right is the legacy
  default), every PDF exporter consumes the same setting, and the grey page
  preview renders the effective name/logo block at the selected edge before
  saving.

## Core clinical-event routing (#321)

Authoritative source: `ProgTrackApp._write_json` supplies the signed-in source
capabilities to `ProgTrackBackend.save_core_data` → `AnimalService.replace_snapshot`
→ `ClinicalMirrorTransaction`. The latter runs inside the **same connection and
write transaction** as Core. Failure rolls both source and clinical writes back;
there is no timer, UI dependency, separate JSON fallback or invented outbox.
`CoreSaveReceipt` acknowledges the accepted policy/revision metadata only after
commit, preserving #315/#316's canonical audit/no-op boundary.

| Canonical event source | Automatic retained clinical original | Configuration |
|---|---|---|
| `surgery`, `oocyte_retrieval` | YES, actual performed Core occurrence | Fixed |
| `embryo_transfer`, `pregnancy`, `birth`, `abortion` | YES | Fixed |
| `special_measurement` (Hearing test) | YES | Fixed |
| `sperm_donation`, `pregnancy_verification`, `pgf`, `fsh`, `progesterone`, `measurement` | NO | Fixed |
| Custom Counting or Limiting event | Captured `mirror_in_medical_history` boolean | Shared installation event definition; default false |
| Raw blood / weight / urine-PdG / sperm results | NO | Not a clinical-event route |
| Surgery Planner `op` / `embryoübertragung` plans | NO | Scheduling only; no performed-completion command exists |
| New Core offspring / experimental offspring | Own birth at immutable birth date | Fixed; separate from mother's actual `birth` event |
| Live Core departure/handover | YES, effective date + reason/recipient | Fixed; death archives do not create a live-departure row |

The actual Core event editors are `_dlg_female_animal`, `_dlg_offspring`,
`_dlg_zuchttier`, `_dlg_versuchstier` (including experimental offspring), and
`_dlg_basic_animal_role`; each offers its applicable event set, not all built-ins.
Their real Save adapters converge on this boundary. Existing Reports row/text
editing is not a second performed-event producer; planner schedules live in
their own domain records (`surgery_planner.py` / `surgery_engine.py`). A later
performed Core record may retain `source_plan_id` or other producer metadata
unchanged. Do not infer completion from a past appointment, display label or
Flow alias. Raw result imports use the same Core Save but create no clinical row.

Shared usage remains `Plugins/core/project_periods.py` → Core statistics,
`AnimalReportIndex`, and Medi's **Project event usage** header. Event types,
recorded-role applicability, current/former periods, Counting versus Limiting,
maxima and Line/Symbol styles are unchanged. Medical history is not a counter:
deleting a source removes its actual usage while retaining clinical evidence.
Existing project/experiment/severity/identity/death routes remain separate;
#183's general audit redesign and #258's death-correlation work are not claimed
implemented by this change.

Storage: one `domain_records` ledger per immutable animal IPID in namespace
`medical.core-events`, with source mappings/tombstones and immutable `entries`.
Key identities include animal IPID + Core event ID + annotation action/revision;
same-day same-type occurrences are never deduplicated by display values. First
recording snapshots the definition identity/revision/label and policy on the
accepted Core payload. An actual edit advances its guarded source revision and
appends a linked correction; deletion appends a linked deletion annotation.
The original payload, effective date, actor, recorded role/period and unknown
producer metadata are preserved. A retired/detached or now-unchecked definition
cannot cancel required annotations. Hard archived-animal deletion also annotates
its captured event sources in the coordinated transaction without erasing the
ledger. PostgreSQL reserves/locks the affected ledger row, including absent rows;
that SQL path is implemented but not validated against a live PostgreSQL server.
This is not a general Core snapshot-CAS/concurrency redesign.

`MediStore.get_entries` combines separately authored `medical/history` records
with a fresh read of that animal's Core ledger; manual edits cannot clobber the
derived records. The table and PDF/XLSX distinguish source corrections/deletions
from performed procedures and show effective dates/source links/old-new values
and signed actor. Source authorization governs this internal derivative command;
no manual `medi_track.add_docs` right is granted. Configuration uses the existing
role-management permission and configuration revision checks; the normalized
boolean survives the actual Settings rebuild, not just the event form.

No side effects on Cancel, invalid/declined Save, reads, reopen, no-op or restart.
Flag changes affect **new first-recorded sources only**, including newly entered
backdated events. They do not backfill existing unmirrored events or remove old
clinical entries. Seed/bootstrap/interchange replay without a recording context
does not synthesize history; packages preserve the domain ledger as ordinary
backend records. No backwards compatibility, migration or historical/seed
backfill is required. No full-population clinical scan or extra Core reload was
added; only substantively changed source ledgers are written.

## Semantic UI icons

`icons/ui/manifest.json` maps semantic identifiers to packaged SVG assets and
exact text fallbacks. The editable SVG masters are maintained under
`Q:\GitHub\Graphics\SVG\UI`. Code resolves icons with
`Plugins.core.ui_icons` and must retain readable localized text and tooltips.
The release's semantic UI package is SVG-only and has no PNG fallback. UI
masters use a fixed black outline; the loader does not recolour it for Qt dark
or high-contrast palettes, so the same outline is rendered on every
workstation. Only deliberately coloured details (for example status accents
and flag artwork) retain their own colours. Semantic IDs may intentionally
share one canonical SVG; duplicate alias files are not shipped. Network Track
discovers only root-level `icons/*.png` and never loads `icons/ui/`.
