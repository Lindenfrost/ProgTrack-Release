# Findings, decisions, recommendations, and open questions

## Status vocabulary

- **Fact:** observed in frozen commit/data.
- **Recorded decision:** already supplied by the reviewer in this Phase 2
  planning history.
- **Recommendation:** architecture conclusion in this revision; requires
  explicit approval or amendment.
- **Open question:** reviewer choice remains necessary.

## Consolidated findings

| ID | Fact | Architectural consequence |
| --- | --- | --- |
| F01 | Composite three-block IPID is dictionary key/cross-plugin reference and current edits rewrite files/folders; `origin` already exists. | Four-block IPID can be the animal key only when IPID and all source fields are immutable. |
| F02 | Core and plugins save independent files. | Current multi-module edits cannot be atomic. |
| F03 | Global lock protects only core JSON. | It cannot provide shared entity editing; Standalone and Shared need distinct coordination under one LockService. |
| F04 | Heritage stores core copies plus heritage-only records. | Export only heritage-only animals/relationships; regenerate projection/cache. |
| F05 | Animal Reports mixes generated aggregate and manual edits/locks. | Split durable override from generated projection. |
| F06 | Project truth is split and `Zeta-1` lacks project metadata. | Normalize project entity and assignment history; derive current membership. |
| F07 | Sample rows mix valid IPID, stale public IDs, Sample IDs, and unresolved animals. | Use typed identifiers; quarantine unresolved source rows. |
| F08 | Managed-file metadata/ownership is incomplete and includes orphan/zero-byte examples. | Reconcile, type ownership, checksum, and manage state before export. |
| F09 | Flow identifiers embed animal IPID/date. | Donations, embryos, transfers, and events need independent facility-owned record IDs. |
| F10 | Surgery uses competing case-sensitive paths/formats. | Resolve staging/published canonical models before cutover. |
| F11 | Manifests omit stores and cannot classify them. | Use explicit path/storage registry; manifests remain capability/resource metadata. |
| F12 | Some permissions are enforced only at UI/launch locations. | Every service command needs actor, semantic permission, and scope. |
| F13 | `users.enc` wrapper is obfuscation, not authenticated encryption. | Profile database stores versioned password hashes; wrapper is not security. |
| F14 | Domain/config/preferences/cache/session data are mixed. | Separate service and runtime-path ownership before cutover. |
| F15 | Network actor is display text. | Retained messages need facility-owned user link plus actor snapshot. |
| F16 | Cranimetry reference import writes under view-only permission. | Add separately grantable/versioned configuration permission. |
| F17 | Sample has one use permission and Embryo only view. | Add semantic read/write/config permissions where mutations exist. |
| F18 | Backup discovery misses managed payload types. | Canonical export comes from services/DocumentService, not extensions. |
| F19 | Frozen snapshot: 11 animals lacked origin and stored `DPZ`/`Iluvatara` differed from the catalogue. | **Resolved 2026-07-28:** deterministic cleaning applied to all 227 animals; catalogue completed; zero violations. |
| F20 | Accepted blood, urine, weight, and sperm import can resolve with `create_missing=True` and create an animal after preview. | Issue #53 must separate measurement import from animal creation at every boundary. |
| F21 | No shared institution-branding config, logo asset, permission, or PDF header service exists. | Issue #52 needs configuration/asset/model/interchange integration, not only UI. |
| F22 | ProgTrack describes itself as portable and serves facilities of very different sizes. | Permanent Standalone SQLite plus Shared PostgreSQL is more useful than server-only production if topology is strictly constrained. |
| F23 | Path inventory, action map, and static results were initially incomplete. | Documents 01/02/08 now carry structured annexes/contracts; a frozen executable verification run remains an approval gate. |

The canonical IPID format is
`name | species | DD.MM.YYYY | origin`; literal separators are stated outside
the table to avoid malformed Markdown cells.

## Recorded reviewer decisions

1. Animal primary/foreign key is immutable four-block IPID. There is no animal
   UUID.
2. `origin` is the existing Herkunftseinrichtung field. No second origin field
   is introduced.
3. IPID, name, species, full birth date, and stored `origin` are immutable
   after creation for every role, including Lord.
4. Catalogue maintenance affects future choices only and never rewrites stored
   origins.
