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

The first column uses the same neutral tab background as the application
(`QTabBar::tab:disabled`, `#e0e0e0`). Keeping each SVG on its own line makes
partially transparent icon artwork readable in both light and dark viewers.

<table>
  <thead>
    <tr><th bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;">Area</th><th>What ProgTrack provides</th></tr>
  </thead>
  <tbody>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/role_offspring.svg" width="28" alt="Animal"><br><strong>Animal records</strong></td><td>Immutable identity, role-specific fields, lifecycle events, archive/restore, and history.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/measure_weight.svg" width="28" alt="Measurements"><br><strong>Measurements</strong></td><td>Weight, blood progesterone, urine PdG, sperm values, and Excel import/export.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/status_sick.svg" width="28" alt="Medical"><br><strong>Medical history</strong></td><td>Diagnoses, treatments, observations, status filters, documents, and exports.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/action_archive.svg" width="28" alt="Housing"><br><strong>Housing</strong></td><td>Building, unit, room, cage, animal placement, movement history, and inspections.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/pedigree_symbol.svg" width="28" alt="Pedigree"><br><strong>Pedigree</strong></td><td>Parent relationships, family nodes, kinship, inbreeding, and genotype annotations.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/action_settings.svg" width="28" alt="Projects"><br><strong>Projects and permissions</strong></td><td>Project association, role/job visibility, user sessions, audit events, and locks.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/flow_freezer.svg" width="28" alt="Samples"><br><strong>Samples and flow</strong></td><td>Sample records, linked documents, embryo-flow views, and freezer inventory.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_user.svg" width="28" alt="Portable"><br><strong>Portable operation</strong></td><td><code>Launcher.exe</code>, bundled Python/Qt libraries, a deterministic fictional seed, and backend interchange packages.</td></tr>
  </tbody>
</table>

## Contents

