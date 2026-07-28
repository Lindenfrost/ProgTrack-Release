# Evidence register

## Repository snapshot

- Repository: `Q:\GitHub\ProgTrack-Release`
- Branch: `Phase-0.1.2`
- Commit: `793aac8bb14762e88efebc31cebb2fcf922c18ba`
- Baseline worktree: clean
- Tracked files at baseline: 1,266
- Audit method: read-only source search, JSON/schema inspection, reference
  reconciliation, manifest comparison, file inventory, sizes, and SHA-256.
- Critical re-review received: 2026-07-28 (`Desktop\rev`).
- Architecture working-paper revision: 2026-07-28.

No application was launched and no runtime save method was invoked. Password
hash/salt values and personal profile values were not copied into the audit.

## Coverage

All 13 shipped manifests were parsed:

1. Animal Reports
2. Cage Track
3. Embryo Track
4. Flow Track
5. Heritage Track
6. Master Track
7. Medi Track
8. Network Track
9. PdG converter
10. Projects Track
11. Sample Track
12. Steroid Track
13. Surgery Planner

Source searches covered:

- direct JSON/text/file writes and atomic replacements;
- directory creation, copies/moves, managed documents, and QSettings;
- data-file constants and literal filenames;
- `animal_id`, `ipid`, animal/project/name, and selected-item references;
- permission definitions and checks;
- disabled-plugin loading, persistence, and tab/tool state;
- plugin backup discovery and manifest `data_files`;
- internal role constants and persisted role boundary values.

## Static check results

| Check | Result |
| --- | --- |
| Manifests present and valid JSON | 13/13 pass |
| Core persisted animal roles use internal English IDs | pass |
| Core non-empty relationship references resolve | 256/256 |
| Heritage non-empty parent references resolve | 240/240 |
| Public animal IDs unique in inspected core data | 227/227 |
| Core record `ipid` equals its dictionary key | 227/227 |
| Actual write stores completely declared by manifests | fail; documented mismatches |
| All managed payloads represented by document metadata | fail; two Medi folder-only files and project folder-only ownership |
| Every plugin animal reference resolves to core | fail; documented example/test references |
| Project identity consistent across current/data/history | fail; documented `Zeta-1`/Crossbreeding differences |
| Every example animal has a controlled catalogue origin | fail; 11 empty origins and stored `DPZ`/`Iluvatara` catalogue differences |
| One unambiguous Surgery schedule source/path/format | fail |
| Canonical backup possible from manifest list alone | fail |

These failures are findings, not changes or runtime defects repaired by issue
#48.

## Critical re-review evidence status

The initial checks were performed read-only and their outcomes are recorded
here, but the review found that they are not yet packaged as a repeatable
verification artifact. Before Issue #48 approval:

- [x] Add a path/pattern inventory covering application/plugin stores,
      QSettings, compatibility/cache paths, managed roots, resources, inputs,
      and generated output (document 01 Annex A).
- [x] Add an action-family permission/service/lock/audit map (document 02
      Annex A).
- [x] Specify the repeatable read-only command/artifact contract below.
- [ ] Execute and retain one structured frozen-commit artifact with checksum,
      interpreter versions, exit status, and before/after clean-tree proof.

Until then, the related Issue #48 acceptance criteria remain open. Document 09
records the integrated amendments and residual evidence/issue work.

## Measurement-import call-path evidence

Frozen main module: `ProgTrack.v.0.1.2.py`.

| Evidence | Frozen-source location |
| --- | --- |
| resolver supports mutating `create_missing=True` | `_resolve_import_animal_key`, around line 4624 |
| unknown Animal ID can call identity prompt and `_ensure_defaults_for_new` | around lines 4654–4675 |
| legacy species/birth can create directly | around lines 4746–4751 |
| missing identity can prompt/create | around lines 4756–4765 |
| preview resolves non-mutating and displays at most 200 rows | `_confirm_measurement_import_preview`, around lines 4792–4854 |
| species/full-birth identity dialog | `_prompt_identity_for_import`, around line 4857 |
| blood measurement/event accepted paths use `create_missing=True` | around lines 21177 and 21243 |
| urine accepted path uses `create_missing=True` | around line 21624 |
| weight accepted path uses `create_missing=True` | around line 21715 |
| sperm accepted path uses `create_missing=True` | around line 21799 |