5. Equal full IPID means an idempotent same-animal reference or a quarantined
   conflict; import never renames the animal.
6. A bad finalized identity is deleted and recreated manually. Phase 2B has no
   alias, merge, supersede, identity rewrite, or automatic dependent-data
   transfer.
7. Facility-owned records use `<facility_tag>:<uuid>`; the Manager defines the
   tag and it becomes immutable after first durable use. Global semantic IDs
   are not facility-tagged.
8. Heritage-only animals are normal animal entities with
   `record_kind=heritage_only`.
9. Managed payload bytes stay outside the database with authoritative metadata
   and SHA-256 in the selected profile database.
10. Canonical full-dataset interchange is distinct from Phase 5 `.pta` and is
    the boundary for future external-source conversion.
11. The Phase 2 cutover edits disposable example data; it is not an automatic
    production-data migration.
12. Measurement imports are existing-animal-only. Unknown animals stay visible
    and are skipped; Managers must create them through New Animal.
13. Issue #48 blocks Phase 2B Issues #49–#53.
14. The deterministic disposable example-origin cleaning was executed and
    validated on 2026-07-28. It is no longer an approval question or Phase 2B
    task.

## Approved target decisions

### R1 — Permanent deployment profiles

**Approved by reviewer on 2026-07-28.**

Keep both adapters permanently with a strict topology boundary:

- **Tiny/Standalone SQLite** is the installation-free database option for
  facilities operating ProgTrack on exactly one workstation. It is also the
  local test/demo backend.
- **Shared PostgreSQL** is the only network solution and is used whenever
  multiple workstations, concurrent users, centralized administration, server
  backup, or cross-client locks are required.
- SQLite on network, NAS, synchronized/cloud, or multi-client paths is rejected.
- Scientific/domain features and file/document semantics remain identical.
- PostgreSQL outage never triggers SQLite fallback.
- Canonical export/import is the backend-neutral transition path.
- Standalone application permissions do not protect against a person who can
  directly modify its local database/files; the facility must restrict OS
  access and backups. Shared PostgreSQL is required when centralized database
  access control is part of the threat model.

Reasoning: this preserves ProgTrack's portable small-facility value without
pretending SQLite is a safe shared server. One codebase and adapter parity avoid
the maintenance cost and drift of separate “lite” and “server” editions.

### R2 — Complete installation transfer includes security identities

**Approved by reviewer on 2026-07-28.**

“Complete” means the complete facility installation context. The canonical
transfer from Tiny/Standalone SQLite to Shared PostgreSQL includes:

- all animal, measurement, medical, housing, sample, report, and related
  domain data;
- all projects and their complete assignment/history records;
- all user accounts required by project ownership and responsibility links;
- access roles, job bundles, direct permission assignments, and user-role/job
  assignments;
- password hashes, salts, algorithms, and parameter metadata needed to retain
  valid logins.

The protected security section is therefore mandatory for a complete
installation transfer, not an optional add-on. The package must be encrypted
and access-controlled because it contains authentication material. Import
preserves stable user references so project contacts, PI/DI/AWO assignments,
history actors, and other responsibility links do not become orphaned.

Live sessions, edit locks, caches, and transient client state are separate
operational state and are decided independently; “all facility data” does not
silently turn stale sessions or locks into valid state on the new backend.

### R2.1 — Legacy audit is discarded at cutover

**Approved by reviewer on 2026-07-28.**

The old text-based audit logs are considered valueless legacy material. They
are not exported, transferred, checksummed as historical artifacts, parsed, or
shown in the future audit interface.

The new structured audit starts fresh on the target backend. The cutover/import
operation itself is the beginning of the new audit history and records its own
new-format operation/domain events. No pre-cutover text line is represented as
a trusted normalized audit event.

### R2.2 — Network chat starts empty after cutover

**Approved by reviewer on 2026-07-28.**

Existing Network Track chat files and messages are not included in the
canonical installation transfer. The target backend starts with an empty chat.
No legacy chat actor text is matched to imported users and no old message is
converted into a canonical `network_message`.

The canonical message entity remains part of the new backend for messages
created after cutover. Those new messages use stable user references and actor
snapshots according to the approved target model.

### R2.3 — Transfer shared facility and scientific configuration

**Approved by reviewer on 2026-07-28.**

