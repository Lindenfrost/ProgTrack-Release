"""Service-level session replacement and account-change revocation contracts."""

from __future__ import annotations

import pytest

from Plugins.core.authorization import CanonicalUnitService
from Plugins.core.backend import ProgTrackBackend
from Plugins.core.backend.errors import SessionRevokedError
from tests.test_current_backend_runtime import runtime_paths


def _backend(tmp_path):
    return ProgTrackBackend(
        runtime_paths(tmp_path),
        acquire_process_lock=False,
        choose_demo_seed=lambda: False,
    )


def _require_session(backend, context):
    with backend.adapter.transaction() as connection:
        return backend.authenticated_sessions.require(connection, context)


def test_220_second_client_login_replaces_first_active_session(tmp_path):
    backend = _backend(tmp_path)
    try:
        first = backend.authenticated_sessions.create_first_account(
            "session-owner", "session-test-password"
        )
        replacement = backend.authenticated_sessions.login(
            "session-owner", "session-test-password", client_type="companion"
        )

        with pytest.raises(SessionRevokedError):
            _require_session(backend, first)
        assert _require_session(backend, replacement)["username"] == "session-owner"
    finally:
        backend.close()


def test_232_editing_an_account_revokes_its_current_session_and_audits_target(tmp_path):
    backend = _backend(tmp_path)
    try:
        administrator = backend.authenticated_sessions.create_first_account(
            "account-admin", "account-admin-password"
        )
        CanonicalUnitService(backend).create(
            "test-unit", "Test Unit", actor="account-admin", authorized=True
        )
        backend.account_commands.create(
            administrator,
            "test-user",
            "test-user-password",
            role="user",
            unit_id="test-unit",
        )
        target_session = backend.authenticated_sessions.login(
            "test-user", "test-user-password"
        )

        mutation = backend.account_commands.edit(
            administrator,
            "test-user",
            {"display_name": "Updated Test User"},
        )

        assert mutation.changed
        assert mutation.targets == ("test-user",)
        with pytest.raises(SessionRevokedError):
            _require_session(backend, target_session)
        events = [
            event for event in backend.audit.list_events()
            if event["action"] == "user_edited"
            and event["entity_type"] == "account"
            and event["entity_id"] == "test-user"
        ]
        assert events
        assert events[0]["payload"]["reason"] == "user_account_changed"
        assert events[0]["payload"]["revoked_session_id"] == target_session.session_id
    finally:
        backend.close()
