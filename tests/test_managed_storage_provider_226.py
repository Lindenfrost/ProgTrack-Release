"""Focused #226 storage-access tests. No server or share is contacted."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from Plugins.core.backend import managed_storage as storage


@pytest.mark.parametrize("username,password,expected", [
    ("", "", ("runtime", "runtime secret")),
    ("storage", "", ("storage", "runtime secret")),
    ("", "storage secret", ("runtime", "storage secret")),
    ("storage", "storage secret", ("storage", "storage secret")),
])
def test_independent_dsn_fallback(username, password, expected):
    result = storage.resolve_storage_credentials(
        "host=fixture user=runtime password='runtime secret'",
        username=username, password=password,
    )
    assert (result.username, result.password) == expected
    assert "secret" not in repr(result)


def test_local_root_is_checked_without_creating_anything(tmp_path):
    missing = tmp_path / "share"
    with pytest.raises(storage.ManagedStorageUnavailable) as failure:
        storage.require_storage_access(missing)
    assert failure.value.reason == "not_available"
    assert not missing.exists()
    assert storage.require_storage_access(tmp_path, writable=True) == tmp_path
    assert not list(tmp_path.iterdir())


def test_mounted_root_does_not_accept_bare_underlying_directory(tmp_path):
    bare = tmp_path / "mount"
    bare.mkdir()
    child = bare / "documents"
    child.mkdir()
    with pytest.raises(storage.ManagedStorageUnavailable) as failure:
        storage.require_storage_access(child, mount_anchor=bare)
    assert failure.value.reason == "mount_unavailable"


def test_windows_unc_passes_share_and_credential_to_provider(monkeypatch):
    from contextlib import nullcontext

    connector = Mock()
    monkeypatch.setattr(storage, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(storage, "_temporary_windows_connection", connector)
    monkeypatch.setattr(storage.os, "scandir", lambda path: nullcontext(iter(())))
    root = r"\\fixture\share\documents"
    if not Path(root).is_absolute():
        pytest.skip("Windows path semantics require a Windows runner")
    credentials = storage.StorageCredentials("storage-user", "secret")
    assert storage.require_storage_access(root, credentials=credentials) == Path(root)
    connector.assert_called_once_with(r"\\fixture\share", credentials)


@pytest.mark.parametrize("code,reason", [
    (1219, "credential_conflict"), (1326, "authentication_failed"), (53, "not_accessible"),
])
def test_wnet_errors_are_typed_and_no_disconnect(monkeypatch, code, reason):
    import ctypes

    connect = Mock(return_value=code)
    win_dll = Mock(return_value=SimpleNamespace(WNetAddConnection2W=connect))
    monkeypatch.setattr(ctypes, "WinDLL", win_dll, raising=False)
    with pytest.raises(storage.ManagedStorageUnavailable) as failure:
        storage._temporary_windows_connection(r"\\fixture\share", storage.StorageCredentials("user", "secret"))
    assert failure.value.reason == reason
    assert "secret" not in str(failure.value)
    win_dll.assert_called_once_with("mpr")
    assert connect.call_args.args[1:] == ("secret", "user", 4)


def test_non_windows_never_calls_network_credential_provider(tmp_path, monkeypatch):
    connector = Mock(side_effect=AssertionError("should use OS mount account"))
    monkeypatch.setattr(storage, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(storage, "_temporary_windows_connection", connector)
    storage.require_storage_access(tmp_path, credentials=storage.StorageCredentials("storage", "secret"))
    connector.assert_not_called()


def test_read_only_is_rejected_in_write_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(os, "access", lambda path, mode: False)
    with pytest.raises(storage.ManagedStorageUnavailable) as failure:
        storage.require_storage_access(tmp_path, writable=True)
    assert failure.value.reason == "read_only"
    assert storage.require_storage_access(tmp_path) == tmp_path

def test_client_local_profile_secret_is_independent_and_rollback_safe(tmp_path, monkeypatch):
    from Plugins.core import backend_configuration as config_module
    from test_phase3_postgresql_management import MemoryCredentials as MemoryCredentialStore, paths as runtime_paths

    monkeypatch.setattr(config_module, "sys", SimpleNamespace(platform="win32"))
    paths = runtime_paths(tmp_path)
    store = MemoryCredentialStore()
    config = config_module.BackendConfigurationService(paths, environ={}, credential_store=store)
    args = {
        "profile": config_module.BackendProfile.SHARED_POSTGRESQL,
        "sqlite_filename": "local.sqlite3",
        "postgresql": config_module.PostgreSQLSettings(
            host="fixture", database="active", user="runtime", managed_root=str(paths.managed_root),
        ),
        "password": "runtime-secret",
        "authorized": True,
    }
    config.save(**args, storage_username="storage", storage_password="storage-secret")
    old_document = paths.profile_file.read_bytes()
    assert b"secret" not in old_document
    assert config.read_storage_password() == "storage-secret"
    assert config.storage_credentials().username == "storage"
    assert config.storage_credentials().password == "storage-secret"

    with monkeypatch.context() as forced:
        forced.setattr(config_module.os, "replace", Mock(side_effect=OSError("sensitive detail")))
        with pytest.raises(config_module.BackendConfigurationValidationError) as failure:
            config.save(**(args | {"password": "replacement"}), storage_password="another")
        assert "sensitive detail" not in str(failure.value)
    assert paths.profile_file.read_bytes() == old_document
    assert config.read_password() == "runtime-secret"
    assert config.read_storage_password() == "storage-secret"
    config.save(**(args | {"password": ""}), storage_password="")
    assert config.storage_credentials().password == "runtime-secret"


def test_other_database_root_must_be_explicit_and_never_uses_admin_credentials(tmp_path, monkeypatch):
    from Plugins.core.backend.postgresql_admin import PostgreSQLAdministrationService
    from Plugins.core.backend_configuration import PostgreSQLSettings
    from Plugins.core.backend.errors import BackendConfigurationError
    from Plugins.core import managed_storage_worker

    config = Mock()
    config.environ = {}
    active = tmp_path / "active"
    explicit = tmp_path / "alternate"
    admin = PostgreSQLAdministrationService(config, PostgreSQLSettings(database="a", managed_root=str(active)),
                                            authorized=True)
    make_client = Mock()
    monkeypatch.setattr(managed_storage_worker, "ManagedStorageClient", make_client)
    with pytest.raises(BackendConfigurationError, match="client-local"):
        admin._target_storage("b")
    make_client.assert_not_called()
    admin._target_storage("b", explicit, writable=True)
    make_client.assert_called_once_with(config, explicit, runtime_dsn=None)
    make_client.return_value.check.assert_called_once_with(writable=True)


def test_unavailable_storage_stops_reconcile_before_metadata_changes(tmp_path):
    from Plugins.core.backend.managed_files import ManagedFileService

    access = Mock(side_effect=storage.ManagedStorageUnavailable("not_available"))
    adapter = Mock()
    service = ManagedFileService(adapter, tmp_path / "documents", tmp_path / "assets",
                                 create_roots=False, storage_access=access)
    assert service.reconcile() == {
        "status": "storage_unavailable",
        "reason": "not_available",
        "findings": [],
    }
    assert not list(tmp_path.iterdir())
    assert adapter.mock_calls == []
