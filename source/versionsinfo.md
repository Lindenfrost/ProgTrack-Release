# ProgTrack version inventory and release patch map

Status: active release-control document
Audit date: 2026-11-21
Repository: `Q:\GitHub\ProgTrack-Phase3-work` for the 0.3.0 candidate
Audit scope: tracked ProgTrack source, data, documentation, manifests and
launcher sources. The bundled `_internal/` tree is deliberately excluded: it
contains third-party runtime material and its package versions are not
ProgTrack versions.

## Single target value

For a release/version-bump pass, change only the value on the next line first:

```text
TARGET_VERSION = "0.3.0"
```

The value is the current ProgTrack application/release version. The tables below
are the patch manifest: apply current-version substitutions only to rows whose policy is `PATCH_CURRENT` or
`PATCH_FILENAME_AND_REFERENCES`. Do not perform a global replacement of
`0.2.1`, `0.2.2`, or another number.

This file (`source/versionsinfo.md`, line 16) is itself the control document;
its `TARGET_VERSION` value is the only number intended to be edited for a
future release pass before applying the manifest. The active payload filename
must be discovered from the repository root (there must be exactly one file
matching `ProgTrack.v.*.py`); it is never inferred from a hard-coded previous
version in this document.

## ProgTrack 0.3.0 candidate inventory

The reviewed paths, platform membership, owners, effective compatibility floors
and sources are recorded in [release_inventory.json](../release_inventory.json).
Both packagers ship this inventory at the payload root. Technical Phase 2A
evidence, unchanged artwork, legal notices, data shapes and third-party parts
are represented in this file without modifying their contents. A null minimum
for raw artwork records a reviewed asset with no ProgTrack host API dependency.
The launcher,
the bundled CPython runtime and the application each retain their own versions.

### Effective plugin compatibility floors

| Plugin directory | Minimum ProgTrack | Compatibility evidence |
| --- | --- | --- |
| Animal_Reports | 0.2.3 | Both report entry and index import Core project_periods. It exists in v0.2.3 and is absent from v0.2.1 and v0.2.2. |
| Cage__Track | 0.3.0 | Housing and inspection mutations depend on session-bound animal and structure leases. 8ab9ed7 has a PostgreSQL profile but no _uses_shared_leases hook, so its guarded branch would fall through to legacy reads and writes on Shared. |
| Embryo_Track | 0.3.0 | PostgreSQL loads and updates the reference through load_shared_embryo_reference and save_shared_embryo_reference, which are absent in 8ab9ed7. |
| Flow_Track | 0.3.0 | The transfer editor uses shared_flow.flow_record_animals on both profiles. Shared reads and writes also require authenticated lease commands. |
| Heritage_Track | 0.3.0 | The PostgreSQL dialect selects SharedHeritageStore and passes an authenticated session to the Core snapshot reader. The previous backend provides neither interface. |
| Master_Track | 0.3.0 | First account creation, login, account commands, and the client heartbeat depend on authenticated_sessions, account_commands and the session error types. |
| Medi_Track | 0.3.0 | Module import requires Core document_messages. Managed documents require revision-aware publish, retry, read and retirement APIs on both profiles. |
| Network_Track | 0.3.0 | Its PostgreSQL path calls load_shared_chat and save_shared_chat_message. Those commands are absent in the earlier PostgreSQL-capable host. |
| PdG_converter | 0.3.0 | Its PostgreSQL path uses authenticated load_shared_pdg_model and save_shared_pdg_model with a Core animal lease. |
| Projects_Track | 0.3.0 | Module import requires Core document_messages. Shared editing and managed document operations use the Phase 3 service contracts. |
| Sample_Track | 0.3.0 | Module import requires sample_species_abbreviation from shared_samples. Shared editing requires the corresponding command and lease APIs. |
| Steroid_track | 0.2.1 | The feature gate entry only returns the host, and its implementation is unchanged from v0.2.1. |
| Surgery_Planner | 0.3.0 | Module import requires SharedSurgeryStore from shared_surgery. Shared edits also consume authenticated animal leases. |

