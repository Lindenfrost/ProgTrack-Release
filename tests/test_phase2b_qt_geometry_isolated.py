"""Run the Qt geometry checks in a separate process.

Qt/PyQt can abort during interpreter teardown when combined with the
headless matplotlib/reportlab tests.  The product test itself is unchanged;
the subprocess keeps that platform cleanup isolated and makes the aggregate
suite deterministic.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
QT_TEST = ROOT / "tests" / "qt_phase2b_dialog_geometry.py"


def test_qt_dialog_geometry_in_isolated_process() -> None:
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(QT_TEST)],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + "\n" + result.stderr
