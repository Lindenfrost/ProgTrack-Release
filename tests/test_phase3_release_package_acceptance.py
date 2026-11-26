"""Candidate-package checks for Phase 3 issues #48 and #50.

These checks run only through ``tests/run_phase3_package_acceptance.py`` and
against an extracted 0.3.0 package selected by the runner. They intentionally
stay out of the normal source-tree test run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
_package_root_value = os.environ.get("PROGTRACK_TEST_PACKAGE_ROOT")
PACKAGE_ROOT = Path(_package_root_value or ROOT).resolve()

PLUGIN_FOLDERS = {
    "Animal Reports": "Animal_Reports",
    "Cage Track": "Cage__Track",
    "Embryo Track": "Embryo_Track",
    "Flow Track": "Flow_Track",
    "Heritage Track": "Heritage_Track",
    "Master Track": "Master_Track",
    "Medi Track": "Medi_Track",
    "Network Track": "Network_Track",
    "PdG converter": "PdG_converter",
    "Projects Track": "Projects_Track",
    "Sample Track": "Sample_Track",
    "Steroid Track": "Steroid_track",
    "Surgery Planner": "Surgery_Planner",
}


@pytest.mark.release_package
def test_48_assembled_package_contains_the_plugins_in_the_approved_matrix():
    assert _package_root_value, "Run through tests/run_phase3_package_acceptance.py."
    matrix = (
        ROOT
        / "manual/technical documentation/architecture/Phase 2A backend migration architecture"
        / "02 Plugin capability and storage matrix.md"
    ).read_text(encoding="utf-8")
    section = matrix.split("## Summary matrix", 1)[1].split("\n## ", 1)[0]
    matrix_plugins = set()
    for line in section.splitlines():
        if not line.startswith("|") or line.count("|") < 3:
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        if columns[0] in {"Plugin", "---"}:
            continue
        # The summary matrix also has cross-cutting capability rows. Plugin
        # rows identify a concrete implementation path as inline code in the
        # owner/entry-point column.
        if columns[1].startswith("`"):
            matrix_plugins.add(columns[0])
    assert matrix_plugins == set(PLUGIN_FOLDERS), (
        "Update PLUGIN_FOLDERS to match the approved #48 architecture matrix."
    )

    missing = [
        folder for folder in PLUGIN_FOLDERS.values()
        if not (PACKAGE_ROOT / "Plugins" / folder).is_dir()
    ]
    assert not missing, f"Candidate package omits #48 matrix plugin folders: {missing}"
    assert (PACKAGE_ROOT / "Plugins/core/backend").is_dir()
    inventory = PACKAGE_ROOT / "release_inventory.json"
    assert inventory.is_file()
    assert json.loads(inventory.read_text(encoding="utf-8"))["application_version"] == "0.3.0"
    assert (PACKAGE_ROOT / "Resources/Seed/progtrack_seed.ptdb").is_file()
    assert (PACKAGE_ROOT / "ProgTrack.v.0.3.0.py").is_file()
    assert len(list(PACKAGE_ROOT.glob("ProgTrack.v.*.py"))) == 1


@pytest.mark.release_package
def test_50_backend_imports_and_runtime_paths_resolve_from_candidate_package():
    assert _package_root_value, "Run through tests/run_phase3_package_acceptance.py."
    import Plugins.core.backend as backend_package
    import Plugins.core.runtime_paths as runtime_paths_module

    backend_path = Path(backend_package.__file__).resolve()
    runtime_path = Path(runtime_paths_module.__file__).resolve()
    assert backend_path.is_relative_to(PACKAGE_ROOT), backend_path
    assert runtime_path.is_relative_to(PACKAGE_ROOT), runtime_path