Guarded optional hooks are evaluated against both supported profiles. A fallback
that would bypass the new Shared write contract cannot support an older minimum.

### Core and platform contracts

- Phase 3 backend modules and clients require the 0.3.0 host contract. This
  includes request transactions, sessions and leases, compatibility gates,
  backend administration, managed documents, disconnected startup and Core
  configuration. Database schema revision 4 and backend contract generation 1
  remain storage identifiers.
- Unchanged Core helpers retain the earliest verified matching released source
  among v0.2.1, v0.2.2 and v0.2.3. The dialog-section UI helper still uses the
  v0.2.2 interface, and the current identity helper no longer carries local
  path-based persistence. The application plugin manager's default identifies
  the active payload and its prior API is available from v0.2.3.
- The main script retains the Launcher 0.2.1 minimum. The Windows packager's
  default selects the 0.3.0 application script. Its launcher remains 0.2.1, and
  its FileVersion and ProductVersion are launcher-owned. The Linux launcher
  and archive version remain 0.3.0. The Linux packager discovers the application
  version from the sole payload filename. The bundled CPython is 3.13.
- The remaining 0.2.3 product references in README release history and the
  Heritage plugin's version are intentional. The release inventory also
  retains audited historical paths and independently versioned artifacts.

## Classification rules

| Policy | Meaning | Release action |
| --- | --- | --- |
| `PATCH_CURRENT` | The file describes the current ProgTrack application payload. | Replace the recorded product version with `TARGET_VERSION`. |
| `PATCH_FILENAME_AND_REFERENCES` | The version is part of the editable main-script filename and references to it. | Rename the file and update every listed reference. |
| `KEEP_MINIMUM` | Minimum supported ProgTrack/launcher compatibility. | Keep until an implementation really requires a newer minimum. |
| `KEEP_PLUGIN_VERSION` | The plugin's own release version, independent of the application payload. | Keep unless that plugin itself is released. |
| `KEEP_SEED_OR_SCHEMA` | Seed, schema, model, verifier, or data-format version. | Keep unless that data contract changes. |
| `KEEP_HISTORICAL` | Evidence, archive, incident, or release-history record. | Preserve as historical provenance. |
| `LAUNCHER_EXCLUDED_TEST` | Launcher/build code or launcher-owned metadata. | Inventory it, but leave it unchanged in the application-only `TARGET_VERSION` test. Update in the corresponding launcher release pass. |

## Current application markers to patch

The following entries are current product metadata. They are changed to
`TARGET_VERSION` by the controlled version pass. The audit started from the
0.2.2 payload and the controlled test now leaves the active payload at 0.2.3;
that baseline is historical evidence, not a future patch input.

