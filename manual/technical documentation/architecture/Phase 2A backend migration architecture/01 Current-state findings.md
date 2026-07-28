# Current-state findings

This document records observed facts. Interpretations, recommendations, and
questions are kept in the separate decision document.

## Application-level persistence

- Core live/example data are stored in `progtrack_daten.json`, schema version
  `4.0`.
- The inspected file contains 134 active and 93 archived animals.
- Every animal is keyed by a composite IPID in the form
  `name | species | DD.MM.YYYY`; the record repeats the same value in `ipid`.
- All 227 public `id` values are unique in the inspected example data.
- The core record embeds measurements and events (`gewicht`, `daten`, `pdg`,
  `sperm`, `events`, `lifecycle_events`), status/lifecycle fields, current
  project, project history, role, four parent fields, and partner fields.
- A whole-file `progtrack_daten.lock` is acquired with a Windows file lock.
  Failure makes the application read-only and starts a 30-second reacquire
  timer. This lock does not cover plugin files.
- Core saves replace the complete JSON file. Plugin saves happen separately;
  no common transaction or revision spans core and plugin data.
- Root settings are expected in `progtrack_settings.json` when created.
  Disabled plugin state can be stored in `disabled_plugins.json`; logged-in
  users instead persist it in Master Track session JSON.
- Configurable roles are stored in `Plugins/core/animal_roles.json`.
  Identity/lifecycle conventions and controlled catalogues are stored in
  `Plugins/core/identity_lifecycle_conventions.json` and `Plugins/Resources`.

## Identity and references

- The current IPID is both a displayable composite identity and the practical
  foreign key used by plugins.
- Identity edits invoke cross-file reference-rewrite helpers and folder moves.
  This confirms that the inspected implementation does not yet enforce the
  approved target: four-block IPID, name, full birth date, species, and stored
  `origin` (Herkunftseinrichtung) immutable after creation, without a Lord
  exception.
- Core relationship fields use IPID text:
  `eizellspenderin`, `samenspender`, `ziehmutter`, `ziehvater`, and
  `verpaart_mit`.
- The inspected 256 non-empty core relationship references all resolve.
- Heritage Track has 240 non-empty parent references; all resolve within its
  own 243-animal set.
- Project references use project names, not stable project IDs.
- User references in Project Track use login names.
- Several UIs retain selected rows/items and then recover IPID/name text from
  them. Selection is presentation state, not a durable reference boundary.
- Animal origins are already controlled through the manager-editable
  `Animal_Origins.txt` catalogue and selected into each record's `origin`
  field. The inspected example data contain 216 animals with an origin and 11
  without one. Stored values include `DPZ` and `Iluvatara`, while the current
  catalogue contains only `Aulë`, `Iluvatar`, and `Morgoth`.

Target note, not a source fact: document 06 defines the reviewed disposable
example-data correction before `origin` becomes the fourth IPID block.

### Subsequent sample-data cleaning (2026-07-28)

After the frozen-source audit, the reviewer authorized the deterministic
cleaning pass. It changed only `origin` fields in `progtrack_daten.json` and
added `DPZ` to `Plugins/Resources/Animal_Origins.txt`:

- parentless Macaca: `Aulë`;
- parentless Callithrix/current sample `Callitrix`: `Iluvatar`;
- parentless Papio: `Morgoth`;
- every non-parentless, other, unknown, or indeterminate animal: `DPZ`;
- legacy `Iluvatara`: normalized to `Iluvatar`.

All four parent fields determined parentless status. The 227 current
three-block dictionary keys were intentionally not changed. Validation found
zero rule/catalogue violations. The original snapshot facts above remain audit
history rather than a description of the cleaned working tree.

## Internal role IDs

The persisted core role values are internal English IDs:

| Role ID | Count |
| --- | ---: |
| `breeding_animal` | 93 |
| `offspring` | 115 |
| `partner_animal` | 3 |
| `egg_cell_donor` | 3 |
| `sperm_donor` | 7 |
| `surrogate` | 2 |
| `experimental_animal` | 1 |
| `unknown` | 3 |

The configured registry uses the same `role_id`/`value` identifiers. German
legacy labels were found only in language/resource text during the boundary
scan, not in the inspected core role field.

## Referential-integrity snapshot

| Store/reference set | Total | Resolves to core animal | Does not resolve to core |
| --- | ---: | ---: | ---: |
| Heritage animals | 243 | 227 | 16 heritage-only/test animals |
| Cage occupants | 228 | 227 | 1 (`Andy…`) |
| Cage movement-history keys | 228 | 227 | 1 (`Andy…`) |
| Medi animal blocks | 145 | 135 | 10 heritage/test animals |
| Animal Report edit/lock records | 5 | 5 | 0 |
| PdG fitted models | 1 | 1 | 0 |
| Surgery pre-plan IPIDs | 3 unique | 3 | 0 |
| Surgery published-plan IPIDs | 2 unique | 2 | 0 |
| Project-history animal IPIDs | 29 unique | 27 | 2 |