This proves the Issue #53 motivation. It is not evidence that the target has
been implemented.

## Disposable example-origin mapping evidence

A read-only calculation on the frozen core JSON treated an animal as parentless
only when all four current parent references were empty.

| Store / species group | Parentless count | Proposed target origin |
| --- | ---: | --- |
| active / Callithrix group (current `Callitrix`) | 15 | `Iluvatar` |
| active / Macaca | 18 | `Aulë` |
| active / Papio | 20 | `Morgoth` |
| active / other/unknown | 5 | `DPZ` |
| archived / Callithrix group (current `Callitrix`) | 19 | `Iluvatar` |
| archived / Macaca | 22 | `Aulë` |
| archived / Papio | 1 | `Morgoth` |
| archived / other/unknown | 1 | `DPZ` |

All non-parentless animals receive `DPZ`. This initial calculation was
read-only; the subsequently authorized execution is recorded below.

### Cleaning execution and validation

Reviewer authorization moved this work out of the Phase 2A approval process.
The cleaning was executed on 2026-07-28:

- records checked: 227 (`134` active, `93` archived);
- `origin` values changed: 41;
- empty origins after cleaning: 0;
- `Iluvatara` values after cleaning: 0;
- rule violations: 0;
- origins absent from controlled catalogue: 0;
- catalogue: `Aulë`, `DPZ`, `Iluvatar`, `Morgoth`;
- non-`origin` JSON content lines changed: 0;
- three-block dictionary keys changed: 0;
- cleaned `progtrack_daten.json` SHA-256:
  `759ec95a567741023a2f74b56b999bba9b1679f1cd0d18e8f912744cd3d8f186`;
- focused origin/resource regression module: 6 tests passed.

Resulting counts:

| Store | `Aulë` | `DPZ` | `Iluvatar` | `Morgoth` |
| --- | ---: | ---: | ---: | ---: |
| active | 18 | 81 | 15 | 20 |
| archived | 22 | 51 | 19 | 1 |

The earlier static-table failure remains a valid fact about the frozen
pre-clean snapshot. This subsection records its subsequent resolution. A full
test discovery attempt exceeded the 60-second command window and produced no
final result; it is not counted as validation evidence.

## Orphan Sample/Project example-data cleaning evidence

Reviewer-authorized cleaning on 2026-07-28 removed:

- 1 unresolved organ row and 3 unresolved other-sample rows;
- 3 unresolved explicit Project Track history entries;
- `Plugins/Projects_Track/documents/A/file_csv.png`;
- `Plugins/Projects_Track/sop/A/file_img.png`.

Post-clean validation:

- remaining Sample Track rows: 5;
- unresolved remaining Sample Track animal references: 0;
- remaining explicit Project Track history IPID references: 32;
- unresolved remaining explicit Project Track history IPIDs: 0;
- remaining `documents/A` or `sop/A` directories: 0;
- focused Sample/Project regression modules: 10 tests passed.

Post-clean SHA-256:

| File | SHA-256 |
| --- | --- |
| `Plugins/Sample_Track/organs.json` | `ced14a78fd1a9dff5d0f717e5a0c85a7f19387c96841d3a36bf857014b870fa2` |
| `Plugins/Sample_Track/other.json` | `131ac38be53f6ba68aae6f212b49f5de9792109deba88bf547196bf2522f4cf9` |
| `Plugins/Projects_Track/projects_history.json` | `b20c13a7b4900d5abf889b9d89fdddd8f6e6e8eacaa99e8e99d6e22e0d28a258` |

The historical managed-payload table below intentionally retains the deleted
`A` file hashes as evidence of the frozen pre-clean snapshot.

## Published issue consistency review

| Issue | Evidence/status required before Phase 2B |
| --- | --- |
| #11 | project visibility remains service scope; caches are not authorization |
| #12 | UI/launch checks remain defensive; service enforcement is mandatory |
| #15 | closed Phase 1 history is valid, but post-creation identity editing and import-created animals must receive a Phase 2 superseding note |
| #16 | grantable permission catalogue and internal-only distinction remain binding |
| #34 | example workbooks remain measurement templates for existing animals only; mixed fixtures belong to #53 validation |
| #48 | closed as completed after final approval and versioned publication; #49–#53 are unblocked |
| #49 | owns both profile runtime paths plus managed config/document roots |
| #50 | currently requires synchronization to four-block IPID, typed IDs, two supported profiles, and #53 service separation |
| #51 | packaged icons remain resources, not managed payload/interchange |
| #52 | depends on #49/#50 and uses managed configuration asset/permission |
| #53 | existing-animal-only full-file plan, partial success, direct-service enforcement, safe retry |

