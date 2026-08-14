# Phase 3 PostgreSQL administration and IT test

ProgTrack supports two backend profiles:

- **Tiny/Standalone SQLite** for one local workstation, tests, demos, and
  disposable example data;
- **Shared PostgreSQL** for concurrent networked use inside the institution's
  network or through the approved VPN.

The Shared PostgreSQL profile is configured only by a Lord. The backend dialog
supports password authentication, server TLS, and optional mutual TLS. `verify-
ca` and `verify-full` require a readable CA bundle; a client certificate and
matching private key are required together. Private-key passphrases are stored
in the operating-system credential store and never in profile JSON, logs, seed
data, or exports.

## Server database administration

After a successful secure connection, the Lord can enumerate authorized
databases, see their availability and ProgTrack schema compatibility, create a
new database, select the active database for the next clean restart, archive or
unarchive a database, create a complete backup, restore a verified backup, and
delete a database only after a verified backup and typed-name confirmation.

All actions are audited. A failed operation leaves the previously active target
unchanged. Other roles cannot use these controls.

## Canonical SQLite transfer

The dialog can select a local SQLite source and a PostgreSQL target. ProgTrack
exports the source through the versioned canonical interchange package, shows a
preflight/validation result, and imports only into an empty target. Records,
users, projects, housing, plugin data, managed documents, and checksums are
transferred through the shared service. Unexpected package members, checksum
errors, or partial imports are rejected and rolled back.

## Managed documents

In Shared PostgreSQL mode, the effective managed-document root is stored in the
PostgreSQL installation record and resolved identically by every client. It is
deployment-owned storage on the PostgreSQL server or its explicitly managed
server volume, exposed through one canonical client path. There is no local
fallback. Ordinary documents remain external files; PostgreSQL stores stable
IDs, ownership links, relative paths, MIME type, size, and checksums.

The complete PostgreSQL backup envelope contains a `pg_dump` archive and the
managed-document payloads with checksums. Restore verifies all payloads before
publishing them.

## IT validation gate

The development workstation cannot prove a real PostgreSQL deployment. Before
Issue #50 can close, IT must run the accompanying request in:

`Q:\GitHub\Issue Tracker\Documentation\Phase 3 PostgreSQL IT test request.md`

The gate must cover internal-network and active-VPN access, TLS and mutual TLS,
two-client locks, database lifecycle operations, SQLite transfer, complete
backup/restore, managed-document availability, and the no-local-fallback rule.
