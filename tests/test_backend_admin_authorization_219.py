"""Exercise #219 service and Qt routes without an external server."""

from contextlib import nullcontext
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from psycopg.conninfo import conninfo_to_dict
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog, QFileDialog, QInputDialog, QLineEdit, QMessageBox

from Plugins.core.backend.errors import BackendConfigurationError, ConflictError, PermissionDeniedError
from Plugins.core.backend.interchange import InterchangeService
from Plugins.core.backend.postgresql_admin import BackendAdminError, PostgreSQLAdministrationService
from Plugins.core.backend_configuration import BackendConfigurationService, PostgreSQLSettings
from Plugins.core.backend_configuration_dialog import BackendConfigurationDialog
from test_phase3_postgresql_management import MemoryCredentials, paths
from test_backend_database_catalog_215_218 import refresh_catalog


class PostgresFixture:
    def __init__(self, root):
        self.sessions = []
        self.sql = []
        self.root = root
        self.owner = True
        self.can_create = True
        self.bad_credentials = False
        self.databases = {"active": (9, True), "selected": (10, True)}
        self.revision = 0
        self.dirty = False

    def connect(self, dsn, **kwargs):
        options = conninfo_to_dict(dsn)
        self.sessions.append((options, kwargs))
        if self.bad_credentials:
            raise RuntimeError(f"private connection details: {dsn}")
        fixture = self

        class Cursor:
            def execute(self, sql, args=()):
                sql = sql if isinstance(sql, str) else sql.as_string()
                fixture.sql.append((options["user"], options["dbname"], sql, args))
                self.row = None
                if sql == "SELECT session_user, current_user":
                    self.row = (options["user"], options["user"])
                elif sql == "SELECT session_user, current_user, current_database()":
                    self.row = (options["user"], options["user"], options["dbname"])
                elif sql.startswith("SELECT session_user"):
                    self.row = (options["user"], options["user"], fixture.can_create)
                elif sql.startswith("SELECT pg_try_advisory_lock"):
                    self.row = (True,)
                elif sql.startswith("SELECT pg_try_advisory_xact_lock_shared"):
                    self.row = (True,)
                elif sql.startswith("SELECT current_database(), oid FROM pg_database"):
                    database = options["dbname"]
                    self.row = (database, fixture.databases[database][0])
                elif sql.startswith("SELECT count(*) FROM managed_files"):
                    self.row = (0,)
                elif sql.startswith("SELECT document_id,category"):
                    self.rows = []
                elif sql == "SELECT pg_export_snapshot()":
                    self.row = ("fixture-snapshot",)
                elif sql.startswith("SELECT epoch::text"):
                    self.row = ("fixture-epoch", fixture.revision, fixture.dirty)
                elif sql.startswith("UPDATE progtrack_catalog.database_revision"):
                    fixture.revision += 1
                    fixture.dirty = "dirty=TRUE" in sql
                    self.rowcount = 1
                elif sql.startswith("SELECT oid,") and args[0] in fixture.databases:
                    oid, allows = fixture.databases[args[0]]
                    self.row = (oid, allows, False, fixture.owner)
                elif sql.startswith("SELECT datname"):
                    self.rows = [(name, "fixture_dba", 42, allows, False)
                                 for name, (_, allows) in fixture.databases.items()]
                elif sql.startswith("SELECT EXISTS"):
                    self.row = (True,)
                elif sql.startswith("SELECT value_json"):
                    self.row = (str(fixture.root),)
                elif sql.startswith("SELECT revision"):
                    self.row = (3,)

            def fetchone(self):
                return self.row

            def fetchall(self):
                return self.rows

        connection = SimpleNamespace(
            cursor=lambda: nullcontext(Cursor()),
            autocommit=kwargs.get("autocommit", False),
            user=options["user"],
            database=options["dbname"],
        )
        return nullcontext(connection)


