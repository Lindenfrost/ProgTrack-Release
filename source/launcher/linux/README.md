# ProgTrack native Linux launcher

This source tree builds the folder-portable `ProgTrack-<version>-linux-x86_64.tar.gz`
artifact. The release launcher uses a bundled CPython runtime and does not require
host Python, pip, PyQt, scientific packages, fonts, or a PostgreSQL client install.
`requirements-linux-bundled.txt` records the exact package pins used by the
manifest-driven assembler `package_linux_release.py`.
By default, the assembler writes only the final tarball and its SHA-256 sidecar
to `Q:\GitHub\Release Packages`; build intermediates remain in temporary
storage. Test and publish the same final archive from that folder.

The Phase 3 native acceptance target is the designated Ubuntu x86_64 test
workstation. Record its exact Ubuntu release, kernel, architecture, and glibc
version; passing there does not certify every Ubuntu release. The archive uses
a manylinux_2_28-compatible runtime baseline.
The bundle includes CPython 3.13.15, PyQt6/Qt6, the plotting/scientific/PDF/XLSX
stack, Matplotlib fonts, and Psycopg's binary PostgreSQL client with libpq/TLS
libraries. It contains no Windows `.exe`, `.dll`, or `.pyd` runtime. The POSIX
entry point is `./ProgTrack`; it sets the bundled runtime and Qt/library paths and
refuses to fall back to host Python unless `PROGTRACK_ALLOW_EXTERNAL_PYTHON=1` is
explicitly set for source-tree diagnostics.

By default application data use XDG data/config/cache/state directories, so the
read-only application folder remains untouched. Set `PROGTRACK_PORTABLE=1` only
when the extracted folder is writable and a local `ProgTrackData/` tree is wanted.
`./ProgTrack --diagnose-paths` prints the resolved paths.

The portable `fonts/` directory includes a dedicated `LICENSE_NOTICE.md`.
It identifies the rights holders and terms for the unchanged DejaVu, STIX, and
Computer Modern/BaKoMa fonts; the upstream Matplotlib license files remain in
`fonts/matplotlib/`.

The Linux 0.3.0 candidate contains the ProgTrack 0.3.0 application payload. Native Linux
support remains unverified until the required clean-machine acceptance passes:
double-click launch, Qt/font rendering, PDF/XLSX export, SQLite, PostgreSQL/TLS,
XDG/read-only behavior, locks, and launcher diagnostics. See issue #49; this
artifact does not by itself declare Linux as a validated supported platform.
