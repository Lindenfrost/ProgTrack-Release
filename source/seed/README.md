# Fictional seed authoring

`manifest.json` is the complete input inventory. Fixtures contain reviewed
authored facts; scenario JSON contains profiles, mouse naming/parentage,
weight profiles and reproductive recipes. Python expands the recipes,
projects the facts into existing Core/plugin contracts, and validates them.
The previous `.ptdb`, external archives and active databases are not inputs.
Shared Role Setup and craniometry catalogues are explicitly declared repository
resources, not duplicated here. Old PdG fitted parameters are explicitly
excluded because their animal identity is obsolete; runtime models start empty.

## Commands

From the repository root, with the development Python environment:

```powershell
python source/build_sample_seed.py
python source/build_sample_seed.py --check
python source/build_sample_seed.py --output-dir C:/Temp/ProgTrackSeedReview
```

The first command stages and validates a complete package before replacing
`Resources/Seed/progtrack_seed.ptdb`, `integrity_report.json` and
`SCENARIO_COVERAGE.md`. An ordinary publication exception restores the previous
artifact set. This is not a crash-atomic multi-file filesystem transaction.
`--check` compiles in isolation and fails on any stale/missing output without
replacing it. All commands use disposable SQLite with seed bootstrap disabled.
No command imports into or changes the active installation.

## Editing rules

- Edit fixtures or scenario declarations, never generated package/report data.
- Every authored occurrence (Core/lifecycle event, medical entry, inspection,
  sample collection and embryo donor/transfer date) is audited before pruning
  and again in the compiled data. Missing/invalid dates fail with the source
  path; never silently drop an orphan or use today's date. Repair an occurrence
  in its authoring source using its known chronology; ambiguous dates require
  review. Role event definitions and genuinely absent outcomes are not events.
- Missing, malformed, undeclared, wrongly shaped or escaping inputs fail.
- Animal IPIDs must match immutable identity; active/archived keys cannot collide.
- Generated scenario/event keys must stay stable when correcting values or notes.
  Existing IDs are pinned in `fixtures/event-identities.json`; no previous output
  is needed to recover them. Do not use unrelated population indices as IDs.
- Same animal, measurement kind and calendar day has one seed reading. This
  does not restrict real application measurements. Separate blood/urine kinds
  and distinct same-day events remain valid. Conflicts fail rather than being
  silently merged. Post-PGF monitoring calculates one low reading and keeps
  its sample number.
- Organizational Units are workgroups, not physical housing Units.
- Clinical witnesses are explicitly pinned, not the first four sorted animals.
- Default housing cages and project role-list order are explicitly authored in
  `scenarios/profiles.json`, not inferred from JSON object key order. Existing
  valid individual housing placements remain unchanged.
- Source hashes, scenario IDs, compiled counts and exclusions accompany the
  package. Text hashes normalize CRLF/LF. The compiler refuses source changes
  since import or during compilation; restart it after authoring edits.
- A build is reproducible under the same Python/compression toolchain. Do not
  infer identical compressed bytes across arbitrary library versions.

## Reproduction and FlowTrack

`scenarios/experiment-enrollments.json` pins project entry and experiment
admission for all 19 currently experimental animals by immutable identity.
Admission begins with the first experimental monitoring or before the first
intervention, never before birth/project entry. Legolas is assigned on
20 April 2026 and admitted the next day. Ringbearer admissions precede the
September 2024 surgeries; their former Shire memberships end at the individual
project-transfer dates. Azog's completed experiment and 21 July 2026 readmission
are retained, including existing historical IDs. These are authored fictional
dates, not a reconstruction of real institutional history.

The compiler aligns Core project dates/lifecycle events and daily Reports notes,
Project Track's active/former memberships, and Medi Track's corresponding
entries. It corrects the old OTOF spelling-change artifact rather than inventing
a project exit. Stable deterministic IDs prevent duplicate admissions on rebuild.
Identity, procedures, biological relationships and current experiment flags are
unchanged. Missing enrollments, dates after monitoring/procedures and inconsistent
cross-plugin projections fail validation.