@pytest.fixture
def state(tmp_path, monkeypatch):
    config = BackendConfigurationService(paths(tmp_path), credential_store=MemoryCredentials(),
                                         environ={"LOCALAPPDATA": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path)})
    settings = PostgreSQLSettings(host="fixture-host", database="active", user="runtime", managed_root=str(config.paths.managed_root))
    server = PostgresFixture(config.paths.managed_root)
    monkeypatch.setattr("psycopg.connect", server.connect)
    # This fixture models authorization and target identity. Schema/ACL
    # classification is verified independently against PostgreSQL.
    monkeypatch.setattr("Plugins.core.backend.migration.probe_shared_compatibility", lambda connection: True)
    identity = Mock(return_value="ProgTrackLord")
    audit = Mock()
    admin = PostgreSQLAdministrationService(config, settings, discovery_password="runtime-secret", authorized=True,
                                           validate_initiator=identity, active_database="active", audit_callback=audit)
    return SimpleNamespace(config=config, settings=settings, server=server, admin=admin, identity=identity, audit=audit)


def authorize(admin, action, database):
    return admin.authorize(action, database, login="fixture_dba", password="fixture-dba-secret")


def test_discovery_uses_runtime_credentials_and_read_only_transactions(state):
    assert [row.name for row in state.admin.list_databases()] == ["active", "selected"]
    assert [row.name for row in state.admin.list_databases()] == ["active", "selected"]
    assert all(opts["user"] == "runtime" and opts["password"] == "runtime-secret" for opts, _ in state.server.sessions)
    assert sum(sql == "SET TRANSACTION READ ONLY" for _, _, sql, _ in state.server.sql) == 6
    assert all(sql.startswith(("SET TRANSACTION READ ONLY", "SELECT ")) for _, _, sql, _ in state.server.sql)
    state.identity.assert_not_called()
    state.audit.assert_not_called()


def test_unscoped_or_forged_calls_never_connect(state):
    svc = state.admin
    for invoke in (
        lambda: svc.test_connection(), lambda: svc.test_connection(for_save=True),
        lambda: svc.switch_database("selected"), lambda: svc.create_database("other"),
        lambda: svc.archive_database("selected"), lambda: svc.backup_database("unused", database="selected"),
        lambda: svc.restore_backup("unused", database="selected", confirmed=True),
        lambda: svc.delete_database("selected", typed_confirmation="selected", operation=object()),
        lambda: svc.migrate_interchange("unused", database="selected"),
    ):
        with pytest.raises(PermissionDeniedError, match="Fresh authorization"):
            invoke()
    assert state.server.sessions == []


def test_exact_scope_single_use_real_identity_and_secret_free_audit(state):
    with authorize(state.admin, "select", "selected") as op:
        before = len(state.server.sessions)
        with pytest.raises(PermissionDeniedError):
            state.admin.switch_database("active", operation=op)
        assert len(state.server.sessions) == before
        assert state.admin.switch_database("selected", operation=op).database == "selected"
        with pytest.raises(PermissionDeniedError):
            state.admin.switch_database("selected", operation=op)
    assert not op.active and op.password == ""
    with pytest.raises(PermissionDeniedError):
        state.admin.switch_database("selected", operation=op)
    with authorize(state.admin, "save", "active") as another:
        state.admin.test_connection(operation=another, for_save=True)
    assert op.correlation_id != another.correlation_id
    assert [(opts["user"], opts["password"]) for opts, _ in state.server.sessions] == [
        ("fixture_dba", "fixture-dba-secret"),
        ("fixture_dba", "fixture-dba-secret"),
        ("runtime", "runtime-secret"),
        ("fixture_dba", "fixture-dba-secret"),
        ("fixture_dba", "fixture-dba-secret"),
        ("runtime", "runtime-secret"),
    ]
    for call in state.audit.call_args_list:
        action, login, data = call.args
        assert action.startswith("database_") and login == "fixture_dba"
        assert data["backend_admin"] == login and data["initiating_application_user"] == "ProgTrackLord"
        assert data["result"] == "success" and data["database"] in state.server.databases
        assert "secret" not in repr(call)


@pytest.mark.parametrize("action", ["select", "save"])
def test_schema_compatibility_uses_runtime_role_not_database_owner(state, monkeypatch, action):
    compatibility_roles = []

    def runtime_only_compatibility(connection):
        compatibility_roles.append(connection.user)
        return connection.user == "runtime"

    monkeypatch.setattr(state.admin, "_compatible", runtime_only_compatibility)
    if action == "select":
        with authorize(state.admin, "select", "selected") as op:
            result = state.admin.switch_database(
                "selected", operation=op, runtime_password="runtime-secret",
            )
        assert result.database == "selected"
    else:
        with authorize(state.admin, "save", "active") as op:
            state.admin.test_connection(
                operation=op, for_save=True, runtime_password="runtime-secret",
            )

    assert compatibility_roles == ["runtime"]
    assert any(
        options["user"] == "fixture_dba" and options["password"] == "fixture-dba-secret"
        for options, _ in state.server.sessions
    )
    assert any(
        options["user"] == "runtime" and options["password"] == "runtime-secret"
        for options, _ in state.server.sessions
    )


