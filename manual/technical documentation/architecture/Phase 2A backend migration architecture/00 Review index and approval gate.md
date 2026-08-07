# Issue #48 — Phase 2A review package

Status: **approved and archived on 2026-07-28**

GitHub issue: https://github.com/Lindenfrost/ProgTrack-Release/issues/48  
Frozen source audit: 2026-07-23  
Document revision: 2026-07-28  
Inspected repository: `Q:\GitHub\ProgTrack-Release`  
Inspected branch/commit: `Phase-0.1.2` /
`793aac8bb14762e88efebc31cebb2fcf922c18ba`

## Scope guard

This package records discovery and architecture work performed before Phase 2B.
At the time of this frozen audit, no ProgTrack source, plugin, runtime data,
example data, schema, language, manual, README, managed payload, test, or
package was changed and no backend implementation had begun. For current
behavior, use the Phase 2B runtime contract and localized workflow guides.

After approval, the frozen target architecture is copied into versioned
technical documentation under
`Q:\GitHub\ProgTrack-Release\manual\technical documentation\architecture`.
README and user manuals must then describe the two supported deployment
profiles before the resulting release is published.

## Documents

1. [Current-state findings](01%20Current-state%20findings.md)
2. [Plugin capability and storage matrix](02%20Plugin%20capability%20and%20storage%20matrix.md)
3. [Proposed canonical data dictionary and entity map](03%20Proposed%20canonical%20data%20dictionary%20and%20entity%20map.md)
4. [PostgreSQL/SQLite transaction and lock contract](04%20Proposed%20PostgreSQL%20SQLite%20transaction%20and%20entity-lock%20contract.md)
5. [Managed-document, interchange, and external source adapter contract](05%20Managed-document%20interchange%20and%20external%20source%20adapter%20contract.md)
6. [Findings, decisions, and open questions](06%20Findings%20proposed%20decisions%20and%20open%20questions.md)
7. [Phase 2B implementation and validation plan](07%20Phase%202B%20implementation%20and%20validation%20plan.md)
8. [Evidence register](08%20Evidence%20register.md)
9. [Critical re-review resolution register](09%20Critical%20re-review%20findings%20and%20required%20amendments.md)
10. [Concrete path and callable reconciliation](10%20Concrete%20path%20and%20callable%20reconciliation.md)

## Executive target

ProgTrack remains one portable open-source application with one service and
domain model, but supports two durable deployment profiles:

- **Standalone SQLite:** supported for a small facility on one local
  workstation with one active ProgTrack process. It requires no database
  server. The database, configuration, and managed files remain on local
  storage. SQLite on a network share, synchronized/cloud folder, or from
  concurrent clients is rejected. OS access to the local files remains outside
  ProgTrack's application permission boundary and must be restricted.
- **Shared PostgreSQL:** supported and recommended for multiple workstations,
  simultaneous users, centralized administration, server-side backup, and
  visible cross-client entity locks.

Both profiles expose the same scientific and animal-management features,
validation, permissions, audit contract, canonical interchange format, and
managed-document semantics. They are not separate editions. Deployment
capabilities differ only where concurrency and server operation inherently
differ. A canonical package moves a standalone installation to PostgreSQL
without a direct database conversion.

The target also requires:

- one shared service/repository boundary; plugins never execute SQL;
- immutable four-block animal IPID
  `name | species | DD.MM.YYYY | origin` as animal primary/foreign key;
- facility-owned record IDs `<facility_tag>:<uuid>` for durable records created
  by an installation;
- untagged global semantic IDs for permissions, built-in roles, plugin keys,
  schemas, statuses, units, analytes, specimens, and other frozen protocol
  vocabulary;
- managed documents outside the database, with typed ownership, state,
  relative path, size, MIME type, and SHA-256 metadata;
- a versioned backend-neutral full-dataset package for example-data cutover,
  standalone-to-server transfer, backup/interchange, and future external-source
  conversion;
- caches, generated projections, live locks/sessions, per-user UI state, and
  ordinary exports excluded from canonical domain data.

## Highest-priority blockers

1. Current IPID is a mutable three-block dictionary key. The target adds the
   existing `origin` field (Herkunftseinrichtung), freezes IPID and all four
   source fields for every role including Lord, and introduces no animal UUID.
2. Current JSON/plugin stores cannot provide cross-domain transactions,
   revisions, shared authorization, or reliable canonical export.
3. Measurement import preview can mark unknown animals and the accepted path
   can subsequently create them through an identity-completion dialog. Issue
   #53 requires all blood, urine, weight, and sperm imports to be
   existing-animal-only at UI, service, repository, and database boundaries.
4. Current store/path coverage, action-level permission mapping, and static
   audit reproduction are not yet complete enough to claim full coverage.
5. Managed-document ownership is incomplete and filesystem/database activation
   needs an explicit crash-recovery state machine.
6. Institution branding from Issue #52 needs installation configuration,
   managed configuration-asset ownership, permission, backup/interchange, and
   shared PDF rendering rules.
7. Project, report, Heritage, Sample, Flow, Surgery, user/session, and cache
   stores contain duplicated, mixed-purpose, name-keyed, or unresolved data
   that must be normalized or quarantined.
8. Published Issues #15, #34, and #48–#53 were synchronized on 2026-07-28
   with the recorded IPID, deployment-profile, storage, branding, and
   measurement-import contracts. Historical comments were retained.

Issue #48 formally blocked Issues #49, #50, #51, #52, and #53 until the
approved package was published. It was closed as completed on 2026-07-28;
Issues #49–#53 are now unblocked.

Completed before approval:

- [x] On 2026-07-28, the disposable example origins were cleaned
      deterministically for all 227 active/archived animals and `DPZ` was added
      to the controlled catalogue. This is no longer a Phase 2A decision or a
      Phase 2B data-correction task.

## Review gate

Phase 2A may be approved only when the reviewer accepts or amends:

- [x] permanent Standalone SQLite and Shared PostgreSQL profile contract;
- [x] typed animal, facility-owned, and global semantic identifier policy;
- [x] delete-and-recreate policy for an erroneous finalized animal identity,
      with no automatic dependent-data transfer;
- [x] canonical entity and ownership model;
- [x] path/store classification and runtime-path destinations, including the
      normative concrete inventory in document 10;
- [x] action/permission/service enforcement map, including all 73 detected
      write callables in document 10;
- [x] transaction, revision, audit, and deployment-specific lock rules;
- [x] existing-animal-only measurement-import plan and partial-success rules;
- [x] managed-document state machine and reconciliation;
- [x] institution-branding configuration/asset placement and bounded
      aspect-ratio-preserving PDF rendering;
- [x] canonical package, standalone-to-server transfer, and external-source boundary;
- [x] repeatable read-only verification contract, first retained evidence run,
      and final clean-baseline run;
- [x] ordered Phase 2B implementation and validation plan;
- [x] published issue synchronization completed locally and on GitHub.

Reviewer decisions and residual questions are in document 06. Document 09
records which critical re-review requirements have been integrated and which
external/approval actions remain.

Final reviewer approval was granted on 2026-07-28 after the concrete
path/callable reconciliation and clean-baseline verifier passed. The approved
package was published in repository commit `bb227f5` and Issue #48 was closed
as completed. Closure evidence:
https://github.com/Lindenfrost/ProgTrack-Release/issues/48#issuecomment-5105002497
