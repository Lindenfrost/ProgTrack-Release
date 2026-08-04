# ProgTrack 0.2.1

<p align="center">
  <img src="icons/Splash.png" alt="ProgTrack splash" width="520">
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.2.1-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-lightgrey">
  <img alt="Runtime" src="https://img.shields.io/badge/runtime-portable%20Python-green">
  <img alt="License" src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue">
</p>

**ProgTrack** is a portable, modular desktop application for animal-centered
research workflows. It combines animal records, role-specific measurements,
reproductive events, medical history, cage placement, pedigree data, sample
tracking, projects, reports, and planning tools in one inspectable Windows
bundle.

Version `0.2.1` is the Phase 2B backend release. It uses one backend service
contract for the complete application and provides a tiny local SQLite profile
for one workstation or testing plus a shared PostgreSQL profile for networked
facilities.

## At a glance

| Area | What ProgTrack provides |
| --- | --- |
| <img src="icons/ui/role_offspring.svg" width="24" alt="Animal"> Animal records | Immutable identity, role-specific fields, lifecycle events, archive/restore, and history. |
| <img src="icons/ui/measure_weight.svg" width="24" alt="Measurements"> Measurements | Weight, blood progesterone, urine PdG, sperm values, and Excel import/export. |
| <img src="icons/ui/medi_current_sick.svg" width="24" alt="Medical"> Medical history | Diagnoses, treatments, observations, status filters, documents, and exports. |
| <img src="icons/ui/action_archive.svg" width="24" alt="Housing"> Housing | Building, unit, room, cage, animal placement, movement history, and inspections. |
| <img src="icons/ui/pedigree_symbol.svg" width="24" alt="Pedigree"> Pedigree | Parent relationships, family nodes, kinship, inbreeding, and genotype annotations. |
| <img src="icons/ui/action_settings.svg" width="24" alt="Projects"> Projects and permissions | Project association, role/job visibility, user sessions, audit events, and locks. |
| <img src="icons/ui/flow_freezer.svg" width="24" alt="Samples"> Samples and flow | Sample records, linked documents, embryo-flow views, and freezer inventory. |
| <img src="icons/ui/account_user.svg" width="24" alt="Portable"> Portable operation | `Launcher.exe`, bundled Python/Qt libraries, a deterministic fictional seed, and backend interchange packages. |

## Contents