def test_runtime_compatibility_rejects_a_replaced_database_oid(state, monkeypatch):
    original_connect = state.server.connect

    def replace_target_before_runtime_probe(dsn, **kwargs):
        options = conninfo_to_dict(dsn)
        if options["user"] == "runtime" and options["dbname"] == "selected":
            state.server.databases["selected"] = (11, True)
        return original_connect(dsn, **kwargs)

    monkeypatch.setattr("psycopg.connect", replace_target_before_runtime_probe)
    with authorize(state.admin, "select", "selected") as op:
        with pytest.raises(ConflictError, match="target changed"):
            state.admin.switch_database(
                "selected", operation=op, runtime_password="runtime-secret",
            )


def test_runtime_compatibility_failure_is_actionable_and_read_only(state, monkeypatch):
    compatibility_roles = []

    def incompatible(connection):
        compatibility_roles.append(connection.user)
        return False

    monkeypatch.setattr(state.admin, "_compatible", incompatible)
    with authorize(state.admin, "select", "selected") as op:
        with pytest.raises(BackendAdminError) as caught:
            state.admin.switch_database(
                "selected", operation=op, runtime_password="runtime-secret",
            )

    assert caught.value.reason == "runtime_schema"
    assert "CONNECT, schema USAGE, and table SELECT" in str(caught.value)
    assert compatibility_roles == ["runtime"]
    assert all(
        sql.startswith(("SELECT", "SET TRANSACTION READ ONLY"))
        for _, _, sql, _ in state.server.sql
    )


def test_prelogin_recovery_compatibility_uses_runtime_role(state, monkeypatch):
    from psycopg.conninfo import make_conninfo
    from Plugins.core.runtime_paths import BackendProfile

    settings = state.settings
    configuration = SimpleNamespace(
        paths=SimpleNamespace(profile=BackendProfile.SHARED_POSTGRESQL),
        saved_postgresql=lambda: settings,
        connection_dsn=lambda requested, *, password, **_kwargs: make_conninfo(
            host="fixture-host", dbname=requested.database,
            user=requested.user, password=password,
        ),
    )
    compatibility_roles = []

    def runtime_only_compatibility(connection):
        compatibility_roles.append(connection.user)
        return connection.user == "runtime"

    monkeypatch.setattr(PostgreSQLAdministrationService, "_compatible", staticmethod(runtime_only_compatibility))
    PostgreSQLAdministrationService.test_recovery_target(
        configuration, settings, login="fixture_dba", password="fixture-dba-secret",
        runtime_password="runtime-secret", client_key_passphrase="",
    )
    assert compatibility_roles == ["runtime"]


def test_auth_failure_privilege_failure_and_revoked_initiator_abort_before_action(state):
    with pytest.raises(BackendAdminError, match="separate"):
        with state.admin.authorize("test", "active", login="runtime", password="runtime-secret"):
            pytest.fail("runtime account authorized")
    with pytest.raises(BackendAdminError, match="separate"):
        with state.admin.authorize("test", "active", login="fixture_dba", password=""):
            pytest.fail("empty credential authorized")
    assert state.server.sessions == []
    state.server.bad_credentials = True
    with pytest.raises(BackendConfigurationError) as caught:
        with authorize(state.admin, "archive", "selected"):
            pytest.fail("failed authentication authorized")
    assert "fixture-dba-secret" not in str(caught.value)
    assert state.audit.call_args.args[2]["backend_admin"] is None
    state.server.bad_credentials = False
    state.server.owner = False
    with pytest.raises(BackendAdminError, match="does not own"):
        with authorize(state.admin, "archive", "selected"):
            pytest.fail("underprivileged owner authorized")
    assert state.audit.call_args.args[2]["backend_admin"] == "fixture_dba"
    state.server.owner = True
    with pytest.raises(BackendAdminError, match="no longer valid"):
        with authorize(state.admin, "archive", "selected") as op:
            state.identity.return_value = ""
            state.admin.archive_database("selected", operation=op)
    assert all(sql.startswith(("SELECT", "SET TRANSACTION READ ONLY")) for _, _, sql, _ in state.server.sql)


