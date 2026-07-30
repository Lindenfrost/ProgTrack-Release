# Plugin capability and storage matrix

## Classification legend

- **A** — authoritative domain/security record; must map to canonical records.
- **D** — authoritative derived scientific result worth preserving.
- **C** — disposable/rebuildable cache or projection.
- **G** — shared plugin/application configuration.
- **U** — per-user/session/UI state.
- **R** — packaged resource, language catalogue, example/template, or sound.
- **M** — managed attachment/document payload.
- **O** — generated user-selected export; not live storage.
- **T** — transient lock/temp/backup/compatibility artifact.

“Authoritative” means the current program cannot reconstruct the information
without losing a user decision, history, or scientific/operational result. It
does not mean the current file format should survive Phase 2B.

## Summary matrix

| Plugin | Owner / entry point / current key | Current stores and classification | Animal/project reference | Permission and disabled behavior | Export and profile cutover |
| --- | --- | --- | --- | --- | --- |
| Animal Reports | `Animal_Reports/animal_reports.py`; `animal_reports.AnimalReportsPlugin`; `animal_reports` | `animal_report_data.json` **A+C** (manual edits/locks mixed with aggregate); optional `animal_reports_locked.json` **A** but absent | IPID-keyed; reads core animal dictionary; selection items retain key/name; permission-scoped report/export candidates inherit current project visibility | `reports.view` tab access; `reports.write` core gating; per-user disable hides tab; store methods lack independent actor context | PDF/XLSX report output **O**. Split manual override/locked-entry records from regenerated report projection. |
| Cage Track | `Cage__Track/cage_track_widget.py`, `cage_store.py`; `CageTrackPlugin`; `cage_track` | `cage.json`: structures/occupancy/history **A**, project colors **G**, UI state **U**; `inspection.json` **A** | Occupant/history maps keyed by IPID; project colors by project name; persisted occupants include all scopes, so project visibility depends on widget/app filtering rather than the store | `cage.view`, `record_inspection`, `assign_locations`, `manage_rooms_buildings`, `edit`, `export_pdf`; per-user disable hides tab | PDF **O**. Normalize housing nodes, occupancy intervals, movements, and inspections; do not export UI state as domain data. |
| Embryo Track | `Embryo_Track/embryo_track.py`; `EmbryoTrackerWidget`; no `_disabled_plugins` key (`embryo_tracker_action`) | `cranimetry_reference.json` **G/R** (editable institutional reference); `Example_Craniometry.csv` **R** | Reads the caller-selected core animal and embedded embryo/reproductive measurements; no animal-keyed plugin store; project scope is inherited from the launch selection | Only `embryo_track.view`; no separate permission for overwriting reference data; action is permission-gated, not in seven-key disable map | Diagnostics are UI/generated. Decide governance and import permission for reference curves; canonical package may include approved reference-data version, not example CSV. |
| Flow Track | `Flow_Track/flow_track_widget.py`; `FlowTrackWidget`; `flow_track` | `flowtrack_daten.json` **A+D**; `flowtrack_config.json` shared scientific/display settings **G** plus visibility/UI state **U** | Donor/surrogate maps and fields use IPID/name; transfer IDs embed IPID/date; project visibility/current selection depends on parent-app scope, not stored project IDs | `flow_track.open/use/create/edit/delete`; per-user disable hides tab; data save validates and checks edit, settings write is separate | JSON/XLSX/PDF/graph exports **O**. Normalize donation, surgery, embryo, transfer, and cryostorage records with stable IDs; split shared config from user view state. |
| Heritage Track | `Heritage_Track/heritage_track_widget.py`, `heritage_store.py`; `HeritageTrackPlugin`; `heritage_track` | `heritage_animals.json`: 227 core projections **C**, 16 heritage-only animals/links **A**, inbreeding values **C/D**, genotype colors **G**; `heritage_settings.json` **U/G** | IPID keys and four IPID parent fields; project filtering/current project are read from core app records/state | `heritage.view/edit_links/export`; per-user disable hides tab | Graph images **O**. Do not import duplicate core projections. Convert heritage-only/dummy animals to stable animal entities and all pedigree links to relationship records. Treat layout as user state. |
| Master Track | `Master_Track/plugin.py`; `MasterTrackPlugin`; `master_track` | `users.enc` **A security**; `jobs.json` **G/A permission config**; `settings.json` **G**; sessions **U**; audit logs **A legacy evidence**; permission labels **R** | Users referenced by login name in projects/audit/sessions; no stable user UUID in files | Central role/job/direct-permission source. Special lord-only global enable/disable behavior | Audit export **O**. Move users/roles/jobs/grants to security repositories; use secure password-hash records. Sessions and entity locks are operational state. Preserve legacy logs separately rather than pretending they are normalized events. |
| Medi Track | `Medi_Track/medi_track_widget.py`; `MediTrackWidget`; `medi_track` | `medi_history.json` entries/doc metadata **A**; condition catalogues **R**; `medi_track/**` **M** | Animal blocks keyed by IPID; entries have UUID strings; documents use path strings; project/history displayed from core; exports explicitly ask the app for permission-visible animals | `medi_track.view/filter_use/add_docs/upload_document/delete_document/status_enable/status_manage`; per-user disable hides tab; some handlers rely on UI gating | Medical PDF/document export **O+M copies**. Normalize medical entries/issues and stable document links; import folder-only files through reconciliation/quarantine. |
| Network Track | `Network_Track/network_track.py`; `NetworkTrackWidget`; no `_disabled_plugins` key (`network_track_action`) | `chat_log.txt` **A** if chat retention is required; settings **G/U**; `notification.wav` **R** | Actor stored as display-name text; no animal/project references | `network.view/create_entry/edit_entry`; tool permission-gated, not in seven-key disable map | No domain export. Map retained messages to stable message/actor records, or explicitly define chat as ephemeral and exclude. Polling a shared text file is replaced by backend queries/notifications. |
| PdG converter | `PdG_converter/plugin.py`; `PdGConverterPlugin`; no independent disable key (capability/action gated by `pdg_converter`/Steroid availability) | `data/models.json` **D** (fitted parameters and provenance fields) | Map keyed by IPID; reads embedded core measurements for the selected animal; project visibility is inherited from the calling UI | `pdg_converter.use`; launch gated, store method itself has no actor/permission boundary; steroid feature gate affects availability | Plot/table output **O**. Store versioned fitted-model result linked to stable animal and source measurement revisions. |
| Projects Track | `Projects_Track/ProjectsTrack_plugin.py`, `project_track_tab.py`; `ProjectsTrackPlugin`; `projects_track` | `project_data.json` **A**; `projects_history.json` **A**; global and per-user caches **C/U**; `documents/**`, `sop/**` **M** | Project name is identity; animal history uses IPID; user assignments use login names; core animal `project` duplicates active membership | `project.view/view_all/create/manage/project_assign/archive/...document/...sop`; per-user disable hides tab/sidebar | Project reports/docs **O/M**. Create stable project/user/animal links, unify current assignment and history, exclude caches, and create document metadata. |
| Sample Track | `Sample_Track/sample_track_widget.py`; `__init__.initialize`; no `_disabled_plugins` key (`sample_track_action`) | `organs.json`, `other.json` **A**; `samples_*.json` **R**; `QSettings` geometry **U** | Rows use composite animal name and public animal ID snapshots; sample number is separate; autocomplete/automatic creation read the broad app animal dictionaries rather than a project-scoped repository | One `sample_track.use` permission covers access and mutation; tool permission-gated, not in seven-key disable map | PDF **O**. Normalize sample, sample type, aliquot, storage location, and animal link; preserve both sample ID and animal public ID with distinct meanings. |
| Steroid Track | `Steroid_track/__init__.py`; `initialize`; `steroid_track` | No plugin data file; operates as a feature gate over core records/settings | Reads/writes core measurement/reproductive fields through core UI paths; project visibility follows the core animal surface; depends on internal animal role IDs | Per-user disable rebuilds hormone/reproductive controls; uses core permissions plus dependent plugin permissions | No independent canonical records beyond the core measurement/event entities it exposes. |
| Surgery Planner | `Surgery_Planner/surgery_planner.py`; `SurgeryPlannerPlugin`; no `_disabled_plugins` key (`op_planner_action`) | block days **A/G**; pre-plan and published schedules **A**; config **G**; lower-case legacy schedule path **T/A unresolved**; `.bak` **T** | Schedule entries use IPID plus name snapshots; actor is username/display text; animal loader reads the full core JSON directly rather than a project-scoped service | `op_scheduler.view/use`; action permission-gated, not in seven-key disable map | CSV/XLSX/PNG/JSON **O**. Resolve staging/published lifecycle and case-sensitive path conflict; use stable plan/event/animal/user IDs. |
| Cross-cutting measurement imports | Core import methods exposed from role-tab measurement blocks; Steroid Track gates hormone/reproductive availability; Sample Track consumes resulting sample identifiers but does not own all four callers | selected XLSX is transient input; preview/import plan is not persisted today | Current preview resolves with `create_missing=False`, but accepted blood, urine, weight, and sperm paths resolve with `create_missing=True`; unknown Animal ID may trigger species/birth dialog and core animal creation | blood/urine/sperm: `core.import` + `core.edit_animal_research_data`; weight: `core.import` + `core.edit_animal_measurements`; role-block visibility also applies | Issue #53: complete-file immutable plan, unknown rows shown/skipped, partial success for existing animals, no creation for any role, safe retry and operation audit |
| Institution branding target | Shared Settings, configuration, asset, and PDF-rendering services; not currently implemented | installation branding record **G** plus logo **managed configuration asset**; neither **M** animal/project document nor **R** packaged icon | no animal/project ownership; facility-owned configuration and asset IDs | grantable `settings.manage_institution_branding`; Lord/Master defaults, Manager configurable | Optional protected configuration/backup inclusion; shared PDF header, missing/corrupt-logo fallback; Issue #52 |

