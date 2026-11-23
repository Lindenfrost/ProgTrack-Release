# Current automated tests

This directory contains automated tests for the Phase 3 ProgTrack 0.3.0 candidate and its current backend architecture. Mutable animal, project, housing, medical, report, sample, session, and role data are tested through the configured backend; retired JSON stores are not runtime fixtures. The suite and fixtures are being reconciled against the current issue contracts under #325.

Run the aggregate suite from the release root:

```powershell
python -m pytest -q
```

Automated cross-checks for localized manual section structure (#227) and Windows launcher copyright metadata (#299) are in test_phase3_manual_guide_contract_227.py and test_launcher_windows_copyright_299.py. To validate an extracted Windows launcher executable, set PROGTRACK_TEST_WINDOWS_LAUNCHER to its full path before running the #299 test; the native Explorer Details view remains in Manual Retest 3A. The default command uses pytest.ini and excludes only explicitly marked extended_heritage cases. Run the extended set explicitly with: python -m pytest -o addopts= -q. Do not count omitted markers as passes; issue #325 tracks the full coverage reconciliation, and Manual Retest 3A is reserved for human/native/site-only acceptance.

Run only the complete Heritage Track suite with live per-test and per-case progress. Each matrix case reports its selection, mode, depth, or fixture
details as soon as it finishes, while the remaining cases continue:

```powershell
$heritageTests = Get-ChildItem -LiteralPath tests -Filter 'test_heritage_*.py' |
    Select-Object -ExpandProperty FullName
python -m pytest -s -q $heritageTests
```

The Heritage suite includes the current structural contracts, the two
vertical modes, renderer-calibrated geometry, cache/ownership boundaries,
manual-placement precedence, collapse removal, hover-free interaction, the
existing middle-button pan contract, and the new left-button free-space pan
contract. Native/package acceptance remains a separate manual gate.

The Qt dialog geometry test is kept as `qt_phase2b_dialog_geometry.py` and is
run by `test_phase2b_qt_geometry_isolated.py` in a separate process to avoid
platform-specific PyQt teardown crashes when combined with headless plotting.

The synthetic 1,000-node Heritage router check is a non-blocking performance
benchmark. Its 20-second target is a reference metric, not a functional test
gate: routing completeness and validity still fail normally, while a slower
valid result is reported as XFAIL. Run it by itself when measuring changes:

```powershell
python -m pytest -q -m non_blocking_benchmark tests/test_heritage_track_stability_step1.py
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

The former JSON/0.1.2 tests are preserved outside this discovery path in
`Q:\GitHub\Issue Tracker\Tests\archive\legacy_tests_0.1.2` for historical
reference only.
