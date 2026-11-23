"""Helpers for tests that need an isolated database initialized from the seed."""

from __future__ import annotations

import tempfile
from pathlib import Path

from Plugins.core.backend import ProgTrackBackend
from tests.test_current_backend_runtime import runtime_paths


def open_disposable_seed_backend() -> tuple[tempfile.TemporaryDirectory, ProgTrackBackend]:
    """Open the current bundled seed in a fresh SQLite database under temp."""
    temporary = tempfile.TemporaryDirectory(prefix="progtrack-seed-test-")
    try:
        backend = ProgTrackBackend(
            runtime_paths(Path(temporary.name)),
            acquire_process_lock=False,
            choose_demo_seed=lambda: True,
        )
    except BaseException:
        temporary.cleanup()
        raise
    return temporary, backend
