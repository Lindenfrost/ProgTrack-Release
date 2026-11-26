# Manual retest — Phase 3A

Use this checklist for native, two-client, live-site, and human acceptance only. It does not replace the automated suite, source-level checks, or unresolved implementation work. Test only the exact Windows and Linux candidate archives below; do not rebuild between retest and publication. Do not use production data. No screenshots are required. For each test, replace [ ] with [v] PASS, [x] FAIL, or [s] SKIP, then record evidence or the reason on the following # Comments line. A skipped prerequisite is not a pass.

## Candidate under test

- Commit: f4c4ac76c80bc22d30568765a9b9ca48aceff9c0
- Windows: Q:\GitHub\Release Packages\ProgTrack-0.3.0.zip
- Windows SHA-256: 5ad7e1d51445bfe689459ba4e81ed8fd679bb8a230a1ff4f3651f1f1d238f95e
- Linux: Q:\GitHub\Release Packages\ProgTrack-0.3.0-linux-x86_64.tar.gz
- Linux SHA-256: b546bf042ab5d00995f6e3625b38cc7c7921824741d68775fa32bc559e1dd658

Before testing, recalculate both archive hashes and compare them with these values. If either archive changes, stop and replace the candidate identity above before continuing. Any unresolved Linux component or license entry keeps #49 and the release gate open, even if runtime tests pass. Manual results cannot close the remaining #50 implementation/integration work or automated acceptance criteria.

## Manual source for fresh-client connection values

Use the localized candidate manual's subsection **“Same-server Shared setup and connection fields”** (or its translated equivalent) as the only source for the example connection values. P3A-01 must follow its Windows column; P3A-03 must follow its Ubuntu column. The table in that subsection lists every Backend dialog line and what to enter or select. Do not copy values from a second test-only connection table.

For each fresh-client workflow, compare every visible Backend dialog label and control with the corresponding manual row before entering values. Record any missing field, incorrect label/value, ambiguous instruction, or impossible step on that workflow's `# Comments:` line. Passwords are entered interactively and must never be written into this file.

The example server is PostgreSQL and Samba on Ubuntu at 172.16.9.77. Samba's [progtrack-managed] share is backed by /srv/progtrack-managed; ProgTrack uses its documents and config-assets child directories. The smb_app Unix/Samba account does not exist in the recorded audit and must be provisioned in group progtrack before SMB client checks. The audit also found UFW inactive and Samba listening on all IPv4/IPv6 addresses at ports 445 and 139. Complete the Samba/firewall preflight in P3A-09 before treating shared-file access as production-ready. PostgreSQL and SMB use separate credentials.

## Prepare once

The test coordinator or local IT prepares:

- One clean Windows client for the first action in P3A-01, one Ubuntu x86_64 client for the Linux package and CIFS checks, and access to a macOS 13+ workstation for the SMB guide walkthrough only. Record the Ubuntu release/version, architecture, kernel, and glibc for #49. macOS is not a ProgTrack package test.
- A disposable Shared PostgreSQL server with separate approved database-owner, ProgTrack runtime, and backend-administrator credentials; TLS settings and a test certificate; one empty database; one initialized ProgTrack database; and one non-ProgTrack database. Do not disclose credentials in this file.
- The Ubuntu test server at 172.16.9.77 hosts both PostgreSQL and Samba. Its share is progtrack-managed backed by /srv/progtrack-managed; the share root and documents/config-assets directories currently have root:progtrack ownership and mode 2770. Before testing, IT must create the missing non-administrator smb_app OS/Samba account in group progtrack, configure least-privilege client-network firewall access, and complete P3A-09's Samba checks. Do not use psr-admin for document access. Mount this one share on Windows and Ubuntu; the macOS workstation uses the same server for the guide walkthrough.
- Test-Lord (ProgTrack Lord), Test-Editor (ordinary ProgTrack account with the Core parentage-edit permission), Test-Viewer (ordinary account without that permission), a user in another Unit for the cross-Unit denial check, one account for each additional permission combination required by #156, and Test-Session (ordinary account that can be edited in Master Track). Use the same Test-Session credentials on both clients. Prepare a separate backend-administrator login; it is not a ProgTrack account.
- In the disposable ProgTrack database, create Test-Unit-A and Test-Unit-B. Assign Test-Editor and Test-Child to Test-Unit-A; assign the cross-Unit test user and one parent candidate to Test-Unit-B. Create Test-Mother, Test-Father, and Test-Child of one species, with both proposed parents born before Test-Child; Test-Child is assigned to Test-Project. Also prepare one eligible archived/deceased parent and one ineligible candidate according to #156, plus the authorized/denied parentage permission combinations required by #156 (including direct and job/custom-role grants where configured). Give Test-Editor the project-document permissions needed for the upload check. Prepare a separate local SQLite source database with Test-Transfer-Animal and one managed test document in its managed storage for the explicit transfer test. Do not use personal or production records.
- Two clean Windows OS profiles with no ProgTrack data for the first-start choices in P3A-01. Use separate Windows OS accounts or clean test profiles; do not remove or rename a real user's ProgTrack data.
- For P3A-08, a disposable old-schema database clone and the exact older client build that the current compatibility policy says must be rejected. Do not manually create schema objects: ProgTrack's registered schema initialization must create and verify them.

## P3A-01 — Fresh Windows first start and Shared PostgreSQL connection (#229, #241, #327, #331)

Begin on a clean Windows machine/profile with no existing ProgTrack data or backend profile. Follow the Windows column in the shared-connection table in the candidate manual; do not put any password in this file.

1. Extract the Windows candidate and launch ProgTrack for the first time. Confirm the initial screen does not expose the main workspace or backend maintenance before authentication. When asked whether to install example data in the new local database, select **No, start empty**.
2. When **Create Lord Account** appears, create a temporary local account named Setup-Lord; enter its password only in the dialog and sign in. Confirm the animal list is empty. Close and reopen ProgTrack; confirm it does not ask about example data again and the Standalone database remains empty.
3. As Setup-Lord, open **Settings → Backend** and select **Shared PostgreSQL**. Compare each visible field and control with the Windows column in the candidate manual, then enter its non-secret values. Leave mTLS fields empty. Enter the Windows UNC share root in **Managed document storage**. Because the SMB account differs from the PostgreSQL runtime role, expand **Storage credentials**, enter smb_app in **Storage user**, and type its password locally after IT has provisioned the account. Record manual/UI mismatches in `# Comments:`.
4. Click **Refresh list** and confirm discovery succeeds without an administrator prompt. Select progtrack_test_023 in the Database dropdown. Click **Test connection** and authorize that operation with the separate PostgreSQL backend administrator progtrack_admin; confirm success without saving the profile. Click **Use selected** and authorize it in a separate fresh prompt. Confirm the runtime user remains progtrack_app; do not grant progtrack_admin SELECT on application tables or add role membership. Click **Save** and authorize this validation as another separate operation with progtrack_admin. Confirm the configured runtime account progtrack_app is what ProgTrack checks for application-table readability.
5. Close and restart ProgTrack. Confirm it starts with the Shared profile and presents the server database's authentication flow. Sign in as the provisioned server-side Test-Lord, not the local Setup-Lord. Confirm server-side animals appear, no local demo/SQLite records are copied into PostgreSQL, and the local Setup-Lord record is not treated as a server user. Confirm the configured SMB root is still the same host as PostgreSQL.
6. In a second clean local Windows profile, repeat first launch, choose **Yes, install** for example data, create its initial Lord account, and confirm the demo records appear once. Restart twice and confirm the choice is not asked again and no duplicate seed occurs.

[ ] P3A-01 Result
# Comments:

## P3A-02 — Candidate package, inventory, and Windows copyright (#49, #299)

1. Confirm this is the same pair of archives whose hashes were checked before the first launch; record the candidate commit and verified hashes.
2. In the Linux archive's component inventory, check that every bundled runtime, extension, shared library, Qt plugin, font, checksum, and license source is listed and reconciles with the package's third-party notices. If any license/component remains unresolved, mark this test FAIL for release readiness and record each unresolved entry; continue other useful tests if desired.
3. Extract the Windows archive. In File Explorer, right-click Launcher.exe → Properties → Details. Confirm Copyright is exactly “Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH”. Record CompanyName, FileDescription, FileVersion, ProductName, and ProductVersion. Compare them with the unchanged values in source/launcher/windows/launcher_version_info.txt from the same candidate commit.
4. Inspect the Windows and Linux archive contents. Confirm the mutable runtime JSON copies animal_roles.json, role_block_presets.json, identity_lifecycle_conventions.json, and Plugins/core/user_lang/messages_*.json are absent, while the packaged base translations under lang/messages_*.json remain. Confirm the current configuration seed is present for explicit initialization, not automatic Shared-database population.
5. Record whether the same two exact archives are the ones used in every later test.

