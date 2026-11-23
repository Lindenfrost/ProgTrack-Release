"""Disconnected Shared PostgreSQL startup must retain an empty shared shell."""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from Plugins.core.backend.errors import BackendUnavailableError
from Plugins.core.runtime_paths import BackendProfile
from Plugins.core.shared_connection import EmptySharedBackend
from tests.test_current_backend_runtime import runtime_paths

ROOT = Path(__file__).resolve().parents[1]


def _load_main_module():
    spec = importlib.util.spec_from_file_location(
        "progtrack_phase3_disconnected_startup_229",
        ROOT / "ProgTrack.v.0.3.0.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_229_empty_shared_shell_never_reads_or_creates_the_standalone_database(tmp_path):
    paths = runtime_paths(tmp_path)
    paths = replace(paths, profile=BackendProfile.SHARED_POSTGRESQL)
    backend = EmptySharedBackend(paths)

    snapshot = backend.load_core_data()

    assert snapshot == {"animals": {}, "archived_animals": {}, "settings": {}}
    assert not paths.database_path.exists()


def test_229_shared_data_reader_returns_empty_projection_without_sqlite_fallback():
    module = _load_main_module()
    backend = EmptySharedBackend(SimpleNamespace())
    harness = SimpleNamespace(
        _uses_shared_leases=lambda: True,
        master_track=SimpleNamespace(session_context=object()),
        backend=backend,
    )

    data = module.ProgTrackApp._read_json(harness)

    assert data["animals"] == {}
    assert data["archived_animals"] == {}
    assert data["settings"] == {}


def test_229_connection_failure_enters_database_unavailable_state():
    module = _load_main_module()
    transitions = []
    harness = SimpleNamespace(
        _shared_connection_state="connecting",
        _update_shared_connection_notice=lambda: transitions.append("updated"),
        _show_startup_gate=lambda: transitions.append("gate"),
    )

    module.ProgTrackApp._shared_failure(
        harness, BackendUnavailableError("PostgreSQL is not reachable")
    )

    assert harness._shared_connection_state == "unavailable"
    assert transitions == ["updated", "gate"]
