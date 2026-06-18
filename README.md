# ProgTrack 0.1.1

<p align="center">
  <img src="icons/Splash.png" alt="ProgTrack splash">
</p>

<p align="center">
  <img alt="Version" src="https://img.shields.io/badge/version-0.1.1-blue">
  <img alt="Platform" src="https://img.shields.io/badge/platform-Windows-lightgrey">
  <img alt="Runtime" src="https://img.shields.io/badge/runtime-portable%20Python-green">
  <img alt="License" src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue">
</p>

**ProgTrack** is a portable, modular desktop application for animal-centered
research workflows. It brings hormone measurements, reproductive events,
animal roles, medical history, cage placement, pedigree data, sample tracking,
project assignment, reports, and planning tools into one Windows bundle.

> 🌱 **Release status:** `0.1.1` is an early public testing release.  
> It is meant for evaluation, feedback, bug reports, and workflow testing.

## 💡 Why ProgTrack?

Animal-centered research data often live in many separate places: spreadsheets,
local notes, printed forms, project files, medical records, cage lists, sample
lists, and individual memory. That makes it difficult to answer simple but
important questions quickly:

- What is known about this animal right now?
- Which measurements, events, projects, treatments, samples, and housing
  changes belong together?
- Which information is relevant for a vet, researcher, manager, or keeper?
- How can data be shared without turning every handover into a manual search?

ProgTrack addresses this need by tracking individual animal data in one
holistic, open-source, and readable system. The goal is not only to store data,
but to make it easier to see, compare, aggregate, export, document, and share
the information that already exists across daily research workflows.

In practical terms, ProgTrack is meant to provide:

| Need | ProgTrack Approach |
| --- | --- |
| 🐒 Individual overview | Each animal has a central record with role-specific data and history. |
| 📊 Aggregated insight | Measurements, events, reports, projects, cages, samples, and medical notes can be viewed together. |
| 🔎 Faster orientation | Sidebar filters, plots, reports, and plugins help users find relevant information quickly. |
| 🤝 Easier sharing | Portable files, exports, readable reports, and open source code make review and collaboration easier. |
| 🧾 Transparency | Local data files, audit-oriented workflows, manuals, and GPL licensing support inspection and reproducibility. |
| 🧩 Extensibility | The plugin structure allows specialized workflows to grow without hiding the core data model. |

## ✨ At A Glance

| Area | What ProgTrack Helps With |
| --- | --- |
| 🐒 Animal records | Role-specific animal profiles, lifecycle events, archive/restore workflows. |
| 📈 Hormone data | Blood progesterone, urine PdG, unified progesterone-equivalent views. |
| ⚖️ Measurements | Weight, sperm values, time-stamped events, Excel import/export. |
| 🧫 Samples | Biological and tissue sample tracking through Sample Track. |
| 🩺 Medical history | Diagnoses, notes, treatments, documents, and abnormal/sick status. |
| 🏠 Housing | Building, room, cage, animal placement, and movement history. |
| 🌳 Pedigree | Parent relationships, kinship, inbreeding, genotype annotations. |
| 📋 Projects | Project filters, assignment history, and project management support. |
| 🔐 Users | Login, guest mode, roles, jobs, permissions, sessions, and audit logs. |
| 🚀 Portability | `Launcher.exe` runs the app with a bundled Python runtime. |

## 🧭 Contents