## Store-by-store canonical destination

| Current store/content | Canonical destination | Export rule |
| --- | --- | --- |
| `progtrack_daten.json` animals/archived | animal, identifiers, roles, status/lifecycle, measurements, procedures, relationships, project assignments | Export normalized records; never one opaque JSON blob. |
| Core role registry and controlled catalogues | role definition and reference-data/config records | Include versioned semantic IDs; translations remain resources. |
| Animal Reports edits/locked data | report override / timeline override | Include only manual decisions; regenerate aggregate/report cache. |
| Cage structures, occupants, movement | housing node, occupancy interval, movement event | Include; validate parent hierarchy and one current location. |
| Cage inspections | housing inspection plus inspected-scope links | Include; stable actor and scope IDs. |
| Cranimetry reference | reference dataset/version | Include only the active approved reference set if reviewer chooses installation-scoped export. |
| Flow manual data | gamete donation, donor procedure, embryo, embryo transfer, cryostorage event | Include; generated transfer key is a legacy source ID only. |
| Heritage core copies | none | Exclude as duplicate cache. |
| Heritage-only animals and parent links | animal (`record_kind=heritage_only`) and animal relationship | Include. |
| Heritage layout/collapse/grid | user/plugin preference | Exclude from domain package; optional user-preference section only. |
| Master users/permissions | user/security configuration | Excluded from ordinary domain package by default; optional protected seed/security section after review. |
| Master sessions | session/user preference | Exclude from canonical domain package. |
| Legacy audit text | legacy audit artifact | Preserve separately when required; do not import as trusted normalized event. |
| Medi history | medical issue/event/note and provenance | Include. |
| Medi files | document metadata plus payload | Include after owner reconciliation and checksum validation. |
| Network chat | network message | Include only if chat retention decision is “authoritative”. |
| PdG fitted models | analysis model result | Include with model version and input-revision provenance. |
| Project data/history | project, protocol metadata, user assignments, animal assignment history | Include and reconcile duplicate current/history representation. |
| Project caches | none | Exclude and regenerate. |
| Project documents/SOP | document metadata plus payload | Include after owner reconciliation. |
| Sample records | sample, aliquot, sample-storage assignment | Include resolvable records; quarantine unresolved rows. |
| Sample localized catalogues | resource/reference data | Package as application resources or versioned reference data, not sample records. |
| Surgery block days/config/schedules | planning calendar/config, plan, schedule event | Include authoritative plans; exclude compatibility backups. |
| `.bak`, `.tmp`, lock files | none | Exclude. |