def test_create_initializes_using_same_admin_dsn_and_archive_rechecks_target(state, monkeypatch):
    adapter = Mock()
    adapter.get_installation_value.return_value = None
    adapter_ctor = Mock(return_value=adapter)
    monkeypatch.setattr("Plugins.core.backend.adapters.PostgreSQLAdapter", adapter_ctor)
    with authorize(state.admin, "create", "created") as op:
        state.admin.create_database("created", operation=op)
    connection = conninfo_to_dict(adapter_ctor.call_args.args[0])
    assert connection["user"] == "fixture_dba" and connection["dbname"] == "created"
    assert connection["password"] == "fixture-dba-secret"
    adapter.migrate.assert_called_once()
    adapter.close.assert_called_once()
    assert any(sql == 'CREATE DATABASE "created"' for _, _, sql, _ in state.server.sql)
    with authorize(state.admin, "archive", "selected") as op:
        state.admin.archive_database("selected", operation=op)
    with authorize(state.admin, "archive", "selected") as op:
        state.server.databases["selected"] = (10, False)
        state.admin.archive_database("selected", operation=op)
    archive_sql = [sql for _, _, sql, _ in state.server.sql if sql.startswith("ALTER DATABASE")]
    assert archive_sql == ['ALTER DATABASE "selected" WITH ALLOW_CONNECTIONS FALSE',
                           'ALTER DATABASE "selected" WITH ALLOW_CONNECTIONS TRUE']
    with pytest.raises(PermissionDeniedError, match="target or administrator privilege changed"):
        with authorize(state.admin, "archive", "selected") as op:
            state.server.databases["selected"] = (11, True)
            state.admin.archive_database("selected", operation=op)


def test_backup_restore_and_delete_use_selected_target_and_verified_artifact(state, tmp_path, monkeypatch):
    recorded = []

    def run(args, **kwargs):
        recorded.append((args, kwargs))
        if args[0] == "pg_restore":
            assert "PGPASSWORD" not in kwargs["env"]
            return SimpleNamespace(returncode=0)
        settings = conninfo_to_dict(args[args.index("--dbname") + 1])
        assert settings["dbname"] == "selected" and settings["user"] == "fixture_dba"
        assert "password" not in settings and kwargs["env"]["PGPASSWORD"] == "fixture-dba-secret"
        if "--format=custom" in args:
            Path(args[args.index("--file") + 1]).write_bytes(b"fixture PostgreSQL dump")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("Plugins.core.backend.postgresql_admin.shutil.which", lambda tool: tool)
    monkeypatch.setattr("Plugins.core.backend.postgresql_admin.subprocess.run", run)
    with authorize(state.admin, "backup", "selected") as op:
        artifact = state.admin.backup_database(tmp_path / "selected.ptbackup", database="selected", operation=op, storage_root=state.settings.managed_root)
    assert artifact.database == "selected" and artifact.database_oid == 10
    with authorize(state.admin, "restore", "selected") as op:
        state.admin.restore_backup(artifact.path, database="selected", confirmed=True, operation=op, storage_root=state.settings.managed_root)
    with pytest.raises(ConflictError, match="active"):
        with authorize(state.admin, "delete", "active") as op:
            state.admin.delete_database("active", typed_confirmation="active", backup=artifact, operation=op)
    with authorize(state.admin, "delete", "selected") as op:
        state.admin.delete_database("selected", typed_confirmation="selected", backup=artifact, operation=op)
    assert [args[0] for args, _kwargs in recorded] == ["pg_dump", "pg_restore", "psql"]
    artifact.path.write_bytes(b"changed")
    with pytest.raises(ConflictError, match="verified backup"):
        with authorize(state.admin, "delete", "selected") as op:
            state.admin.delete_database("selected", typed_confirmation="selected", backup=artifact, operation=op)
    assert sum(sql.startswith("DROP DATABASE") for _, _, sql, _ in state.server.sql) == 1


