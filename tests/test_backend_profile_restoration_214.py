import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from PyQt6.QtWidgets import QApplication

from Plugins.core.backend_configuration import BackendConfigurationService, PostgreSQLSettings
from Plugins.core.backend_configuration_dialog import BackendConfigurationDialog
from Plugins.core.backend.catalog_revision import CatalogRevision
from Plugins.core.backend.postgresql_admin import PostgreSQLDatabaseInfo
from test_phase3_postgresql_management import MemoryCredentials as MemoryCredentialStore, paths as runtime_paths
from test_backend_database_catalog_215_218 import await_catalog


POSTGRESQL = {
    "host": "db.example.test", "port": 5441, "database": "progtrack_test",
    "user": "facility_runtime", "sslmode": "verify-full",
    "server_name": "db.example.test", "ca_file": "ca.pem",
    "client_cert_file": "client.pem", "client_key_file": "client.key",
    "connect_timeout": 17, "pool_min": 2, "pool_max": 6,
}


def sample_dialog(tmp_path: Path, profile: str | None):
    paths = runtime_paths(tmp_path)
    local_path = tmp_path / "local-example" / "facility.sqlite3"
    pg = {**POSTGRESQL, "managed_root": str(tmp_path / "shared-documents")}
    document = {"sqlite_path": str(local_path), "postgresql": pg}
    if profile is not None:
        document["profile"] = profile
    original = json.dumps(document)
    paths.profile_file.write_text(original, encoding="utf-8")
    service = BackendConfigurationService(
        paths, environ={}, credential_store=MemoryCredentialStore()
    )
    service.credential_store.write(service.credential_target, "fixture-password")
    dialog = BackendConfigurationDialog(service, {}, authorized=True)
    return dialog, paths, local_path, pg, original


@pytest.mark.parametrize("profile,index", [
    ("shared_postgresql", 1), ("standalone_sqlite", 0),
    (None, 0), ("", 0), ("unknown", 0),
])
def test_saved_profile_page_is_restored_before_and_after_show(tmp_path, profile, index, monkeypatch):
    app = QApplication.instance() or QApplication([])
    dialog, paths, local_path, pg, original = sample_dialog(tmp_path, profile)
    catalog = Mock()
    catalog.catalog_revision.return_value = CatalogRevision("epoch", 1, False)
    catalog.list_databases.return_value = [PostgreSQLDatabaseInfo(pg["database"], "owner", 1, True, False, True)]
    monkeypatch.setattr(dialog, "_admin_service", Mock(return_value=catalog))
    monkeypatch.setattr(dialog.service, "catalog_cache_path", lambda: tmp_path / "catalog.json")
    try:
        assert dialog.pages.currentIndex() == index
        assert dialog.sqlite_radio.isChecked() == (index == 0)
        assert dialog.postgres_radio.isChecked() == (index == 1)
        dialog.show()
        await_catalog(dialog)
        app.processEvents()
        assert dialog.pages.currentIndex() == index
        assert dialog.sqlite_folder.text() == str(local_path.parent)
        assert dialog.sqlite_filename.text() == local_path.name
        assert dialog.postgresql_settings() == PostgreSQLSettings(**{**pg, "database": pg["database"] if index else ""})
        assert dialog.pg_password.text() == ""
        assert dialog.pg_client_key_passphrase.text() == ""
        assert paths.profile_file.read_text(encoding="utf-8") == original
    finally:
        dialog.close()