## Cross-plugin cutover rules

1. Plugins receive service interfaces, not the main animal dictionary or a
   database connection.
2. Animal links cross the boundary as immutable IPID. Durable
   installation-created project/user/cage/sample/document/event links use
   facility-owned record IDs `<facility_tag>:<uuid>`. Permissions, built-in
   roles, plugin keys, schemas, statuses, units, analytes, specimen types, and
   other frozen protocol vocabulary use global semantic IDs. Names, usernames,
   public IDs, role labels, and dates are snapshots/search fields.
3. Every mutation carries actor/session, permission intent, expected revision,
   correlation ID, and transaction context.
4. Project visibility is applied by the query/service layer before records are
   returned; a UI filter is not a security boundary.
5. Plugin-disabled state blocks UI/launch/callback entry points but never
   changes data ownership or causes deletion.
6. Generated views and caches declare their derivation and can be dropped.
7. Manifests may describe capabilities/resources, but canonical export uses a
   reviewed storage registry/service contract, not extension scanning.

Built-in animal role IDs are global semantic IDs. Custom animal roles, custom
job bundles, and other installation-created definitions are facility-owned
configuration records with stable facility-owned record IDs; their visible
labels and local keys are not portable foreign keys.

## Annex A — Action, permission, service, lock, and audit map