def test_transfer_uses_scoped_backend_and_direct_shared_import_denied(state, tmp_path, monkeypatch):
    private_backend = Mock()
    private_backend.interchange.import_package.return_value = SimpleNamespace(valid=True)
    ctor = Mock(return_value=private_backend)
    monkeypatch.setattr("Plugins.core.backend.facade.ProgTrackBackend", ctor)
    monkeypatch.setattr(state.admin, "_initialize_target", Mock())
    package = tmp_path / "source.ptdb"
    with authorize(state.admin, "transfer", "selected") as grant:
        assert state.admin.migrate_interchange(package, database="selected", operation=grant, storage_root=state.settings.managed_root).valid
    options = conninfo_to_dict(ctor.call_args.kwargs["postgres_dsn"])
    assert options["dbname"] == "selected" and options["password"] == "fixture-dba-secret"
    private_backend.interchange.import_package.assert_called_once_with(package, require_empty=True, admin_operation=grant)
    private_backend.close.assert_called_once()
    shared = InterchangeService(SimpleNamespace(adapter=SimpleNamespace(dialect="postgresql")), None)
    with pytest.raises(PermissionDeniedError, match="Fresh PostgreSQL transfer authorization"):
        shared.import_package(package)


def test_encrypted_tls_context_retained_for_psycopg_and_cli_fails_closed_without_transport(state, tmp_path, monkeypatch):
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    cert.touch()
    key.touch()
    admin = PostgreSQLAdministrationService(
        state.config, replace(state.settings, client_cert_file=str(cert), client_key_file=str(key)),
        authorized=True, validate_initiator=state.identity, client_key_passphrase="fixture-tls-secret",
    )
    with authorize(admin, "test", "active") as grant:
        admin.test_connection(operation=grant)
    assert state.server.sessions[-1][0]["sslpassword"] == "fixture-tls-secret"
    run = Mock()
    monkeypatch.setattr("Plugins.core.backend.postgresql_admin.sys.platform", "win32")
    monkeypatch.setattr("Plugins.core.backend.windows_pg_service_pipe.WindowsPgServicePipe",
                        Mock(side_effect=PermissionError("ACL unavailable")))
    monkeypatch.setattr("Plugins.core.backend.postgresql_admin.shutil.which", lambda tool: tool)
    monkeypatch.setattr("Plugins.core.backend.postgresql_admin.subprocess.run", run)
    with pytest.raises(BackendAdminError, match="protected PostgreSQL tool credential transport"):
        with authorize(admin, "backup", "selected") as grant:
            admin.backup_database(tmp_path / "target.ptbackup", database="selected", operation=grant, storage_root=state.settings.managed_root)
    run.assert_not_called()
    assert not (tmp_path / "target.ptbackup").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows named-pipe API")
def test_real_windows_libpq_consumes_pid_bound_service_entry(state, tmp_path):
    cert = tmp_path / "client.crt"
    key = tmp_path / "client.key"
    cert.touch()
    key.touch()
    tls = "Fíxture=private+tést"
    settings = replace(state.settings, host="127.0.0.1", port=1, connect_timeout=1,
                       client_cert_file=str(cert), client_key_file=str(key))
    admin = PostgreSQLAdministrationService(state.config, settings, authorized=True,
                                            validate_initiator=state.identity, client_key_passphrase=tls)
    probe = """
from hashlib import sha256
from psycopg import pq
import os, sys
c = pq.PGconn.connect_start(sys.argv[1].encode('utf-8'))
values = {option.keyword: option.val for option in c.info}
actual = values.get(b'sslpassword') or b''
valid = sha256(actual).hexdigest() == os.environ['PROGTRACK_TLS_TEST_HASH']
valid = valid and values.get(b'password') == os.environ['PGPASSWORD'].encode('utf-8')
c.finish()
raise SystemExit(0 if valid else 2)
"""
    # Use the real installed libpq in a separate child. The probe checks the
    # parsed options even when localhost:1 has no listener. No PostgreSQL
    # login, certificate, or password is needed for this test.
    with authorize(admin, "test", "active") as grant:
        admin.test_connection(operation=grant)
        with admin._tool_connection(grant) as (dsn, env, transport):
            assert tls not in dsn and "fixture-dba-secret" not in dsn
            assert tls not in env.values()
            assert env["PGSERVICEFILE"].startswith(r"\\.\pipe\ProgTrack.PgService.")
            env["PROGTRACK_TLS_TEST_HASH"] = hashlib.sha256(tls.encode("utf-8")).hexdigest()
            returncode, _stderr = admin._run_with_service_pipe([sys.executable, "-c", probe, dsn], env, transport,
                                                               subprocess.CREATE_NO_WINDOW)
            assert returncode == 0
            assert transport.delivered
        with pytest.raises(OSError):
            os.open(transport.name, os.O_RDONLY)


