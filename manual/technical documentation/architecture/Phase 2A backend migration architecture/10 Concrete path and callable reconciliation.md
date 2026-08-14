# Concrete path and callable reconciliation

Status: **complete for Phase 2A approval**

Baseline:

- repository: `Q:\GitHub\ProgTrack-Release`;
- branch: `Phase-0.1.2`;
- commit: `3fc22583799b6ed394544035f1387e1c759c3aea`;
- verifier: `phase2a_readonly_audit.ps1` version `1.0.1`;
- result schema: `phase2a-audit-evidence/1`;
- result: `phase2a_audit_result_clean.json`;
- result SHA-256:
  `193ac3c6b09b55350daeba07ffc3c6015c04880d98151dda7911d71dcb34ba2e`.

The verifier completed with exit code `0`, `passed = true`, and an empty Git
status before and after. `_internal` is intentionally excluded from content
analysis because it is the bundled runtime. The complete Git-status comparison
still detects worktree changes involving it.

## Normative concrete path annex

The `persistence_inventory` array in `phase2a_audit_result_clean.json` is the
normative path-level annex. It contains one row, relative path, byte count,
SHA-256, and raw classification for every tracked persistence/resource
candidate in scope. It contains 134 rows and no path without a classification.

The `managed_payloads` array separately records all six files found under the
managed Medi/Project document roots, including byte count, SHA-256, and
zero-byte status.

Raw inventory classes reconcile to the architecture as follows:

| Raw verifier class | Rows | Final Phase 2 disposition |
| --- | ---: | --- |
| `authoritative_mixed_core` | 1 | Split into canonical animal, measurement, event, project-link, preference, and configuration entities through the services in document 03. |
| `data_or_configuration_review_required` | 77 | Resolve by the path rules below; no row remains generically owned after this reconciliation. |
| `managed_document_payload` | 4 | `DocumentService` payload with typed owner, relative path, MIME, byte size, checksum, and state machine from documents 04/05. |
| `packaged_import_template` | 4 | Read-only existing-animal measurement templates; not authoritative data or interchange records. |
| `packaged_or_controlled_resource` | 14 | Packaged scientific/control resource. Editable controlled catalogues become versioned shared configuration; example/reference workbooks remain resources. |
| `rebuildable_cache_or_user_state` | 1 | Rebuildable Project Track cache; never authorization or interchange truth. |
| `resource_managed_payload_or_output_review_required` | 21 | Root `icons/**` and screenshot are packaged resources; `Example_Craniometry.csv` is an import/reference example. None is a managed domain document. |
| `security_or_configuration` | 3 | Users/password metadata and jobs/settings move through `SecurityService`/`ConfigurationService`; protected interchange rules apply. |
| `session_or_user_state` | 4 | Existing files are source evidence. Live sessions are excluded from transfer; approved durable preferences are exported separately. |
| `shared_configuration` | 5 | Versioned shared configuration and localized labels through `ConfigurationService`. |

### Resolution rules for the 77 generic rows

| Concrete path rule | Final owner/classification |
| --- | --- |
| `info*.json` | Historically named HTML About-page resources; parse as text, never as JSON data. |
| `lang/messages_*.json`, `Plugins/*/lang/*.json`, `Plugins/Sample_Track/samples_*.json` | Packaged localization/control vocabulary; not facility records. |
| `Plugins/*/manifest.json` | Packaged plugin manifest/resource; entry modules were verified for all 13 plugins. |
| `third_party_licenses/**`, `source/launcher/windows/hiddenimports.txt`, `source/launcher/windows/launcher_version_info.txt` | Packaged build/license metadata. |
| `Plugins/Animal_Reports/animal_report_data.json` | Authoritative report override/data source → `ReportService`. |
| `Plugins/Cage__Track/cage.json`, `inspection.json` | Authoritative housing/inspection data → `HousingService`. |
| `Plugins/Embryo_Track/cranimetry_reference.json` | Versioned scientific reference configuration → `ReferenceDataService`. |
| `Plugins/Flow_Track/flowtrack_config.json` | Shared scientific/plugin configuration → `ConfigurationService`. |
| `Plugins/Flow_Track/flowtrack_daten.json` | Authoritative reproduction workflow data → `ReproductionService`. |
| `Plugins/Heritage_Track/heritage_animals.json`, `heritage_settings.json` | Pedigree/heritage-only animal data and durable graph settings → `AnimalService`/preferences. |
| `Plugins/Master_Track/permissions_labels.json` | Packaged global semantic-permission labels. Version 1.0.1 verified exact 73/73 coverage in all four languages. |
| `Plugins/Medi_Track/medi_history.json` | Authoritative medical records/document metadata → `MedicalService`/`DocumentService`. |
| `Plugins/Network_Track/chat_log.txt` | Legacy chat explicitly excluded; target chat starts empty. |
| `Plugins/Network_Track/network_track_settings.json` | Shared/durable configuration according to setting scope; transient UI values excluded. |
| `Plugins/PdG_converter/data/models.json` | Versioned analysis model parameters/results → `AnalysisService`. |
| `Plugins/Projects_Track/project_data.json`, `projects_history.json` | Authoritative project/history records → `ProjectService`. |
| `Plugins/Sample_Track/organs.json`, `other.json` | Authoritative sample/aliquot/storage records → `SampleService`. |
| `Plugins/Surgery_Planner/*.json` | Planning configuration, block days, staging/published plans → `PlanningService`. |