Published synchronization completed on 2026-07-28:

- local and GitHub bodies for Issues #48–#53 match exactly after UTF-8
  normalization;
- closed Issue #15 received superseding comment
  `issuecomment-5104489778`;
- closed Issue #34 received superseding comment
  `issuecomment-5104490970`;
- open blocking Issue #48 received current correction comment
  `issuecomment-5104492341`;
- Issue #52 received the reviewer logo-scaling clarification comment
  `issuecomment-5104678946`, and its local/GitHub body was resynchronized;
- Issue #48 received the final clean-baseline evidence comment
  `issuecomment-5104741156`;
- the approved architecture was published in repository commit `bb227f5`;
- Issue #48 received the final completion evidence comment
  `issuecomment-5105002497` and was closed as completed;
- no historical comment was edited or deleted;
- #48 is closed in milestone `0.2.0`; #49–#53 remain open and unblocked in
  milestone `0.2.1`.

## Repeatable read-only verification contract

Artifact schema version: `phase2a-audit-evidence/1`.

The verifier runs from the repository root and emits one
UTF-8 JSON result plus a SHA-256. Required sections:

1. repository path, branch, commit, `git status --porcelain` before/after;
2. interpreter/tool versions and invocation;
3. manifest parse/entry-point resolution;
4. concrete path/write-store/QSettings/managed-root inventory;
5. authoritative-store and managed-payload hashes;
6. animal/project/user/reference reconciliation;
7. built-in/custom role and typed-ID boundary checks;
8. action/permission/label/launch/store comparison;
9. disabled-plugin mapping;
10. measurement-import `create_missing=True` call-site detection;
11. direct database-driver/SQL detection (expected after Phase 2B to exist only
    inside approved adapters);
12. expected-result comparison and exit-code rules.

Exit `0` only when the command completed, the tree stayed clean, and all
expected pass/fail snapshot assertions match. A changed source/runtime/example
file, missing evidence section, or unexpected result is non-zero. The command
may create its result only under this Issue Tracker audit directory or a
designated temporary output directory, never inside ProgTrack.

Executable artifact:
`phase2a_readonly_audit.ps1` (current verifier version `1.0.1`; the first
retained dirty-baseline run used version `1.0.0`).

The verifier intentionally excludes `_internal` from content parsing,
hashing, and source-call analysis because it is the bundled runtime rather
than ProgTrack source or mutable facility data. The complete Git-status
comparison still detects any worktree change involving that directory.

First retained run:

- generated: `2026-07-28T12:44:41.2759699Z`;
- repository: branch `Phase-0.1.2`, commit
  `793aac8bb14762e88efebc31cebb2fcf922c18ba`;
- invocation: Windows PowerShell with `-NoProfile -ExecutionPolicy Bypass`,
  repository root and output path passed explicitly;
- result: `phase2a_audit_result.json`;
- result SHA-256:
  `73c027d45b2fd1d8b354932720e6b07f648d6820e333c9f0819186a2518c6408`;
- exit code: `0`; schema result: `passed = true`;
- worktree proof: the same seven approved cleanup changes occurred before and
  after the run; `status_unchanged = true`;
- checked: 13 manifests, 134 persistence/resource candidates, six managed
  payloads, 227 animals, 256 relationship references, five Sample Track
  animal references, and 32 explicit Project Track IPID references;
- static evidence retained: 182 write call sites, zero direct database/SQL
  call sites, five current measurement `create_missing=True` call sites, and
  93 disabled-plugin-related call sites.

This first run proves that the verifier is executable and read-only against
the approved but uncommitted cleanup state. It does not replace the required
final rerun after those cleanups are committed; that clean-baseline result is
the Phase 2B entry gate.

Final clean-baseline run:

- generated: `2026-07-28T13:25:10.7538929Z`;
- repository: branch `Phase-0.1.2`, commit
  `3fc22583799b6ed394544035f1387e1c759c3aea`;
