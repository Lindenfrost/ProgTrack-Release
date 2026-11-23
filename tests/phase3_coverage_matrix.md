# Phase 3 automated and manual coverage map

Status: initial inventory for issue #325, 2026-11-23. This map records candidate test locations, not passing results. The full suite has not yet completed cleanly; see the current test run record and issue #325 before interpreting any row as accepted.

| GitHub issue | Automated coverage in the candidate | Manual Retest 3A |
|---|---|---|
| #49 Linux packaging/readiness | test_linux_release_packaging.py; test_license_notice_coverage.py | P3A-02: clean native Linux Mint workstation and visible launch/export |
| #50 shared backend, leases, schema and PostgreSQL | test_domain_record_json_50.py; test_current_backend_runtime.py; test_backend_schema_bootstrap.py; test_phase3_postgresql_management.py; test_phase3_authenticated_sessions.py | P3A-03: actual site database/TLS and two installed clients |
| #156 Core-owned parentage transaction | test_heritage_issue_156.py; test_heritage_parentage_command.py | P3A-03: actual multi-client deployment check only; transaction assertions remain automated |
| #214 saved backend profile restoration | test_backend_profile_restoration_214.py | — |
| #215 server database dropdown/catalog | test_backend_database_catalog_215_218.py | — |
| #216 saved password masking | test_backend_password_mtls_216_217.py | — |
| #217 collapsible mTLS controls | test_backend_password_mtls_216_217.py | — |
| #218 database compatibility grouping | test_backend_database_catalog_215_218.py | — |
| #219 operation-scoped backend-admin authorization | test_backend_admin_authorization_219.py | — |
| #220 single active user session | test_phase3_authenticated_sessions.py | P3A-03: actual independent client session handoff |
| #226 client-local managed-storage path and credentials | test_backend_storage_dialog_226.py; test_managed_storage_provider_226.py | P3A-03: IT-managed mounted share across installed clients |
| #227 PostgreSQL/network setup guides | test_phase3_manual_guide_contract_227.py | P3A-04: human semantic and UI-label review in all four languages |
| #228 invalid/unavailable storage startup | test_managed_storage_startup_228.py | P3A-03: actual unavailable mount while PostgreSQL remains reachable |
| #229 unavailable PostgreSQL startup/reconnect | test_shared_disconnected_startup_229.py | — |
| #230 managed-document transactions and reconciliation | test_managed_documents_230.py | P3A-03: actual institution share and client mount |
| #231 compatibility gate and exclusive migrations | test_phase3_schema_maintenance_231.py; test_phase3_postgresql_management.py; test_backend_schema_bootstrap.py | — |
| #232 account-change session revocation | test_user_account_commands_232.py; test_phase3_authenticated_sessions.py | P3A-03: actual installed-client session state |
| #241 explicit local demo opt-in; no Shared PostgreSQL seed | test_phase3_demo_seed_consent.py | — |
| #246 backend-owned Core configuration | test_phase3_core_configuration_246.py | — |
| #299 Windows launcher copyright metadata | test_launcher_windows_copyright_299.py; test_license_notice_coverage.py | P3A-01: native Explorer Details field display |
| #325 test-suite reconciliation | This issue is the cross-cutting inventory/maintenance gate; it does not own a product behavior test. | P3A-01 through P3A-04 are the only manual gates listed here. |

## Boundary

The automated filenames above identify coverage candidates. They must be collected and reviewed against the owning issue's current acceptance criteria; a filename or a green focused run alone is not proof that all criteria are covered. Test failures and stale contracts must be classified under #325.

Manual Retest 3A is restricted to the native, site-specific, or human-judgment observations that the automated suite cannot establish on the test workstation. Database schema, service logic, seed policy, dialog behavior, authorization, revision conflicts, concurrency algorithms, and failure injection remain automated whenever repeatable.
