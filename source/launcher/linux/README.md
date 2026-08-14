# ProgTrack native Linux launcher

This source tree builds the folder-portable `ProgTrack-<version>-linux-x86_64.tar.gz`
artifact. The release launcher uses a bundled CPython runtime and does not require
host Python, pip, PyQt, scientific packages, fonts, or a PostgreSQL client install.
`requirements-linux-bundled.txt` records the exact package pins used by the
manifest-driven assembler `package_linux_release.py`.

The pinned target is Linux Mint 22.3 x86_64 (manylinux_2_28/glibc-compatible).
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

The local engineering artifact is deliberately not advertised as supported until
manual native Linux validation passes: clean-machine double-click launch, Qt/font
rendering, PDF/XLSX export, SQLite, PostgreSQL/TLS, XDG/read-only behavior, locks,
and launcher diagnostics. Do not publish the tarball before that gate is complete.