The two project-history-only references are `Andy…` and a Papio `Lindir…`.
Flow Track also contains that Papio `Lindir…`; generated transfer IDs embed
animal IPID text and therefore cannot be validated as plain animal IDs.

Sample Track currently has mixed reference quality:

- valid composite animal names can coexist with stale public `id` snapshots;
- rows for Donor/Doner, a 2024 Lindir, and Petrulla do not resolve to current
  core animals;
- sample/aliquot locations are JSON strings nested inside JSON records;
- `sample_number`, public animal `id`, and composite `animal_name` serve
  different purposes and are not enforced as distinct foreign keys.

### Subsequent orphan example-data cleaning (2026-07-28)

After the frozen audit, the reviewer authorized removal rather than guessing:

- four unresolved Sample Track rows were removed: Donor, Doner, the unmatched
  2024 Lindir row, and Petrulla;
- three unresolved explicit Project Track history entries were removed: Papio
  Lindir and two `Andy | Unknown species` entries;
- the orphan test files under `Projects_Track/documents/A` and
  `Projects_Track/sop/A` were removed; `A` was not interpreted as `Anode`.

After cleaning, all five remaining Sample Track rows and all 32 remaining
explicit Project Track history IPIDs resolve to a core active/archived animal.
This did not address separate Flow, Cage, or Medi findings, which remain in
their respective Phase 2B scope.

## Project-store consistency

- `project_data.json` contains six projects:
  Anode, Backcrossing, Crossbreeding, OTOF-, Oakshield, and Zucht.
- `projects_history.json` contains five and omits Crossbreeding.
- Current animal records reference those six plus `Zeta-1`.
- `Zeta-1` therefore has current animal assignments but no project-data record.
- `projects_cache.json` and per-user `project_assignment_cache/**` contain
  visibility/filter/session projections and are regenerated.
- A stale `veto` cache directory remains although the current example account
  is `Veti`; it is cache evidence, not an authoritative user record.

## Managed files

Observed managed data:

- five files under `Plugins/Medi_Track/medi_track/**`;
- two files under `Plugins/Projects_Track/documents/**`;
- one file under `Plugins/Projects_Track/sop/**`.

Medi metadata currently links three files (Arwen and Azog). Two Thranduil files
are discovered only by directory scanning and have no JSON document record.
The Thranduil PDF is zero bytes. Project document/SOP ownership is inferred
from folder names; `Zucht` resolves, while folder `A` does not exactly match a
project. Identical test images occur under multiple owners and share hashes.

The later authorized cleaning removed both orphan `A` files/directories. The
statement above remains the frozen-snapshot fact.

Current metadata lacks stable document ID, stable owner ID, MIME type, byte
size, checksum, lifecycle status, and a guaranteed relative managed-storage
path. Medi metadata stores a path string; project files have no separate
metadata records.

## Permissions

Master Track defines stable permission IDs across core, master, network,
heritage, medical, cage, project, reports, plots, PdG, surgery, embryo, sample,
and flow namespaces.

Observed patterns:

- Cage Track maps structure/edit, inspection, and PDF actions to cage
  permissions.
- Flow Track maps create/edit/delete to explicit `flow_track.*` permissions.
- Heritage link mutation checks `heritage.edit_links`.
- Network send/edit checks `network.create_entry`/`network.edit_entry`.
- Surgery edit actions are enabled through `op_scheduler.use`; viewing/export
  uses `op_scheduler.view`.
- Project Track uses separate create/manage/assignment/archive/document/SOP
  permissions.
- Medi uses separate status, entry, upload, and view permissions in the UI.
- Embryo reference-data import writes `cranimetry_reference.json`, but the
  permission vocabulary has only `embryo_track.view`.
- Sample Track has one `sample_track.use` permission for both read and write.
- Animal Reports relies on core/tab gating for `reports.view` and
  `reports.write`; storage methods do not consistently receive an actor or
  permission context.
- PdG parameter storage writes from the converter path; the store method itself
  does not enforce `pdg_converter.use`.
- Some Medi document handling relies on a disabled button; the handler/store
  boundary itself is not uniformly permission-enforcing.

These are current enforcement locations only; no exploit or fix was attempted.

## Current measurement-import behavior

The following is observed behavior in the frozen source, not the Phase 2
target:

- The shared preview calls `_resolve_import_animal_key(...,
  create_missing=False)` and labels an unresolved preview row `New animal`.
- The preview displays at most the first 200 rows. Its current `new_count`
  therefore describes displayed rows, not necessarily the complete file.
