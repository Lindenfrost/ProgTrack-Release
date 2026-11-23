"""Phase 3 Core schema bootstrap and startup boundaries."""

from contextlib import closing, contextmanager
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Plugins.core.backend import BackendSchemaError, ProgTrackBackend
from Plugins.core.backend.adapters import SQLiteAdapter
from Plugins.core.backend.migration import migrate_core_schema, require_shared_schema
from Plugins.core.backend.schema import SCHEMA_VERSION, SQLITE_MIGRATIONS
from Plugins.core.runtime_paths import BackendProfile


@pytest.fixture
def standalone(tmp_path):
    paths = SimpleNamespace(
        profile=BackendProfile.STANDALONE_SQLITE,
        database_path=tmp_path / "standalone.sqlite3",
        runtime=tmp_path,
    )
    with closing(SQLiteAdapter(paths, acquire_process_lock=False)) as adapter:
        yield adapter


def test_fresh_schema_keeps_domain_empty_and_preserves_bootstrap_marker(standalone):
    standalone.migrate()
    initial = standalone.get_installation_value("backend-contract")
    assert initial["core_revision"] == SCHEMA_VERSION
    assert initial["state"] == "ready"
    assert initial["last_migration"]["previous_revision"] == 0
    with standalone.transaction() as connection:
        before = tuple(connection.execute("SELECT * FROM schema_revisions").fetchone())
        for table in ("animals", "domain_records", "managed_files", "entity_leases", "active_sessions"):
            assert connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    standalone.migrate()
    assert standalone.get_installation_value("backend-contract") == initial
    with standalone.transaction() as connection:
        assert tuple(connection.execute("SELECT * FROM schema_revisions").fetchone()) == before


def test_existing_sqlite_migration_preserves_domain_and_old_lease(standalone):
    with closing(sqlite3.connect(standalone.path)) as connection, connection:
        connection.executescript(SQLITE_MIGRATIONS[1])
        connection.executescript(SQLITE_MIGRATIONS[2])
        connection.execute("INSERT INTO schema_revisions VALUES('core', 2, 'previous')")
        connection.execute("INSERT INTO domain_records VALUES('example','existing','{}',7,'created','updated')")
        connection.execute("INSERT INTO entity_leases VALUES('animal','A','user','User','token','a','h','e')")
    standalone.migrate()
    assert standalone.get_installation_value("backend-contract")["last_migration"]["previous_revision"] == 2
    with standalone.transaction() as connection:
        assert tuple(connection.execute("SELECT * FROM domain_records").fetchone()) == (
            "example", "existing", "{}", 7, "created", "updated"
        )
        assert tuple(connection.execute("SELECT token, owner_session_id FROM entity_leases").fetchone()) == (
            "token", None
        )


def test_exact_precreated_lease_table_is_accepted(standalone):
    with standalone.transaction(write=True) as connection:
        connection.execute("""
            CREATE TABLE entity_leases (
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                owner_login TEXT NOT NULL,
                owner_display TEXT NOT NULL,
                token TEXT NOT NULL UNIQUE,
                acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                owner_session_id TEXT,
                PRIMARY KEY(entity_type, entity_id)
            )
        """)
    standalone.migrate()
    standalone.migrate()
    with standalone.transaction() as connection:
        assert connection.execute("SELECT count(*) FROM entity_leases").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM active_sessions").fetchone()[0] == 0


def test_schema_postcheck_failure_rolls_back_bootstrap(standalone):
    with standalone.transaction(write=True) as connection:
        # Complete columns and primary key without the token uniqueness guard.
        connection.execute("""
            CREATE TABLE entity_leases (
                entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
                owner_login TEXT NOT NULL, owner_display TEXT NOT NULL,
                token TEXT NOT NULL, acquired_at TEXT NOT NULL,
                heartbeat_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                PRIMARY KEY(entity_type, entity_id)
            )
        """)
    with pytest.raises(BackendSchemaError, match="entity_leases lacks its required primary/unique keys"):
        standalone.migrate()
    with standalone.transaction() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"entity_leases"}
        assert "owner_session_id" not in {row[1] for row in connection.execute("PRAGMA table_info(entity_leases)")}


def test_newer_revision_is_never_rewritten(standalone):
    standalone.migrate()
    with standalone.transaction(write=True) as connection:
        connection.execute("UPDATE schema_revisions SET revision=99 WHERE component='core'")
    with pytest.raises(BackendSchemaError, match="revision 99"):
        standalone.migrate()
    with standalone.transaction() as connection:
        assert connection.execute("SELECT revision FROM schema_revisions").fetchone()[0] == 99


def test_current_schema_distinguishes_compatible_range_and_malformed_bounds(standalone):
    standalone.migrate()
    metadata = standalone.get_installation_value("backend-contract")
    metadata["max_contract"] += 1
    standalone.set_installation_value("backend-contract", metadata)
    standalone.migrate()
    metadata["min_contract"] = True
    standalone.set_installation_value("backend-contract", metadata)
    with pytest.raises(BackendSchemaError, match="backend-contract metadata"):
        standalone.migrate()


def test_shared_facade_probes_before_repositories_and_releases_failed_pool(monkeypatch):
    adapter = Mock()
    adapter.require_current_schema.side_effect = BackendSchemaError("requires administration")
    create = Mock(return_value=adapter)
    animals = Mock()
    monkeypatch.setattr("Plugins.core.backend.facade.create_adapter", create)
    monkeypatch.setattr("Plugins.core.backend.facade.AnimalService", animals)
    paths = SimpleNamespace(profile=BackendProfile.SHARED_POSTGRESQL)
    with pytest.raises(BackendSchemaError, match="requires administration"):
        ProgTrackBackend(paths)
    adapter.require_current_schema.assert_called_once_with()
    adapter.migrate.assert_not_called()
    animals.assert_not_called()
    adapter.close.assert_called_once_with()


@pytest.mark.parametrize("operation", [migrate_core_schema, require_shared_schema])
def test_old_shared_schema_issues_no_mutating_sql(operation):
    connection = Mock()
    statements = []

    def execute(sql, params=()):
        statements.append(sql.strip())
        cursor = Mock()
        if "pg_try_advisory_xact_lock" in sql:
            cursor.fetchone.return_value = {"acquired": True}
        elif "pg_attribute" in sql:
            cursor.fetchall.return_value = [
                {"table_name": "schema_revisions", "column_name": "revision"}
            ]
        elif "SELECT revision FROM schema_revisions" in sql:
            cursor.fetchone.return_value = {"revision": 2}
        return cursor

    @contextmanager
    def transaction(*, write=False):
        yield connection

    connection.execute.side_effect = execute
    adapter = SimpleNamespace(dialect="postgresql", _schema_transaction=transaction)
    with pytest.raises(BackendSchemaError, match="core revision 2 is incompatible"):
        operation(adapter)
    assert any("schema_revisions" in statement for statement in statements)
    assert all(statement.startswith(("SELECT", "SET TRANSACTION READ ONLY"))
               for statement in statements)