- [Quick start](#quick-start)
- [Starter accounts](#starter-accounts)
- [What is included](#what-is-included)
- [Platform support and release artifacts](#platform-support-and-release-artifacts)
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
| `Veti` | Dr. Veterinary Medicinsdottir | `user` | `vet`, `AWO` |
| `Manager` | Dr. Manager Plansdottir | `user` | `manager` |
| `Keeper` | Keeper Breedsson | `user` | `keeper` |
| `Tester` | Tester Aitisson | `user` | `tester` |

## What is included

### Application payload

- `ProgTrack.v.0.2.1.py` — editable main application script;
- `Plugins/` — the core and optional plugin modules;
- `icons/` — splash, file-type, job, language, and UI assets;
- `lang/` — English, German, Italian, and Russian message catalogs;
- `manual/` — localized user guides and technical documentation;
- `Resources/ExampleFiles/` — current measurement import examples;
- `Resources/Seed/progtrack_seed.ptdb` — the deterministic fictional backend seed.

### Portable runtime

- `Launcher.exe` — neutral portable launcher metadata, version `0.2.1`;
- `_internal/` — the bundled Python/Qt runtime and native libraries;
- `third_party_licenses/`, `THIRD_PARTY_NOTICES.md`, and `LICENSE`.

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

## Platform support and release artifacts

The `0.2.1` release published here is a native Windows portable release. Its
`Launcher.exe`, Windows Qt/Python runtime, and bundled native libraries must be
kept together; this ZIP is not a native Linux package and is not supported
through Wine.

A separate native Linux artifact uses the same application
payload and backend contracts. A local engineering archive is now assembled as
`ProgTrack-0.3.0-linux-x86_64.tar.gz`: it contains a pinned CPython runtime, Qt/PyQt6,
scientific/PDF/XLSX dependencies, bundled fonts, and the Psycopg binary client.
It is kept local until the required native Linux Mint 22.3 x86_64 workstation gate
(ELF/linker, GUI, PDF, SQLite, PostgreSQL/TLS, XDG, and clean-machine checks) passes.
Windows and Linux artifacts remain separate rather than combining incompatible
`.dll`/`.pyd` and `.so` runtimes in one package.

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

In the default portable profile the important writable locations are:

| Path | Purpose |
| --- | --- |
| `ProgTrackData/database/progtrack.sqlite3` | Standalone SQLite database. |
| `ProgTrackData/config/backend.json` | Non-secret backend profile selection and connection settings. |
| `ProgTrackData/managed/documents/` | Uploaded and generated managed document payloads. |
| `ProgTrackData/managed/config-assets/` | Managed configuration assets such as the institution logo. |
| `ProgTrackData/state/runtime/standalone.lock` | Exclusive Standalone process lock. |
| `ProgTrackData/state/logs/` | Application and launcher diagnostics. |
| `ProgTrackData/exports/` | Default export destination. |
| `ProgTrackData/config/preferences/` | Per-user presentation preferences. |

## Backend profiles and data ownership

Both profiles use the same services, validation, locks, audit repository, and
interchange format:

- **Standalone SQLite** is the tiny, installation-free profile for one local
  workstation and for tests. Its database must stay on local storage; SQLite
  is not the network-sharing solution.
- **Shared PostgreSQL** is the network and multi-workstation profile. The
  desktop application uses Psycopg 3 and a bounded connection pool.

Only a Lord account can open `Settings -> Backend`. The dialog can select the
profile and edit the SQLite database name/local folder or the PostgreSQL host, port,
database, user, TLS, CA bundle, optional client certificate/private key,
timeout, server-managed storage, and pool settings. PostgreSQL passwords and
client-key passphrases are kept in the operating-system credential store.
Connection testing exercises the selected TLS configuration and is
non-mutating. A Lord can list authorized server databases, create/select one,
archive, back up, restore, or delete it after the required confirmations.
The dialog also provides the preflighted canonical SQLite-to-PostgreSQL
transfer. Saving selects the profile for the next clean restart; it does not
silently transfer or overwrite data. If the selected backend
cannot be opened, startup fails with a diagnostic instead of silently using
the other profile. The release contains the PostgreSQL client driver, not a
PostgreSQL server; server provisioning and PostgreSQL/Linux deployment checks
are separate installation tests.

The selected backend is authoritative for animals, users, projects, role/job
configuration, measurements, reproductive events, medical history, reports,
housing, samples, plugin records, sessions, locks, and audit events. PDFs and
other uploaded documents are stored in backend-managed storage; database rows
hold ownership, safe paths, and checksums.

A truly empty backend imports the fictional `progtrack_seed.ptdb`
automatically. An existing database is left unchanged.

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
facility-specific identifiers) are institution-tagged so data exchanged
between facilities cannot silently collide.

Measurement imports keep **both** identifiers:

- `Animal ID` identifies the existing animal;
- `Sample ID` identifies the individual measurement/sample.

The import preview validates headers, dates, numeric values, and identity
matches before any write. A new `Animal ID` is previewed and clearly warned,
but the Researcher/Keeper cannot create an animal from measurement data. Only
rows for existing animals are imported; a Manager must create the new animal
first, after which its measurements can be imported.

## Animal workflows and roles

Roles control the fields, event blocks, limits, and role-tab actions shown in
the application. The role builder is available under `Settings -> Conventions ->
Role setup` to authorized Lord, Master, and Manager users. It controls active
roles, labels, ordering, dialog block presets, and whether the `New
Animal` action is available in each role tab. The `All` tab remains the general
overview and keeps the role-edit action; measurement-import buttons are shown
only in role tabs whose configured blocks support them. Its icon picker shows
every packaged SVG in `icons/ui`; custom role labels are facility-owned text
and do not require additions to the shipped language catalogs.

The filter row below the main animal list combines a short-name prefix search
with three icon checkboxes for female, male, and unknown sex. All three sex
filters start enabled. Clear one or more checkboxes to hide those animals; the
result is combined immediately with the name, role, project, species, archive,
and active-plugin filters rather than changing any animal record.

<table>
  <thead><tr><th bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;">UI icon</th><th>Current role</th><th>Typical use</th></tr></thead>
  <tbody>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/role_female.svg" width="24" alt="Female role"></td><td>Egg cell donor / surrogate</td><td>Steroid, urine, blood, and reproductive workflows.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/role_male.svg" width="24" alt="Male role"></td><td>Sperm donor</td><td>Sperm measurements and donor events.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/role_offspring.svg" width="24" alt="Offspring role"></td><td>Offspring</td><td>Young animals and offspring-specific records.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/role_partner.svg" width="24" alt="Partner role"></td><td>Partner</td><td>Partner animals with the configured basic blocks.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/pedigree_symbol.svg" width="24" alt="Breeding role"></td><td>Breeding animal</td><td>Mature breeding-colony animals.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/role_experimental.svg" width="24" alt="Experimental role"></td><td>Experimental animal</td><td>Experimental workflows and configured event blocks.</td></tr>
  </tbody>
</table>

The common action icons are the same SVGs used by the UI:

<table>
  <thead><tr><th bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;">UI icon</th><th>Action</th></tr></thead>
  <tbody>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/action_add.svg" width="22" alt="Add"></td><td>Create a new animal where the active role allows it.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/action_edit.svg" width="22" alt="Edit"></td><td>Open the role-specific editor.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/action_edit_role.svg" width="22" alt="Edit role"></td><td>Change a role from the <code>All</code> view when permitted.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/action_archive.svg" width="22" alt="Archive"></td><td>Archive an animal without deleting its history.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/action_restore.svg" width="22" alt="Restore"></td><td>Restore an archived animal.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/action_delete.svg" width="22" alt="Delete"></td><td>Permanently delete an archived example record.</td></tr>
  </tbody>
</table>

## Measurements, imports, and plots

<table>
  <thead><tr><th bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;">UI icon</th><th>Data stream</th><th>Current use</th></tr></thead>
  <tbody>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/measure_blood.svg" width="24" alt="Blood"></td><td>Blood progesterone</td><td><code>Progesteron (ng/ml)</code> time series.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/measure_urine.svg" width="24" alt="Urine"></td><td>Urine PdG</td><td><code>PdG</code> time series in the configured unit.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/measure_weight.svg" width="24" alt="Weight"></td><td>Weight</td><td>Body-weight time series in grams.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/measure_sperm.svg" width="24" alt="Sperm"></td><td>Sperm values</td><td>Count, motility, and progressive motility.</td></tr>
  </tbody>
</table>

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
| Medi Track | Main tab | Medical history, status filters, treatment/observation records, documents, and PDF/XLSX exports. Multi-animal File-menu export shows determinate progress, supports cancellation between atomic outputs, and reports exactly which valid files remain. |
| Surgery Planner | Dialog | Surgery and embryo-transfer planning, recovery rules, blocked days, and export. |
| Embryo Tracker | Dialog | Gestation-day prediction from ultrasound measurements. |
| PdG to Progesterone Converter | Dialog | Per-animal PdG-to-progesterone model fitting. |
| Flow Track | Main tab | Embryo flow between donors, surrogates, and freezer inventory. |
| Heritage Track | Main tab | Pedigree graphs, family nodes, kinship, inbreeding, genotype annotations, and complex-family routing. |
| Cage Track | Main tab | Building → Unit → Room → Cage hierarchy, placement, movements, inspections, and PDF export. It projects the complete animal-list selection into one deterministic building, highlights matching occupants, and remembers each signed-in user's inspection-table sort. |
| Sample Track | Window | Organ/biological samples, aliquots, linked files, filters, and PDF export. |
| Projects Track | Sidebar and tab | Project/species visibility, project history, IACUC/AWO assignment, documents/SOPs, and experiment state. Each project has a localized `Draft`, `Active`, or `Closed` lifecycle state; lifecycle and archive state are independent. |
| Network Track | Window | Backend-backed team chat with polling and optional notification sounds. |

## Users, account roles, and job bundles

Master Track separates the immutable account role from configurable job bundles:

- the account role defines the administrative baseline;
- a job bundle grants practical task permissions;
- direct grants and revocations can further adjust a user;
- sessions, passwords, users, and job overrides are stored in the selected backend.

<table>
  <thead><tr><th bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;">UI icon</th><th>Account role</th><th>Meaning</th></tr></thead>
  <tbody>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_lord.svg" width="26" alt="Lord"></td><td><code>lord</code></td><td>IT administrative role for global settings and installation, with unlimited rights.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_master.svg" width="26" alt="Master"></td><td><code>master</code></td><td>Animal-facility administrative role for user management and ProgTrack fine-tuning.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_user.svg" width="26" alt="User"></td><td><code>user</code></td><td>Standard account; effective access comes from its baseline, jobs, grants, and revocations.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_guest_locked.svg" width="26" alt="Guest"></td><td><code>guest</code></td><td>Restricted read-oriented fallback account.</td></tr>
  </tbody>
</table>

The default job bundles are `vet`, `AWO` (`animal_welfare_officer`), `keeper`,
`manager`, `researcher`, and `tester`. The AWO job can only be assigned together
with the Vet job. Their permissions are bootstrapped into the backend and can be
configured by authorized administration; there is no runtime `jobs.json`
authority. Typical responsibilities are:

<table>
  <thead><tr><th bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;">UI icon</th><th>Job</th><th>Typical focus</th></tr></thead>
  <tbody>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_veterinarian.svg" width="28" alt="Vet"></td><td><code>vet</code></td><td>Medical review, health status, Medi Track, reports, and welfare-relevant visibility.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_awo.svg" width="28" alt="AWO"></td><td><code>AWO</code> (<code>animal_welfare_officer</code>)</td><td>Animal Welfare Officer work for IACUC and welfare assignments; assignable only together with the <code>vet</code> job.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/accountkeeper.svg" width="28" alt="Keeper"></td><td><code>keeper</code></td><td>Housing, cage placement/inspection, animal core data, and permitted measurements.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_manager.svg" width="28" alt="Manager"></td><td><code>manager</code></td><td>Animal creation, imports, archiving/deletion, projects, cages, documents, and role configuration.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_researcher.svg" width="28" alt="Researcher"></td><td><code>researcher</code></td><td>Research measurements, imports/exports, reports, PdG, planning, flow, samples, and associated projects.</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/account_it_specialist.svg" width="28" alt="Tester"></td><td><code>tester</code></td><td>Restricted verification workflows and selected plugin access.</td></tr>
  </tbody>
</table>

## Languages, icons, and appearance

The interface message catalogs are:

<table>
  <thead><tr><th bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;">UI icon</th><th>File</th><th>Language</th></tr></thead>
  <tbody>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/flag_gb.svg" width="24" alt="English"></td><td><code>lang/messages_en.json</code></td><td>English</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/flag_de.svg" width="24" alt="German"></td><td><code>lang/messages_de.json</code></td><td>Deutsch</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/flag_it.svg" width="24" alt="Italian"></td><td><code>lang/messages_it.json</code></td><td>Italiano</td></tr>
    <tr><td bgcolor="#e0e0e0" style="background-color:#e0e0e0 !important;color:#111111 !important;" align="center"><img src="icons/ui/flag_ru.svg" width="24" alt="Russian"></td><td><code>lang/messages_ru.json</code></td><td>Русский</td></tr>
  </tbody>
</table>

Use `Settings -> Language` to change language and `Settings -> Conventions` to
configure measurement/event colors, markers, line styles, role labels, and
role-dialog blocks.

`icons/ui/manifest.json` is the canonical semantic UI icon registry. The
application and this README use the same SVG filenames; the UI does not rely
on emoji or PNG fallbacks for these controls. Semantic identifiers provide the
localized tooltip text while allowing several actions to share one canonical
SVG. Editable masters are maintained under `Q:\GitHub\Graphics\SVG\UI`.
Qt keeps the master colours on light palettes and adapts the canonical outline
to the active palette when dark surfaces would otherwise hide icon details.
This SVG-only rule applies to `icons/ui`; splash, message, job, and file-type
graphics at the root of `icons/` may remain PNG assets.

## PDF branding and exports

Lord, Master, and Manager users can open `Settings -> Conventions ->
Institution branding` and
store an institution name plus an optional PNG/JPEG logo in backend-managed
storage. A shared position setting places the complete branding block at the
top left or top right of every supported PDF page; top right remains the
default for older configurations. The grey page preview shows the effective
institution name, logo, and selected edge before saving. The logo keeps its
aspect ratio and is automatically scaled down when the source image is too
large, so it cannot fill or cover the page. The setting is shared by the
selected backend and is included in backend interchange packages.

PDF exports are available across Animal Reports, Medi Track, Sample Track, Cage
Track, Flow Track, and Master Track audit logs. XLSX exports use plain text
cells, can omit the signatures column when signatures are disabled, and may
include linked documents in the associated document folder where the plugin
supports them.

## Backups and backend interchange

Use the complete backend interchange package (`.ptdb`) for transfer and backup.
It contains validated database records plus managed document payloads and
checksums. The same package is accepted by an empty Standalone SQLite or Shared
PostgreSQL backend.

Do not copy a live SQLite file or managed folder while ProgTrack is running.
The configured backend is the only operational data source and there is no
automatic profile fallback. An import preview validates the package before any
target write; the target backend must be empty unless a future, explicitly
authorized merge workflow says otherwise.

Standalone SQLite also uses one local process lock: a second writable ProgTrack
instance is refused until the live owner closes, while stale local-process
locks are reclaimed safely. Entity locks and optimistic revisions apply to
reviewed writes in both profiles. Only the Lord-authorized service can
force-release an entity lock, and it requires an audited reason.

## Troubleshooting

### ProgTrack does not start

Check the launcher/runtime logs below the configured runtime state directory,
or use `Master Track -> Open tech logs` when logged in as Lord or Master.
Confirm that `Launcher.exe`, `_internal/`, `ProgTrack.v.0.2.1.py`, `Plugins/`,
`icons/`, `lang/`, `manual/`, and `Resources/` remain together. A partial copy
usually causes missing Qt, SciPy, Excel, PDF, or PostgreSQL-library errors.

### A plugin is missing

Check that its folder under `Plugins/` contains the expected Python modules and
`manifest.json`, and review the technical log for import or initialization
errors. A plugin which cannot be loaded remains unavailable; ProgTrack does not
substitute another data store. In a packaged installation, restore the complete
`_internal/` frozen runtime instead of installing individual libraries beside
the application.

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

Launcher build sources are in source/ and are not part of the compact release
ZIP. Each platform has one self-contained source tree: `source/launcher/windows/`
and `source/launcher/linux/`.

Windows build inputs are all under `source/launcher/windows/`:

- source/launcher/windows/launcher.py
- source/launcher/windows/launcher_small.spec
- source/launcher/windows/hiddenimports.txt
- source/launcher/windows/launcher_version_info.txt
- source/launcher/windows/build_launcher_small.bat
- source/launcher/windows/package_release.ps1
- source/launcher/windows/progtrack_icon.ico
- source/launcher/windows/requirements-windows-build.txt
- source/launcher/windows/frozen_runtime_smoke.py
- source/launcher/windows/generate_component_inventory.py
- source/launcher/windows/LAUNCHER_VERSIONS.md
- source/launcher/windows/LICENSE_NOTICE.md

The Windows build uses the repository-local Python environment created by the
build script. The resulting frozen runtime is verified with the backend and
frozen-runtime smoke tests before release packaging.

Linux build inputs are all under `source/launcher/linux/`:

- source/launcher/linux/ProgTrack — POSIX launcher entry point
- source/launcher/linux/launcher.py — Linux launcher and runtime-path logic
- source/launcher/linux/progtrack.desktop — optional desktop integration
- source/launcher/linux/progtrack.png — Linux application icon
- source/launcher/linux/README.md
- source/launcher/linux/package_linux_release.py
- source/launcher/linux/requirements-linux-bundled.txt

The Linux tree is intentionally independent of the Windows `.exe`, `.dll`, and
`.pyd` runtime. `package_linux_release.py` now assembles the self-contained CPython/Qt
artifact from `linux_runtime_manifest.json`; the local tarball is an engineering
pre-release and remains unsupported until the native Linux acceptance gate
is completed.
## Roadmap and release history

| Version / phase | Focus |
| --- | --- |
| `0.2.1` / Phase 2B | Backend services and adapters, deterministic seed, runtime paths, immutable identities, locks, interchange packages, SVG icon registry, PDF branding, and launcher/runtime hardening. |
| `0.2.0` / Phase 2A | Read-only backend migration audit, canonical data dictionary, storage matrix, interchange contract, and approved PostgreSQL/SQLite architecture. |
| `0.1.2` / Phase 1 | Role tabs, Cage Track, Heritage Track, imports, reports, medical history, and the first portable workflow. Superseded by the backend-based 0.2.x architecture. |
| `0.1.1` | Historical stabilization milestone before the shared backend architecture. |
| `0.1.0 RC` | Historical initial public testing package. |

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
