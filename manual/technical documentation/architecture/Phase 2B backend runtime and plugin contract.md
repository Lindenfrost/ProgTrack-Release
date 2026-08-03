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

## Mutable paths

Packaged application resources are read-only. Mutable data, configuration,
cache, state, logs, runtime locks, exports, preferences, managed documents,
and managed configuration assets resolve through
`Plugins.core.runtime_paths`.

Portable mode is explicit and is used only when the application directory is
writable. Installed Windows mode uses LocalAppData/AppData. Installed Linux
mode follows XDG data, config, cache, and state roots. A read-only application
directory automatically selects installed/user-profile roots.

## Plugin storage

Operational plugin records use stable `namespace`/`record_id` backend records
or normalized domain services. Managed documents are copied through the
managed-file service; database rows retain safe relative paths, ownership,
state, size, media type, and SHA-256. New plugin schemas register ordered,
idempotent revisions before the plugin can use them.

Animal identity is the immutable four-block IPID:

`Name | Species | DD.MM.YYYY | Origin`

Name, species, complete birth date, origin, and IPID cannot change after
creation, including for Lord. Other public IDs must follow the configured
facility-qualified convention.

## Interchange and seed

The canonical ZIP interchange contains a manifest, checksummed JSONL records,
and managed payloads. Import validates paths and hashes and initializes an
empty backend through services. Legacy JSON is not a Phase 2B migration input.
`Resources/Seed/progtrack_seed.ptdb` is the single deterministic, fictional
initialization source for both profiles.

## Entity leases

Animal, project, housing, and other reviewed edits use backend leases plus
optimistic revisions. Leases expose owner and expiry, support heartbeat, and
are released on save/cancel. Only Lord can force-release another lease, a
reason is mandatory, and the action is audited.

## Semantic UI icons

`icons/ui/manifest.json` maps semantic identifiers to packaged 64×64 PNG
assets and exact text fallbacks. Each packaged PNG is generated from a matching
editable SVG source under `Q:\GitHub\Graphics\SVG\UI`; generated PNG backups
are under `Q:\GitHub\Graphics\UI`. Code resolves icons with
`Plugins.core.ui_icons` and must retain a readable fallback. Network Track
discovers only root-level `icons/*.png` and never loads `icons/ui/`.
