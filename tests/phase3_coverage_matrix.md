# Phase 3 automated and manual coverage map

This map records the current test paths for the open 0.3.0 issues. The Phase 3 default suite includes repeatable Core/backend/UI tests and excludes Heritage Track plugin rendering, layout, cache, and interaction tests because those are reserved for the Phase 4 overhaul. Excluded tests remain collected and explicitly marked; they are not counted as passing.

The latest Phase 3 default-suite result is recorded below. Heritage tests remain deferred as requested. The source suite does not validate the final 0.3.0 packages; run `tests/run_phase3_package_acceptance.py` after both final archives are assembled. See issue #325 for the test-suite disposition.

| Issue | Automated coverage in the current tree | Manual / external coverage |
|---|---|---|
| #48 approved plugin architecture/storage matrix | `run_phase3_package_acceptance.py`; `test_phase3_release_package_acceptance.py` checks both assembled package plugin inventories against the approved matrix | 3A P3A-02: confirm the Windows and Linux package plugin sets; #48's issue-level runtime test remains explicitly not applicable |
| #49 Linux runtime paths and packaging | `test_linux_release_packaging.py`; `test_linux_source_compatibility_preflight.py`; `test_license_notice_coverage.py` | 3A P3A-02/P3A-03: native Ubuntu package launch and export; record exact Ubuntu release and glibc baseline |
| #50 backend database, shared data access, leases, audit | `test_current_backend_runtime.py`; `test_backend_schema_bootstrap.py`; `test_phase3_postgresql_management.py`; `test_audit_logging.py`; `test_phase3_sessions_220_232.py`; package mode runs these against each extracted final package and asserts imports/runtime paths resolve inside it | 3A P3A-01, P3A-04 through P3A-09: institution PostgreSQL/TLS, two clients, managed files, and operations from the same final packages |
| #156 Core-owned parentage command | `test_heritage_issue_156.py` (Core command boundary; retained in Phase 3). `test_heritage_parentage_command.py` is Heritage-plugin behavior and remains deferred. | 3A P3A-06: shared PostgreSQL transaction with two clients |
| #214 saved backend-profile restoration | `test_backend_profile_restoration_214.py` | — |
| #215 PostgreSQL database discovery/dropdown | `test_backend_database_catalog_215_218.py` | — |
| #216 password masking | `test_backend_password_mtls_216_217.py` | — |
| #217 collapsed mTLS group and dynamic dialog layout | `test_backend_password_mtls_216_217.py` | — |
| #218 compatibility grouping of discovered databases | `test_backend_database_catalog_215_218.py` | — |
| #219 operation-scoped backend-admin authorization | `test_backend_admin_authorization_219.py` | 3A P3A-01/P3A-05/P3A-07: live installed-client workflow |
| #220 one active session across clients | `test_phase3_sessions_220_232.py`; `test_current_backend_runtime.py` | 3A P3A-06: same-account session handoff between clients |
| #226 client-local managed-storage settings and credentials | `test_backend_storage_dialog_226.py`; `test_managed_storage_provider_226.py` | 3A P3A-01/P3A-05/P3A-09: two clients using one IT-managed share |
| #227 PostgreSQL/network setup guides | `test_phase3_manual_guide_contract_227.py` | 3A P3A-09: human review of meaning and localized UI labels |
| #228 unavailable managed-storage path | `test_managed_storage_provider_226.py`; `test_backend_storage_dialog_226.py` | 3A P3A-05/P3A-07/P3A-09: unavailable share while PostgreSQL remains reachable |
| #229 unavailable Shared PostgreSQL and reconnect | `test_shared_disconnected_startup_229.py` | `Q:\GitHub\Issue Tracker\Tests\manual tests phase 3.md`, B8: native unavailable-server/reconnect check |
| #230 managed-document transactions/interchange/reconciliation | `test_current_backend_runtime.py`; `test_phase3_postgresql_management.py`; `test_managed_storage_provider_226.py` | 3A P3A-07/P3A-09: real shared documents and client mount |
| #231 compatibility checks and exclusive migrations | `test_phase3_schema_maintenance_231.py`; `test_backend_schema_bootstrap.py`; `test_phase3_postgresql_management.py` | — |
| #232 account changes revoke the target's session | `test_phase3_sessions_220_232.py` | 3A P3A-06: installed-client session state |
| #241 explicit local demo-seed consent; never seed Shared PostgreSQL | `test_phase3_demo_seed_consent.py`; `test_backend_schema_bootstrap.py`; `test_shared_disconnected_startup_229.py` | 3A P3A-01/P3A-05: clean local first start and newly created empty Shared database |
| #246 backend-owned Core configuration | `test_current_backend_runtime.py::test_role_configuration_is_backend_owned`; `test_role_builder_configuration.py`; `test_identity_resource_catalogs.py` (text resources and pure ID helpers only) | — |
| #299 Windows launcher copyright metadata | `test_launcher_windows_copyright_299.py`; `test_license_notice_coverage.py`; package mode reads `LegalCopyright` from the final archive's `Launcher.exe` | 3A P3A-02: Copyright field in the final package's Explorer Details view |
| #325 test-suite reconciliation | This matrix, `conftest.py`, `pytest.ini`, and the complete default suite | 3A remains limited to native/site/human checks; it is not a second test-suite run |
| #326 branded Reports PDF styling | `test_phase2b_branding_pdf_exports.py`; `test_phase2b_branding_positions.py` | 3A P3A-07: review branded PDF from the assembled clients |
| #327 login-first gate and dump attribution | `test_issue_327_startup_auth_gate.py` | 3A P3A-01/P3A-10/P3A-11: fresh startup, outage behavior, and optional WinDbg attribution |
| #331 select database with separate backend/runtime roles | `test_backend_admin_authorization_219.py`; `test_current_backend_runtime.py` | 3A P3A-01/P3A-05 step 1: progtrack_admin selects while progtrack_app remains the runtime account |

