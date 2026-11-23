"""First-run demo consent and profile isolation."""
from unittest.mock import Mock

import pytest

from Plugins.core.backend import ProgTrackBackend
from Plugins.core.runtime_paths import BackendProfile
from tests.test_current_backend_runtime import runtime_paths


@pytest.mark.parametrize("answer", [False, None])
def test_decline_or_dismiss_is_per_database_and_never_prompted_again(tmp_path, answer):
    choice = Mock(return_value=answer)
    paths = runtime_paths(tmp_path)
    backend = ProgTrackBackend(paths, choose_demo_seed=choice)
    try:
        assert backend.adapter.get_installation_value("demo-seed-choice") is False
        assert backend.load_core_data()["animals"] == {}
    finally:
        backend.close()

    other_choice = Mock(return_value=True)
    backend = ProgTrackBackend(paths, choose_demo_seed=other_choice, bootstrap_seed=True)
    try:
        other_choice.assert_not_called()
        assert backend.load_core_data()["animals"] == {}
        assert backend.records.namespace_names() == []
    finally:
        backend.close()
    choice.assert_called_once()


def test_existing_database_without_choice_remains_empty(tmp_path):
    paths = runtime_paths(tmp_path)
    backend = ProgTrackBackend(paths, bootstrap_seed=True)
    backend.close()
    choice = Mock(return_value=True)
    backend = ProgTrackBackend(paths, choose_demo_seed=choice)
    try:
        choice.assert_not_called()
        assert backend.load_core_data()["animals"] == {}
    finally:
        backend.close()


def test_shared_guard_precedes_even_snapshot_or_adapter_access():
    backend = ProgTrackBackend.__new__(ProgTrackBackend)
    backend.paths = Mock(profile=BackendProfile.SHARED_POSTGRESQL)
    backend.adapter = Mock()
    backend.load_core_data = Mock(side_effect=AssertionError("unexpected snapshot"))
    backend._demo_seed_pending = True
    backend._bootstrap_seed_if_empty()
    backend.load_core_data.assert_not_called()
    assert backend.adapter.mock_calls == []


def test_failed_import_does_not_repeat_consent_or_import(tmp_path, monkeypatch):
    paths = runtime_paths(tmp_path)
    choice = Mock(return_value=True)
    owner = Mock(side_effect=RuntimeError("import failed"))
    monkeypatch.setattr(ProgTrackBackend, "_bootstrap_seed_if_empty", owner)
    with pytest.raises(RuntimeError, match="import failed"):
        ProgTrackBackend(paths, choose_demo_seed=choice)
    backend = ProgTrackBackend(paths, choose_demo_seed=choice)
    try:
        assert backend.adapter.get_installation_value("demo-seed-choice") is True
        assert backend.load_core_data()["animals"] == {}
    finally:
        backend.close()
    owner.assert_called_once()
    choice.assert_called_once()

def test_shared_constructor_never_invokes_demo_path_even_with_legacy_flag(tmp_path, monkeypatch):
    from dataclasses import replace

    paths = replace(runtime_paths(tmp_path), profile=BackendProfile.SHARED_POSTGRESQL)
    adapter = Mock()
    adapter.get_installation_value.return_value = str(paths.managed_root)
    monkeypatch.setattr("Plugins.core.backend.facade.create_adapter", Mock(return_value=adapter))
    choice = Mock(side_effect=AssertionError("unexpected prompt"))
    helper = Mock(side_effect=AssertionError("unexpected seed inspection"))
    monkeypatch.setattr(ProgTrackBackend, "_bootstrap_seed_if_empty", helper)
    for _ in range(2):
        backend = ProgTrackBackend(paths, choose_demo_seed=choice, bootstrap_seed=True)
        backend.close()
    choice.assert_not_called()
    helper.assert_not_called()
    assert adapter.require_current_schema.call_count == 2
    adapter.set_installation_value.assert_not_called()
