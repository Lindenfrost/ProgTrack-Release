"""Run the order-sensitive #309 dialog test with a fresh Qt process."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEST_NODE = str(ROOT / "tests" / "test_phase023_core_repair_regressions.py") + (
    "::CoreRepairTests::test_309_effective_status_uses_authorized_checkbox_or_coerced_result"
)


def test_309_effective_status_dialog_in_isolated_process() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", str(TEST_NODE)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
