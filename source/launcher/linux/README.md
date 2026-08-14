# ProgTrack native Linux launcher

This launcher is the entry point for the folder-portable
`ProgTrack-<version>-linux-x86_64.tar.gz` artifact. It uses the system
`python3` selected by `PROGTRACK_PYTHON` (the source tree pins the supported
Python minor version in `requirements-linux-managed.txt`) and never requires
root. The release-packaging script and its requirements file live beside this
launcher so the Linux build is self-contained at the source level.

By default, application data are resolved through the XDG data/config/cache/
state directories. Set `PROGTRACK_PORTABLE=1` only when the extracted folder is
writable and a self-contained `ProgTrackData/` tree is desired. Use
`./ProgTrack --diagnose-paths` to print the resolved bundle and runtime paths.

The bundle is intentionally separate from the Windows launcher: it contains no
`.exe`, `.dll`, or Windows `.pyd` runtime. The first Linux artifact is a
managed-runtime source payload; IT/user validation must install the pinned
Linux requirements and distribution Qt/font prerequisites before startup.