[ ] P3A-02 Result
# Comments:

## P3A-03 — Clean native Linux runtime (#49)

Run on the designated Ubuntu test workstation with the exact Linux archive above. Record `cat /etc/os-release`, `uname -m`, `uname -r`, and `ldd --version`. Do not install ProgTrack-specific Python, Qt, font, libpq, Psycopg, or other dependencies. Follow the Ubuntu column in the shared-connection table in the candidate manual for Shared setup.

1. Extract the archive under a read-only application directory. From the file manager, launch ProgTrack. Close it, set the working directory to the home directory, and launch the extracted ProgTrack executable again.
2. Run the supported diagnostic command, ./ProgTrack --diagnose-paths. Confirm paths resolve under ~/.local/share/ProgTrack, ~/.config/ProgTrack, ~/.cache/ProgTrack, and ~/.local/state/ProgTrack (or the explicitly configured XDG roots); confirm launch did not write into the read-only application folder. Run ldd/readelf on the launcher and bundled shared libraries, record OS/glibc/OpenSSL/font/display details, and confirm there are no unresolved libraries.
3. Start the local SQLite profile, create a disposable database, close and reopen it, and confirm data persist. Attempt a second writable instance and confirm the documented single-writer protection. Test stale-lock recovery only by the documented recovery procedure; do not delete a lock file manually.
4. Open every shipped plugin once from the normal application UI. Switch the UI language, inspect icons and representative text containing German, Cyrillic, Greek micro/mu, and status symbols, then export and open one Reports PDF and one spreadsheet.
5. After the server firewall and SMB account prerequisites in P3A-09 pass, compare each visible Backend dialog field/control with the Ubuntu column in the candidate manual and configure Shared PostgreSQL from that column. Mount the same share root at /mnt/progtrack using the manual's CIFS instructions and set PROGTRACK_STORAGE_MOUNT_ANCHOR=/mnt/progtrack in the desktop-launcher environment. Use **Refresh list** to discover progtrack_test_023, select it, and separately authorize **Test connection**, **Use selected**, and **Save** as progtrack_admin when each prompt appears. Enter /mnt/progtrack in **Managed document storage**. Restart ProgTrack and perform a normal read/write/read-back on a disposable record. Confirm pool shutdown and normal application exit. Record manual/UI mismatches in `# Comments:`.
6. In both the Standalone SQLite and Shared PostgreSQL profiles, verify that authentication is required before animal data, the main workspace, or backend-maintenance actions appear. Complete login and then logout; confirm the protected workspace closes before any new user can use it.

[ ] P3A-03 Result
# Comments:

## P3A-04 — Backend dialog, database catalog, and local cache (#214–#218)

Use the two clients with matching Shared PostgreSQL connection context.

1. On Windows, open Settings → Backend. Save a Shared PostgreSQL profile, close the dialog, reopen it, and confirm the Shared radio button, PostgreSQL page, saved non-secret fields, and last active page are restored. Repeat after selecting Standalone SQLite and confirm its radio button/page restore correctly.
2. With a saved PostgreSQL password, confirm the password control displays the fixed asterisk mask ******** and the accessible text says that a password is stored; leaving the field unchanged keeps it. The stored secret itself is never displayed. Save without typing a replacement and reconnect; confirm the stored credential still works. Confirm mTLS is collapsed by default, its toggle uses the existing SVG control, expanding/collapsing immediately changes dialog height, and entered certificate/key/passphrase values survive the toggle.
3. Inspect the discovered database list. Confirm real database names appear exactly once in the correct ProgTrack and non-ProgTrack groups, with visible nonselectable dividers; group labels cannot be selected as databases. If an unknown-status database is returned, confirm it appears in its own group. Confirm an empty list has no selectable database.
4. Confirm initial database discovery and Refresh list do not request backend-administrator credentials. Confirm the list cache persists as the client-local JSON display/selection snapshot and is not treated as authorization or current server truth.
5. Keep Client B's Backend dialog closed. On Client A, perform a supported catalog mutation. Confirm B's cached list is not refreshed while its dialog remains closed and no heartbeat polling refreshes it. Open B's Backend dialog; confirm the dirty revision is checked then and the list refreshes once. Repeat with explicit Refresh and confirm the cache updates only after a successful current server read.