@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows named-pipe API")
def test_windows_pipe_never_serves_a_different_process_and_releases_handles():
    from Plugins.core.backend.windows_pg_service_pipe import WindowsPgServicePipe

    script = """
import os
import sys
try:
    with open(sys.argv[1], 'rb', buffering=0) as stream:
        premature_read = stream.read(4096)
except OSError:
    premature_read = None
raise SystemExit(1 if premature_read else 0)
"""
    with WindowsPgServicePipe(b"the reader must receive no bytes") as transport:
        child = subprocess.Popen([sys.executable, "-c", script, transport.name],
                                 creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            # The child shares our Windows user but is not the authorized process.
            transport.serve_process(os.getpid())
            assert not transport.wait_for_delivery(5)
        except BaseException:
            child.kill()
            child.wait(timeout=5)
            raise
    try:
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
    with pytest.raises(OSError):
        os.open(transport.name, os.O_RDONLY)


@pytest.mark.skipif(sys.platform != "win32", reason="requires the Windows named-pipe API")
def test_windows_pipe_cancels_unanswered_connection_on_exit():
    from Plugins.core.backend.windows_pg_service_pipe import WindowsPgServicePipe

    with WindowsPgServicePipe(b"synthetic") as transport:
        transport.serve_process(os.getpid())
    assert not transport.delivered
    with pytest.raises(OSError):
        os.open(transport.name, os.O_RDONLY)


@pytest.fixture
def qtapp(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_qt_refresh_cancel_repeated_test_and_save_use_separate_credentials(state, qtapp, monkeypatch):
    from Plugins.core.backend_configuration_dialog import BackendAdminAuthorizationDialog

    msgs = json.loads((Path(__file__).parents[1] / "lang" / "messages_en.json").read_text(encoding="utf-8"))
    messages = []
    for signal in ("warning", "information"):
        monkeypatch.setattr(QMessageBox, signal, lambda *values: messages.append(values[1:]))
    dialogs = []
    cancelled = [True]

    def modal(prompt):
        dialogs.append(prompt)
        assert prompt.password.echoMode() is QLineEdit.EchoMode.Password
        assert prompt.password.text() == prompt.login.text() == ""
        if cancelled and cancelled.pop():
            prompt.password.setText("cancelled-secret")
            return QDialog.DialogCode.Rejected
        prompt.login.setText("fixture_dba")
        prompt.password.setText("fixture-dba-secret")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BackendAdminAuthorizationDialog, "exec", modal)
    storage_probe = Mock()
    monkeypatch.setattr("Plugins.core.managed_storage_worker.check_configured_storage", storage_probe)
    dialog = BackendConfigurationDialog(state.config, msgs, authorized=True, validate_admin_initiator=state.identity)
    dialog.pg_host.setText("fixture-host")
    dialog.pg_user.setText("runtime")
    dialog.pg_password.setText("runtime-secret")
    dialog.pg_managed_root.setText(state.settings.managed_root)
    dialog.postgres_radio.setChecked(True)
    dialog._catalog_opened = True
    dialog.show()
    refresh_catalog(dialog)
    dialog.pg_database.setCurrentIndex(dialog.pg_database.findData("active"))
    refresh_catalog(dialog)
    assert len(dialogs) == 0
    before = len(state.server.sessions)

    def wait_for_storage():
        for _ in range(100):
            if dialog._staged_storage_request is None:
                return
            QTest.qWait(10)
        pytest.fail("the staged storage check did not complete")

    dialog._test_connection()
    assert len(state.server.sessions) == before and dialogs[-1].password.text() == ""
    for count, action in enumerate((dialog._test_connection, dialog._test_connection, dialog._save), 2):
        action()
        # The immediate scrub precedes Qt deleting the completed prompt.
        assert len(dialogs) == count and dialogs[-1].password.text() == ""
        wait_for_storage()
    assert storage_probe.call_count == 3
    assert len(state.server.sessions) == before + 5
    assert [(opts["user"], opts["password"]) for opts, _ in state.server.sessions[before:]] == [
        ("fixture_dba", "fixture-dba-secret"),
        ("fixture_dba", "fixture-dba-secret"),
        ("fixture_dba", "fixture-dba-secret"),
        ("fixture_dba", "fixture-dba-secret"),
        ("runtime", "runtime-secret"),
    ]
    assert state.config.read_password() == "runtime-secret"
    assert "fixture-dba-secret" not in state.config.paths.profile_file.read_text(encoding="utf-8")
    dialog.close()


def test_qt_each_management_handler_passes_exact_selected_target(state, qtapp, monkeypatch, tmp_path):
    from Plugins.core.backend_configuration_dialog import BackendAdminAuthorizationDialog

    dialog = BackendConfigurationDialog(state.config, {}, authorized=True, validate_admin_initiator=state.identity)
    dialog.pg_host.setText("fixture-host")
    dialog.pg_user.setText("runtime")
    dialog.pg_password.setText("runtime-secret")
    dialog.pg_managed_root.setText(state.settings.managed_root)
    dialog.show()
    refresh_catalog(dialog)
    dialog.pg_database.setCurrentIndex(dialog.pg_database.findData("selected"))
    dialog._selected_pg_database = lambda: "selected"
    dialog._refresh_pg_databases = Mock()
    monkeypatch.setattr(QInputDialog, "getText", lambda *a: ("selected", True))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a: (str(tmp_path / "restore.ptbackup"), ""))
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "save.ptbackup"), ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a: state.settings.managed_root)
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: pytest.fail(a[2]))
    invoked = Mock()
    original_type = PostgreSQLAdministrationService
    for name in ("create_database", "archive_database", "backup_database", "restore_backup", "delete_database", "migrate_interchange"):
        def action(self, *args, _method=name, operation=None, **kwargs):
            invoked(_method, operation.action, operation.database)
            operation.used = True
        monkeypatch.setattr(original_type, name, action)

    def modal(prompt):
        prompt.login.setText("fixture_dba")
        prompt.password.setText("fixture-dba-secret")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BackendAdminAuthorizationDialog, "exec", modal)
    for handler in (dialog._archive_selected_pg_database, dialog._backup_selected_pg_database,
                    dialog._restore_selected_pg_database, dialog._delete_selected_pg_database):
        handler()
    source = tmp_path / "source.sqlite3"
    source.touch()
    dialog.pg_sqlite_source.setText(str(source))
    dialog.backend = SimpleNamespace(paths=SimpleNamespace(database_path=source), interchange=Mock())
    dialog.backend.interchange.preview.return_value = SimpleNamespace(valid=True, counts={})
    dialog._migrate_sqlite_to_selected()
    dialog._use_selected_pg_database()
    assert dialog.pg_database.currentData() == "selected"
    state.server.databases.pop("selected")
    dialog._create_pg_database()
    assert [call.args[1:] for call in invoked.call_args_list] == [(action, "selected") for action in
                                                                    ("archive", "backup", "restore", "delete", "transfer", "create")]
    dialog.close()


