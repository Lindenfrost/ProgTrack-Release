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

## Semantic UI icons

`icons/ui/manifest.json` maps semantic identifiers to packaged SVG assets and
exact text fallbacks. The editable SVG masters are maintained under
`Q:\GitHub\Graphics\SVG\UI`. Code resolves icons with
`Plugins.core.ui_icons` and must retain readable localized text and tooltips.
The release's semantic UI package is SVG-only and has no PNG fallback. The
loader preserves master colours on Qt light palettes and substitutes a
palette-derived outline only when the canonical outline lacks contrast on Qt
dark or high-contrast surfaces. Semantic IDs may intentionally share one
canonical SVG; duplicate alias files are not shipped. Network Track discovers
only root-level `icons/*.png` and never loads `icons/ui/`.
