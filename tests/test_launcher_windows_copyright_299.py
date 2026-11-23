from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_COPYRIGHT = "Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH"


class WindowsLauncherCopyright299Test(unittest.TestCase):
    def test_version_resource_contains_approved_field_and_is_wired_into_spec(self):
        version_resource = (ROOT / "source/launcher/windows/launcher_version_info.txt").read_text(
            encoding="utf-8"
        )
        expected_entry = f"StringStruct('LegalCopyright', '{EXPECTED_COPYRIGHT}')"
        self.assertIn(expected_entry, version_resource)

        spec = (ROOT / "source/launcher/windows/launcher_small.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "version=str(version_info_path) if version_info_path.exists() else None",
            spec,
        )

    def test_candidate_exe_exposes_copyright_in_windows_version_resource(self):
        if sys.platform != "win32":
            self.skipTest("Windows PE version-resource inspection requires Windows.")
        executable = os.environ.get("PROGTRACK_TEST_WINDOWS_LAUNCHER")
        if not executable:
            self.skipTest(
                "Set PROGTRACK_TEST_WINDOWS_LAUNCHER to the extracted candidate Launcher.exe."
            )
        path = Path(executable)
        self.assertTrue(path.is_file(), f"Candidate launcher does not exist: {path}")

        command = (
            "$v=[System.Diagnostics.FileVersionInfo]::GetVersionInfo("
            "$env:PROGTRACK_TEST_WINDOWS_LAUNCHER).LegalCopyright; "
            "[Console]::Out.Write($v)"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), EXPECTED_COPYRIGHT)


if __name__ == "__main__":
    unittest.main()