def test_qt_failed_runtime_compatibility_keeps_selected_database_unchanged(state, qtapp, monkeypatch):
    from Plugins.core.backend_configuration_dialog import BackendAdminAuthorizationDialog

    dialog = BackendConfigurationDialog(state.config, {}, authorized=True, validate_admin_initiator=state.identity)
    dialog.pg_host.setText("fixture-host")
    dialog.pg_user.setText("runtime")
    dialog.pg_password.setText("runtime-secret")
    dialog.pg_managed_root.setText(state.settings.managed_root)
    dialog.postgres_radio.setChecked(True)
    dialog.show()
    refresh_catalog(dialog)
    dialog._selected_pg_database = lambda: "selected"
    initial_database = dialog.pg_database.currentData()
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: messages.append(args[-1]))
    monkeypatch.setattr(
        PostgreSQLAdministrationService,
        "_compatible",
        staticmethod(lambda _connection: False),
    )

    def authorize_admin(prompt):
        prompt.login.setText("fixture_dba")
        prompt.password.setText("fixture-dba-secret")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(BackendAdminAuthorizationDialog, "exec", authorize_admin)
    dialog._use_selected_pg_database()

    assert dialog.pg_database.currentData() == initial_database
    assert len(messages) == 1
    assert "CONNECT, schema USAGE, and table SELECT" in messages[0]
    dialog.close()
