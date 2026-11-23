"""Focused regression checks for the mandatory startup authentication gate."""

from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPushButton

from Plugins.Master_Track.dialogs import LoginDialog
from Plugins.Master_Track.plugin import MasterTrackPlugin
from Plugins.core.backend_configuration import PostgreSQLSettings
from Plugins.core.backend.adapters import shared_connection_error
from Plugins.core.backend.errors import (
    BackendAuthenticationRejectedError,
    BackendConfigurationError,
    BackendUnavailableError,
)
from Plugins.core.local_shared_lord_status import (
    SharedLordStatusStore,
    local_user_state_root,
)
from Plugins.core.shared_recovery_dialog import SharedRecoveryDialog
from Plugins.core.startup_auth_gate import SharedStartupGate

ROOT = Path(__file__).resolve().parents[1]
_APP = None


def _app_module():
    name = "progtrack_issue_327_startup_gate"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(name, ROOT / "ProgTrack.v.0.3.0.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _windows_launcher_module():
    name = "progtrack_issue_327_windows_launcher"
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    path = ROOT / "source" / "launcher" / "windows" / "launcher.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _qapp():
    global _APP
    _APP = QApplication.instance() or QApplication([])
    return _APP


def test_startup_login_cancellation_does_not_fall_back_to_guest():
    plugin = object.__new__(MasterTrackPlugin)
    observed = {}
    plugin.login_interactive = lambda **kwargs: observed.update(kwargs) or False
    plugin._set_guest = lambda: observed.update(guest=True)

    assert plugin.startup() is False
    assert observed == {"save_session": False, "required": True}


def test_required_login_dialog_hides_guest_action():
    _qapp()
    dialog = LoginDialog(None, {}, object(), allow_guest=False)
    guest = next(button for button in dialog.findChildren(QPushButton)
                 if button.text() == "Continue as Guest")

    assert guest.isHidden()
    dialog.close()


def test_locked_shared_startup_exposes_only_retry_settings_and_exit():
    _qapp()
    gate = SharedStartupGate({}, on_retry=lambda: None,
                             on_settings=lambda: None, on_exit=lambda: None)
    labels = {button.text().casefold() for button in gate.findChildren(QPushButton)}

    assert labels == {"reconnect", "shared postgresql connection settings", "exit"}
    assert gate.retry_button.isHidden()
    assert gate.settings_button.isHidden()
    gate.set_state("unavailable", recovery_allowed=False)
    assert gate.retry_button.isHidden()
    assert gate.settings_button.isHidden()
    gate.set_state("unavailable", recovery_allowed=True)
    assert not gate.retry_button.isHidden()
    assert not gate.settings_button.isHidden()
    gate.set_state("authentication_rejected", recovery_allowed=True)
    assert gate.retry_button.isHidden()
    assert gate.settings_button.isHidden()
    gate.set_state("login_cancelled")
    assert not gate.retry_button.isHidden()
    assert gate.settings_button.isHidden()
    assert all("test connection" not in label for label in labels)
    gate.close()


def test_shared_lord_status_is_server_confirmed_minimal_and_cleared_for_other_roles():
    with tempfile.TemporaryDirectory() as root:
        store = SharedLordStatusStore(root)
        assert store.sync_from_server_account({
            "username": "lord.test", "role": "lord", "active": True,
            "password_hash": "must not be persisted", "salt": "must not be persisted",
        })
        cached = store.load()
        assert cached["username"] == "lord.test"
        assert cached["role"] == "lord"
        assert "password_hash" not in cached
        assert "salt" not in cached
        assert "password" not in store.path.read_text(encoding="utf-8").casefold()

        assert not store.sync_from_server_account({
            "username": "test.vet", "role": "user", "active": True,
        })
        assert store.load() is None


def test_shared_lord_status_file_is_owner_only_on_posix():
    if os.name != "posix":
        return
    with tempfile.TemporaryDirectory() as root:
        store = SharedLordStatusStore(root)
        assert store.sync_from_server_account({"username": "lord.test", "role": "lord"})
        assert store.directory.stat().st_mode & 0o777 == 0o700
        assert store.path.stat().st_mode & 0o777 == 0o600


def test_lord_status_uses_os_user_state_not_portable_install_state():
    windows_root = local_user_state_root(
        environ={"LOCALAPPDATA": r"C:\Users\TestUser\AppData\Local"},
        home=r"C:\Users\TestUser",
        platform="nt",
    )
    assert windows_root.parts[-2:] == ("ProgTrack", "state")
    assert "TestUser" in str(windows_root)

    linux_root = local_user_state_root(
        environ={"XDG_STATE_HOME": "/home/test-user/.local/state"},
        home="/portable/install",
        platform="posix",
    )
    assert linux_root == Path("/home/test-user/.local/state/ProgTrack")


def test_windows_fault_logs_are_pid_scoped_and_identify_process_start():
    launcher = _windows_launcher_module()
    log_path = launcher._fault_log_path(Path("logs"), pid=4321)
    assert log_path.name == "launcher_fault_4321.log"

    from io import StringIO

    output = StringIO()
    launcher._write_fault_log_header(output, pid=4321)
    header = output.getvalue()
    assert "pid=4321" in header
    assert "started_at=" in header
    assert "launcher_version=" in header


def test_postgresql_authentication_rejection_is_not_classified_as_outage():
    rejected = SimpleNamespace(sqlstate="28P01")
    missing_db = SimpleNamespace(sqlstate="3D000")
    network = SimpleNamespace(sqlstate="08006")
    unknown_transport = SimpleNamespace(sqlstate=None)

    assert isinstance(shared_connection_error(rejected), BackendAuthenticationRejectedError)
    assert isinstance(shared_connection_error(missing_db), BackendConfigurationError)
    assert not isinstance(shared_connection_error(missing_db), BackendUnavailableError)
    assert isinstance(shared_connection_error(network), BackendUnavailableError)
    assert isinstance(shared_connection_error(unknown_transport), BackendUnavailableError)


def test_offline_recovery_is_only_allowed_for_a_lord_and_an_outage():
    module = _app_module()
    harness = SimpleNamespace(
        _shared_connection_state="unavailable",
        _is_lord_session=lambda: False,
        _has_server_confirmed_lord_status=lambda: True,
    )
    assert module.ProgTrackApp._shared_profile_recovery_is_allowed(harness)

    harness._shared_connection_state = "authentication_rejected"
    assert not module.ProgTrackApp._shared_profile_recovery_is_allowed(harness)
    harness._shared_connection_state = "maintenance"
    assert not module.ProgTrackApp._shared_profile_recovery_is_allowed(harness)


def test_recovery_configuration_callback_fails_closed_for_ordinary_user():
    module = _app_module()
    harness = SimpleNamespace(_shared_profile_recovery_is_allowed=lambda: False)

    module.ProgTrackApp._show_shared_recovery_configuration(harness)


def test_cancelled_required_reauthentication_closes_locked_application():
    module = _app_module()
    observed = []
    master = SimpleNamespace(
        is_logged_in=False,
        login_interactive=lambda **kwargs: observed.append(kwargs) or False,
    )
    harness = SimpleNamespace(
        master_track=master,
        _batch_ui_updates=lambda: nullcontext(),
        close=lambda: observed.append("closed"),
    )

    module.ProgTrackApp._do_master_login(harness, required=True)

    assert observed == [{"save_session": False, "required": True}, "closed"]


def test_pre_auth_recovery_hides_server_test_action():
    _qapp()
    configuration = SimpleNamespace(
        saved_postgresql=lambda: PostgreSQLSettings(),
        environment_overrides=lambda: [],
        SSL_MODES=("require",),
        read_password=lambda: "",
        read_client_key_passphrase=lambda: "",
    )
    dialog = SharedRecoveryDialog(configuration, {}, None, allow_test=False)

    assert dialog.test.isHidden()
    dialog._test()  # Guard also prevents invoking the hidden action directly.
    dialog.close()


def test_schema_failure_status_is_reported_in_locked_gate():
    module = _app_module()
    observed = {}
    gate = SimpleNamespace(
        set_state=lambda state, detail=None, **kwargs: observed.update(
            state=state, detail=detail, **kwargs
        )
    )
    error = SimpleNamespace(reason="incompatible")
    error.__str__ = lambda: "Database core revision 2 is incompatible with required revision 4."
    harness = SimpleNamespace(
        _shared_startup_gate=gate,
        _shared_connection_state="maintenance",
        _shared_schema_error=error,
        _shared_connection_notice=None,
        _shared_profile_recovery_is_allowed=lambda: False,
        messages={"backend.schema.maintenance.incompatible": "Schema update required."},
    )

    module.ProgTrackApp._update_shared_connection_notice(harness)

    assert observed == {
        "state": "maintenance",
        "detail": "Schema update required.",
        "recovery_allowed": False,
    }


def test_shared_settings_and_data_load_wait_for_authenticated_connection():
    module = _app_module()
    events = []

    class StatusBar:
        def showMessage(self, *_args):
            pass

    harness = SimpleNamespace(
        _shared_connection_is_pending=lambda: True,
        _load_settings=lambda: None,
        _load_messages=lambda _lang: None,
        _init_application_state=lambda: events.append("init"),
        _load_persistence=lambda: events.append("data"),
        _refresh_ui_after_data_load=lambda: None,
        messages={},
        lang="en",
        statusBar=lambda: StatusBar(),
        setWindowTitle=lambda _title: None,
    )

    module.ProgTrackApp._load_settings_async(harness)
    module.ProgTrackApp._load_data_async(harness)

    assert events == []

    harness._shared_connection_is_pending = lambda: False
    harness._startup_authentication_cancelled = True
    module.ProgTrackApp._load_data_async(harness)
    assert events == []


def test_master_track_cannot_be_removed_by_persisted_plugin_disable_flag():
    module = _app_module()
    backend = SimpleNamespace(
        records=SimpleNamespace(get=lambda *_args, **_kwargs: ["master_track", "heritage_track"])
    )
    harness = SimpleNamespace(backend=backend)

    assert module.ProgTrackApp._load_disabled_plugins(harness) == {"heritage_track"}