- After confirmation, blood progesterone, urine PdG, weight, and sperm
  importers call `_resolve_import_animal_key(..., create_missing=True)`.
- An explicitly supplied but unknown Animal ID can open the
  species/full-birth-date identity-completion dialog.
- A completed dialog can call `_ensure_defaults_for_new`, create the core
  animal, and then attach measurements. If species and birth date are present
  in a legacy row, creation can occur without that dialog.
- `Animal ID` and `Sample ID` are parsed separately, but an unknown Animal ID
  currently remains an animal-creation trigger.
- Blood, urine, and sperm require `core.import` plus
  `core.edit_animal_research_data`; weight requires `core.import` plus
  `core.edit_animal_measurements`.

Issue #53 replaces this target behavior with non-mutating full-file
classification, visible skipped unknown rows, partial import for existing
animals, and no animal creation for any account.

## Institution-branding observation

The frozen application has generated PDF/report paths and packaged icon
resources, but no shared installation-scoped institution-branding
configuration, managed logo asset, branding permission, or common PDF header
service. This is the current-state gap addressed by Issue #52.

## Disabled-plugin behavior

- Seven bottom-group plugin keys have explicit per-user enable/disable state:
  `animal_reports`, `flow_track`, `projects_track`, `heritage_track`,
  `cage_track`, `medi_track`, and `steroid_track`.
- Their tabs/sidebar or feature controls are hidden/rebuilt when toggled.
- Master Track has separate global/session behavior and a lord-only internal
  toggle permission.
- Network, Embryo, PdG, Sample, and Surgery are primarily availability/
  permission-gated tool actions rather than participants in the same
  per-user disabled-plugin map.
- Capability helpers check both installed objects and selected disabled keys,
  but there is no single manifest-driven lifecycle contract for every plugin.
- Disabling hides access; it does not move, invalidate, or transform persistent
  plugin data.

## Backup/export discovery behavior

The current plugin backup collector combines manifest declarations with
extension-based directory scanning. Consequences:

- undeclared JSON/text files may be included without knowing whether they are
  cache, session, resource, or source of truth;
- image/PDF managed documents are not generally included by the fallback;
- Network Track is special-cased to only `chat_log.txt`;
- Embryo Track is special-cased to only `cranimetry_reference.json`;
- `.bak` files and compatibility artifacts require an explicit exclusion
  policy;
- export and backup cannot safely use the manifest `data_files` list as a
  canonical data contract.

## Manifest/store mismatches

| Plugin | Observed mismatch |
| --- | --- |
| Animal Reports | Declares `animal_reports_locked.json`, which is absent in the snapshot; `animal_report_data.json` carries mixed generated/manual content and two schema assumptions in code. |
| Cage Track | Manifest omits authoritative `inspection.json`. |
| Flow Track | Manifest declares no data files although it writes `flowtrack_daten.json` and `flowtrack_config.json`. |
| Medi Track | Manifest omits the managed `medi_track/**` document tree. |
| Projects Track | Manifest omits per-user assignment caches and managed `documents/**`/`sop/**`. |
| Surgery Planner | Manifest lists both upper- and lower-case schedules; code uses competing paths and formats, while the lower-case file is absent. |

## Security/storage observation

`users.enc` is authoritative user data with PBKDF2 password hashes and salts,
but the file wrapper is base64 obfuscation, not authenticated encryption. Its
fixed/machine-derived prefix is discarded on read rather than verified.
Both target profile databases must treat password hashes as security records
and must not rely on the `.enc` wrapper as a confidentiality boundary.

## Annex A — Path-level persistence inventory

This inventory is pattern-level where filenames are dynamic. `A` means
authoritative, `C` rebuildable cache/projection, `G` installation/shared
configuration, `U` user/session state, `M` managed payload, `R` packaged
resource/template, `O` generated export, and `T` transient/compatibility.

