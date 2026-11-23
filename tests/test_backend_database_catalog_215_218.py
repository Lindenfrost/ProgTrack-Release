"""Database dropdown, read-only freshness and the typed grouped table."""

from dataclasses import replace
from itertools import product
import json
from threading import Event, current_thread
from time import monotonic
from unittest.mock import Mock

import pytest
from psycopg.conninfo import conninfo_to_dict
from PyQt6.QtCore import QEventLoop, QThreadPool, QTimer, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QMessageBox

from Plugins.core.backend.catalog_cache import CatalogSnapshot, context_key, read_snapshot, write_snapshot
from Plugins.core.backend.catalog_revision import CatalogRevision
from Plugins.core.backend.errors import BackendConfigurationError
from Plugins.core.backend.postgresql_admin import PostgreSQLDatabaseInfo
from Plugins.core.backend_catalog_table import selected_database_row
from Plugins.core.backend_catalog_worker import BoundedCatalogReader
from Plugins.core.backend_configuration import BackendConfigurationService
from Plugins.core.backend_configuration_dialog import BackendConfigurationDialog
from test_phase3_postgresql_management import MemoryCredentials, paths


def row(name, compatible=True, active=True):
    return PostgreSQLDatabaseInfo(name, "fixture-owner", 42, active, False, compatible)


def revision(number=2, dirty=False):
    return CatalogRevision("fixture-epoch", number, dirty)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    config = BackendConfigurationService(paths(tmp_path), credential_store=MemoryCredentials(),
                                         environ={"LOCALAPPDATA": str(tmp_path), "XDG_CACHE_HOME": str(tmp_path)})
    config.paths.profile_file.write_text(json.dumps({"profile": "shared_postgresql", "postgresql": {
        "host": "db.example.test", "user": "fixture-runtime", "database": "beta",
        "managed_root": str(config.paths.managed_root),
    }}), encoding="utf-8")
    config.credential_store.write(config.credential_target, "fixture-runtime-secret")
    service = Mock()
    service.catalog_revision.return_value = revision()
    service.list_databases.return_value = [row("beta"), row("archive", active=False), row("unsupported", False), row("pending", None), row("ALPHA")]
    factory = Mock(return_value=service)
    monkeypatch.setattr(QMessageBox, "warning", Mock())
    monkeypatch.setattr("Plugins.core.backend_configuration_dialog.BackendConfigurationDialog._admin_service", factory)
    dialogs = []

    def make():
        dialog = BackendConfigurationDialog(config, {}, authorized=True)
        dialogs.append(dialog)
        return dialog

    yield config, service, factory, make
    for dialog in dialogs:
        dialog.close()
    app.processEvents()


def choices(dialog):
    return [dialog.pg_database.itemData(i) for i in range(dialog.pg_database.count())]


def await_catalog(dialog):
    until = monotonic() + 4
    while dialog._catalog_request is not None and monotonic() < until:
        QTest.qWait(5)
    assert dialog._catalog_request is None


def open_catalog(dialog):
    dialog.show()
    await_catalog(dialog)


def refresh_catalog(dialog):
    dialog._refresh_pg_databases()
    await_catalog(dialog)


def test_cache_open_once_coalesces_revision_change_without_background_refresh(catalog):
    config, service, factory, make = catalog
    first = make()
    open_catalog(first)
    assert not first.pg_database.isEditable()
    assert choices(first) == ["ALPHA", "beta"] and first.pg_database.currentData() == "beta"
    assert service.list_databases.call_count == 1
    assert service.catalog_revision.call_count == 2
    assert factory.call_args.kwargs == {"discovery": True}
    assert config.catalog_cache_path().is_file()
    cached = config.catalog_cache_path().read_bytes()
    assert b"fixture-runtime-secret" not in cached and b"ProgTrack compatible" not in cached
    assert not config.catalog_cache_path().is_relative_to(config.paths.application_root / "ProgTrackData")
    first.close()
    service.reset_mock()
    matching = make()
    open_catalog(matching)
    service.catalog_revision.assert_called_once_with()
    service.list_databases.assert_not_called()
    assert config.catalog_cache_path().read_bytes() == cached
    matching.close()
    service.reset_mock()
    service.catalog_revision.return_value = revision(5)
    service.list_databases.return_value = [row("replacement")]
    QApplication.processEvents()
    assert not service.mock_calls and config.catalog_cache_path().read_bytes() == cached
    changed = make()
    open_catalog(changed)
    assert service.list_databases.call_count == 1
    assert choices(changed) == ["replacement"] and changed.pg_database.currentIndex() == -1
    assert b"replacement" in config.catalog_cache_path().read_bytes()


