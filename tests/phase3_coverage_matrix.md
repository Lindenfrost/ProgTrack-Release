# Phase 3 automated and manual coverage map

This map records the current test paths for the open 0.3.0 issues. The Phase 3 default suite includes repeatable Core/backend/UI tests and excludes Heritage Track plugin rendering, layout, cache, and interaction tests because those are reserved for the Phase 4 overhaul. Excluded tests remain collected and explicitly marked; they are not counted as passing.

Last complete Phase 3 default run: 2026-11-23, `python -m pytest -q` with `QT_QPA_PLATFORM=offscreen` — **822 passed, 1 skipped, 289 deselected, 1,040 unittest subtests passed, 2 warnings** in 1,375.94 seconds. The skipped item requires a final Windows launcher executable path. Heritage tests were deferred as requested. See issue #325 for the complete disposition and warnings.

| Issue | Automated coverage in the current tree | Manual / external coverage |
|---|---|---|
| #49 Linux runtime paths and packaging | `test_linux_release_packaging.py`; `test_linux_source_compatibility_preflight.py`; `test_license_notice_coverage.py` | 3A P3A-02: clean native Linux Mint package launch and export |
| #50 backend database, shared data access, leases, audit | `test_current_backend_runtime.py`; `test_backend_schema_bootstrap.py`; `test_phase3_postgresql_management.py`; `test_audit_logging.py`; `test_phase3_sessions_220_232.py` | 3A P3A-03: institution PostgreSQL/TLS and independent clients |
| #156 Core-owned parentage command | `test_heritage_issue_156.py` (Core command boundary; retained in Phase 3). `test_heritage_parentage_command.py` is Heritage-plugin behavior and remains deferred. | 3A P3A-03: shared PostgreSQL transaction with two clients |
| #214 saved backend-profile restoration | `test_backend_profile_restoration_214.py` | — |
| #215 PostgreSQL database discovery/dropdown | `test_backend_database_catalog_215_218.py` | — |
| #216 password masking | `test_backend_password_mtls_216_217.py` | — |
| #217 collapsed mTLS group and dynamic dialog layout | `test_backend_password_mtls_216_217.py` | — |
| #218 compatibility grouping of discovered databases | `test_backend_database_catalog_215_218.py` | — |
| #219 operation-scoped backend-admin authorization | `test_backend_admin_authorization_219.py` | 3A P3A-03: live installed-client workflow |
| #220 one active session across clients | `test_phase3_sessions_220_232.py`; `test_current_backend_runtime.py` | 3A P3A-03: same-account session handoff between clients |
| #226 client-local managed-storage settings and credentials | `test_backend_storage_dialog_226.py`; `test_managed_storage_provider_226.py` | 3A P3A-03: two clients using one IT-managed share |
| #227 PostgreSQL/network setup guides | `test_phase3_manual_guide_contract_227.py` | 3A P3A-04: human review of meaning and localized UI labels |
| #228 unavailable managed-storage path | `test_managed_storage_provider_226.py`; `test_backend_storage_dialog_226.py` | 3A P3A-03 step 8: unavailable share while PostgreSQL remains reachable |
| #229 unavailable Shared PostgreSQL and reconnect | `test_shared_disconnected_startup_229.py` | `Q:\GitHub\Issue Tracker\Tests\manual tests phase 3.md`, B8: native unavailable-server/reconnect check |
| #230 managed-document transactions/interchange/reconciliation | `test_current_backend_runtime.py`; `test_phase3_postgresql_management.py`; `test_managed_storage_provider_226.py` | 3A P3A-03: real shared documents and client mount |
| #231 compatibility checks and exclusive migrations | `test_phase3_schema_maintenance_231.py`; `test_backend_schema_bootstrap.py`; `test_phase3_postgresql_management.py` | — |
| #232 account changes revoke the target's session | `test_phase3_sessions_220_232.py` | 3A P3A-03: installed-client session state |
| #241 explicit local demo-seed consent; never seed Shared PostgreSQL | `test_phase3_demo_seed_consent.py`; `test_backend_schema_bootstrap.py`; `test_shared_disconnected_startup_229.py` | — |
| #246 backend-owned Core configuration | `test_current_backend_runtime.py::test_role_configuration_is_backend_owned`; `test_role_builder_configuration.py`; `test_identity_resource_catalogs.py` (text resources and pure ID helpers only) | — |
| #299 Windows launcher copyright metadata | `test_launcher_windows_copyright_299.py`; `test_license_notice_coverage.py` | 3A P3A-01: Copyright field in the final candidate's Explorer Details view |
| #325 test-suite reconciliation | This matrix, `conftest.py`, `pytest.ini`, and the complete default suite | 3A remains limited to native/site/human checks; it is not a second test-suite run |

## Execution boundary

- Standard command: `python -m pytest -q`. It runs all selected non-Heritage tests, including subprocess wrappers for Qt cases marked `isolated_qt`.
- Heritage Track rendering/layout/cache/interaction tests are deferred: `python -m pytest -o addopts= -m "phase4_heritage or extended_heritage" -q` is a Phase 4 command and is not part of the Phase 3 acceptance run.
- `test_heritage_issue_156.py` remains selected because it tests the Core-owned parentage API. Tests of Heritage plugin rendering or interaction are deferred even when their test module also mentions parentage.
- Native Windows/Linux, institution PostgreSQL/TLS/share, and human-language review remain manual gates listed above. SQLite/service simulations do not claim real PostgreSQL acceptance.
- A filename is a coverage pointer, not proof that every issue criterion passed. The final pytest result and any issue-specific external gates are recorded separately.