The complete installation transfer includes all shared configuration that
defines the facility's scientific and operational workflow:

- controlled catalogues, including animal origins;
- custom animal roles, role blocks, and related presets;
- jobs, permission configuration, and assignments;
- approved scientific/reference datasets such as cranimetry references;
- shared limits, conventions, and domain configuration;
- institution name, branding settings, and managed logo asset;
- plugin configuration where it is scientific, operational, or shared.

Personal presentation state is not part of this shared configuration. Window
geometry, last tab, temporary filters, table widths, selections, and similar
per-user UI preferences are handled separately under Q7.

### R2.4 — Remove unresolved disposable Sample/Project test data

**Approved and executed on 2026-07-28.**

Because these are disposable development/example data, unresolved records were
removed rather than guessed or quarantined:

- four Sample Track rows: Donor, Doner, unmatched 2024 Lindir, and Petrulla;
- three Project Track history entries: Papio Lindir and two
  `Andy | Unknown species` entries;
- orphan files/directories `Projects_Track/documents/A` and
  `Projects_Track/sop/A`.

`A` was not interpreted as `Anode`. Remaining Sample Track and explicit
Project Track history animal references all resolve. Separate unresolved Flow,
Cage, or Medi findings remain separate implementation work and were not
silently broadened into this cleaning pass.

### R2.5 — Transfer durable user preferences, not transient UI state

**Approved by reviewer on 2026-07-28.**

The complete installation transfer includes durable personal preferences:

- preferred language;
- individually enabled/disabled plugins;
- persistently selected display or working modes;
- other values explicitly defined as durable user preferences.

It excludes transient presentation/session state:

- window position and size;
- last open tab;
- temporary filters and search text;
- current selections and scroll positions;
- open dialogs;
- live authenticated sessions and their runtime state.

The preference registry must classify each key explicitly. Unknown UI keys are
not transferred by default.

### R2.6 — Edit-lock coverage and Lord-only forced release

**Approved by reviewer on 2026-07-28.**

Only Lord may forcibly release another session's edit lock. This authority is
not granted to Master or Manager and is not available as a grantable permission
for other roles.

Forced release:

- displays the owner, lock age, context, and target before confirmation;
- requires an explicit Lord action and reason;
- is always written as a security/operation audit event;
- never bypasses optimistic revision checks or overwrites newer data.

Initial long-lived edit-lock coverage includes:

- animals;
- projects;
- buildings, units, rooms, cages, and inspections;
- medical records/issues;
- samples;
- surgery plans;
- shared reference and institution configuration;
- user accounts, jobs, and custom roles.

Short append-only measurement/event commands normally use transaction and
revision protection without retaining an editor lease.

### R2.7 — One atomic transaction per measurement file

**Approved by reviewer on 2026-07-28.**

After non-mutating full-file classification and user confirmation, every row
planned as valid is written in one database transaction:

- unknown, archived, ambiguous, invalid, duplicate, or permission-rejected rows
  are excluded by the confirmed plan and reported as skipped;
- transactional revalidation may move a row to `newly_invalid` before writing;
- an unexpected failure while writing any planned valid row rolls back every
  measurement write from that file;
- no permanently committed batches or partial valid-file commit are allowed;
- cancellation is available before commit starts, not during the short atomic
  commit;
- the final result distinguishes imported, pre-classified skipped, duplicate,
  invalid, and newly invalid rows.

Both Tiny/Standalone SQLite and Shared PostgreSQL implement the same atomic
file-import outcome.

### R2.8 — Mandatory repeatable read-only baseline audit

**Approved by reviewer on 2026-07-28.**

A versioned read-only audit program is created and executed before Phase 2B.
Its structured JSON result and SHA-256 are retained under the Phase 2A audit
directory. Phase 2B may not start unless the verifier:

- inventories stores/write paths, manifests, managed roots, references,
  permissions, disabled-plugin mapping, and relevant import/database call
  sites;
- reports expected frozen findings separately from unexpected failures;
- records repository branch/commit, tool versions, invocation, and exit rules;
- proves that ProgTrack's worktree status and tracked/untracked file set are
  unchanged by the audit;
- exits successfully under the documented result schema.

Because the reviewer-approved origin/orphan cleanups are currently uncommitted,
the first run compares the complete dirty status before and after and requires
exact equality. After those cleanups are committed, the same audit is rerun
against the clean baseline commit; that clean run is the final Phase 2B gate.