def test_unverified_open_preserves_matching_display_and_failure_clears_controls(catalog):
    _, service, _, make = catalog
    first = make()
    open_catalog(first)
    service.catalog_revision.return_value = None
    service.reset_mock()
    offline = make()
    open_catalog(offline)
    assert "unavailable" in offline.pg_catalog_status.text()
    assert choices(offline) and not offline.pg_database.isEnabled()
    service.list_databases.assert_not_called()
    offline.pg_host.setText("different.example.test")
    assert not choices(offline) and offline.pg_database_table.rowCount() == 0
    service.list_databases.side_effect = BackendConfigurationError("network error")
    refresh_catalog(offline)
    assert not choices(offline) and "could not be verified" in offline.pg_catalog_status.text()
    service.list_databases.side_effect = None
    refresh_catalog(offline)
    assert choices(offline) and offline.pg_database.isEnabled()
    assert "unavailable" in offline.pg_catalog_status.text()


def test_changed_while_listing_never_publishes_current_snapshot(catalog):
    config, service, _, make = catalog
    dialog = make()
    open_catalog(dialog)
    initial = config.catalog_cache_path().read_bytes()
    service.catalog_revision.side_effect = [revision(3, True), revision(4)]
    refresh_catalog(dialog)
    assert not dialog.pg_database.isEnabled() and not dialog.test_button.isEnabled()
    assert config.catalog_cache_path().read_bytes() == initial


def test_cache_context_covers_cert_file_identity_account_and_credential_epoch(catalog, tmp_path):
    config, _, _, make = catalog
    settings = make().postgresql_settings()
    spec = tmp_path / "client.pem"
    spec.write_text("public certificate one", encoding="utf-8")
    settings = replace(settings, client_cert_file=str(spec))
    initial = context_key(config, settings)
    assert context_key(config, replace(settings, database="some-other-candidate")) == initial
    assert context_key(config, replace(settings, user="another-runtime-account")) != initial
    assert context_key(config, settings, draft_generation="draft") != initial
    spec.write_text("public certificate two", encoding="utf-8")
    assert context_key(config, settings) != initial
    renewed = context_key(config, settings)
    document = config.load_document()
    document["credential_generation"] = "rotated-fixture"
    config.paths.profile_file.write_text(json.dumps(document), encoding="utf-8")
    assert context_key(config, settings) != renewed
    path = config.catalog_cache_path()
    snapshot = CatalogSnapshot((row("real"),), revision(), "2026-11-20T00:00:00+00:00")
    write_snapshot(path, renewed, snapshot)
    assert read_snapshot(path, renewed) == snapshot and read_snapshot(path, initial) is None
    path.write_text('{"format": 99}', encoding="utf-8")
    assert read_snapshot(path, renewed) is None