- result: `phase2a_audit_result_clean.json`;
- result SHA-256:
  `193ac3c6b09b55350daeba07ffc3c6015c04880d98151dda7911d71dcb34ba2e`;
- exit code: `0`; schema result: `passed = true`;
- clean-tree proof: zero Git-status entries before and after;
  `status_unchanged = true`;
- checked: 13 manifests, 134 persistence/resource candidates, six managed
  payloads, 227 animals, 256 relationship references, five Sample Track
  references, and 32 explicit Project Track IPID references.

This satisfies the clean frozen-commit evidence gate. The two removed orphan
payload files reduce the tracked-file count from 1266 to 1264 as expected.
Verifier 1.0.1 additionally enforces exact 73/73 permission-label coverage in
all supported languages and supplies the corrected 154-site write inventory
reconciled in document 10.

The document 10 AST set comparison returned 73 actual callables, 73 mapped
callables, zero missing, and zero extra. Its `persistence_inventory`
reconciliation covers all 134 concrete candidate paths.

## Earlier inspected authoritative-store hashes

The table below preserves the earlier pre-clean inspection snapshot. Current
post-clean hashes are authoritative in `phase2a_audit_result_clean.json`;
neither set is an interchange manifest.

| Path | Bytes | SHA-256 |
| --- | ---: | --- |
| `progtrack_daten.json` | 405617 | `845764a9f7f8c93a238d246998d40e1f63503a8b936f25b9a75d219f7115de24` |
| `Plugins/core/animal_roles.json` | 12332 | `aa9097a0eccb06be9e89a453f6f44e3aec77d1d141a282c204fd134138156598` |
| `Plugins/core/identity_lifecycle_conventions.json` | 805 | `25139df18affb0117ee560742376a6fca75f9e033f06a7fced6308b10700ba7f` |
| `Plugins/Animal_Reports/animal_report_data.json` | 9640 | `5bd4d0f600fcc1ffec40ec62d7e19076434f66b44c298685988cc9da4968e7f5` |
| `Plugins/Cage__Track/cage.json` | 245116 | `586110253c936da5dc5fcd6251f3ac0fd2d277f1b962c19eb35ab45d49fea088` |
| `Plugins/Cage__Track/inspection.json` | 2263 | `dfb8a3d80845500c96a52aa14e7a2a9ec584f0baf474c3279a72ef923eee37ca` |
| `Plugins/Embryo_Track/cranimetry_reference.json` | 5972 | `360c20f9015e8d10c5a3cf4ddddce7a470de1ae616a3c29c012fd3e55c0b5cab` |
| `Plugins/Flow_Track/flowtrack_daten.json` | 4425 | `9c13f433df85eb99b498a7a3a97d0e580e83d714f2b7d59ccd4a96751e06c453` |
| `Plugins/Heritage_Track/heritage_animals.json` | 159790 | `a58986711022658add155f66e808afc991aac7ecac152afcab09eb423deb3e62` |
| `Plugins/Master_Track/users.enc` | 5660 | `3132fc4fdab98f9c10fda71322978c76c8487b24b4c432a6ef6a05d66ee809b1` |
| `Plugins/Master_Track/jobs.json` | 3875 | `238353d35503f4f8288bdc0a50fc1d1eb76fdacc35cbbc2033afe9533f4b8d67` |
| `Plugins/Master_Track/audit_2026-06.log` | 436813 | `48dbffce1044a3d3ea94acd0a5fd67e7e3846d3c4bf7e855324152e611c59b4a` |
| `Plugins/Medi_Track/medi_history.json` | 145309 | `75d7f17a8add70bb6aa9cb2c680a2161a3b45678714ee5253cd773cb57060753` |
| `Plugins/Network_Track/chat_log.txt` | 252 | `6a56a8c382a437f8d3e20ffc91d30064d11cf1c07b9b72c960705a74e6bf65e9` |
| `Plugins/PdG_converter/data/models.json` | 465 | `f993aa1b6b8a24d8b0cc2d979e4223d10eed7036df96633b2b3dae00600d1225` |
| `Plugins/Projects_Track/project_data.json` | 8693 | `0dcd92bc51fb28297f3a60a514177a992f1066af3a69964b41705808592e6af3` |
| `Plugins/Projects_Track/projects_history.json` | 12597 | `1d90704b4f06cda2b868afb451c290e08dc38b6acf8070e19b2f57c6ed441399` |
| `Plugins/Sample_Track/organs.json` | 1538 | `280672872b8630f51a31a7d8893768953a2269c96532ebaae35b08cdf38413aa` |
| `Plugins/Sample_Track/other.json` | 2696 | `7e440e545d202a9fa85a04e84d43194d6c8967f9b0b2722311388a1001bbdcb2` |
| `Plugins/Surgery_Planner/Surgery_Pre_Planner.schedule.json` | 2339 | `ff765643974b29f1b83592587c8a8084b8c3ebb634b39c7fe1229d724e16ae6f` |
| `Plugins/Surgery_Planner/Surgery_Planner.schedule.json` | 1561 | `a76bfa58d952f653a48928dabf5a4fde486579ea3f2d0b3e4228ca210ca77359` |
| `Plugins/Surgery_Planner/Surgery_Planner.block-days.json` | 8867 | `8ef58572d35fb97e90cadcd964acc7f89731247cab837328bc6b8951468e951d` |