The first retained run completed successfully on 2026-07-28 with verifier
version `1.0.0`, schema `phase2a-audit-evidence/1`, exit code `0`, and an
unchanged seven-entry cleanup status before/after. Its result and checksum are
stored beside the verifier as `phase2a_audit_result.json` and
`phase2a_audit_result.json.sha256`. `_internal` is excluded from content
analysis as bundled runtime, while the full Git-status equality check remains
in force. The clean-baseline rerun remains mandatory after commit.

The final clean-baseline rerun completed successfully after commit
`3fc22583799b6ed394544035f1387e1c759c3aea`. It recorded zero Git-status
entries before and after, exit code `0`, and `passed = true`. The retained
result is `phase2a_audit_result_clean.json` with SHA-256
`193ac3c6b09b55350daeba07ffc3c6015c04880d98151dda7911d71dcb34ba2e`.
The executable evidence gate is therefore complete.

### R3 — Typed identifier taxonomy

**Approved by reviewer on 2026-07-28.**

- animal: immutable IPID;
- installation-created durable record: facility-owned record ID;
- application protocol/reference constant: global semantic ID;
- custom role/job/configuration: facility-owned configuration record, even
  where built-in equivalents use global IDs.

### R4 — Audit outcomes

**Approved by reviewer on 2026-07-28.**

Successful mutations have atomic domain events. Sensitive denials have
independent security events. Import/export lifecycle uses correlated operation
events. “Permission denied” means no domain write, not necessarily no audit
write.

### R5 — Document recovery

**Approved by reviewer on 2026-07-28.**

Approve `staged`, `pending`, `active`, `quarantined`, and `deleted`, the
two-transaction activation protocol, startup/scheduled reconciliation, and
crash tests at every transition.

### R6 — Institution branding ownership

**Approved by reviewer on 2026-07-28.**

Installation branding is versioned configuration plus a managed configuration
asset. It is separate from animal origin, facility tag, domain documents, and
packaged icons. Use a grantable branding permission and optional protected
configuration-package inclusion.

Every PDF renderer reserves a compact bounded header area. A configured logo
is proportionally scaled down to fit both the maximum header width and height;
it is never stretched, cropped, or allowed to expand over the page/content
area. Large source-image pixel dimensions alone do not make the logo fill the
page. The Settings preview uses the same layout calculation as PDF output.

### R2.9 — Published issue synchronization

**Approved by reviewer on 2026-07-28.**

Synchronize local and GitHub Issues #15, #34, and #48–#53 with the recorded
Phase 2A contracts. Historical comments remain unchanged. Closed Issues #15
and #34 receive new superseding notes; Issue #48 receives a current correction
comment and continues to block #49–#53 until the Phase 2A approval gate is
complete.

## Open questions

No numbered Q1–Q11 questions remain open.

## Approval record

- Reviewer: Dimitri L. Lindenwald
- Review date: 2026-07-28
- Approved recorded decisions: [x]
- Approved recommendation R1: [x] 2026-07-28
- Approved recommendation R2: [x] 2026-07-28
- Approved decision R2.1: [x] 2026-07-28
- Approved decision R2.2: [x] 2026-07-28
- Approved decision R2.3: [x] 2026-07-28
- Approved decision R2.4: [x] 2026-07-28
- Approved decision R2.5: [x] 2026-07-28
- Approved decision R2.6: [x] 2026-07-28
- Approved decision R2.7: [x] 2026-07-28
- Approved decision R2.8: [x] 2026-07-28
- Approved decision R2.9 / Q11 issue synchronization: [x] 2026-07-28
- Approved recommendation R3: [x] 2026-07-28
- Approved recommendation R4: [x] 2026-07-28
- Approved recommendation R5: [x] 2026-07-28
- Approved recommendation R6: [x] 2026-07-28
- Approved recommendations R3–R6: [x] 2026-07-28
- Questions Q1–Q11 answered: [x] 2026-07-28
- Approved without further amendment: [ ]
- Approved with listed and integrated amendments: [x] 2026-07-28
- Returned for revision: [ ]
- Notes: Final approval includes documents 00–10, verifier version 1.0.1, the
  clean-baseline result, and local/GitHub Issue synchronization.
