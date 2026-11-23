"""Saved credential presentation and backend mTLS section contracts."""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QMessageBox, QPushButton

from Plugins.core.backend.catalog_cache import CatalogSnapshot
from Plugins.core.backend.catalog_revision import CatalogRevision
from Plugins.core.backend.postgresql_admin import PostgreSQLDatabaseInfo
from Plugins.core.backend_configuration import BackendConfigurationService
from Plugins.core.backend_configuration_dialog import BackendConfigurationDialog
from test_phase3_postgresql_management import MemoryCredentials, paths


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    rp = paths(tmp_path)
    config = BackendConfigurationService(rp, environ={"LOCALAPPDATA": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path)},
                                         credential_store=MemoryCredentials())
    for name in ("ca.pem", "client.pem", "client.key"):
        (tmp_path / name).write_text("test fixture only", encoding="utf-8")
    original = {
        "profile": "shared_postgresql",
        "postgresql": {
            "host": "fixture-host", "user": "fixture-runtime", "database": "fixture",
            "managed_root": str(rp.managed_root), "sslmode": "verify-ca",
            "ca_file": str(tmp_path / "ca.pem"), "client_cert_file": str(tmp_path / "client.pem"),
            "client_key_file": str(tmp_path / "client.key"),
        },
    }
    rp.profile_file.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "warning", Mock())
    monkeypatch.setattr(QMessageBox, "information", Mock())
    dialogs = []

    def make(language="en"):
        path = Path(__file__).parents[1] / "lang" / f"messages_{language}.json"
        dialog = BackendConfigurationDialog(config, json.loads(path.read_text(encoding="utf-8")), authorized=True)
        dialog._catalog_opened = True
        # #226 tests the asynchronous storage check separately. Exercise the
        # actual profile and credential persistence after an available result.
        monkeypatch.setattr(dialog, "_check_staged_storage", lambda on_available: on_available())
        dialogs.append(dialog)
        return dialog

    yield config, make, app
    for dialog in dialogs:
        dialog.close()
    app.processEvents()


def enable_candidate(dialog):
    snapshot = CatalogSnapshot((PostgreSQLDatabaseInfo("fixture", "owner", 0, True, False, True),),
                               CatalogRevision("epoch", 3, False), "2026-11-20T08:00:00Z")
    dialog._show_catalog(snapshot, usable=True, preferred="fixture")


@pytest.mark.parametrize("language", ("en", "de", "it", "ru"))
def test_saved_secret_has_fixed_marker_and_readable_localized_help(fixture, language):
    config, make, _ = fixture
    for saved in ("xy", "fictional-runtime-secret-which-is-much-longer"):
        config.credential_store.write(config.credential_target, saved)
        dialog = make(language)
        field = dialog.pg_password
        assert field.text() == "" and field.echoMode() == QLineEdit.EchoMode.Password
        assert field.placeholderText() == "********"
        assert field.toolTip() == field.accessibleDescription() == dialog.messages["backend.postgresql.password.saved"]
        assert saved not in field.toolTip() and dialog._password() == saved


def test_replace_clear_and_save_do_not_confuse_marker_with_typed_asterisks(fixture, monkeypatch):
    config, make, _ = fixture
    config.credential_store.write(config.credential_target, "previous-fixture-password")
    dialog = make()
    enable_candidate(dialog)
    validate = Mock(return_value=(True, None))
    monkeypatch.setattr(dialog, "_admin_action", validate)
    writes = Mock(wraps=config.credential_store.write)
    monkeypatch.setattr(config.credential_store, "write", writes)
    dialog.pg_password.setText("temporary-draft")
    assert not dialog.pg_database.isEnabled() and dialog._draft_generation is not None
    dialog.pg_password.clear()
    assert dialog.pg_password.text() == "" and dialog.pg_password.placeholderText() == "********"
    assert dialog._password() == "previous-fixture-password"
    enable_candidate(dialog)
    dialog._save()
    writes.assert_not_called()
    assert config.read_password() == "previous-fixture-password"
    assert validate.call_args.args[:2] == ("save", "fixture")

    replacement = make()
    monkeypatch.setattr(replacement, "_admin_action", validate)
    replacement.pg_password.setText("********")  # A real replacement input.
    enable_candidate(replacement)
    replacement._save()
    writes.assert_called_once_with(config.credential_target, "********")
    serialized = config.paths.profile_file.read_text(encoding="utf-8")
    assert all(secret not in serialized for secret in ("previous-fixture-password", "temporary-draft", "********"))
    reopened = make()
    assert reopened.pg_password.text() == "" and reopened.pg_password.placeholderText() == "********"