The rows below define action families; each Phase 2B callable generated from a
family inherits the same command-boundary requirements. `Grantable` means the
permission is present in the editable catalogue and all supported language
labels. Internal-only permissions are never silently made grantable.

| Module / action family | Kind | Current UI/launch enforcement | Current store enforcement | Target service command and permission | Scope; lock/revision; audit | Disabled behavior |
| --- | --- | --- | --- | --- | --- | --- |
| Core create animal | mutation | New Animal visibility + `core.create_animals` | handler checks vary | `AnimalService.create`; `core.create_animals` (grantable) | visibility rules; transaction revision; domain event | callback unavailable |
| Core edit animal identity | mutation | identity fields/role gates | current rewrite helpers can mutate keys | no update command after creation; only create/delete permissions | immutable DB constraint; denied attempts security-audited | callback unavailable |
| Core edit measurements/events | mutation | measurement/research permissions and role blocks | core record save | `MeasurementService.add/update`; corresponding grantable semantic permission | project/entity scope; animal revision or append contract; domain event | feature controls hidden/disabled |
| Measurement XLSX import: blood/urine/sperm | import | role block + `core.import` + `core.edit_animal_research_data` | current accepted path can create animal | `MeasurementImportService.plan/commit`; both permissions; never `core.create_animals` | full-file plan; transactional revalidation; operation event; no long editor lock | Steroid/role capability unavailable |
| Measurement XLSX import: weight | import | role block + `core.import` + `core.edit_animal_measurements` | current accepted path can create animal | same service; weight permissions; never create animal | same Issue #53 contract | role capability unavailable |
| Core archive/delete animal | mutation | archive/delete permissions | core save/deletion helpers | `AnimalService.archive/delete`; grantable permissions | entity lock + revision; correlated dependent checks; domain event | callback unavailable |
| Core structured domain import/export | import/export | `core.import`/`core.export` | file/handler-specific | `InterchangeService.preview/commit/export`; grantable | installation/project scope; operation events with correlation | action unavailable |
| Animal Reports view/edit/export | read/mutation/export | `reports.view`, `reports.write` | store lacks uniform actor context | `ReportService.query/update_override/export` | project scope; override revision/lock; mutation/export events | tab hidden |
| Cage view, assign, structure edit, inspection, PDF | read/mutation/export | `cage.*` mappings | cage stores partially independent | `HousingService` commands with matching grantable IDs | project/facility scope; node/inspection lock + revision; event/export outcome | tab hidden |
| Embryo view and reference import | read/config import | currently only `embryo_track.view` | reference file write lacks separate gate | `ReferenceDataService.import`; new grantable config permission | installation scope; config lock/revision; event | action unavailable |
| Flow open/create/edit/delete/config/export | open/mutation/config/export | `flow_track.open/use/create/edit/delete` | save checks edit; config separate | `ReproductionService` commands; preserve semantic split | project scope; aggregate revision/lock; events/exports | tab hidden |
| Heritage view/edit links/export | read/mutation/export | `heritage.view/edit_links/export` | link save checks mutation | `AnimalService.set_relationship`; matching permission | animal/project scope; multi-animal lock order + revisions; event | tab hidden |
| Master users/jobs/permissions/settings | mutation/config | Lord/Master/Manager and semantic permissions | Master stores | `SecurityService`/`ConfigurationService` | installation scope; user/config locks + revisions; security/domain events | Master-specific protected behavior |
| Medi view/filter/status/entry/document/PDF | read/mutation/document/export | `medi_track.*` | some handlers rely on UI gating | `MedicalService`, `DocumentService`, export commands | visible animals; record locks/revisions; document/operation events | tab hidden |
| Network view/send/edit | read/mutation | `network.view/create_entry/edit_entry` | text log path | `MessageService` commands | authorized channel scope; append/revision; event | action unavailable |
| PdG use/model save/export | mutation/derived/export | `pdg_converter.use` at launch | model store lacks actor gate | `AnalysisService.fit/save/export`; grantable | visible animal; input revisions; event/export outcome | action unavailable with feature |
| Project view/manage/assign/archive/docs/SOP/export | read/mutation/document/export | `project.*` permissions | mixed store/handler gates | `ProjectService`/`DocumentService` commands | project visibility; project lock/revision; domain/document/export events | tab hidden |
| Sample open/create/edit/storage/export | read/mutation/export | one `sample_track.use` | shared broad permission | split `SampleService` semantic read/write/export permissions in Phase 2B | project/animal scope; sample revision/lock; event/export | action unavailable |
| Steroid feature gate | capability | disabled plugin + dependent permissions | no independent store | no authorization permission by itself | commands remain governed by MeasurementService | controls rebuilt/hidden |
| Surgery read-only open, edit/use, config, export | open/mutation/config/export | `op_scheduler.view` differs from `op_scheduler.use` | file writes under planner | `PlanningService` query/edit/config/export | project scope; plan/config locks + revisions; events/exports | action unavailable |
| Institution branding manage/preview | configuration | target Settings section | not implemented | `ConfigurationService.set_branding`; `settings.manage_institution_branding` (grantable) | installation scope; config lock/revision; managed asset + domain event | Settings item unavailable if component disabled |

All launch handlers repeat the UI capability check defensively, but the service
command is the authority. A denied command performs no domain write; a
security-sensitive denial may still write a separate immutable security
event. Project visibility comes from the service query/command contract, not
from Project Track caches. Read-only open permissions may intentionally differ
from edit/use permissions, as in Surgery Planner.