[ ] P3A-04 Result
# Comments:

## P3A-05 — Backend-admin prompts and managed-storage settings (#219, #226, #228)

Use disposable server databases and the least-privilege test share. A new backend-admin authorization is required separately for each operation below; never substitute the ProgTrack user's password. Cancel/rejected credentials must make no server change.

1. From Settings → Backend, test each operation independently, starting without an active backend-admin authorization: Test connection; Use selected; Create database; Archive/unarchive (once to archive and once to unarchive); Backup; Restore; Delete; Transfer SQLite to selected database. For each, confirm one fresh authorization prompt, authorize with the backend-administrator account, and verify the intended result. For the #331 **Use selected** check, choose the disposable database `progtrack_test_023`, configure `progtrack_app` as the runtime account, and authorize as `progtrack_admin`. Its tables are owned by `progtrack_app`; `progtrack_admin` has database ownership, CONNECT, and schema USAGE but no table SELECT. Confirm **Use selected** succeeds, the Database field changes to `progtrack_test_023`, and no grant or role membership is added. Do not click Save as part of this subcheck. For Create database, create a fresh disposable database, select it with its own fresh authorization, restart into it, create the first Lord account if prompted, and confirm the schema exists with no demo animals, measurements, events, or plugin sample records. Do not alter or clear progtrack_test_023. For transfer, review the preflight counts, transfer Test-Transfer-Animal and its document, and confirm them on the Shared client. Do not reuse a successful authorization for the next operation.
2. Cancel one prompt, then try empty, invalid, and underprivileged backend-admin credentials on disposable operations. Confirm no mutation occurs and there is no fallback to the ordinary ProgTrack login. Confirm repeated Discovery/Refresh stays prompt-free.
3. Configure the optional client-local storage credential override in Client A and a separate value in Client B. Confirm the fallback and per-field behavior match the guide, secrets are masked, and neither path nor storage credential is written into PostgreSQL or the catalog JSON.
4. First save a valid managed-storage path while the IT share is reachable. Have IT make that mount unavailable, then restart Client A. Confirm ProgTrack still starts, PostgreSQL-backed animal operations remain available, and the warning identifies the client-local storage problem. Confirm startup creates no share directory. Restore the mount, use Test connection to revalidate, and confirm the warning clears and a managed document can be read/written.
5. Inspect audit/log output for the backend-admin actor and operation result; confirm passwords, key passphrases, and storage credentials are absent.

[ ] P3A-05 Result
# Comments:

## P3A-06 — Shared records, parentage, sessions, account revocation, and config sync (#50, #156, #220, #232, #246)

Use Test-Editor, Test-Viewer, Test-Session, Test-Lord, Test-Mother, Test-Father, Test-Child, and both real clients on the same Shared PostgreSQL database.