- [Quick start](#quick-start)
- [Starter accounts](#starter-accounts)
- [What is included](#what-is-included)
- [Portable launcher](#portable-launcher)
- [Folder layout](#folder-layout)
- [Backend profiles and data ownership](#backend-profiles-and-data-ownership)
- [Animal identity and ID conventions](#animal-identity-and-id-conventions)
- [Animal workflows and roles](#animal-workflows-and-roles)
- [Measurements, imports, and plots](#measurements-imports-and-plots)
- [Plugin overview](#plugin-overview)
- [Users, account roles, and job bundles](#users-account-roles-and-job-bundles)
- [Languages, icons, and appearance](#languages-icons-and-appearance)
- [PDF branding and exports](#pdf-branding-and-exports)
- [Backups and backend interchange](#backups-and-backend-interchange)
- [Troubleshooting](#troubleshooting)
- [User guides and technical documentation](#user-guides-and-technical-documentation)
- [Build source](#build-source)
- [Roadmap and release history](#roadmap-and-release-history)
- [License](#license)

## Quick start

1. Download the release ZIP from [GitHub Releases](https://github.com/Lindenfrost/ProgTrack-Release/releases).
2. Extract the complete folder; do not separate `Launcher.exe` from `_internal/` or the application payload.
3. Start `Launcher.exe`.
4. Choose the configured backend profile through `Settings -> Backend` when you are logged in as Lord. The profile is applied after a clean restart.
5. Use the fictional seed data for evaluation first. Read the user guide in `manual/` before entering real data.

The release bundle contains the Python interpreter, PyQt6, plotting, Excel,
PDF, PostgreSQL, and other runtime dependencies. A release user does not need
to install Python or third-party packages separately.

### Starter accounts

The fictional seed contains starter accounts. The initial password for every
account is `123456`; change these passwords before any sensitive or shared use.

| Username | Display name | Account role | Job bundle |
| --- | --- | --- | --- |
| `Admin` | Administrator Administratorson | `lord` | none |
| `Researcher` | Dr. Researcher Sciencedottir | `user` | `researcher` |
| `Vet` | Dr. Veterinary Medicinsson | `user` | `vet` |
| `Manager` | Dr. Manager Plansdottir | `user` | `manager` |
| `Keeper` | Keeper Breedsson | `user` | `keeper` |
| `Tester` | Tester Aitisson | `user` | `tester` |
| `Veti` | Dr. Veterinary Medicinsdottir | `AWO` | `vet` |

## What is included

### Application payload

- `ProgTrack.v.0.2.1.py` — editable main application script;
- `Plugins/` — the core and optional plugin modules;
- `icons/` — splash, file, language, and UI SVG assets;
- `lang/` — English, German, Italian, and Russian message catalogs;
- `manual/` — localized user guides and technical documentation;
- `Resources/ExampleFiles/` — current measurement import examples;
- `Resources/Seed/progtrack_seed.ptdb` — the deterministic fictional backend seed.

### Portable runtime

- `Launcher.exe` — neutral portable launcher metadata, version `0.2.1`;
- `_internal/` — the bundled Python/Qt runtime and native libraries;
- `third_party_licenses/`, `THIRD_PARTY_NOTICES.md`, and `LICENSE`.

The release ZIP intentionally omits repository-only launcher build sources,
automated tests, caches, logs, and temporary audit output.

## Portable launcher

`Launcher.exe` is a PyInstaller OneDir launcher. At startup it:

- resolves its own folder and sets it as the working directory;
- prepares the bundled Python, Qt, and native-library paths;
- creates a writable runtime state/cache area for logs and Matplotlib;
- discovers the highest natural-version `ProgTrack.v.*.py` script beside itself;
- executes that script while keeping the application payload visible and editable.

To select a script explicitly:

```text
Launcher.exe --script ProgTrack.v.0.2.1.py
```

The launcher is not the application itself and does not embed the editable
ProgTrack source. Keep these items together when copying a release:
`Launcher.exe`, `_internal/`, `ProgTrack.v.0.2.1.py`, `Plugins/`, `icons/`,
`lang/`, `manual/`, and `Resources/`.

## Folder layout

| Path | Purpose |
| --- | --- |
| `Launcher.exe` | Portable Windows launcher. |
| `_internal/` | Bundled Python, Qt, Psycopg/libpq, and third-party libraries. |
| `ProgTrack.v.0.2.1.py` | Main application payload. |
| `Plugins/` | Core services and plugin modules. |
| `icons/ui/` | Canonical UI SVG registry and manifest used by the application. |
| `icons/` | Splash, file-type, language, and other non-UI assets. |
| `lang/` | Localized UI message catalogs. |
| `manual/` | Localized workflow guides and technical architecture notes. |
| `Resources/ExampleFiles/` | Blood, urine, weight, and sperm import templates. |
| `Resources/Seed/progtrack_seed.ptdb` | Complete fictional seed package for an empty backend. |
| `LICENSE`, `LICENSE_NOTICE.md` | ProgTrack licensing and copyright notices. |
| `THIRD_PARTY_NOTICES.md`, `third_party_licenses/` | Bundled dependency notices. |

Mutable runtime data is kept below `ProgTrackData/` in a writable portable
folder, or below the operating-system user data/config/cache/state roots when
the application folder is read-only. It is not stored in the source payload.

## Backend profiles and data ownership

Both profiles use the same services, validation, locks, audit repository, and
interchange format:

- **Standalone SQLite** is the tiny, installation-free profile for one local
  workstation and for tests. Its database must stay on local storage; SQLite
  is not the network-sharing solution.
- **Shared PostgreSQL** is the network and multi-workstation profile. The
  desktop application uses Psycopg 3 and a bounded connection pool.

Only a Lord account can open `Settings -> Backend`. The dialog can select the
profile and edit the SQLite database name/path or the PostgreSQL host, port,
database, user, TLS, timeout, managed-storage, and pool settings. PostgreSQL
passwords are kept in the operating-system credential store rather than in
`backend.json`. Connection testing is non-mutating. Saving selects the profile
for the next clean restart; it does not silently migrate data.

The selected backend is authoritative for animals, users, projects, role/job
configuration, measurements, reproductive events, medical history, reports,
housing, samples, plugin records, sessions, locks, and audit events. PDFs and
other uploaded documents are stored in backend-managed storage; database rows
hold ownership, safe paths, and checksums.

An empty backend imports the fictional `progtrack_seed.ptdb` automatically.
Legacy dynamic JSON stores are not a runtime fallback and are not imported by
the Phase 2 backend. Static catalogs used for bootstrap or localization may
remain in the application payload.

## Animal identity and ID conventions

An animal's immutable identity consists of:

1. name;
2. species;
3. complete birth date;
4. origin / `Tierherkunft` (the facility-origin value configured by the Manager);
5. the generated IPID derived from these identity components.

Once an animal has been created, Lord, Master, Manager, and all other users are
blocked from changing these identity components. An incorrect example record is
deleted and recreated. Other IDs (sample IDs, project IDs, cage IDs, and
facility-specific identifiers) must be institution-tagged so data exchanged
between facilities cannot silently collide.

Measurement imports keep **both** identifiers:

- `Animal ID` identifies the existing animal;
- `Sample ID` identifies the individual measurement/sample.

The import preview validates headers, dates, numeric values, and identity
matches before any write. A new `Animal ID` is previewed and clearly warned,
but the researcher/keeper cannot create an animal from measurement data. Only
rows for existing animals are imported; a Manager must create the new animal
first, after which its measurements can be imported.

## Animal workflows and roles

Roles control the fields, event blocks, limits, and role-tab actions shown in
the application. The role builder is available under `Settings -> Style ->
Role setup` to authorized Lord, Master, and Manager users. It controls active
roles, labels, ordering, icons, dialog block presets, and whether the `New
Animal` action is available in each role tab. The `All` tab remains the general
overview and keeps the role-edit action; measurement-import buttons are shown
only in role tabs whose configured blocks support them.

| UI icon | Current role | Typical use |
| --- | --- | --- |
| <img src="icons/ui/role_female.svg" width="24" alt="Female role"> | Egg cell donor / surrogate | Steroid, urine, blood, and reproductive workflows. |
| <img src="icons/ui/role_male.svg" width="24" alt="Male role"> | Sperm donor | Sperm measurements and donor events. |
| <img src="icons/ui/role_offspring.svg" width="24" alt="Offspring role"> | Offspring | Young animals and offspring-specific records. |
| <img src="icons/ui/role_partner.svg" width="24" alt="Partner role"> | Partner | Partner animals with the configured basic blocks. |
| <img src="icons/ui/role_breeding.svg" width="24" alt="Breeding role"> | Breeding animal | Mature breeding-colony animals. |
| <img src="icons/ui/role_experimental.svg" width="24" alt="Experimental role"> | Experimental animal | Experimental workflows and configured event blocks. |

The common action icons are the same SVGs used by the UI:

| UI icon | Action |
| --- | --- |
| <img src="icons/ui/action_add.svg" width="22" alt="Add"> | Create a new animal where the active role allows it. |
| <img src="icons/ui/action_edit.svg" width="22" alt="Edit"> | Open the role-specific editor. |
| <img src="icons/ui/action_edit_role.svg" width="22" alt="Edit role"> | Change a role from the `All` view when permitted. |
| <img src="icons/ui/action_archive.svg" width="22" alt="Archive"> | Archive an animal without deleting its history. |
| <img src="icons/ui/action_restore.svg" width="22" alt="Restore"> | Restore an archived animal. |
| <img src="icons/ui/action_delete.svg" width="22" alt="Delete"> | Permanently delete an archived example record. |

## Measurements, imports, and plots

| UI icon | Data stream | Current use |
| --- | --- | --- |
| <img src="icons/ui/measure_blood.svg" width="24" alt="Blood"> | Blood progesterone | `Progesteron (ng/ml)` time series. |
| <img src="icons/ui/measure_urine.svg" width="24" alt="Urine"> | Urine PdG | `PdG` time series in the configured unit. |
| <img src="icons/ui/measure_weight.svg" width="24" alt="Weight"> | Weight | Body-weight time series in grams. |
| <img src="icons/ui/measure_sperm.svg" width="24" alt="Sperm"> | Sperm values | Count, motility, and progressive motility. |

### Excel import templates

The authoritative examples are under `Resources/ExampleFiles/`. They contain
`Animal ID` and `Sample ID`; they do not contain species or birth-date columns.
The exact current headers are:

| File / action | Required data columns |
| --- | --- |
| Blood progesterone | `Name`, `Animal ID`, `Datum`, `Progesteron (ng/ml)`, `F`, `Sample ID` |
| Urine PdG | `Name`, `Animal ID`, `Datum`, `PdG`, `Sample ID` |
| Weight | `Name`, `Animal ID`, `Datum`, `Gewicht`, `Sample ID` |
| Sperm values | `Datum`, `Name`, `Animal ID`, `% Motility`, `% Progressive`, `Sperms/ml`, `Sample ID` |

`Name` is a human-readable check value; matching is performed through the
existing `Animal ID`. `Sample ID` remains separate and is never used as an
animal identity. The preview is cancellable and no database write occurs until
the user confirms it.

Plots can combine hormone views, show weight or sperm overlays, display event
markers, apply female phase filters, and synchronize the recent-data window
across selected animals.

## Plugin overview

Plugins are loaded from `Plugins/` at startup. Some are tabs, some are dialogs,
and some provide feature gates or backend services.

| Plugin | Kind | Purpose |
| --- | --- | --- |
| Steroid Track | Feature gate | Steroid roles, hormone imports, sperm imports, reproductive events, phase filters, and PdG integration. |
| Master Track | Administration | Login, users, jobs, permissions, sessions, locks, and audit logs. |
| Animal Reports | Main tab | Monthly reports, locked/manual lines, signatures, and PDF/XLSX export. |
| Medi Track | Main tab | Medical history, status filters, treatment/observation records, documents, and exports. |
| Surgery Planner | Dialog | Surgery and embryo-transfer planning, recovery rules, blocked days, and export. |
| Embryo Tracker | Dialog | Gestation-day prediction from ultrasound measurements. |
| PdG to Progesterone Converter | Dialog | Per-animal PdG-to-progesterone model fitting. |
| Flow Track | Main tab | Embryo flow between donors, surrogates, and freezer inventory. |
| Heritage Track | Main tab | Pedigree graphs, family nodes, kinship, inbreeding, genotype annotations, and complex-family routing. |
| Cage Track | Main tab | Building → Unit → Room → Cage hierarchy, placement, movements, inspections, and PDF export. |
| Sample Track | Window | Organ/biological samples, aliquots, linked files, filters, and PDF export. |
| Projects Track | Sidebar and tab | Project/species visibility, project history, IACUC/AWO assignment, documents/SOPs, and experiment state. |
| Network Track | Window | Backend-backed team chat with polling and optional notification sounds. |

## Users, account roles, and job bundles

Master Track separates the immutable account role from configurable job bundles:

- the account role defines the administrative baseline;
- a job bundle grants practical task permissions;
- direct grants and revocations can further adjust a user;
- sessions, passwords, users, and job overrides are stored in the selected backend.

| UI icon | Account role | Meaning |
| --- | --- | --- |
| <img src="icons/ui/account_lord.svg" width="26" alt="Lord"> | `lord` | IT administrative role for global settings and installation, with unlimited rights. |
| <img src="icons/ui/account_master.svg" width="26" alt="Master"> | `master` | Animal-facility administrative role for user management and ProgTrack fine-tuning. |
| <img src="icons/ui/account_awo.svg" width="26" alt="AWO"> | `AWO` | Animal Welfare Officer account for IACUC and welfare assignments. The user overview shows `AWO`. |
| <img src="icons/ui/account_user.svg" width="26" alt="User"> | `user` | Standard account; effective access comes from its baseline, jobs, grants, and revocations. |
| <img src="icons/ui/account_guest_locked.svg" width="26" alt="Guest"> | `guest` | Restricted read-oriented fallback account. |

The default job bundles are `vet`, `keeper`, `manager`, `researcher`, and
`tester`. Their permissions are bootstrapped into the backend and can be
configured by authorized administration; there is no runtime `jobs.json`
authority. Typical responsibilities are:

| UI icon | Job | Typical focus |
| --- | --- | --- |
| <img src="icons/ui/medi_current_sick.svg" width="24" alt="Vet"> | `vet` | Medical review, health status, Medi Track, reports, and welfare-relevant visibility. |
| <img src="icons/ui/role_breeding.svg" width="24" alt="Keeper"> | `keeper` | Housing, cage placement/inspection, animal core data, and permitted measurements. |
| <img src="icons/ui/action_settings.svg" width="24" alt="Manager"> | `manager` | Animal creation, imports, archiving/deletion, projects, cages, documents, and role configuration. |
| <img src="icons/ui/account_user.svg" width="24" alt="Researcher"> | `researcher` | Research measurements, imports/exports, reports, PdG, planning, flow, samples, and associated projects. |
| <img src="icons/ui/action_refresh.svg" width="24" alt="Tester"> | `tester` | Restricted verification workflows and selected plugin access. |

## Languages, icons, and appearance

The interface message catalogs are:

| UI icon | File | Language |
| --- | --- | --- |
| <img src="icons/flag_gb.svg" width="24" alt="English"> | `lang/messages_en.json` | English |
| <img src="icons/flag_de.svg" width="24" alt="German"> | `lang/messages_de.json` | Deutsch |
| <img src="icons/flag_it.svg" width="24" alt="Italian"> | `lang/messages_it.json` | Italiano |
| <img src="icons/flag_ru.svg" width="24" alt="Russian"> | `lang/messages_ru.json` | Русский |

Use `Settings -> Language` to change language and `Settings -> Style` to
configure measurement/event colors, markers, line styles, role labels, and
role-dialog blocks.

`icons/ui/manifest.json` is the canonical semantic UI icon registry. The
application and this README use the same SVG filenames; the UI does not rely
on emoji or PNG fallbacks for these controls. File-type and splash graphics
outside `icons/ui/` may remain PNG assets where they are not UI controls.

## PDF branding and exports

Lord, Master, and Manager users can open `Settings -> Institution branding` and
store an institution name plus an optional PNG/JPEG logo in backend-managed
storage. Branding is applied to every supported PDF export, right-aligned in a
bounded header area. The logo keeps its aspect ratio and is automatically
scaled down when the source image is too large; it cannot fill or cover the
whole page. The setting is shared by the selected backend and is included in
backend interchange packages.

PDF exports are available across Animal Reports, Medi Track, Sample Track, Cage
Track, Flow Track, and Master Track audit logs. XLSX exports use plain text
cells, can omit the signatures column when signatures are disabled, and may
include linked documents in the associated document folder where the plugin
supports them.

## Backups and backend interchange

Use the complete backend interchange package (`.ptdb`) for transfer and backup.
It contains validated database records plus managed document payloads and
checksums. It is the common path for moving a standalone SQLite installation to
PostgreSQL later and for a future LAVAN source adapter.

Do not copy a live SQLite file or managed folder while ProgTrack is running.
Do not reintroduce legacy JSON files as a fallback. An import preview validates
the package before any target write; the target backend must be empty unless a
future, explicitly authorized merge workflow says otherwise.

Entity locks are backend records and apply to both SQLite and PostgreSQL. A
Lord can force-release a lock with a reason; ordinary users cannot silently
overwrite another user's active edit.

## Troubleshooting

### ProgTrack does not start

Check the launcher/runtime logs below the configured runtime state directory,
or use `Master Track -> Open tech logs` when logged in as Lord or Master.
Confirm that `Launcher.exe`, `_internal/`, `ProgTrack.v.0.2.1.py`, `Plugins/`,
`icons/`, `lang/`, `manual/`, and `Resources/` remain together. A partial copy
usually causes missing Qt, SciPy, Excel, PDF, or PostgreSQL-library errors.

### A plugin is missing

Check that its folder under `Plugins/` contains the expected Python modules and
`manifest.json`, and review the technical log for import errors.

### Excel import fails

Use the matching example workbook, preserve the exact headers, keep dates
valid, use numeric measurement values, and confirm that the `Animal ID` already
exists. Review the preview and warning rows before confirming an import.

### Plotting or export fails

Use the complete portable bundle, including `_internal/`. Do not copy only the
launcher executable. Check the configured writable data/cache/state paths and
the technical log.

## User guides and technical documentation

Localized workflow guides are in `manual/`:

- `ProgTrack_User_Guide - en.html`
- `ProgTrack_User_Guide - de.html`
- `ProgTrack_User_Guide - it.html`
- `ProgTrack_User_Guide - ru.html`

The guides cover startup, backend selection, IDs, roles, permissions, imports
and previews, measurements, plotting, plugins, exports, branding,
troubleshooting, and licensing. The approved backend architecture and
interchange contracts are versioned below
`manual/technical documentation/architecture/`.

## Build source

Launcher build sources are in `source/` and are not part of the compact release
ZIP. Important files include `launcher.py`, `launcher_small.spec`,
`hiddenimports.txt`, `build_launcher_small.bat`, and `progtrack_icon.ico`.
The build uses the repository-local Python environment created by the build
script. The resulting frozen runtime is verified with the backend and frozen
runtime smoke tests before release packaging.

## Roadmap and release history

| Version / phase | Focus |
| --- | --- |
| `0.2.1` / Phase 2B | Backend services and adapters, deterministic seed, runtime paths, immutable identities, locks, interchange packages, SVG icon registry, PDF branding, and launcher/runtime hardening. |
| `0.2.0` / Phase 2A | Read-only backend migration audit, canonical data dictionary, storage matrix, interchange contract, and approved PostgreSQL/SQLite architecture. |
| Phase 3 | Shared PostgreSQL operational hardening, Linux packaging/readiness, scheduled backup/restore, and complete audit-trail improvements. |
| Phase 4 | Further UI/job modes, Heritage Track optimization, plots, cage-room plans, genotype correction policy, medical templates, offspring roles, and report designer work. |
| Phase 5+ | LAVAN source adapter and bulk import, portable package transfer, surgery/planning extensions, web/tablet companion, and later integrations. |

### Historical releases

- `0.1.2` was the Phase 1 validation release for role tabs, Cage Track,
  Heritage Track, imports, reports, medical history, and the first portable
  workflow. It is superseded by the backend-based 0.2.x architecture.
- `0.1.1` and `0.1.0 RC` are retained as historical development milestones.
  Their JSON-oriented storage and launcher details do not describe the current
  0.2.1 runtime.

## License

Copyright (C) 2026 Dimitri L. Lindenwald, PhD, Deutsches Primatenzentrum GmbH,
Leibniz Institute for Primate Research, Kellnerweg 4, 37077 Goettingen,
Germany.

ProgTrack is released under the GNU General Public License version 3.0 or
later. You may use, modify, and redistribute it under that license. A copy is
included as `LICENSE` and is available at
<https://www.gnu.org/licenses/gpl-3.0.html>.

The compiled launcher contains a portable Python runtime and third-party
libraries governed by their own licenses. Notices are documented in
`THIRD_PARTY_NOTICES.md` and `third_party_licenses/`.

## Acknowledgement

ProgTrack is developed at Deutsches Primatenzentrum GmbH, Leibniz Institute
for Primate Research, Goettingen, Germany.