| Current path or pattern | Owner; readers/writers | Class and scope | Authoritative? | Phase 2 destination and package rule | Issue #49 path owner / evidence |
| --- | --- | --- | --- | --- | --- |
| `progtrack_daten.json` | Core; main application | A; installation | yes, mixed | normalized service records; include | profile database; source constants/save path |
| `progtrack_daten.lock`, `.bak`, temp/replace variants | Core persistence | T; process | no | exclude | runtime locks/temp; save helpers |
| `progtrack_settings.json` | Core settings | G; installation | yes for choices | typed installation config; optional config package | config path; settings loader |
| `disabled_plugins.json` | Core/plugin manager | G/U; installation fallback | yes for local choice | preference/config record; exclude from domain by default | config/preferences; plugin manager |
| `Plugins/core/animal_roles.json` | Core role registry | G; installation | yes | built-in semantic role references plus facility-owned custom-role records | config; role registry |
| `Plugins/core/role_block_presets.json` | Core role builder | G; installation | yes | versioned role-block configuration | config; role builder |
| `Plugins/core/identity_lifecycle_conventions.json` | Core identity/lifecycle | G; installation | yes | versioned controlled configuration | config; identity helpers |
| `Plugins/Resources/Animal_Origins.txt` and controlled catalogues | Core resources/settings | G/R; installation | yes when editable | controlled reference revision; include config/reference section | config/resource path; catalogue loader |
| packaged translations, icons, sounds, example workbooks | Core/plugins | R; package | no live data | keep with application; exclude from domain/interchange | packaged resources; manifests/source |
| user language override paths | Core localization | U/G; user/install | yes for override | preference/config service; optional config package | preferences/config; localization loader |
| `Plugins/Animal_Reports/*.json` | Animal Reports | A+C; installation | mixed | report overrides only; regenerate projection | profile database after cutover; plugin source |
| `Plugins/Cage__Track/cage.json`, `inspection.json` | Cage Track | A+G+U; installation/user | mixed | housing/inspection records; split config/UI state | profile database/preferences; cage stores |
| `Plugins/Embryo_Track/cranimetry_reference.json` | Embryo Track | G; installation | yes | versioned reference configuration | config/database; import/save callable |
| `Plugins/Flow_Track/flowtrack_daten.json`, `flowtrack_config.json` | Flow Track | A+D+G+U | mixed | reproduction records; split config/preferences | profile database/config; flow stores |
| `Plugins/Heritage_Track/heritage_animals.json`, `heritage_settings.json` | Heritage Track | A+C+D+G+U | mixed | heritage-only animals/links; regenerate projections; split settings | profile database/preferences; heritage stores |
| `Plugins/Master_Track/users.enc`, `jobs.json`, `settings.json` | Master Track | A security+G | yes | SecurityService and installation config; protected package only | profile database/config; Master stores |
| `Plugins/Master_Track/sessions/*.json` | Master Track | U; user/session | operational | backend session/preference; exclude from domain package | runtime/session or profile database |
| `Plugins/Master_Track/audit_*.log` | Master Track | A legacy evidence | historical | optional checksummed legacy artifact; never normalized trusted events | managed legacy/audit path |
| `Plugins/Medi_Track/medi_history.json` | Medi Track | A | yes | medical records/document metadata | profile database; Medi store |
| `Plugins/Medi_Track/medi_track/**` | DocumentService target | M | yes after reconciliation | managed document root; include payload/checksum | managed documents; directory scan/store |
| `Plugins/Network_Track/chat_log.txt`, settings | Network Track | A or disposable + G/U | decision-dependent | message records if retained; settings split | profile database/config/preferences |
| `Plugins/PdG_converter/data/models.json` | PdG converter | D | yes | analysis result records | profile database; model store |
| `Plugins/Projects_Track/project_data.json`, `projects_history.json` | Projects Track | A | yes | project/assignment/history records | profile database; project stores |
| `Plugins/Projects_Track/projects_cache.json`, `project_assignment_cache/**` | Projects Track | C/U | no | regenerate; exclude | cache path; project cache code |
| `Plugins/Projects_Track/documents/**`, `sop/**` | DocumentService target | M | yes after reconciliation | managed documents; include payload/checksum | managed documents; project file handlers |
| `Plugins/Sample_Track/organs.json`, `other.json` | Sample Track | A | yes | sample/aliquot/storage records | profile database; sample store |
| `Plugins/Sample_Track/samples_*.json` | Sample Track | R/reference | no sample truth | packaged/versioned reference catalogue | resources/config; sample loader |
| Surgery plan/config/block-day JSON variants and `.bak` files | Surgery Planner | A+G+T | mixed | planning records/config; exclude compatibility backups | profile database/config/temp; planner constants |
| `QSettings` organization/application keys for geometry, tabs, filters, plugin UI | Core/plugins | U | no domain truth | UserPreferenceService; exclude by default | preferences path; `QSettings` call sites |
| future institution name/logo/toggle | Settings/PDF services | G + managed config asset | yes | installation branding record + config asset; optional protected config package | config/config-assets; Issue #52 target |
| file-picker input and `Resources/ExampleFiles/*.xlsx` | measurement import/resources | transient input + R | no live truth | parse to transient import plan; templates stay packaged | input/resource; import call sites |
| generated PDF/XLSX/CSV/PNG/JSON output directories | exporting module/user | O | no unless registered | exclude; user-selected export path | exports; export dialogs |

Every concrete Phase 2B path must be registered under exactly one runtime-path
owner. New fallback writes beside code are forbidden. Standalone SQLite stores
its database and managed roots locally; Shared PostgreSQL stores domain data
on the server while each client still uses local cache/export/temp paths and
the installation's configured managed-payload service.
