# Critical re-review resolution register

Status: **all document amendments, concrete path/callable reconciliation,
clean-baseline evidence, external issue synchronization, and final reviewer
approval completed on 2026-07-28**

This file replaces the earlier short re-review document while retaining its
history. It evaluates
`C:\Users\Dimitri\Desktop\rev` against the working package and records how its
expanded document 09 was applied.

## Revision-source comparison

| File | Comparison result |
| --- | --- |
| 00–05 | `rev` and the pre-revision working files were byte-identical. Their newer Desktop timestamps did not represent newer content. |
| 06 | `rev` was older and lost later decisions (#53 dependency, revised audit/orphan/identity handling) and had malformed Markdown escaping. It was not used as replacement. |
| 07 | `rev` was older and omitted the later Issue #53 plan. It was not used as replacement. |
| 08 | `rev` was older formatting/content and did not improve the current evidence register. |
| 09 | `rev` was substantially newer and more comprehensive: authoritative corrections A01–A08, findings R01–R17, document-by-document amendments, issue synchronization, and approval gates. It became the principal re-review input. |

The correct strategy was therefore selective integration, not copying the
whole `rev` directory.

## Integrated architecture corrections

### A01 — Animal identity and sole origin

- canonical animal key:
  `name | species | DD.MM.YYYY | origin`;
- `origin` is the existing Herkunftseinrichtung field;
- no competing origin field or animal UUID;
- IPID and all four source fields immutable for every role;
- complete imported IPID preserved; equal conflicts quarantined, never renamed;
- unknown imported origin blocks creation until catalogue approval;
- deterministic disposable example correction recorded and evidenced.

### A02 — Wrong finalized identity

No Phase 2B alias, merge, supersede, rewrite, or automatic reassignment.
Authorized delete plus correct creation is required; dependent data/documents
are manually re-entered. This is now explicit in documents 03, 04, 06, and 07.

### A03 — Typed identifiers

- animal reference: IPID;
- installation-created durable record: facility-owned record ID
  `<facility_tag>:<uuid>`;
- application protocol/reference constant: global semantic ID;
- built-in role: global semantic ID;
- custom role/job/configuration: facility-owned configuration record.

Concrete owner/surrogate/user/project/housing/document field errors were
corrected in document 03. No blanket “UUID means every non-animal key” rule
remains.

### A04 — Existing-animal-only measurement import

Documents 00–08 now record:

- current create-missing behavior and call-path evidence;
- non-mutating full-file plan;
- distinct row statuses;
- visible skipped unknown rows and consolidated warning;
- partial success for existing animals;
- transactional revalidation and deterministic retry;
- identifier separation;
- relevant measurement permission plus `core.import`, never creation;
- direct-service enforcement and operation audit;
- Issue #53 placed directly after core services in the Phase 2B order.

### A05 — Audit outcomes

Domain transaction events, independent denied/security events, and correlated
import/export operation events are distinct. A denied command makes no domain
write but may make a security-event write.

### A06 — Managed payload recovery

Documents 04/05 define `staged`, `pending`, `active`, `quarantined`, and
`deleted`, exact activation order, reconciliation, failure compensation, and
crash injection. Package payload ownership uses typed metadata and
`payload/documents/<document_id>/<sanitized-original-name>`.

### A07 — Institution branding

Branding is installation configuration plus a managed configuration asset,
grantable permission, optional protected package section, and shared renderer.
It is separate from animal origin, facility tag, domain documents, and
packaged icons.

### A08 — Dependency set

Issue #48 blocks existing Issues #49–#53. Issue #53 is not described as “a new
one.”

### A09 — Permanent deployment profiles

Added in this revision after critical evaluation of ProgTrack's portable
open-source positioning:

- Standalone SQLite is permanently supported production for one active local
  ProgTrack process on one workstation;
- Shared PostgreSQL is permanently supported/recommended for concurrent users,
  multiple workstations, central operations, and cross-client locks;
- SQLite on network/NAS/synchronized/cloud paths is rejected;
- one codebase, domain model, service contract, audit model, document model,
  and interchange format serves both;
- no automatic fallback between profiles;
- canonical package supports Standalone-to-Shared transition;
- Standalone documentation must disclose that direct local-file access sits
  outside ProgTrack permission enforcement and requires OS-level protection;
- README and manuals must explain profile choice after implementation is
  finalized.

This is more useful than PostgreSQL-only production and safer than pretending
network SQLite is a server. It also avoids a divergent “lite” edition.

## Document amendment status

| Document | Integrated changes | Status |
| --- | --- | --- |
| 00 | typed IDs, two profiles, #53 and branding blockers, evidence gates, #48→#49–#53 | done |
| 01 | exact origin terminology, current measurement behavior, branding absence, path inventory annex | done at pattern level |
| 02 | measurement/branding rows, typed IDs, built-in/custom distinction, action/permission/service annex | action-family contract done; exact callable reconciliation completed in document 10 |
| 03 | removed supersede, consumes completed cleaned origins, typed fields, branding/audit/config entities, measurement DTOs/service boundary | done |
| 04 | two supported profiles, transactions/revisions/locks, audit split, identity deletion policy, #53 flow, payload state machine | done |
| 05 | typed owners, document/config-asset paths, state/recovery, completed-origin validation, operational-workbook separation, profile portability | done |
| 06 | facts vs recorded decisions vs recommendations vs open questions; F01 repaired; #53/profile decisions included | done |
| 07 | reordered #53 after core cutover; profile, audit, payload, branding, issue and evidence tests | done |
| 08 | re-review date, measurement call sites, origin calculation, issue consistency, verification artifact and retained runs | complete, including verifier 1.0.1 clean baseline |
| 10 | normative concrete path annex and all detected write-callable mappings | complete |

## Residual evidence limitations

The expanded `rev/09` required a path row for every concrete path and an action
row for every callable. Documents 01/02 provide the pattern and action-family
contracts. Document 10 plus the clean verifier result provide the exhaustive
machine-readable path annex and exact mapping of all 73 detected write
callables/154 write primitives.

Before final Phase 2B approval:

- [x] run a read-only verifier against the approved cleanup worktree;
- [x] retain structured path/call-site inventories and their checksums;
- [x] record tool version, command, exit code, and unchanged-tree proof in
  document 08;
- [x] rerun the same verifier after cleanup commit against a clean frozen
  baseline;
- [x] reconcile the clean-baseline result with document 01/02;
- [x] promote omissions and reconciliation rules back into the annexes before
  freezing architecture.

Verifier `phase2a_readonly_audit.ps1` version `1.0.0` passed its first retained
run. The result is intentionally not the final gate because the approved
origin/orphan cleanups have not yet been committed.

Subsequent reviewer-approved cleanups on 2026-07-28 removed the deterministic
example-origin decision and unresolved Sample/Project `A` test-data decision
from the approval queue. Their execution evidence is recorded in documents 01,
06, and 08.

## Published issue synchronization completed

Local and GitHub records were synchronized on 2026-07-28:

- [x] #48 body/latest correction states it blocks #49–#53 and supersedes stale
      UUID/legacy-audit comment wording without rewriting history;
- [x] #50 uses four-block IPID, typed non-animal IDs, both supported profiles,
      immutable DB constraints, deterministic origins, and #53 service
      separation;
- [x] #49 owns Standalone/Shared runtime paths and managed configuration assets;
- [x] #51 packaged icons remain outside managed/interchange payloads;
- [x] #52 uses #49/#50 configuration/asset/permission contracts;
- [x] #53 retains full-file plan, no creation, partial success, safe retry, and
      direct-service enforcement;
- [x] closed #15 receives a historical superseding note for immutable identity
      and removed measurement-import creation;
- [x] closed #34 states its workbooks are existing-animal measurement templates
      and mixed fixtures belong to #53 validation.

## README and manual follow-up

Do not edit README/manuals before the architecture decision and implementation
are finalized. The Phase 2 release documentation must eventually explain:

- who should choose Standalone SQLite versus Shared PostgreSQL;
- the strict single-instance/local-storage SQLite restriction;
- PostgreSQL server/client prerequisites and central backup responsibility;
- identical domain features but different concurrency/operations;
- canonical export/import for moving a facility between profiles;
- managed document/configuration asset placement and backup;
- no silent backend fallback.

## Approval gate

### Architecture

- [x] reviewer approves permanent deployment profiles;
- [x] deterministic example-origin correction executed and validated;
- [x] typed ID taxonomy approved;
- [x] no animal UUID, identity edit, or automatic supersede remains;
- [x] measurement import cannot create animals at any boundary;
- [x] branding/audit/payload contracts approved.

### Evidence

- [x] path-pattern annex exists;
- [x] action-family permission annex exists;
- [x] executable evidence contract is specified;
- [x] first structured verifier output is retained and reconciled;
- [x] ProgTrack worktree status remained exactly unchanged during verification;
- [x] clean frozen-commit verifier output is retained as the Phase 2B gate.

### External consistency

- [x] Issues #15, #34, and #48–#53 synchronized after reviewer authorization;
- [x] Issue #48 explicitly approved;
- [x] #49–#53 may begin after #48 is closed and the approved package is
      published under the versioned technical-manual path.

## Approval record

- Reviewer:
- Review date:
- Approved without further amendment: [ ]
- Approved with residual amendments: [ ]
- Returned for revision: [ ]
- Notes:
