<!-- Part of: ProgTrack 0.3.0. Required ProgTrack version: 0.3.0 or newer. -->
# Phase 3 PostgreSQL administration and IT test

ProgTrack supports two backend profiles:

- **Tiny/Standalone SQLite** for one local workstation, tests, demos, and
  disposable example data;
- **Shared PostgreSQL** for concurrent networked use from clients on the same
  internal LAN as the server or through an explicitly approved, institution-
  managed VPN. Other VPNs and subnets are outside this deployment boundary.

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

## Cluster-wide database catalog clock

The catalog clock used for server-database discovery is installed once per PostgreSQL cluster in the `postgres` maintenance database. It is cluster metadata, not part of `progtrack_test_023` or another application database. The release includes `Resources/ExampleFiles/ProgTrack_catalog_clock.sql`; the script creates `progtrack_catalog.database_revision` and its initial row, but grants no privileges. Run it as the PostgreSQL operating-system administrator from the extracted ProgTrack package root:

```bash
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres \
  -f Resources/ExampleFiles/ProgTrack_catalog_clock.sql
```

For the example deployment, grant only the required maintenance-database access (replace role names if the installation differs):

```bash
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d postgres \
  -c "GRANT CONNECT ON DATABASE postgres TO progtrack_app, progtrack_admin" \
  -c "GRANT USAGE ON SCHEMA progtrack_catalog TO progtrack_app, progtrack_admin" \
  -c "GRANT SELECT ON TABLE progtrack_catalog.database_revision TO progtrack_app, progtrack_admin" \
  -c "GRANT UPDATE (revision, dirty) ON TABLE progtrack_catalog.database_revision TO progtrack_admin"

sudo -u postgres psql -X -P pager=off -d postgres \
  -c "SELECT singleton, epoch, revision, dirty FROM progtrack_catalog.database_revision;"

sudo -u postgres psql -X -P pager=off -d postgres \
  -c "SELECT role_name, has_database_privilege(role_name, 'postgres', 'CONNECT') AS can_connect, has_schema_privilege(role_name, 'progtrack_catalog', 'USAGE') AS schema_usage, has_table_privilege(role_name, 'progtrack_catalog.database_revision', 'SELECT') AS can_select, has_column_privilege(role_name, 'progtrack_catalog.database_revision', 'revision', 'UPDATE') AS can_update_revision, has_column_privilege(role_name, 'progtrack_catalog.database_revision', 'dirty', 'UPDATE') AS can_update_dirty FROM (VALUES ('progtrack_app'), ('progtrack_admin')) AS roles(role_name);"
```

After first installation, the state query should return one row with `revision = 0` and `dirty = f`. In the privilege query, the `progtrack_app` row should show `can_connect`, `schema_usage`, and `can_select` true and both `can_update_*` fields false. The `progtrack_admin` row should show all five permission fields true. Re-running the installation script does not reset an existing revision. Discovery requires the runtime role to read this row; catalog mutations require the authorized backend-administration role to read and update only `revision` and `dirty`. The script contains recovery and epoch-rotation instructions for interrupted operations and database/cluster restoration. Do not install this table in an application database, grant database ownership, or grant broad table privileges for this purpose. If the SQL file is not present on the server, transfer it through the approved IT process and adjust the `-f` path.

## Canonical SQLite transfer

The dialog can select a local SQLite source and a PostgreSQL target. ProgTrack
exports the source through the versioned canonical interchange package, shows a
preflight/validation result, and imports only into an empty target. Records,
users, projects, housing, plugin data, managed documents, and checksums are
transferred through the shared service. Unexpected package members, checksum
errors, or partial imports are rejected and rolled back.

## Managed documents

In Shared PostgreSQL mode, each client stores its absolute managed-document
root and any optional storage credentials in that client's local backend
profile. Client paths may differ, but IT must configure them to reach the same
logical managed share. PostgreSQL stores document IDs, ownership links, safe
relative references, MIME type, size, and checksums; it must not store or
override a client's absolute path or storage credentials. The runtime must not
fall back to a local directory when the configured share is unavailable.
Ordinary documents remain external files. See #226 for the client-local path
and credential contract and #230 for the recoverable file/database protocol.

The complete PostgreSQL backup envelope contains a `pg_dump` archive and the
managed-document payloads with checksums. Restore verifies all payloads before
publishing them.

## IT validation gate

The development workstation cannot prove a real PostgreSQL deployment. Before
Issue #50 can close, IT must run the accompanying request in:

`Q:\GitHub\Issue Tracker\Documentation\Phase 3 PostgreSQL IT test request.md`

The gate must cover same-LAN and approved institutional-VPN access, rejection
from unapproved networks, TLS and mutual TLS,
two-client locks, database lifecycle operations, SQLite transfer, complete
backup/restore, managed-document availability, and the no-local-fallback rule.