1. On Client A, sign in as Test-Editor. In All, select Test-Child and open Edit. Set its eligible parent links and save. Confirm the current parentage appears after a fresh read on Client B and in Heritage's read-only Core projection. Confirm an eligible archived/deceased candidate remains available with its deceased marker, while the ineligible candidate is rejected. In Heritage, confirm real Core parentage controls are read-only.
2. Sign in as Test-Viewer and try the same Core parentage edit. Confirm it is denied without a partial write. Repeat the allowed/denied check for each configured #156 permission combination, including a cross-Unit reference. Before each language pass, use Settings → Language and select English, Deutsch, Italiano, then Русский; repeat the allowed and denied edit and confirm the deceased-candidate marker remains ✟ in every language. Confirm all changes are audited with the acting ProgTrack username and no secrets.
3. Open Test-Child in an editable dialog on Client A. On Client B, confirm the other user sees the active lease and cannot edit the same animal. Cancel on A and use the normal list refresh on B; confirm the lock indicator clears. Confirm two different animals can still be edited by different users.
4. Sign in as Test-Session on Client A, then sign in as Test-Session on Client B. On A's next protected action, confirm the session is revoked and it cannot save or renew its old lease; B remains active. Confirm cleanup of A's old session does not remove B's replacement session/lease.
5. While Test-Session is active on Client B, use Test-Lord on Client A → Master Track → Manage Users. Change one target-account field per cycle (job, role/custom-role membership, direct permission, and Unit); after each save, confirm only Test-Session is forced to log in again, then sign it in again before the next cycle. Confirm editing another account does not revoke Test-Lord. If testing a self-account change, use a disposable administrator and confirm it commits before that administrator is returned to login.
6. On the fresh Shared database, sign in as Test-Viewer and open Settings → Initialize Core configuration. Confirm initialization is denied and the configuration records are not implicitly created. Sign in as Test-Lord and use Settings → Initialize Core configuration. Confirm the seven server-owned configuration records are initialized: animal-roles, role-block-presets, identity-lifecycle-conventions, and user-message-overrides for de, en, it, and ru. Confirm base translations remain available and localized overrides display correctly.
7. In Settings → Conventions → Role setup and Identity and lifecycle conventions, make one reversible change in the disposable database. Confirm the server owns the change and Client B receives it through its existing authenticated configuration refresh/heartbeat. This is separate from #215: do not use the backend-list dirty hook for configuration sync. Restore the original setting afterward.

[ ] P3A-06 Result
# Comments:

## P3A-07 — Managed documents, backup/restore, and branded PDFs (#50, #219, #230, #326)

Use the same test share and disposable Shared database. Never use production documents.

1. In Projects Track, select Test-Project, expand Documents, click Upload, and confirm Test-Editor can upload a file. Open it from Client B, upload a replacement, and confirm Client B sees the replacement after refresh. Keep the file until the backup check below.
2. As Test-Lord, set a disposable institution name/logo in Settings → Conventions → Institution, then replace the logo with a second test image. On Windows and Linux, open All, select Test-Child, open Reports, choose a year and month, export the PDF, and open it. Confirm the institution name/logo appear, report text and pages remain intact, and no overlay is missing or clipped.
3. Use the per-operation backend-admin prompt to back up the disposable database plus managed files, then restore to a separate disposable target. Confirm the project document and institution logo both survive and their bytes/checksums match. Delete only the designated disposable target using its own fresh authorization.
4. In Settings → Backend, run Check current managed documents in read-only mode. Confirm it reports no unexpected missing payload, checksum/size mismatch, stale staging file, or unreferenced file. Do not run destructive cleanup.
5. Have IT make the test share unavailable while PostgreSQL remains reachable. Confirm a managed-document operation fails with a recoverable warning while ordinary database operations continue; no local fallback or false-success is allowed. Restore the share and confirm the document is readable again. Then, through the normal UI, delete the test project document and remove the test logo; confirm both changes persist after refresh/restart. Run Check current managed documents again and record any finding.

[ ] P3A-07 Result
# Comments:

## P3A-08 — Schema migration and old-client compatibility (#50, #231)

Use a disposable clone of the prior schema and the exact older client build required by the current minimum-client policy. Do not point an old client at a shared database containing real data.

1. Restore the old-schema clone to a disposable test database using the approved database backup/restore procedure. Do not manually create entity_leases or other ProgTrack tables.
2. Start the candidate through its normal startup and authorized migration flow. Confirm the registered schema migration creates/verifies the complete required structure, including entity_leases, and records the new schema version only after success.
3. Restart the candidate twice. Confirm migration is idempotent: no duplicate schema objects or data, and the current client remains usable.
4. Start the older client against the migrated database. Confirm the compatibility gate rejects it before it can read/write using stale schema assumptions. Confirm migration start/result and rejection are auditable and expose no secrets.


[ ] P3A-08 Result
# Comments:

## P3A-09 — Same-server Ubuntu PostgreSQL/Samba and localized client setup (#227, #226, #228)

Use only the disposable server, the ordinary client OS accounts, and the IT test directory. Do not use psr-admin for SMB storage. The server profile and non-secret ProgTrack values are listed above.