This closes the path-level acceptance criterion: every row is preserved in the
machine-readable annex and every generic row is resolved by an explicit rule
above.

## Exact write-callable reconciliation

Verifier 1.0.1 found 154 write primitives inside 73 Python callables. Simple
serialization (`json.dumps`) and non-write `Popen` occurrences are excluded.
The table names every callable exactly once. Low-level persistence helpers
inherit the authorization and scope of their calling command; they are not
independent user actions.

| Source / exact callables | Current enforcement interpretation | Phase 2 target command, permission, and disabled behavior |
| --- | --- | --- |
| Animal Reports: `AnimalReportsWidget._save_locked_entries`, `._load_data`, `._save_data` | Tab/handlers use `reports.view` and `reports.write`; stores have incomplete actor context. | `ReportService.query/update_override`; `reports.write` for mutation; project visibility and revision/audit; tab unavailable when disabled. |
| Cage Track: `CageStore.save_data`, `CageTrackWidget._save_inspections` | Low-level cage/inspection stores inherit UI `cage.*` decisions. | `HousingService` commands with `cage.edit`, `cage.assign_locations`, `cage.manage_rooms_buildings`, or `cage.record_inspection`; node/inspection lock, revision, event; tab unavailable when disabled. |
| Embryo Track: `EmbryoTrackerWidget._load_excel_data` | Import is reachable under current Embryo view capability but lacks a distinct configuration-write gate. | `ReferenceDataService.import`; new grantable reference-config permission; installation scope, config lock/revision/event; action unavailable when disabled. |
| Flow Track: `FlowTrackWidget._save_settings`, `._save_flow_track_data`, `._export_json` | Launch/action permissions are `flow_track.open/use/create/edit/delete`; settings/export currently share plugin context. | `ReproductionService` plus `ConfigurationService`/export command; preserve semantic permissions and project scope; aggregate revision/lock/events; tab/action unavailable when disabled. |
| Heritage Track: `HeritageStore._atomic_write` | Internal store used by pedigree mutation; caller must hold `heritage.edit_links`. | `AnimalService.set_relationship`; `heritage.edit_links`; deterministic multi-animal lock order/revisions/event; tab unavailable when disabled. |
| Master security/config: `UserDB.save`, `MasterTrackPlugin._save_settings`, `.save_job_bundles` | Protected Master UI and semantic user/job permissions; persistence helper itself has no actor boundary. | `SecurityService`/`ConfigurationService`; corresponding `master.*` permission; installation scope, user/config locks/revisions, security/domain events; protected Master behavior. |
| Master audit/log operations: `MasterTrackPlugin.audit`, `._write_log_locations_file`, `.open_logs_folder` | Audit/log-location maintenance is internal; folder opening requires `master.view_audit`. | Structured `AuditService` events; runtime log metadata intentionally ungated internally; log-folder UI requires `master.view_audit`; no domain mutation on denial. |
| Master sessions: `SessionManager.__init__`, `.save` | Internal authentication/session persistence; not a grantable user action. | `SecurityService` session commands; authenticated lifecycle/security events; sessions excluded from interchange. |
| Medi Track: `_copy_document_files_to_directory`, `MediStore.save`, `MediTrackWidget._export_animal_to_xlsx`, `._animal_docs_folder`, `._on_add_document_clicked` | Current UI uses `medi_track.*`; several low-level file helpers rely on caller gating. | `MedicalService`/`DocumentService`/export commands with matching `medi_track.*`; visible-animal scope, record revision/lock, document state machine and operation events; tab unavailable when disabled. |
| Network Track: `NetworkTrackWidget._save_settings`, `._init_chat_log`, `._replace_message_in_log`, `._send_message` | `network.view/create_entry/edit_entry` at UI; file helpers inherit caller. | `MessageService` query/send/edit and scoped configuration; matching permissions, append/revision/event; action unavailable when disabled. Legacy log is not imported. |
| PdG converter: `PdGConverterPlugin.__init__`, `.save_parameters` | Launch is gated by `pdg_converter.use`; model persistence has no separate actor check. | `AnalysisService.fit/save`; `pdg_converter.use`; visible-animal/input-revision scope and event; feature/action unavailable when disabled. |
| Project history/cache: `HistoryStore._save`, `ProjectsTrackPlugin._cache_path_for_identity`, `._save_cache`, `._save_session_state`, `.invalidate_user_caches` | History writes inherit project command; caches/session state are non-authoritative and never authorization. | `ProjectService` owns history; cache helpers are intentionally ungated derived/client state and excluded from authorization/interchange; tab unavailable when disabled. |
| Project authoritative mutation: `ProjectsTrackPlugin.on_animal_added`, `ProjectTrackTab._save_data` | Current project handlers use `project.create/manage/project_assign/...`; store helpers do not enforce independently. | `ProjectService` matching grantable permission; project visibility, lock/revision/domain event; tab unavailable when disabled. |
| Project documents: `ProjectTrackTab._on_doc_upload`, `._on_sop_upload` | Explicit `project.upload_document` / `project.upload_sop` UI checks. | `DocumentService.stage/activate` with the same permissions, project lock/revision and document/operation events; action unavailable when disabled. |
| Sample Track: `JsonStore.write` | All Sample UI mutation currently shares broad `sample_track.use`; helper inherits caller. | Split `SampleService` read/write/storage/export permissions in Phase 2B; animal/project scope, sample revision/lock/event; action unavailable when disabled. |
| Surgery module helpers: `save_block_days`, `save_schedule_to_plugin`, `save_plugin_settings`, `export_schedule_to_csv` | Read/open uses `op_scheduler.view`; mutation/config/export uses `op_scheduler.use`. | `PlanningService` query/edit/config/export; matching permission, project/config scope, plan/config lock/revision/events; action unavailable when disabled. |
| Surgery widget: `GanttWidget.generate_new_schedule_workflow`, `._load_saved_schedule`, `._persist_temp_schedule`, `._generate`, `._on_dot_clicked.save_changes`, `._save_schedule_as`, `._save_schedule` | Generated/staging/published plan writes inherit `op_scheduler.use`; file-export dialog is also under use permission. | `PlanningService` staging/publish/edit/export commands; plan revision/lock and events; temporary state separated from durable plan; action unavailable when disabled. |
| Historical identity rewrite: `rewrite_animal_reference_file`, `move_medi_document_folder` | Phase 1 identity-edit path; caller used privileged core identity editing. | No Phase 2 update command. Immutable IPID/name/species/birth date/origin blocks invocation at service and DB level; denied attempt may create security event. |
| Shared configuration helpers: `AnimalRoleRegistry.save_roles`, `save_conventions`, `_atomic_write_lines`, `RoleBlockPresetRegistry.save_presets` | Settings UI uses `core.manage_animal_roles`, protected identity/catalog controls, or role-setup permissions; helpers inherit caller. | `ConfigurationService` commands with the corresponding grantable semantic permission; installation/config lock, revision, event; Settings action unavailable when component disabled. |
| Core runtime/bootstrap: `<module>`, `_configure_logging`, `try_acquire_lock` | Required local runtime/log/process-safety initialization; no user domain action. | `RuntimePathService`/Standalone process lock; intentionally ungated infrastructure, excluded from domain interchange; startup fails safely if unavailable. |
| Core plugin/personal/shared settings: `ProgTrackApp._save_disabled_plugins`, `._get_user_style_settings_file`, `._save_user_style_settings`, `._save_settings`, `._save_role_label_overrides` | Plugin toggle is protected by plugin-control policy; personal style settings belong to current user; shared role labels use role-management settings. | `ConfigurationService` and preference service; semantic permission for shared mutation, authenticated owner for personal preference; revision/event where durable/shared; disabled component action unavailable. |
| Core persistence/recovery: `ProgTrackApp._read_json`, `._write_json`, `._save_trace` | Internal core storage/recovery/diagnostic helpers. They inherit the initiating command; trace/log writes are intentionally ungated diagnostics. | Repository/adapters behind domain services; no UI direct persistence; atomic domain audit from caller; diagnostics excluded from canonical records. |
| Core report mutation: `ProgTrackApp._prepare_unlocked_report_rows_for_reentry`, `._save_report_data` | Explicit `reports.write` path. | `ReportService.update_override`; `reports.write`, project scope, revision/lock/domain event; tab unavailable when disabled. |
| Core database/export: `ProgTrackApp._save_database`, nested `._copy_within_scope` | File-menu export uses `core.export`; nested copy inherits it. | `InterchangeService.export`; `core.export`, consistent snapshot, correlated operation event; unavailable when required service/component disabled. |
| Launcher: `setup_environment`, `execute_script` | Runtime/fault-log initialization and crash logging; no user domain action. | `RuntimePathService` diagnostics; intentionally ungated, local installation state, excluded from interchange. |

### Reconciliation result

- 73 of 73 callables are listed above.
- 154 of 154 detected write primitives are owned by those callables.
- An AST-derived set comparison of the verifier callables against this table
  returned 73 actual, 73 mapped, zero missing, and zero extra names.
- Every user-triggerable mutation/export maps to a target service and semantic
  permission or to a documented new Phase 2 permission.
- Every low-level helper is explicitly marked as caller-inherited or
  intentionally ungated infrastructure/cache/diagnostic state.
- Disabled behavior is defined at the feature/action boundary; low-level
  helpers are unreachable when their owning command is unavailable.
- Five current measurement `create_missing=True` call sites remain a known
  Phase 2B finding owned by Issue #53, not an unmapped Phase 2A write action.
- Direct database-driver/SQL call sites remain zero.

This closes the callable/action-level acceptance criterion without changing
ProgTrack code.
