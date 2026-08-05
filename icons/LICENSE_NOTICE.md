# ProgTrack Icon and Artwork License Notice

Copyright (C) 2026 Dimitri L. Lindenwald, PhD, Deutsches Primatenzentrum GmbH,
Leibniz Institute for Primate Research, Kellnerweg 4, 37077 Goettingen,
Germany.

The image and icon files in this folder are ProgTrack graphical assets. Unless
a separate notice is provided for an individual file, they are licensed as part
of ProgTrack under the GNU General Public License version 3.0 or later. See the
repository-level files `../LICENSE` and `../LICENSE_NOTICE.md`.

Ownership remains with Deutsches Primatenzentrum GmbH; the GPL grants the
rights to use, modify, and redistribute these assets under its terms.

## Included Icon and Artwork Files

- `Splash.png`
- `deletion.png`
- `error.png`
- `file_csv.png`
- `file_img.png`
- `file_pdf.png`
- `file_text.png`
- `grin.png`
- `information.png`
- `job_keeper.png`
- `job_lord.png`
- `job_manager.png`
- `job_master.png`
- `job_researcher.png`
- `job_tester.png`
- `job_vet.png`
- `job_AWO.png`
- `progtrack_icon.ico`
- `question.png`
- `warning.png`

Windows-generated cache files such as `Thumbs.db`, if present, are not
ProgTrack artwork source assets.

## UI SVG artwork and shared semantic IDs

The canonical SVG UI masters are stored in `ui/` and are licensed under the
same GPL-3.0-or-later terms.  The semantic IDs in `ui/manifest.json` may share
one artwork file intentionally; this keeps the visual meaning identical across
role, status, and Medi Track contexts.  The current shared mappings are:

- `role.breeding` -> `ui/pedigree_symbol.svg`
- `status.partner` -> `ui/role_partner.svg`
- `medi_track.filter.current_sick` -> `ui/status_sick.svg`
- `medi_track.filter.current_abnormal` and `status.warning` -> `ui/status_abnormal.svg`

Alias copies with those former semantic names are not separate artwork and are
not shipped.  The editable masters and PNG review previews are maintained in
`Q:/GitHub/Graphics/SVG/UI` and `Q:/GitHub/Graphics/UI`; the application uses
the packaged SVG masters directly without a PNG fallback.