1. On Ubuntu, record sudo testparm -s; the effective values for interfaces, bind interfaces only, smb ports, netbios name, map to guest, usershare allow guests, server min protocol, and ntlm auth; systemctl is-active smbd; sudo ss -ltnp | grep -E ':(445|139)\b'; sudo ufw status verbose; getent group progtrack; id smb_app; namei -l /srv/progtrack-managed /srv/progtrack-managed/documents /srv/progtrack-managed/config-assets; and getfacl -p for those three directories. Confirm PostgreSQL and the SMB share remain on 172.16.9.77; confirm the share points to /srv/progtrack-managed, is not browsable, has valid users = @progtrack, force group = progtrack, masks 0660/02770, and the child directories inherit group access. The original audit found smb_app absent, UFW inactive, and smbd listening on all IPv4/IPv6 addresses on 445 and 139. Mark this step FAIL/PENDING until IT provisions the dedicated account and restricts network access.
2. Have IT create or verify the non-login Unix account smb_app, add it to progtrack, and enable its separate Samba password interactively. Confirm it has no sudo/admin rights and id smb_app shows the expected group. Never record the password in this file.
3. Have IT harden the share before client acceptance: set guest ok = no and map to guest = Never; disable user shares with guests; use only TCP 445 for these modern clients; set a short NetBIOS name such as PROGTRACK to remove the current length warning. Keep the already verified server min protocol = SMB2_02 and ntlm auth = ntlmv2-only. Restrict TCP 445 to the institution-approved client network and PostgreSQL TCP 5432 to approved database clients. Preserve a verified SSH/admin rule before enabling UFW. If binding Samba to an interface, include loopback and the trusted LAN interface. Run testparm again, restart/reload smbd, and confirm only approved listener/network exposure; do not expose SMB or PostgreSQL to the public Internet.
4. Review all four candidate files: manual/ProgTrack_User_Guide - en.html, manual/ProgTrack_User_Guide - de.html, manual/ProgTrack_User_Guide - it.html, and manual/ProgTrack_User_Guide - ru.html. Confirm each has a field-by-field table with the exact localized Backend UI labels and Windows/Ubuntu columns; the values, separate PostgreSQL and SMB accounts, TLS/CA-only example, and button workflow agree across languages. Confirm the Windows UNC and Ubuntu mount point identify the same share root, ProgTrack appends documents and config-assets, TCP 5432 is PostgreSQL and TCP 445 is SMB, and no secret appears. P3A-01 and P3A-03 must have followed these manual columns on fresh clients and recorded every UI/manual mismatch in their own comments.
5. As the ordinary Windows OS account that runs ProgTrack, open File Explorer and enter \\172.16.9.77\progtrack-managed directly; the share is hidden from browse lists. Authenticate as smb_app. In **Settings → Backend → Shared PostgreSQL**, enter that UNC root in **Managed document storage** and smb_app under **Storage user**. Create, read, rename, and delete one disposable file in the designated test directory.
6. On the ordinary Ubuntu client, mount //172.16.9.77/progtrack-managed at /mnt/progtrack using the root-only /etc/samba/credentials-progtrack file for smb_app. Confirm findmnt --mountpoint /mnt/progtrack --output SOURCE,FSTYPE,TARGET reports the expected CIFS mount. Set PROGTRACK_STORAGE_MOUNT_ANCHOR=/mnt/progtrack in the desktop-launcher environment and set **Settings → Backend → Managed document storage** to /mnt/progtrack (the share root, not /mnt/progtrack/documents). Create a disposable file from Ubuntu and read/rename/delete that same file from Windows. This confirms both clients reach the same server-side directory.
7. On macOS 13+, connect in Finder through **Go → Connect to Server** to smb://172.16.9.77/progtrack-managed, authenticate as the approved SMB account, and use the mounted volume root in **Managed document storage**. Verify the same test file is visible. This is an OS/client-guide walkthrough only, not native macOS package acceptance.
8. Confirm the candidate manuals' Windows, Ubuntu, and macOS steps match their actual UI labels. The IT/operator verification must state that local paths/credentials are not stored in PostgreSQL, an unavailable share never falls back to SQLite, least privilege is used, and the same test file is visible from both Windows and Ubuntu. Record any inaccurate instruction or translation.

