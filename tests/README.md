# Current automated tests

This directory contains the maintained automated tests for ProgTrack 0.3.0. Mutable animal, project, housing, medical, report, sample, session, and role data are tested through the configured backend; retired JSON stores are not runtime fixtures. Test modules are versioned. Local run artifacts and historical UI evidence are ignored.

Run the Phase 3 suite from the release root:

```powershell
python -m pytest -q
```

After the final Windows and Linux 0.3.0 archives and their `.sha256` sidecars
have been assembled in `Q:\GitHub\Release Packages`, run the focused package
acceptance command from the repository root on Windows:

```powershell
python tests/run_phase3_package_acceptance.py
```

It verifies both sidecar hashes, safely extracts each exact archive to a
temporary directory, checks that the shipped plugin folders match the #48
architecture matrix, and runs the existing #50 backend/schema/session/audit
tests against the code imported from each extracted package. It also checks the
Linux package manifest and the Copyright version resource in the final Windows
`Launcher.exe` for #299. The command fails if either final archive is missing
or its checksum does not match. It never builds or edits an artifact. The same
archives must then be used for Manual Retest 3A; native OS and live PostgreSQL
acceptance remain manual checks.

The package directory can be overridden for a controlled handoff with
`$env:PROGTRACK_RELEASE_PACKAGES_DIR`. Do not point it at a staging/build
directory.

Last recorded complete default run before the release-package acceptance wiring
(2026-11-23, Windows/Python 3.13, offscreen Qt):
822 passed, 1 skipped, 289 deselected, 1,040 unittest subtests passed, and 2
`pypdf` deprecation warnings in 1,375.94 seconds. The single skip is the
candidate-EXE copyright check; no extracted launcher path was supplied. The
289 deselected items are not passes: Phase 4 Heritage cases are intentionally
deferred, while source tests marked `isolated_qt` run in the selected suite
through subprocess wrappers. That historical result does not include the new
assembled-package command above; no 0.3.0 release packages were present when
this wiring was added.

Automated cross-checks for localized manual section structure (#227) and Windows launcher copyright metadata (#299) are in `test_phase3_manual_guide_contract_227.py` and `test_launcher_windows_copyright_299.py`. To validate an extracted Windows launcher executable, set `PROGTRACK_TEST_WINDOWS_LAUNCHER` to its full path before running the #299 test; native Explorer Details verification remains in Manual Retest 3A.

The default Phase 3 run excludes tests marked `phase4_heritage` and `extended_heritage`. This keeps Heritage Track renderer, layout, cache, and interaction checks with the Phase 4 overhaul while retaining shared Core/backend contracts such as #156. Deselected Heritage tests remain discoverable and are not counted as passing. The Phase 3 completion run is the standard command above; do not run the Heritage suite as part of this gate. Phase 4 can run the marked set explicitly:

```powershell
python -m pytest -o addopts= -m "phase4_heritage or extended_heritage" -q
```

Manual Retest 3A contains only native, site-specific, or human-judgment checks that automation cannot establish. Issue #325 tracks test/fixture reconciliation and the results for the Phase 3 default suite.

Run only the complete Heritage Track suite with live per-test and per-case progress. Each matrix case reports its selection, mode, depth, or fixture details as soon as it finishes, while the remaining cases continue:

```powershell
python -m pytest -o addopts= -m "phase4_heritage or extended_heritage" -s -q
```

The Heritage suite includes renderer/layout behavior and the current plugin
contracts. Run it as Phase 4 work; this task does not report Heritage results.
Native/package acceptance remains a separate manual gate.

Qt tests that trigger platform-specific native widget lifetime crashes are run
by small pytest wrappers in separate processes; the wrapped tests still run and
fail the default suite if they fail. This currently applies to the dialog
geometry check (`qt_phase2b_dialog_geometry.py`), #247 deletion, #268 editor
validation, #309 effective-status and five-editor warning flows, #315 event
adapters, #319 localized event dialogs, and the SVG palette/rendering module.
The isolation contains Qt teardown/state contamination; it does not skip or
xfail the checks.

The synthetic 1,000-node Heritage router check is a non-blocking performance
benchmark reserved for Phase 4. Its 20-second target is a reference metric,
not a functional test gate. Run it by itself when measuring changes:

```powershell
python -m pytest -o addopts= -q -m non_blocking_benchmark tests/test_heritage_track_stability_step1.py
```

The branding audit remains a script because it creates and visually evaluates
ten PDF exports:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONPATH = (Resolve-Path .).Path
python tests/test_phase2b_branding_pdf_exports.py
```

All branding-audit PDFs, rendered PNG previews, the generated logo, the contact
sheet, and the JSON evaluation are written to exactly one folder:
`%USERPROFILE%\Desktop\test`. The script does not create timestamped sibling
folders on the Desktop; repeated runs reuse/overwrite the same named artifacts.

The pre-reconciliation suite snapshot is preserved locally under
`archive/test_suite_pre_325_2026-11-23`; see its README and SHA-256 manifest.
The Phase 3 coverage map is `phase3_coverage_matrix.md`; it maps the live
0.3.0 issue set to current automated files or the specific remaining manual /
external acceptance.