| File | Exact location | Meaning and replacement |
| --- | --- | --- |
| repository-root active payload (the sole file matching `ProgTrack.v.*.py`) | filename; line 3 `Part of: ProgTrack <old>` | `PATCH_FILENAME_AND_REFERENCES`; rename the discovered file to `ProgTrack.v.${TARGET_VERSION}.py`, update line 3. |
| repository-root active payload (same discovered file) | line 3956 `PluginManager(... app_version="<old>")` | `PATCH_CURRENT`; runtime application version, replaced with `TARGET_VERSION`. |
| `info.json` | line 2 `Version` | `PATCH_CURRENT`; default information page version. |
| `info_en.json` | line 2 `Version` | `PATCH_CURRENT`; English information page version. |
| `info_de.json` | line 2 `Version` | `PATCH_CURRENT`; German information page version. |
| `info_it.json` | line 2 `Versione` | `PATCH_CURRENT`; Italian information page version. |
| `info_ru.json` | line 2 `Версия` | `PATCH_CURRENT`; Russian information page version. |
| `lang/messages_en.json` | line 2 `app.title` | `PATCH_CURRENT`; English UI title. |
| `lang/messages_de.json` | line 2 `app.title` | `PATCH_CURRENT`; German UI title. |
| `lang/messages_it.json` | line 2 `app.title` | `PATCH_CURRENT`; Italian UI title. |
| `lang/messages_ru.json` | line 2 `app.title` | `PATCH_CURRENT`; Russian UI title. |
| `README.md` | lines 1, 8, 20, 124, 134, 151, 156, 161, 176, 350, 482 | `PATCH_CURRENT`; current heading, badge, release description, payload filename, launcher example, platform text, package table and troubleshooting. Keep line 565's 0.2.2 release-history row unchanged. |
| `manual/ProgTrack_User_Guide - en.html` | line 5 HTML comment `ProgTrack release version: 0.3.0` | `PATCH_CURRENT`; English user-guide release marker. |
| `manual/ProgTrack_User_Guide - de.html` | line 5 HTML comment `ProgTrack release version: 0.3.0` | `PATCH_CURRENT`; German user-guide release marker. |
| `manual/ProgTrack_User_Guide - it.html` | line 5 HTML comment `ProgTrack release version: 0.3.0` | `PATCH_CURRENT`; Italian user-guide release marker. |
| `manual/ProgTrack_User_Guide - ru.html` | line 5 HTML comment `ProgTrack release version: 0.3.0` | `PATCH_CURRENT`; Russian user-guide release marker. |
| `ruff.toml` | line 17 filename key | `PATCH_FILENAME_AND_REFERENCES`; keep lint exceptions attached to the renamed payload. |

### Active source/plugin headers

Each file below has `# Part of: ProgTrack <current>` on line 3. Every entry is
`PATCH_CURRENT` and becomes `# Part of: ProgTrack ${TARGET_VERSION}`. The nearby
`Required Launcher version` comments are minimum compatibility declarations and
remain `KEEP_MINIMUM`.

| File | Exact location |
| --- | --- |
| `Plugins/Animal_Reports/__init__.py` | line 3 |
| `Plugins/Animal_Reports/animal_reports.py` | line 3 |
| `Plugins/Cage__Track/__init__.py` | line 3 |
| `Plugins/Cage__Track/cage_engine.py` | line 3 |
| `Plugins/Cage__Track/cage_store.py` | line 3 |
| `Plugins/Cage__Track/cage_track_widget.py` | line 3 |
| `Plugins/Cage__Track/ui_address_fields.py` | line 3 |
| `Plugins/Embryo_Track/__init__.py` | line 3 |
| `Plugins/Embryo_Track/embryo_track.py` | line 3 |
| `Plugins/Flow_Track/__init__.py` | line 3 |
| `Plugins/Flow_Track/flow_track_widget.py` | line 3 |
| `Plugins/Heritage_Track/__init__.py` | line 3 |
| `Plugins/Heritage_Track/display_context.py` | line 3 |
| `Plugins/Heritage_Track/display_strategies.py` | line 3 |
| `Plugins/Heritage_Track/engine_cache.py` | line 3 |
| `Plugins/Heritage_Track/ghost_strategies.py` | line 3 |
| `Plugins/Heritage_Track/heritage_store.py` | line 3 |
| `Plugins/Heritage_Track/heritage_track_widget.py` | line 3 |
| `Plugins/Heritage_Track/inbreeding.py` | line 3 |
| `Plugins/Heritage_Track/layout_pipeline.py` | line 3 |
| `Plugins/Heritage_Track/pedigree_engine.py` | line 3 |
| `Plugins/Heritage_Track/pedigree_router.py` | line 3 |
| `Plugins/Heritage_Track/ui_parent_fields.py` | line 3 |
| `Plugins/Master_Track/__init__.py` | line 3 |
| `Plugins/Master_Track/auth.py` | line 3 |
| `Plugins/Master_Track/dialogs.py` | line 3 |
| `Plugins/Master_Track/permissions.py` | line 3 |
| `Plugins/Master_Track/plugin.py` | line 3 |
| `Plugins/Master_Track/session.py` | line 3 |
| `Plugins/Medi_Track/__init__.py` | line 3 |
| `Plugins/Medi_Track/medi_track_widget.py` | line 3 |
| `Plugins/Network_Track/__init__.py` | line 3 |
| `Plugins/Network_Track/network_track.py` | line 3 |
| `Plugins/PdG_converter/converter.py` | line 3 |
| `Plugins/PdG_converter/plugin.py` | line 3 |
| `Plugins/PdG_converter/ui_hooks.py` | line 3 |
| `Plugins/Projects_Track/__init__.py` | line 3 |
| `Plugins/Projects_Track/project_track_tab.py` | line 3 |
| `Plugins/Projects_Track/ProjectsTrack_plugin.py` | line 3 |
| `Plugins/Sample_Track/__init__.py` | line 3 |
| `Plugins/Sample_Track/sample_track_widget.py` | line 3 |
| `Plugins/Steroid_track/__init__.py` | line 3 |
| `Plugins/Surgery_Planner/__init__.py` | line 3 |
| `Plugins/Surgery_Planner/surgery_planner.py` | line 3 |
| `Plugins/core/animal_identity.py` | line 3 |
| `Plugins/core/animal_reference_rewrite.py` | line 3 |
| `Plugins/core/animal_status.py` | line 3 |
| `Plugins/core/platform_helpers.py` | line 3 |
| `Plugins/core/plugin_caps.py` | line 3; line 4 is also current compatibility text |
| `Plugins/core/project_visibility.py` | line 3 |

The core default in `Plugins/core/plugin_manager.py` line 54
(`app_version="<current>"`) is also `PATCH_CURRENT` and becomes
`TARGET_VERSION`.

### Test-suite payload references

The following tests load or inspect the active main payload and therefore are
`PATCH_FILENAME_AND_REFERENCES`; their references to the discovered active
payload filename were updated to `ProgTrack.v.0.2.3.py` in the controlled pass.
For a future pass, find and update every reference to the sole old
`ProgTrack.v.*.py` filename rather than searching for a fixed version string:

`tests/qt_phase2b_dialog_geometry.py`, `tests/test_audit_logging.py`,
`tests/test_codehealth_targeted_fixes.py`,
`tests/test_otof_experimental_offspring.py`,
`tests/test_phase2_permission_catalog_and_enforcement.py`,
`tests/test_phase2b_animal_dialog_statistics_regressions.py`,
`tests/test_phase2b_block2.py`, `tests/test_phase2b_block3.py`,
`tests/test_phase2b_branding_pdf_exports.py`,
`tests/test_phase2b_conventions_dialog_responsive.py`,
`tests/test_phase2b_current_build_fixes.py`,
`tests/test_phase2b_issues_118_123_124.py`,
`tests/test_phase2b_sidebar_project_refinements.py`,
`tests/test_phase2b_unified_prog_dependency.py`,
`tests/test_ringbearer_event_consistency.py`,
`tests/test_role_builder_configuration.py`, `tests/test_role_icon_regressions.py`,
`tests/test_tester_suggestions_20260811.py`, and
`tests/test_ui_svg_palette_contrast.py`.

`tests/test_phase2b_info_and_project_panel.py` line 81 checks the current info
page marker and was updated from `0.2.2` to `0.2.3`. Tests that intentionally
inspect archived 0.2.1/old Linux artifacts (for example
`tests/test_phase2b_block4.py` and `tests/test_linux_release_packaging.py`)
remain historical fixtures and are `KEEP_HISTORICAL`.

The test directory is ignored by the release repository's `.gitignore` and is
therefore local validation material, not release-package content. Its active
payload references were still updated in the working environment so the
controlled tests exercise the renamed file; future release passes should apply
the same filename substitution to any checked-out local tests.

## Deliberately unchanged version families

