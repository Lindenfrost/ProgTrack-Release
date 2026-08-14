# Windows launcher source tree

Everything required to build and validate the Windows launcher is kept in this
directory: `launcher.py`, `launcher_small.spec`, `hiddenimports.txt`, version
metadata, `build_launcher_small.bat`, `package_release.ps1`, the Windows icon,
the pinned build requirements, the frozen-runtime smoke test, and the component
inventory generator. Run the batch build from this directory; its relative
paths intentionally resolve to this tree.

The Windows tree remains separate from `../linux/`. No Windows DLL/PYD runtime
is copied into the Linux artifact.