## Execution boundary

- Standard command: `python -m pytest -q`. It runs all selected non-Heritage tests, including subprocess wrappers for Qt cases marked `isolated_qt`.
- Heritage Track rendering/layout/cache/interaction tests are deferred: `python -m pytest -o addopts= -m "phase4_heritage or extended_heritage" -q` is a Phase 4 command and is not part of the Phase 3 acceptance run.
- `test_heritage_issue_156.py` remains selected because it tests the Core-owned parentage API. Tests of Heritage plugin rendering or interaction are deferred even when their test module also mentions parentage.
- Native Windows/Linux, institution PostgreSQL/TLS/share, and human-language review remain manual gates listed above. SQLite/service simulations do not claim real PostgreSQL acceptance.
- The separate `python tests/run_phase3_package_acceptance.py` command is a required post-assembly automation gate for #48/#50/#299. It consumes the final archives and sidecars from `Q:\GitHub\Release Packages`; it does not rebuild them. #48 has no additional runtime manual gate because its closed issue explicitly classified that as not applicable.
- A filename is a coverage pointer, not proof that every issue criterion passed. The final pytest result and any issue-specific external gates are recorded separately.

## Latest automatic execution — 2026-11-26

- Default command with `QT_QPA_PLATFORM=offscreen`: `python -m pytest -q` — **843 passed, 1 skipped, 299 deselected, 1,003 subtests passed** in 1,450.73 seconds. No warnings or ordinary test failures.
- The single skip is the final Windows launcher-resource check because no extracted candidate `Launcher.exe` path was configured. Deselects include deferred Heritage tests, package-only checks run by the package runner, and original Qt tests executed through selected subprocess wrappers.
- The first full attempt exposed a native Qt access violation in test #318 when it ran inside the long-lived suite process. The test passes alone; it is now marked `isolated_qt` and covered by `test_phase3_qt_isolated_core_318_creation.py`. The rerun completed without native crash. This is test-process isolation, not a production-code fix.
- Linux package-contract tests require the assembled candidate path. The replacement Windows and Linux 0.3.0 archives still need the post-build `tests/run_phase3_package_acceptance.py` gate. Structural validation on Windows is not native Ubuntu acceptance.
- The package runner compiles with `-W error::SyntaxWarning`; the previous invalid-escape warning is gone. Native Ubuntu, live PostgreSQL/TLS/two-client, and component/license checks remain external Phase 3 gates.