def test_absent_and_dsn_only_do_not_show_stored_indicator(fixture, monkeypatch):
    config, make, _ = fixture
    dialog = make()
    assert dialog.pg_password.text() == dialog.pg_password.placeholderText() == ""
    assert dialog._password() == ""
    monkeypatch.setattr(dialog, "_admin_action", Mock(return_value=(True, None)))
    enable_candidate(dialog)
    dialog._save()
    assert QMessageBox.warning.called
    assert not config.read_password()

    config.environ["PROGTRACK_POSTGRES_DSN"] = "host=override-fixture user=override password=dsn-only-fixture dbname=override"
    override = make()
    assert override.pg_password.text() == override.pg_password.placeholderText() == ""
    assert "dsn-only-fixture" not in override.override_label.text()
    monkeypatch.setattr(override, "_admin_action", Mock(return_value=(True, None)))
    enable_candidate(override)
    override._save()
    assert not config.read_password()
    assert "dsn-only-fixture" not in config.paths.profile_file.read_text(encoding="utf-8")


def test_mtls_geometry_keyboard_and_controls_survive_round_trip(fixture, monkeypatch):
    config, make, app = fixture
    dialog = make()
    enable_candidate(dialog)
    section = dialog.pg_mtls_section
    toggle = section.findChild(QPushButton, "animalDialogSectionToggle")
    assert section.title_text() == "mTLS" and not section.is_expanded()
    assert section.content_widget().isHidden()
    assert not section.isAncestorOf(dialog.pg_ca_file)
    fields = (dialog.pg_client_cert_file, dialog.pg_client_key_file, dialog.pg_client_key_passphrase)
    assert all(section.content_widget().isAncestorOf(field) for field in fields)
    icon_size = toggle.iconSize()
    assert toggle.size().width() == 24 and not toggle.icon().isNull()
    original_paths = (fields[0].text(), fields[1].text())
    compact = dialog.sizeHint().height()
    section.set_expanded(True)
    expanded = dialog.sizeHint().height()
    assert expanded > compact
    section.set_expanded(False)
    assert dialog.sizeHint().height() == compact

    dialog.show()
    app.processEvents()
    assert dialog.pg_ca_file.isVisible() and all(not field.isVisible() for field in fields)
    assert dialog.pg_page_scroll.size() == dialog.pages.contentsRect().size()
    admin = Mock(side_effect=AssertionError("toggle triggered server work"))
    saved = Mock(side_effect=AssertionError("toggle saved profile"))
    stored_read = Mock(side_effect=AssertionError("toggle read secure storage"))
    monkeypatch.setattr(dialog, "_admin_service", admin)
    monkeypatch.setattr(config, "save", saved)
    monkeypatch.setattr(config.credential_store, "read", stored_read)
    initial_profile = config.paths.profile_file.read_bytes()

    def tab_cycle():
        dialog.pg_host.setFocus()
        visited = set()
        for _ in range(80):
            current = dialog.focusWidget()
            if current is None or current in visited:
                break
            visited.add(current)
            QTest.keyClick(current, Qt.Key.Key_Tab)
        return visited

    collapsed_focus = tab_cycle()
    assert toggle in collapsed_focus and not set(fields) & collapsed_focus
    for _ in range(3):
        toggle.setFocus()
        QTest.keyClick(toggle, Qt.Key.Key_Space)
        immediate = dialog.height()
        app.processEvents()
        assert section.is_expanded() and dialog.sizeHint().height() == expanded
        assert immediate == min(expanded, dialog.maximumHeight())
        assert dialog.height() == min(expanded, dialog.maximumHeight())
        assert dialog.pg_page_scroll.size() == dialog.pages.contentsRect().size()
        assert toggle.iconSize() == icon_size
        assert all(field.isVisible() for field in fields)
        assert (fields[0].text(), fields[1].text()) == original_paths
        assert set(fields) <= tab_cycle()
        toggle.setFocus()
        QTest.keyClick(toggle, Qt.Key.Key_Space)
        app.processEvents()
        assert not section.is_expanded() and dialog.sizeHint().height() == compact
        assert dialog.height() == min(compact, dialog.maximumHeight())
        assert dialog.pg_page_scroll.size() == dialog.pages.contentsRect().size()
        assert toggle.iconSize() == icon_size
        assert all(not field.isVisible() for field in fields)
    assert dialog.pg_database.currentData() == "fixture" and dialog.pages.currentIndex() == 1
    admin.assert_not_called()
    saved.assert_not_called()
    stored_read.assert_not_called()
    assert config.paths.profile_file.read_bytes() == initial_profile


def test_collapsed_mtls_preserves_unsaved_certificate_and_secret(fixture):
    _, make, app = fixture
    dialog = make()
    dialog.show()
    app.processEvents()
    group = dialog.pg_mtls_section
    group.set_expanded(True)
    dialog.pg_client_cert_file.setText("other-client.pem")
    dialog.pg_client_key_file.setText("other-private.key")
    dialog.pg_client_key_passphrase.setText("fictional-draft")
    group.set_expanded(False)
    assert dialog.postgresql_settings().client_cert_file == "other-client.pem"
    assert dialog.postgresql_settings().client_key_file == "other-private.key"
    assert dialog.pg_client_key_passphrase.text() == "fictional-draft"
    assert dialog.pg_client_key_passphrase.echoMode() == QLineEdit.EchoMode.Password
    group.set_expanded(True)
    assert dialog.pg_client_key_passphrase.text() == "fictional-draft"
    assert not make().pg_mtls_section.is_expanded()