| Files/locations | Policy and reason |
| --- | --- |
| `Plugins/Animal_Reports/manifest.json` lines 4 and 15 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Cage__Track/manifest.json` lines 4 and 15 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Embryo_Track/manifest.json` lines 4 and 19 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Flow_Track/manifest.json` lines 4 and 15 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Heritage_Track/manifest.json` lines 4 and 16 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Master_Track/manifest.json` lines 4 and 16 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Medi_Track/manifest.json` lines 4 and 18 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Network_Track/manifest.json` lines 4 and 16 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/PdG_converter/manifest.json` lines 4 and 18 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Projects_Track/manifest.json` lines 4 and 15 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Sample_Track/manifest.json` lines 4 and 17 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Steroid_track/manifest.json` lines 4 and 12 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| `Plugins/Surgery_Planner/manifest.json` lines 4 and 16 (`version`, `min_progtrack_version`) | `KEEP_PLUGIN_VERSION` / `KEEP_MINIMUM`; plugin release and application compatibility floor. |
| Source-header `Required ProgTrack/Launcher version` values in the candidate inventory | `KEEP_MINIMUM`; compatibility floors, not current-release labels. |
| `source/build_sample_seed.py` lines 1, 470, 1853, 2358, 2459; `Resources/Seed/integrity_report.json` line 17; `Resources/Seed/SCENARIO_COVERAGE.md` line 1 | `KEEP_SEED_OR_SCHEMA`; seed package/integrity metadata remains 0.2.1 until the seed contract is deliberately revised. |
| `Plugins/core/backend/schema.py` (schema revision 4), `Plugins/core/animal_roles.py` (`SCHEMA_VERSION = 2`), `Plugins/core/role_block_presets.py` (`SCHEMA_VERSION = 2`), and model/store `1.0.0` values | `KEEP_SEED_OR_SCHEMA`; data/schema/model versions are not product versions. |
| `icons/ui/manifest.json` line 2 (`version: 2`) | `KEEP_SEED_OR_SCHEMA`; UI-icon manifest schema version, not the ProgTrack application version. |
| `Plugins/Embryo_Track/__init__.py`, `Plugins/Flow_Track/__init__.py`, `Plugins/Network_Track/__init__.py` line 10 (`__version__ = "1.0.0"`) and `Plugins/Projects_Track/ProjectsTrack_plugin.py` line 311 (`version = "1.0.0"`) | `KEEP_PLUGIN_VERSION`; plugin implementation versions. |
| `Plugins/Surgery_Planner/surgery_planner.py` lines 181 and 1240 (`surgery_planner_plugin_v1.0.0`) | `KEEP_PLUGIN_VERSION`; persisted creator/audit identifier, not the application release. |
| `Plugins/Medi_Track/medi_track_widget.py` line 798 and `source/build_sample_seed.py` line 1748 (`version: 1.7` medical-store format) | `KEEP_SEED_OR_SCHEMA`; medical data format version. |
| `Plugins/Cage__Track/cage_store.py` line 38 (`version: 1.0`) and `Plugins/Flow_Track/flow_track_widget.py` lines 478/482 (`version: 3.0`, fallback `1.0`) | `KEEP_SEED_OR_SCHEMA`; plugin storage formats. |
| `manual/technical documentation/architecture/Phase 2A backend migration architecture/08 Evidence register.md` line 88 (`Frozen main module: ProgTrack.v.0.1.2.py`) | `KEEP_HISTORICAL`; frozen evidence of the earlier audit. |
| `manual/technical documentation/architecture/Phase 2A backend migration architecture/phase2a_audit_result_clean.json` lines 1996–2682 (`ProgTrack.v.0.1.2.py` evidence paths) | `KEEP_HISTORICAL`; machine-readable evidence from the earlier audit. Do not rewrite historical paths. |
| Other files under `manual/technical documentation/architecture/Phase 2A backend migration architecture/` | Audited for product-version markers; none beyond the two historical files above. Branch, verifier, and schema versions remain `KEEP_HISTORICAL`/`KEEP_SEED_OR_SCHEMA` as labelled in their surrounding documents. |
| `README.md` release-history rows 0.2.2, 0.2.1, 0.2.0, 0.1.x | `KEEP_HISTORICAL`; do not rewrite history while updating the current-release prose. |
| `source/launcher/windows/launcher.py` | lines 6 and 23 (`ProgTrack Launcher 0.2.1`, `LAUNCHER_VERSION`); `LAUNCHER_EXCLUDED_TEST`, launcher version. |
| `source/launcher/windows/launcher_version_info.txt` | lines 20 and 24 (`FileVersion 0.2.1.0`, `ProductVersion 0.2.1`); `LAUNCHER_EXCLUDED_TEST`, Windows executable metadata. |
| `source/launcher/windows/LAUNCHER_VERSIONS.md` | lines 3, 9–10 and older entries; `KEEP_HISTORICAL`/`LAUNCHER_EXCLUDED_TEST`, launcher release history. |
| `source/launcher/windows/package_release.ps1` | `$Version = "0.3.0"`; current packaging input selecting the application payload. Keep launcher version metadata separate. |
| `source/launcher/windows/build_launcher_small.bat`, `source/launcher/windows/launcher_small.spec` | pinned third-party build/runtime versions only; `LAUNCHER_EXCLUDED_TEST`, not ProgTrack product versions. |
| `source/launcher/linux/launcher.py` | line 22 (`LAUNCHER_VERSION = "0.3.0"`); `LAUNCHER_EXCLUDED_TEST`, Linux launcher version. |
| `source/launcher/linux/package_linux_release.py` | `VERSION = "0.3.0"` is launcher/archive-owned. `_application_payload` discovers the application payload filename and version. |
| `source/launcher/linux/linux_runtime_manifest.json` and `source/launcher/linux/requirements-linux-bundled.txt` | dependency/runtime pins; `LAUNCHER_EXCLUDED_TEST`, third-party versions rather than ProgTrack versions. |
| `source/launcher/linux/README.md`, `source/launcher/linux/progtrack.desktop`, and `source/launcher/linux/release/` metadata | generic launcher/package labels or generated artifact metadata; `LAUNCHER_EXCLUDED_TEST` and generated-output exclusion. |
| `THIRD_PARTY_NOTICES.md` lines 11–43 and `third_party_licenses/*` | dependency/license versions and legal-license revisions; third-party metadata, not ProgTrack product versions. Preserve unless the bundled dependency set changes. |

## Manual/documentation audit result

All four localized user guides now carry a small HTML release marker so their
version is patchable without changing visible wording. The technical
architecture manuals were also searched; they contain no current product
marker and intentionally retain historical branch and verifier versions.
README current-release prose is included in the `PATCH_CURRENT` row above.
This keeps manuals explicitly in the audit scope without changing provenance or
unrelated document-version numbers.

The ignored mutable SQLite runtime database under `ProgTrackData/` was checked
read-only as well. Its `installation` and `schema_revisions` tables contain
installation/schema revisions, but no ProgTrack product-version marker; those
values therefore require no release bump.

## Controlled patch and verification procedure

1. Confirm a clean worktree and record a diff/status snapshot.
2. Set `TARGET_VERSION` above; review the rows and classify each candidate
   before editing.
3. Perform only the replacements in `PATCH_CURRENT` and
   `PATCH_FILENAME_AND_REFERENCES`. Rename the main script with Git and update
   its references. Update the packagers' application payload selections and defaults when required.
   Leave `_internal/` and launcher-owned code, binaries, versions, and legal metadata untouched for
   this application-only test.
4. Run `git diff --check`, parse all JSON manifests/info files, and compile the
   renamed Python payload and changed modules in memory (without creating
   bytecode files).
5. Re-scan for the previous active payload name and verify that any remaining
   occurrence is explained by a `KEEP_*` or `LAUNCHER_EXCLUDED_TEST` row.
   Verify the renamed payload is the only active `ProgTrack.v.*.py` file and
   that launcher changes are limited to the application payload selections and defaults.
6. Run focused application/plugin tests that cover the changed version
   contract, then inspect the final diff and status. Do not publish or push as
   part of this controlled test unless explicitly requested.

This map is intentionally human-readable and patch-oriented: a future release
starts by changing the single `TARGET_VERSION` line, then applies only the
listed current-product substitutions while preserving minimum compatibility,
plugin, seed/schema, historical, third-party, and launcher-owned values.
