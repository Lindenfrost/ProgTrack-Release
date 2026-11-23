# Manual retest — Phase 3A (native and site acceptance only)

Use this file only after the automated test reconciliation in #325 has produced a reviewed candidate and a complete, recorded suite run. It contains only checks that require a real supported workstation, the institution's live network/storage setup, or human review of the displayed result. Do not repeat schema, service, dialog, authorization, migration, seed, or failure-injection tests here; those belong in the automated suite and its coverage map.

Record the exact candidate commit and each package SHA-256 before starting. Do not use production data. Screenshots are not requested.

## Required setup

The test coordinator or local IT prepares these disposable items before the checks:

- One Windows client and one clean Linux Mint 22.3 Zena x86_64 workstation for the final platform check. If Linux Mint is unavailable, mark that test SKIP; Ubuntu is not a substitute for the #49 target.
- The exact final Windows and Linux candidate artifacts, with their hashes and setup notes.
- A prepared disposable local SQLite demo database for the Linux visual/PDF check; do not use this check to retest #241 first-start seed behavior.
- A real reachable Shared PostgreSQL test database with the candidate schema, IT-approved TLS settings, and no implicit seed data.
- An institution-managed document share mounted on both clients. The clients may use different local paths to the same logical share.
- Two distinct application accounts, Test-User-A and Test-User-B, both with the same User role, Test Unit, and the existing job permissions needed to open Edit Animal for Test-Animal. Test-User-A will also be used to check same-account session replacement.
- One disposable animal named Test-Animal in the PostgreSQL database. Create it through ProgTrack if it is absent; do not seed PostgreSQL from the bundled demo data.

For each test, leave the checkbox empty until the result is known. Replace it with [v] for PASS or [x] for FAIL; write a reason after SKIP. Add observations on the following # line. Do not mark a test PASS when a prerequisite was unavailable.

## P3A-01 — Windows Explorer shows the launcher copyright (#299)

1. In File Explorer, open the extracted final Windows candidate folder named in the handoff.
2. Right-click Launcher.exe and select Properties, then open Details.
3. Confirm the Copyright field is exactly: Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH.
4. Record the displayed File version and Product version without changing them; compare them with the candidate handoff.

[ ] P3A-01 Result: PASS / FAIL / SKIP
# Comments:

## P3A-02 — Clean native Linux Mint launch and visible output (#49)

1. On the clean Linux Mint 22.3 workstation, extract the exact Linux candidate artifact identified in the handoff. Do not install Python, Qt, fonts, libpq, or ProgTrack dependencies.
2. Ask IT to place the extracted application folder under a read-only location. Open the file manager and start the ProgTrack launcher from the extracted package.
3. Close ProgTrack. Start it once from the desktop/application menu while the working directory is your home folder.
4. Confirm both launches reach the normal application window without a dependency-install prompt, missing-library error, or attempt to write into the read-only application folder.
5. Open the prepared local SQLite demo database supplied with the QA handoff. Open Reports, select Test-Animal, export one PDF, and open it in the normal PDF viewer. Do not use this check to retest the automated #241 first-start seed choice.
6. Confirm the report opens, text and symbols are legible, and the application closes normally. Record the OS release and whether each launch/export step passed.

[ ] P3A-02 Result: PASS / FAIL / SKIP
# Comments:

## P3A-03 — Real Shared PostgreSQL, two clients, and the institution share (#49/#50/#156/#220/#226/#228/#230/#232)

Use the separate Windows and Linux clients, the prepared PostgreSQL database, Test-User-A, Test-User-B, Test-Animal, and the IT-mounted share from Required setup. This checks the actual installed clients and site network; automated concurrency and service cases are not repeated here.

1. On each client, open Settings → Backend. Select Shared PostgreSQL, enter the IT-provided connection and TLS settings, and enter that client's local path to the same managed-document share. Select Test connection, save, close ProgTrack completely, and reopen it.
2. Confirm each client starts on Shared PostgreSQL and reaches the same server-side Test-Animal record. The test database itself must have been prepared without bundled seed data; do not use this manual run to replace the automated #241 seed checks.
3. On Windows, sign in as Test-User-A. Open All, select Test-Animal, and click Edit. Leave the Edit Animal dialog open.
4. On Linux, sign in as Test-User-B. Open All and locate Test-Animal. Confirm its row is grey. Try to open it for editing; it must remain read-only or refuse the edit with the lock message.
5. Cancel the Edit Animal dialog on Windows. On Linux, use the list's normal refresh action or wait for its next existing refresh. Confirm Test-Animal loses the grey state and can be opened for editing.
6. Close both sessions. Sign in as Test-User-A on Windows, then sign in as the same Test-User-A on Linux. On Windows, make one normal protected request or wait for the next session check. Confirm Windows returns to login and Linux remains active.
7. On Windows, open Settings → Conventions → Institution and save a disposable test logo using the configured share. Refresh the Institution view on Linux and confirm the same logo appears.
8. Close ProgTrack on Linux. Ask IT to make only Linux's mounted share unavailable while leaving PostgreSQL reachable. Reopen ProgTrack. Confirm the main window and Test-Animal remain available with a storage warning; restore the mount and confirm the logo opens again.

[ ] P3A-03 Result: PASS / FAIL / SKIP
# Comments:

## P3A-04 — Human review of the localized PostgreSQL setup instructions (#227)

1. Open each guide: manual/ProgTrack_User_Guide - en.html, de.html, it.html, and ru.html.
2. In each guide, read the section PostgreSQL network accounts and client setup (localized in that guide).
3. Follow the described account separation and the Settings → Backend → Shared PostgreSQL steps on the candidate. Confirm the visible field and button names match the actual dialog.
4. Confirm each guide says the managed-document path is configured per client for the same logical share, and does not claim that PostgreSQL stores a client's absolute path or that SQLite is used as a fallback.
5. Record any translation that is unclear, incorrect, or inconsistent with the candidate. A structural presence check is automated; this human review is for meaning and usability.

[ ] P3A-04 Result: PASS / FAIL / SKIP
# Comments:
