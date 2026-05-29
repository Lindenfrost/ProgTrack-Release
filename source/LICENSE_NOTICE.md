# ProgTrack Launcher Source License Notice

Copyright (C) 2026 Dimitri L. Lindenwald, PhD, Deutsches Primatenzentrum GmbH,
Leibniz Institute for Primate Research, Kellnerweg 4, 37077 Goettingen,
Germany.

The files in this folder are ProgTrack launcher build sources. Unless a separate
notice is provided for an individual file, these resources are the property of
Deutsches Primatenzentrum GmbH and are distributed with ProgTrack under the
same project license: GNU General Public License version 3.0 or later.

This notice supplements the repository-level license files `../LICENSE` and
`../LICENSE_NOTICE.md`. Ownership remains with Deutsches Primatenzentrum
GmbH; the GPL grants the rights to use, modify, and redistribute these resources
as part of the ProgTrack distribution under its terms.

## Included Resources

- `launcher.py` — Main launcher script that bootstraps the portable Python runtime
  and starts the ProgTrack application.
- `launcher_small.spec` — PyInstaller specification for building the compiled
  launcher executable.
- `build_launcher_small.bat` — Windows batch script to invoke the PyInstaller build.
- `hiddenimports.txt` — List of hidden imports required by the PyInstaller build.
- `progtrack_icon.ico` — Application icon used for the compiled launcher.

## Disclaimer

The resources are provided without warranty to the extent permitted by law. No
warranty is given for completeness, correctness, fitness for a particular
purpose, or suitability for scientific, clinical, regulatory, or operational
decisions.
