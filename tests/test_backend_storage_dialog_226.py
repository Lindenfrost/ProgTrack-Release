from pathlib import Path
from unittest.mock import Mock

from PyQt6.QtWidgets import QApplication, QFileDialog, QLineEdit, QMessageBox
from PyQt6.QtTest import QTest

from Plugins.core.backend_configuration import BackendConfigurationService, PostgreSQLSettings
from Plugins.core.backend_configuration_dialog import BackendConfigurationDialog
from test_phase3_postgresql_management import MemoryCredentials as MemoryCredentialStore, paths as runtime_paths


def storage_dialog(tmp_path):
    paths = runtime_paths(tmp_path)
    service = BackendConfigurationService(paths, environ={}, credential_store=MemoryCredentialStore())
    service.save(profile=paths.profile, sqlite_filename="fixture.sqlite3",
                 postgresql=PostgreSQLSettings(), password="", authorized=True)
    service.credential_store.write(service.storage_secret_target(), "saved-storage-secret")
    dialog = BackendConfigurationDialog(service, {}, authorized=True)
    dialog._catalog_opened = True
    dialog.pg_host.setText("fixture.test")
    dialog.pg_user.setText("runtime-user")
    return dialog


def test_storage_foldout_preserves_saved_state_and_geometry(tmp_path):
    app = QApplication.instance() or QApplication([])
    dialog = storage_dialog(tmp_path)
    try:
        dialog.show()
        dialog.postgres_radio.setChecked(True)
        app.processEvents()
        assert not dialog.pg_storage_section.is_expanded()
        if dialog.pg_storage_password.placeholderText():
            assert dialog.pg_storage_password.placeholderText() == "********"
            assert dialog.pg_storage_password.text() == ""
            assert dialog.pg_storage_password.echoMode() is QLineEdit.EchoMode.Password
            assert dialog._storage_password_replacement() is None
        dialog.pg_storage_section._toggle.setChecked(True)
        app.processEvents()
        dialog.pg_storage_username.setText("separate-user")
        dialog.pg_storage_password.setText("draft")
        dialog.pg_storage_section._toggle.setChecked(False)
        app.processEvents()
        assert dialog.pg_storage_username.text() == "separate-user"
        assert dialog._storage_password_replacement() == "draft"
        dialog._use_database_storage_password()
        assert dialog._storage_password_replacement() == ""
    finally:
        dialog.close()


def test_target_b_backup_path_selected_before_authorization_and_cancel_safe(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = storage_dialog(tmp_path)
    targets = iter(["", str(tmp_path / "b")])
    stages = []
    execute = Mock(return_value=None)
    fake_admin = Mock(backup_database=execute)
    dialog._selected_pg_database = lambda: "database_b"

    def choose_root(*args):
        stages.append(("choose", args[1]))
        return next(targets)

    def authorized(action, target, command):
        stages.append(("authorize", target))
        return True, command(fake_admin, "fresh-grant")

    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a: (str(tmp_path / "b.ptbackup"), ""))
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", choose_root)
    monkeypatch.setattr(QMessageBox, "information", Mock())
    dialog._admin_action = authorized
    try:
        dialog._backup_selected_pg_database()
        execute.assert_not_called()
        dialog._backup_selected_pg_database()
        execute.assert_called_once_with(str(tmp_path / "b.ptbackup"), database="database_b",
                                        operation="fresh-grant", storage_root=str(tmp_path / "b"))
        assert [stage for stage, _ in stages] == ["choose", "choose", "authorize"]
        assert "database_b" in stages[0][1]
    finally:
        dialog.close()

def test_test_connection_does_not_save_and_save_persists_selected_b(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog = storage_dialog(tmp_path)
    paths = dialog.service.paths
    existing_a = tmp_path / "a"
    selected_b = tmp_path / "b"
    existing_a.mkdir()
    selected_b.mkdir()
    current = PostgreSQLSettings(
        host="fixture.test", database="database_a", user="runtime-user", managed_root=str(existing_a),
    )
    dialog.service.save(profile=paths.profile.SHARED_POSTGRESQL, sqlite_filename="fixture.sqlite3",
                        postgresql=current, password="runtime-secret", authorized=True)
    initial_profile = paths.profile_file.read_bytes()
    dialog.pg_managed_root.setText(str(existing_a))
    dialog.pg_database.clear()
    dialog.pg_database.addItem("database_b", "database_b")
    dialog.postgres_radio.setChecked(True)
    dialog._catalog_usable = True
    grants = Mock(return_value=(True, None))
    dialog._admin_action = grants
    check = Mock(return_value=selected_b)
    monkeypatch.setattr("Plugins.core.managed_storage_worker.check_configured_storage", check)
    monkeypatch.setattr(QMessageBox, "information", Mock())
    monkeypatch.setattr(QMessageBox, "warning", Mock(side_effect=AssertionError("unexpected warning")))
    roots = iter(["", str(selected_b)])
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *args: next(roots))
    tested = Mock()
    dialog.managed_storage_tested.connect(tested)
    saved = Mock()
    dialog.managed_storage_profile_saved.connect(saved)
    dialog.show()
    app.processEvents()

    def await_signal(signal, calls):
        for _ in range(100):
            if signal.call_count >= calls:
                return
            QTest.qWait(20)
        assert signal.call_count >= calls

    try:
        dialog._test_connection()
        grants.assert_not_called()
        check.assert_not_called()
        dialog._test_connection()
        await_signal(tested, 1)
        assert paths.profile_file.read_bytes() == initial_profile
        assert dialog.pg_managed_root.text() == str(selected_b)
        tested.assert_called_once_with(str(selected_b), True)
        dialog._save()
        await_signal(saved, 1)
        assert dialog.service.saved_postgresql().database == "database_b"
        assert dialog.service.saved_postgresql().managed_root == str(selected_b)
        assert check.call_count == 2
        assert all(call.args[1] == str(selected_b) for call in check.call_args_list)
        assert b"runtime-secret" not in paths.profile_file.read_bytes()
    finally:
        dialog.close()
