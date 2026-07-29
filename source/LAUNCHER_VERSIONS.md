# ProgTrack Launcher Versions

## 0.1.2

Date: 2026-07-29

- Uses neutral public release metadata without implementation details.
- Release archives are assembled from a clean PyInstaller OneDir runtime so
  native Python extension modules are not omitted by Git ignore rules.
- Embedded Windows version metadata:
  - FileVersion: `0.1.2.0`
  - ProductVersion: `0.1.2`

## 0.1.1-log-menu

Date: 2026-06-13

- Updated launcher variant for the Phase 0 `Open logs folder` work.
- Writes launcher error and fault logs under the central `logs` folder for source launcher runs.
- Appends timestamped launcher error entries instead of replacing the previous error log.
- Embedded Windows version metadata:
  - FileVersion: `0.1.1.0`
  - ProductVersion: `0.1.1-log-menu`

## 0.1.0 RC

Date: 2026-05-29

- Initial public release-candidate launcher variant.
