# Windows launcher source boundary

The existing `source/launcher.py`, `launcher_small.spec`, Windows version
resource, and `build_launcher_small.bat` are the Windows launcher/build inputs.
They remain separate from `source/launcher/linux/`; no Windows DLL/PYD is ever
copied into the Linux artifact.