- [🚀 Quick Start](#-quick-start)
- [💡 Why ProgTrack?](#-why-progtrack)
- [🧩 What Is Included?](#-what-is-included)
- [🖥️ Portable Launcher](#️-portable-launcher)
- [📁 Folder Layout](#-folder-layout)
- [🐒 Animal Workflows](#-animal-workflows)
- [📊 Measurements And Plots](#-measurements-and-plots)
- [🔌 Plugin Overview](#-plugin-overview)
- [🔐 Users Jobs And Permissions](#-users-jobs-and-permissions)
- [🌍 Languages And Appearance](#-languages-and-appearance)
- [💾 Data And Backups](#-data-and-backups)
- [🛠️ Troubleshooting](#️-troubleshooting)
- [🏗️ Build Source](#️-build-source)
- [🗺️ Roadmap](#️-roadmap)
- [⚖️ License](#️-license)

## 🚀 Quick Start

1. **Download the release `.zip` file from
   [GitHub Releases](https://github.com/Lindenfrost/ProgTrack-Release/releases).**
2. **Extract the complete folder and keep the folder structure intact.**
3. **Start ProgTrack with `Launcher.exe`.**
4. **Use `File -> Save Database` after larger editing or import sessions.**
5. **Open the user guide in `manual/` for detailed workflows.**

### 🔑 Starter Login Accounts

The release bundle contains starter accounts for testing the role and job
system. The initial password for **each** account is:

```text
123456
```

Passwords can be changed at any time, for example directly on first run.
For real shared use, change the default passwords before entering sensitive data.

| Username | Display Name | Role | Job Bundle |
| --- | --- | --- | --- |
| `Admin` | Administrator Administratorson | `lord` | none |
| `Researcher` | Dr. Researcher Sciencedottir | `user` | `researcher` |
| `Vet` | Dr. Veterinary Medicinsson | `user` | `vet` |
| `Manager` | Dr. Manager Plansdottir | `user` | `manager` |
| `Keeper` | Keeper Breedsson | `user` | `keeper` |
| `Tester` | Tester Aitisson | `user` | `tester` |

The portable release already contains Python and the required runtime
libraries. Users of the release bundle do **not** need to install Python,
PyQt6, matplotlib, pandas, NumPy, SciPy, or other packages.

Versioned downloads are intended to be published through
[GitHub Releases](https://github.com/Lindenfrost/ProgTrack-Release/releases).

## 🧩 What Is Included?

This release combines two layers:

### 🧬 ProgTrack Application

The editable application payload:

- `ProgTrack.v.0.1.1.py`
- `Plugins/`
- `icons/`
- `lang/`
- `manual/`
- local JSON data files

### 🚀 ProgTrack Launcher

The portable Windows runtime wrapper:

- `Launcher.exe`
- `_internal/`
- third-party license notices

The launcher starts ProgTrack, but the ProgTrack payload itself remains visible
and editable beside the launcher.

## 🖥️ Portable Launcher

`Launcher.exe` is a PyInstaller OneDir launcher.

When it starts, it:

- 📍 finds its own folder;
- 🧰 prepares the bundled Python runtime;
- 🪟 prepares Qt/PyQt runtime paths;
- 🎨 creates or reuses `matplotlib_cache/`;
- 🔎 finds the first `ProgTrack.v.*.py` script beside itself;
- ▶️ executes that script as the main application.

The `matplotlib_cache/` folder keeps plotting independent of a writable Windows
temporary directory.

To launch a specific script manually:

```text
Launcher.exe --script ProgTrack.v.0.1.1.py
```

> 💡 The ProgTrack application is **not compiled into** `Launcher.exe`.  
> This keeps the payload inspectable, replaceable, and easier to test.

## 📁 Folder Layout

| Path | Purpose |
| --- | --- |
| `Launcher.exe` | 🚀 Portable Windows launcher. |
| `_internal/` | 🧰 Bundled Python runtime and third-party libraries. |
| `ProgTrack.v.0.1.1.py` | 🧬 Main ProgTrack application script. |
| `Plugins/` | 🔌 ProgTrack modules and plugin data. |
| `icons/` | 🎨 Icons and visual resources. |
| `lang/` | 🌍 User-interface translation files. |
| `manual/` | 📖 HTML user guides. |
| `progtrack_daten.json` | 💾 Main animal and measurement database. |
| `Username + 123456 password.png` | 🔑 Starter-account reference image. |
| `LICENSE` | ⚖️ GPL license text. |
| `LICENSE_NOTICE.md` | 🧾 ProgTrack copyright and license notice. |
| `THIRD_PARTY_NOTICES.md` | 📦 Third-party component overview. |
| `third_party_licenses/` | 📚 Third-party license texts. |

> ⚠️ Moving only `Launcher.exe` is not enough.  
> The launcher and payload folders must stay together.

## 🐒 Animal Workflows

ProgTrack is built around individual animals. Each animal has a role, and that
role controls which fields, tabs, measurements, limits, and workflows are
shown.

### 🏷️ Current Role Labels

| Role | Typical Use |
| --- | --- |
| ♀️ `Egg Donor` / `Surrogate` | Steroid workflow animals. |
| ♂️ `Sperm Donor` | Sperm measurements and donor workflows. |
| 👶 `Offspring` | Young animals and offspring-specific records. |
| 🐾 `Partner` | Partner animals with lighter records. |
| ⚤ `Breeding Animal` | Breeding colony animals. |
| 💡 `Experimental Animal` | Experimental animals with dedicated records. |

### 🧰 Common Actions

| Action | Use |
| --- | --- |
| ➕ `New Animal` | Create a new animal record. |
| ✏️ `Edit` | Open the full role-specific editor. |
| 🫥 `Edit Role` | Change the role/category from the `All` view. |
| 📦 `Archive` | Remove active animals from routine views while keeping data. |
| ⤴️ `Restore` | Restore archived animals. |
| 🗑️ `Delete` | Permanently delete archived animals. |

## 📊 Measurements And Plots

ProgTrack supports manual entry and Excel import for several data streams.

| Data Type | Current Use |
| --- | --- |
| 📈 Blood progesterone | Blood hormone time series in `ng/ml`. |
| 📉 Urine PdG | Urine PdG time series in `ug/mg Cr`. |
| 🔁 Unified Prog. | Progesterone-equivalent view derived from fitted PdG models. |
| ⚖️ Weight | Body-weight time series in grams. |
| 📍 Events | OPs, embryo transfers, pregnancies, births, PGF, FSH, lifecycle events. |
| 🎈 Sperm values | Count, motility, and progressive motility for sperm donors. |

### 📥 Excel Imports

Excel imports expect exact column names.

| Import Button | Required Columns |
| --- | --- |
| 📈 `Load Blood Values` | `Name`, `Datum`, `Progesteron (ng/ml)`, `F` |
| 📉 `Load Urine Values` | `Name`, `Datum`, `PdG (ug/mg Cr)` |
| ⚖️ `Load Weights` | `Name`, `Datum`, `Gewicht` |
| 🎈 `Load Sperm Values` | `Datum`, `Name`, `% Motility`, `% Progressive`, `Sperms/ml` |

Optional sample identifiers are recognized from common columns such as
`Probennummer`, `Sample ID`, `Sample`, `Probe`, `ID`, `sample_id`, or `probe`.

### 🔎 Plot Exploration

The plot view can show:

- combined hormone views;
- blood progesterone only;
- urine PdG only;
- weight overlays;
- sperm value plots;
- reproductive event markers;
- female phase filters when Steroid Track is enabled;
- customized colors, markers, and line styles.

## 🔌 Plugin Overview

Plugins are detected at startup. Some are full tabs, some open dialogs or
windows, and some act as feature gates inside the core interface.

| Plugin | Kind | Purpose |
| --- | --- | --- |
| 🧪 `Steroid Track` | Feature gate | Enables steroid roles, hormone imports, sperm imports, reproductive event controls, phase filters, and PdG converter integration. |
| 🔐 `Master Track` | Administration | Login, guest mode, users, jobs, permissions, sessions, and audit logs. |
| 📄 `Animal Reports` | Main tab | Per-animal reports, timelines, statistics, locked entries, and PDF-style exports. |
| 🩺 `Medi Track` | Main tab | Medical history, abnormal/sick status, diagnoses, treatments, observations, documents, and medical reports. |
| 🗓️ `Surgery Planner` | Dialog | Surgery and embryo-transfer planning with recovery rules, blocked days, Gantt-style planning, and export. |
| 🧠 `Embryo Tracker` | Dialog | Gestation-day prediction from cranial ultrasound measurements. |
| 🔁 `PdG to Progesterone Converter` | Dialog | Per-animal PdG-to-progesterone model fitting. |
| 🧬 `Flow Track` | Main tab | Embryo-flow visualization between egg donors, sperm donors, surrogates, and freezer inventory. |
| 🌳 `Heritage Track` | Main tab | Pedigree graphs, parent editing, kinship, inbreeding, genotype annotations, and large-pedigree rendering. |
| 🏠 `Cage Track` | Main tab | Building, room, cage, animal placement, movement history, inspections, and PDF export. |
| 🧫 `Sample Track` | Window | Organ and biological sample tracking with aliquots, filters, auto-created blood/urine samples, and PDF export. |
| 📋 `Projects Track` | Sidebar and tab | Project/species filters, project assignment cache, project history, and project management. |
| 💬 `Network Track` | Window | Local file-based team chat with polling and optional notification sounds. |

## 🔐 Users Jobs And Permissions

Master Track separates **account role** from **job bundle**.

- The account role defines the administrative level of the user.
- Job bundles add practical task permissions for daily work.
- Direct permission grants and revocations can further adjust a user.

This makes it possible to keep accounts readable and realistic: a user can be a
normal `user` account, but still receive the `vet`, `keeper`, `manager`, or
`researcher` job permissions needed for the actual workflow.

### 👑 Account Roles

| Icon | Role | Meaning |
| --- | --- | --- |
| ![Lord](icons/job_lord.png) | `lord` | Highest authority. Lord has the wildcard permission and can perform all actions, including lord-exclusive actions such as creating users and controlling Master Track itself. |
| ![Master](icons/job_master.png) | `master` | Administrative account with almost all known permissions. Master can manage most users, jobs, permissions, audits, and application workflows, but deliberately lacks the lord-exclusive permissions to create new user accounts and to disable user permission management plugin. |
| ![User](icons/information.png) | `user` | Standard logged-in account. Effective permissions come from the user baseline, assigned jobs, direct grants, and revoked permissions. |
| ![Guest](icons/question.png) | `guest` | Read-oriented fallback account. Guest uses a fixed guest permission baseline; direct grants and revocations are ignored. |

**Lord vs Master in short:**  
`lord` is the owner-level account and always has every permission. `master` is
the daily administration account: powerful enough to manage the system, but
kept below `lord` for user creation and Master Track control.

### 🧰 Current Job Bundles

The current release contains these job bundles in
`Plugins/Master_Track/jobs.json`.

| Icon | Job | Typical Focus |
| --- | --- | --- |
| ![Vet](icons/job_vet.png) | `vet` | Medical review, animal status, Medi Track documents, reports, filters, cage/project visibility, and team communication. |
| ![Keeper](icons/job_keeper.png) | `keeper` | Housing, cage assignment, cage inspections, animal core data, measurements, Medi Track visibility, reports, and team communication. |
| ![Manager](icons/job_manager.png) | `manager` | Animal creation, imports/exports, archiving/deleting, project management, cage management, SOP/project documents, severity handling, and experiment assignment. |
| ![Researcher](icons/job_researcher.png) | `researcher` | Measurement editing, research data, imports/exports, reports, PdG conversion, OP scheduling, embryo tools, flow tools, sample tools, and project visibility. |
| ![Tester](icons/job_tester.png) | `tester` | Restricted testing bundle for read-oriented workflows, selected plugin access, measurements/research checks, and team communication. |

Job bundles and their associated permissions are not fixed in the source code
only. They can be modified for a local installation by users with the
appropriate Master Track administration permissions. The active job definitions
are stored in the plugin folder, and the permission catalog is
defined by Master Track.

This design allows each institution to adapt ProgTrack to its own division of
responsibilities without changing the overall plugin architecture.

## 🌍 Languages And Appearance

The interface is localized through files in `lang/`.

Available language resources:

- 🇬🇧 English
- 🇩🇪 Deutsch
- 🇮🇹 Italiano
- 🇷🇺 Русский

Use `Settings -> Language` to change language.

Use `Settings -> Style` to configure:

- 🎨 measurement colors;
- 📍 event colors;
- 🔘 markers;
- 〰️ line styles.

## 💾 Data And Backups

ProgTrack 0.1.0 stores data locally in JSON files.

Important files include:

- `progtrack_daten.json`
- `progtrack_settings.json`
- plugin-specific JSON files inside `Plugins/`

Recommended backup routine:

1. Close ProgTrack.
2. Copy the complete ProgTrack folder, or at least the main JSON data files, to
   a protected backup location.
3. Reopen ProgTrack.

> ⚠️ Do not edit JSON files manually unless you know exactly what you are
> changing. Prefer the ProgTrack user interface.

## 🛠️ Troubleshooting

### 🚫 ProgTrack Does Not Start

Check:

- the `logs/` folder beside `Launcher.exe`
- `Master Track` -> `Open tech logs`, if ProgTrack starts and you are logged in as Lord/Master

Also confirm that these are still in the same folder:

- `Launcher.exe`
- `_internal/`
- `ProgTrack.v.0.1.1.py`
- `Plugins/`
- `icons/`
- `lang/`
- `manual/`

### 🔌 A Plugin Is Missing

Check whether the plugin folder exists under `Plugins/` and contains its
expected Python files and `manifest.json`.

### 📥 Excel Import Fails

Check:

- exact column names;
- date formatting;
- empty animal names;
- non-numeric measurement values;
- whether the correct import button was used for the file type.

### 📉 Plotting Or Export Crashes

Use the complete portable bundle, including `_internal/`. Missing runtime
libraries can affect plotting, Excel import/export, PDF export, or notification
sounds.

## 📖 User Guides

The `manual/` folder contains detailed HTML user guides:

- 🇬🇧 `ProgTrack_User_Guide - en.html`
- 🇩🇪 `ProgTrack_User_Guide - de.html`
- 🇮🇹 `ProgTrack_User_Guide - it.html`
- 🇷🇺 `ProgTrack_User_Guide - ru.html`

These guides cover startup, data files, animal roles, imports, plotting,
plugins, exports, troubleshooting, and licensing.

## 🏗️ Build Source

Launcher build source is available in the GitHub repository under `source/`.
It is not part of the compact portable release ZIP.

Important files:

- `launcher.py`
- `launcher_small.spec`
- `hiddenimports.txt`
- `build_launcher_small.bat`
- `progtrack_icon.ico`

The intended build uses the repository-local pip environment created by
`build_launcher_small.bat`.

The application payload is not copied into the launcher executable. It remains
beside `Launcher.exe`.

## 🗺️ Roadmap

| Version | Focus |
| --- | --- |
| `0.1.1` | Phase 0 stabilization and release hardening. |
| `0.1.0` | Initial public testing package. |

### 0.1.1 Release Note

`0.1.1` is the first stabilization release after the public testing package. It focuses on making core project workflows safer, clearer, and more predictable before the next feature phase.

Project tracking now preserves and displays previous project history more reliably, including departed severity and previous experimental-history state. Manager permissions for associated users, SOPs, and project documents were aligned with the Master Track permission model. Technical logs are easier to reach through a Lord/Master-only `Open tech logs` action.

The release also improves day-to-day UI stability: lazy-loaded tabs no longer flash unrelated module content, Cage Track avoids unnecessary refresh work during ordinary navigation, and Heritage Track now requires a deliberate double-click to clear the current graph selection from empty space.

The launcher/runtime package was refreshed for this release. The new `0.1.1-log-menu` launcher records its version, supports the updated technical-log workflow, includes the ReportLab runtime imports needed for report export, and keeps the portable Windows structure: `Launcher.exe`, `_internal/`, the editable ProgTrack script, plugins, manuals, language files, demo data, icons, and license notices.

### 0.1.0 RC Release Note

`0.1.0 RC` publishes the current portable Windows launcher and ProgTrack
payload for public testing. The bundle includes the editable ProgTrack Python
source, plugin folders, manuals, language files, icons, bundled runtime
libraries, starter demo accounts, and licensing/third-party notices.

This release is meant for evaluation and feedback. It should be tested with
placeholder or demo data first. Before real shared use, change the starter
passwords, review account permissions, and make regular backups of the local
JSON data files.

## ⚖️ License

Copyright (C) 2026 Dimitri L. Lindenwald, PhD, Deutsches Primatenzentrum GmbH,
Leibniz Institute for Primate Research, Kellnerweg 4, 37077 Goettingen,
Germany.

ProgTrack is released under the GNU General Public License version 3.0 or
later. You may use, modify, and redistribute the software under the terms of
that license.

A copy of the license is available at:

https://www.gnu.org/licenses/gpl-3.0.html

It is also included in this repository as `LICENSE`.

If a derivative work is prepared, that is based on or incorporates ProgTrack or
any part thereof and that is made available to others, then a summary of the
changes made to ProgTrack shall be included in any such work.

The software is provided without any warranty to the extent permitted by law.
In particular, no warranty is given for functionality, freedom from errors, or
fitness for a particular purpose.

The compiled launcher contains a portable Python runtime and third-party
libraries. Those components remain the property of their respective rights
holders and are governed by their own license terms.

Known third-party notices are documented in:

- `THIRD_PARTY_NOTICES.md`
- `third_party_licenses/`

## 🏛️ Acknowledgement

ProgTrack is developed at Deutsches Primatenzentrum GmbH, Leibniz Institute for
Primate Research, Goettingen, Germany.