`scenarios/reproduction.json` is the authority for collections, fertilization,
transfers and outcomes. Each cycle generates Core FSH/retrieval, sperm sample
and donation, transfer/verification and outcome records, plus the current
FlowTrack 3.0 `surgeries`, `donations`, `transfers_by_id` and embryo payloads.

Sperm collection is one day after egg retrieval. Transfer delay is measured
from sperm collection/fertilization: 6-9 days. The current examples also fit
7-9 days after eggs (6-8 after sperm), so both common readings of the requested
window are satisfied. The poor-yield cycle produces no embryos or transfer.
Each transferred embryo has explicit donor IPIDs, egg collection date and
sperm donation date; the latter indexes the donation containing its sample ID.
Counts cannot exceed earlier collection/fertilization stages.

Each egg collection explicitly supplies GV, MI, in-vivo MII, in-vitro
maturation, fertilization, embryo and frozen-embryo counters. Matching sperm
donations supply inseminated/fertilized/embryo counts for both branches.
In-vivo MII counts are deliberately low (0-2 per collection), with a few
fertilizations but **zero** resulting embryos. All 21 embryos arise from
IVF-fertilized, in-vitro-matured MII (`in_vitro_m2` in the existing schema).
The Tiffany/Boromir cycle retains four transferred embryos (including the implanted Boromir record) and two non-implanted embryos in `freezer_main`, frozen on 18 October 2024. Production remains six; no offspring or donor identity changes. Freezer is storage, not a transfer occurrence; its embryos require freeze dates, not a fabricated transfer date. Runtime usable yield counts current transferred plus frozen canonical IDs, never all produced/unassigned embryos. Historical cryopreservation may remain true after thawed transfer; the current container owns allocation.
This is a fictional seed design rule, not a scientific or runtime restriction.
Ekaterina's separate collection includes low-yield stage counts but no
fertilization/embryos or invented parentage links. Thranduil's three unused
samples have explicit zero allocation alongside his linked 2 October sample.

The corrected Legolas cycle preserves all biological parentage and birth
identity. Svetlana is the seed egg donor (1 October 2025), Thranduil's existing
2 October sample is reused, Olga's transfer is corrected to 10 October, and
birth remains 4 March 2026. Existing transfer/birth event types and IDs remain.
FlowTrack recognizes both canonical `oocyte_retrieval` and existing `surgery`
collection events; no duplicate surgery is added to make a donor visible.

Core runtime permissions, Heritage preview behavior and active databases are
unchanged. No backward-compatibility layer or database migration is required
for these fictional authoring changes. Test new seed data only in an isolated
empty backend; an existing backend is deliberately not reseeded automatically.

## Verification

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m unittest tests.test_phase2b_seed_cleanup -v
python -m unittest tests.test_phase2b_project_lifecycle -v
```

The existing local tests are ignored by repository policy; this change does
not publish them or change that policy. New coverage in the seed-cleanup module
checks clean isolated compilation without an archive/previous package,
repeatability and stale-artifact detection; missing/malformed inputs;
measurement duplicates; stable IDs and clinical witnesses; timing/donor/count
failures; Legolas parentage/sample reuse; real FlowTrack event extraction,
metrics and Matplotlib donor-connection rendering; and disposable SQLite import.

Native acceptance remains separate: on a fresh disposable backend, open
FlowTrack and inspect all seven transfer groups, both donor connections for
each of 21 embryos, the three live-birth examples, two negative outcomes and
two abortions. Check donor collection details and sample dates. Inspect
Tiffany's final progesterone/PdG dates for one numbered reading per kind.
In egg-donor details check `GV isolated` and `M2 isolated` (0-2 per collection),
in-vitro maturation/fertilization and both embryo counters. In sperm-donor
details check inseminated/fertilized counts for both branches: IVF fertilization
totals must agree with the egg donors, and in-vivo embryo counts must be zero.
Ekaterina has no fertilization/embryo allocation; Thranduil's three unused
samples remain zero allocations. MI counts are retained in the payload; the
current egg-donor dialog does not expose a separate MI-isolated spinbox.

Do not copy historical runtime sessions, caches, audit logs, backups or deployment
credentials into seed sources. Demo password `123456` is fictional and never
suitable for production.