[ ] P3A-09 Result
# Comments:

## P3A-10 — Login-first gate and restricted outage recovery (#229, #327)

Use two disposable Windows OS profiles preconfigured by IT with the same Shared connection profile: one with no prior Lord marker and one for sequential ProgTrack sign-ins. Keep a known-good Shared profile.

1. With the server reachable, start the candidate as an ordinary user. Confirm the locked login surface appears first: no main window, animals, menus, backend maintenance, second startup gate, or server operation is visible before authentication. Sign in and confirm only the authenticated user's authorized UI is created.
2. In the OS profile with no prior Lord login, make the configured server unreachable and restart. Confirm an ordinary user sees only the localized unavailable warning and Exit; no login/data/SQLite/seed or recovery controls are exposed.
3. In the second OS profile, connect while the server is reachable and sign in live as Test-Lord. Close ProgTrack, make the server unreachable, and restart. Confirm only client-local connection settings, Erneut verbinden, and Exit are available; no server-side discovery, create/archive/backup/restore/delete/transfer, or migration action is available.
4. Restore the server and reconnect. Sign in live as an ordinary non-Lord in that same OS profile, close ProgTrack, disconnect the server, and restart. Confirm the previous Lord marker has been cleared and the profile now shows only the ordinary-user warning and Exit.
5. With the server reachable, test rejected PostgreSQL credentials and an incompatible schema. Confirm neither is misclassified as a network outage or unlocks Lord recovery. Test normal logout and cancelled reauthentication; confirm protected UI/data are removed and no hidden unauthenticated application remains.

[ ] P3A-10 Result
# Comments:

## P3A-11 — Attribute new Windows fault dumps (#327)

WinDbg is required. If it is unavailable, mark this test SKIP and leave the dump-origin part of #327 open; do not mark the login-gate test above as skipped.

1. Close every ProgTrack process and start exactly one candidate under WinDbg. Confirm the new log at %LOCALAPPDATA%\ProgTrack\logs\launcher_fault_<PID>.log identifies timestamp, PID, launcher version, and executable.
2. Break on first-chance 0x8001010d and 0x80040155 while testing connection, login, invalid credentials, cancellation, and success. Record process/thread, native exception/module/stack, UI action, and whether the process terminates. If no exception occurs, record that; do not claim the historic dumps are explained.

[ ] P3A-11 Result
# Comments:

## Issue coverage

| Open issue | Manual checks |
|---|---|
| #49 Linux runtime/package/license readiness | P3A-02, P3A-03 |
| #50 Shared database, transactions, transfer, leases, storage, backup | P3A-01, P3A-04 through P3A-08 |
| #156 Core parentage on Shared PostgreSQL | P3A-06 |
| #214–#218 Backend profile, database list, mask, mTLS, grouping/cache | P3A-01, P3A-04 |
| #219 Separate per-operation backend-admin authorization | P3A-01, P3A-05, P3A-07 |
| #220 One active session across clients | P3A-06 |
| #226 Managed-storage credentials | P3A-01, P3A-05, P3A-09 |
| #227 Localized PostgreSQL/SMB manuals and IT walkthrough | P3A-01, P3A-03, P3A-09 |
| #228 Invalid managed-storage path warning/recovery | P3A-05, P3A-07, P3A-09 |
| #229 Empty locked Shared startup and reconnect | P3A-01, P3A-10 |
| #230 Managed-document integrity and reconciliation | P3A-07, P3A-09 |
| #231 Schema migration and old-client compatibility | P3A-08 |
| #232 Account-change session revocation | P3A-06 |
| #241 No automatic Shared seed; explicit local demo choice | P3A-01, P3A-05 |
| #246 Backend-owned mutable configuration and client sync | P3A-04, P3A-06 |
| #299 Windows launcher copyright property | P3A-02 |
| #326 Branded PDF output | P3A-07 |
| #327 Login-first gate and restricted recovery | P3A-01, P3A-10 |
| #327 Fresh Windows dump attribution | P3A-11 |
| #331 Select with separate backend administrator and runtime account | P3A-01 step 4, P3A-05 step 1 |
