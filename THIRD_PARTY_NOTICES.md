# Third-Party Notices

This binary launcher bundle contains third-party runtime components collected by
PyInstaller. Those components are not authored by the ProgTrack Launcher
copyright holder and remain governed by their own licenses.

The table below reflects the libraries intentionally bundled by the current
small launcher build. License text files copied from the build environment or
from the runtime bundle are stored in `third_party_licenses/`.

| Component | Version in build | License / notice |
| --- | ---: | --- |
| Python runtime | 3.13.3 build environment | Python Software Foundation License |
| SQLite runtime used by Python `sqlite3` | 3.49.1 in the Windows 0.1.2 launcher | Public domain; see `LICENSE_SQLITE.txt` |
| PyInstaller bootloader/runtime | 6.14.1 | GPLv2-or-later with PyInstaller bootloader exception |
| PyQt6 | 6.7.1 | GPL v3 |
| PyQt6-sip | 13.11.1 | SIP license |
| PyQt6-Qt6 | 6.7.3 | Qt license terms |
| Qt runtime libraries bundled via PyQt6 | Qt 6.x | GPL/LGPL/commercial Qt terms; this GPL bundle uses GPL-compatible redistribution |
| matplotlib | 3.10.0 | Matplotlib license |
| NumPy | 2.2.5 | BSD-style license |
| pandas | 2.2.3 | BSD 3-Clause |
| SciPy | 1.15.3 | BSD 3-Clause plus bundled-library notices |
| OpenBLAS / LAPACK components in SciPy wheels, if present | bundled by SciPy | BSD-style notices included in SciPy license text |
| openpyxl | 3.1.5 | MIT |
| reportlab | 4.4.10 | BSD-style ReportLab license |
| Pillow | 11.1.0 | HPND / MIT-CMU style license |
| numexpr | 2.10.2 | MIT-style license |
| python-dateutil | 2.9.0.post0 | Dual license |
| pytz | 2026.2 | MIT |
| fontTools | 4.62.1 | MIT |
| ContourPy | 1.3.3 | BSD 3-Clause |
| kiwisolver | 1.5.0 | Modified BSD |
| charset-normalizer | 3.4.7 | MIT |
| pyqtgraph | 0.14.0 | MIT |
| DejaVu fonts | bundled with matplotlib | DejaVu font license |
| STIX fonts | bundled with matplotlib | STIX font license |
| ReportLab bundled fonts | bundled with reportlab | See ReportLab font license files |

## Important Release Note

This bundle was rebuilt from the repository-local pip virtual environment, not
from the user's Conda environment. The copied `_internal/` runtime no longer
contains Intel MKL DLLs.

This notice file is practical release documentation, not legal advice.
