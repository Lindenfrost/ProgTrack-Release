-- Part of: ProgTrack 0.3.0
-- Required ProgTrack version: 0.3.0 or newer.
-- Explicit installation by IT before using ProgTrack-managed catalog writes.
-- Run once in this cluster's postgres maintenance database. This script grants
-- no roles or permissions. Do not load it in ordinary application databases.
-- The IT administrator grants schema USAGE and table SELECT for discovery and
-- schema USAGE plus SELECT and UPDATE(revision, dirty) for PostgreSQL admin roles
-- which perform catalog mutations. These roles also need CONNECT on postgres.
BEGIN;
DO $$ BEGIN
  IF current_database() <> 'postgres' THEN
    RAISE EXCEPTION 'Install the ProgTrack catalog clock in the postgres maintenance database';
  END IF;
END $$;
CREATE SCHEMA IF NOT EXISTS progtrack_catalog;
CREATE TABLE IF NOT EXISTS progtrack_catalog.database_revision (
    singleton boolean PRIMARY KEY CHECK (singleton),
    epoch uuid NOT NULL,
    revision bigint NOT NULL CHECK (revision >= 0),
    dirty boolean NOT NULL
);
INSERT INTO progtrack_catalog.database_revision(singleton, epoch, revision, dirty)
VALUES (TRUE, gen_random_uuid(), 0, FALSE) ON CONFLICT (singleton) DO NOTHING;
COMMIT;

-- Dirty means a catalog operation began and may have partially committed.
-- After checking its database/schema/storage outcome and resolving partial work,
-- the deployment administrator can publish a settled revision under the same
-- lock. Run these statements together in the postgres maintenance database:
-- BEGIN;
-- SELECT pg_advisory_xact_lock(942637, 215);
-- UPDATE progtrack_catalog.database_revision
-- SET revision=revision+1, dirty=FALSE WHERE singleton;
-- COMMIT;
-- Rotate epoch with a new UUID if the maintenance database is restored from
-- an older backup or the cluster is rebuilt. Never reset a revision in place
-- in the same epoch. Out-of-band changes can publish a settled bump after
-- their own coordinated operation, or clients can use explicit Refresh.
