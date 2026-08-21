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
- `tech.png`

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
not shipped.  Editable masters are maintained in
`Q:/GitHub/Graphics/SVG/UI`; the application uses the packaged SVG masters
directly without a PNG fallback.  Optional raster review evidence outside the
release is not a runtime asset.  The shared Qt loader may adapt only the
canonical outline colour in memory when required by the active palette.

## UI SVG artwork and manifest

Every file under ui/ is covered by this notice: manifest.json, all action,
account, control, flag, flow, heritage, measurement, Medi, network, pedigree,
role, status, and toggle SVGs currently shipped there. This includes
account_awo.svg, account_guest_locked.svg, account_it_specialist.svg, account_lord.svg,
account_manager.svg, account_master.svg, account_researcher.svg,
account_user.svg, account_veterinarian.svg, accountkeeper.svg,
action_add.svg, action_archive.svg, action_delete.svg,
action_edit.svg, action_edit_role.svg, action_refresh.svg,
action_restore.svg, action_settings.svg, control_decrement.svg,
control_increment.svg, flag_de.svg, flag_gb.svg, flag_it.svg,
flag_ru.svg, flow_freezer.svg, heritage_placeholder.svg,
measure_blood.svg, measure_sperm.svg, measure_urine.svg,
measure_weight.svg, medi_ever_abnormal.svg, medi_ever_experiment.svg,
medi_ever_sick.svg, network_insert_symbol.svg, pedigree_symbol.svg,
role_experimental.svg, role_experimental_offspring.svg,
role_female.svg, role_male.svg, role_offspring.svg, role_partner.svg,
role_unknown.svg, status_abnormal.svg, status_deceased.svg,
status_in_experiment.svg, status_not_pregnant.svg, status_offspring.svg,
status_ok.svg, status_possible.svg, status_pregnant.svg,
status_sick.svg, toggle_collapse.svg, and toggle_expand.svg.

All of these SVGs are GPL-3.0-or-later ProgTrack artwork owned by Deutsches
Primatenzentrum GmbH. manifest.json is the authoritative semantic mapping;
the application uses the packaged SVG masters directly without a PNG fallback.