## Managed payload snapshot

- Medi: 5 files; 3 have JSON metadata; 2 Thranduil files are folder-only.
- Projects: 2 document files and 1 SOP file; ownership is folder-derived.
- The Thranduil PDF is zero bytes.
- Reused test images have identical hashes across owners, demonstrating why
  byte deduplication and logical ownership must remain separate.

| Relative source path | Inferred/current owner | Metadata state | MIME | Bytes | SHA-256 |
| --- | --- | --- | --- | ---: | --- |
| `Plugins/Medi_Track/medi_track/Arwen _ Callitrix jacchus _ 23.02.2015/file_csv.png` | Arwen animal IPID | linked in Medi JSON | `image/png` | 4542 | `a68e8af914e4c53ddcc8f1e3af8698150082fed753c45cf573299321829caa9c` |
| `Plugins/Medi_Track/medi_track/Azog _ Papio hamadryas anubis _ 04.04.2010/file_csv.png` | Azog animal IPID | linked in Medi JSON | `image/png` | 4542 | `a68e8af914e4c53ddcc8f1e3af8698150082fed753c45cf573299321829caa9c` |
| `Plugins/Medi_Track/medi_track/Azog _ Papio hamadryas anubis _ 04.04.2010/file_img.png` | Azog animal IPID | linked in Medi JSON | `image/png` | 3502 | `8b77568c23e8f0111ebf7c59344997cb3a51137e99e6e98383fec4b55729375d` |
| `Plugins/Medi_Track/medi_track/Thranduil _ Callitrix jacchus _ 11.02.2021/Thranduil_medi_export_note.txt` | Thranduil animal IPID | folder-only; no Medi JSON metadata | `text/plain` | 4 | `532eaabd9574880dbf76b9b8cc00832c20a6ec113d682299550d7a6e0f345e25` |
| `Plugins/Medi_Track/medi_track/Thranduil _ Callitrix jacchus _ 11.02.2021/Thranduil_medi_export_scan.pdf` | Thranduil animal IPID | folder-only; zero-byte; no Medi JSON metadata | `application/pdf` | 0 | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |
| `Plugins/Projects_Track/documents/A/file_csv.png` | unresolved project folder `A` | folder-derived only | `image/png` | 4542 | `a68e8af914e4c53ddcc8f1e3af8698150082fed753c45cf573299321829caa9c` |
| `Plugins/Projects_Track/documents/Zucht/information.png` | project `Zucht` | folder-derived only | `image/png` | 9131 | `e4f03cc37a54756debf3b286d83ab5bc03e27937dd35a56c465eb165264ca4cc` |
| `Plugins/Projects_Track/sop/A/file_img.png` | unresolved project folder `A` | folder-derived only | `image/png` | 3502 | `8b77568c23e8f0111ebf7c59344997cb3a51137e99e6e98383fec4b55729375d` |

## Program-tree non-mutation proof

At completion, `git status --short` and `git diff --stat` were rechecked in the
ProgTrack repository. Both returned no changes; branch and commit remained
`Phase-0.1.2` and
`793aac8bb14762e88efebc31cebb2fcf922c18ba`. The Phase 2A working papers are
outside that repository under the approved Issue Tracker documentation path.