def test_grouped_table_only_real_rows_are_actionable_and_selection_survives_move(catalog):
    _, service, _, make = catalog
    dialog = make()
    open_catalog(dialog)
    table = dialog.pg_database_table
    labels = [table.item(i, 0).text() for i in range(table.rowCount()) if table.item(i, 0)]
    assert labels == ["ProgTrack compatible", "ALPHA", "archive", "beta", "", "Not ProgTrack compatible", "unsupported", "", "Compatibility unknown", "pending"]
    selectable = Qt.ItemFlag.ItemIsSelectable
    assert all(not table.item(i, 0).flags() & selectable for i in (0, 4, 5, 7, 8))
    assert all(table.columnSpan(i, 0) == 5 for i in (0, 4, 5, 7, 8))
    table.selectRow(6)
    assert dialog._selected_pg_database() == "unsupported" and not dialog.pg_use_database.isEnabled()
    service.list_databases.return_value = [row("beta"), row("unsupported"), row("pending", None)]
    refresh_catalog(dialog)
    assert selected_database_row(table).name == "unsupported"
    assert dialog.pg_use_database.isEnabled()
    old_open = dialog._catalog_opened
    dialog.show()
    QApplication.processEvents()
    assert old_open
    header = table.visualItemRect(table.item(0, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.MouseButton.LeftButton, pos=header)
    assert not dialog._selected_pg_database() and not dialog.pg_backup_database.isEnabled()


@pytest.mark.parametrize("rows", [(), (row("unknown", None),), (row("archived", active=False),), (row("foreign", False),)])
def test_unusable_or_empty_catalog_never_invents_a_choice(catalog, rows):
    _, service, _, make = catalog
    service.list_databases.return_value = rows
    dialog = make()
    open_catalog(dialog)
    assert not choices(dialog) and dialog.pg_database.currentIndex() == -1
    assert not dialog.pg_database.isEnabled() and not dialog.test_button.isEnabled()


@pytest.mark.parametrize("groups", tuple(product((False, True), repeat=3)))
def test_each_combination_has_dividers_only_between_groups(catalog, groups):
    _, service, _, make = catalog
    service.list_databases.return_value = [row(f"fixture_{i}", state) for i, (included, state)
                                          in enumerate(zip(groups, (True, False, None))) if included]
    dialog = make()
    open_catalog(dialog)
    table = dialog.pg_database_table
    real = [table.item(i, 0).data(Qt.ItemDataRole.UserRole) for i in range(table.rowCount())]
    dividers = [i for i in range(table.rowCount()) if table.cellWidget(i, 0) is not None]
    assert [value for value in real if value is not None] == service.list_databases.return_value
    assert len(dividers) == max(0, sum(groups) - 1)
    assert all(0 < i < table.rowCount() - 1 and table.columnSpan(i, 0) == 5 for i in dividers)


def test_slow_read_keeps_gui_responsive_and_never_publishes_a_previous_context(catalog):
    config, service, _, make = catalog
    started, release_old = Event(), Event()
    gui = current_thread()

    def slow_snapshot():
        assert current_thread() is not gui
        started.set()
        assert release_old.wait(timeout=5)
        return [row("old_context")]

    service.list_databases.side_effect = slow_snapshot
    dialog = make()
    try:
        dialog.show()
        assert started.wait(2)
        assert dialog._catalog_request is not None
        loop = QEventLoop()
        events = []
        QTimer.singleShot(0, lambda: (events.append("GUI timer ran while read was pending"), loop.quit()))
        loop.exec()
        assert events and not release_old.is_set() and not config.catalog_cache_path().is_file()

        dialog.pg_host.setText("new.example.test")
        assert not choices(dialog)
        service.list_databases.side_effect = None
        service.list_databases.return_value = [row("new_context")]
        refresh_catalog(dialog)
        assert choices(dialog) == ["new_context"]
        new_contents = config.catalog_cache_path().read_bytes()
    finally:
        release_old.set()
        assert QThreadPool.globalInstance().waitForDone(2500)
        QApplication.processEvents()
    assert choices(dialog) == ["new_context"] and config.catalog_cache_path().read_bytes() == new_contents


def test_close_during_discovery_discards_late_results_and_post_list_probe(catalog):
    config, service, _, make = catalog
    started, release = Event(), Event()

    def stop_between():
        started.set()
        assert release.wait(timeout=5)
        return [row("must_not_publish")]

    service.list_databases.side_effect = stop_between
    dialog = make()
    try:
        dialog.show()
        assert started.wait(timeout=2)
        dialog.close()
    finally:
        release.set()
        assert QThreadPool.globalInstance().waitForDone(2500)
        QApplication.processEvents()
    assert not config.catalog_cache_path().is_file()
    service.catalog_revision.assert_called_once_with()


def test_discovery_connection_budget_and_cancelled_followup(catalog, monkeypatch):
    config, _, _, _ = catalog
    connect = Mock()
    monkeypatch.setattr("psycopg.connect", connect)
    pg = replace(config.saved_postgresql(), connect_timeout=300)
    reader = BoundedCatalogReader(config, pg, authorized=True, discovery_password="fixture-reader-only")
    cancel = Event()
    reader.bind_budget(cancel)
    reader._connect("postgres")
    options = conninfo_to_dict(connect.call_args.args[0])
    assert options["dbname"] == "postgres" and options["user"] == "fixture-runtime"
    assert int(options["connect_timeout"]) <= 30
    assert "default_transaction_read_only=on" in options["options"]
    assert "statement_timeout=" in options["options"] and "lock_timeout=" in options["options"]
    cancel.set()
    with pytest.raises(TimeoutError):
        reader._connect("must_not_connect")
    assert connect.call_count == 1
