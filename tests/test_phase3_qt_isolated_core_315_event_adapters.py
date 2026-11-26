"""Run the six-adapter event-save regression in a clean Qt process."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_NODE = str(ROOT / "tests" / "test_phase023_core_repair_regressions.py") + (
    "::CoreRepairTests::test_315_real_new_event_save_all_five_adapters_and_experimental_offspring"
)


def test_315_event_adapters_in_isolated_process() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", TEST_NODE],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
