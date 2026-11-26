"""Focused contract checks for the Phase 3 Linux release artifact.

The repository deliberately ignores this test directory in release payloads;
the checks protect the archive contract without exercising unrelated plugins.
"""

from __future__ import annotations

import hashlib
import json
import os
import tarfile
import unittest
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.release_package

_artifact_setting = os.environ.get("PROGTRACK_TEST_LINUX_PACKAGE")
ARTIFACT = Path(_artifact_setting).resolve() if _artifact_setting else None
CHECKSUM = ARTIFACT.with_name(ARTIFACT.name + ".sha256") if ARTIFACT else None


class LinuxReleasePackagingContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if ARTIFACT is None:
            raise unittest.SkipTest(
                "set PROGTRACK_TEST_LINUX_PACKAGE to the assembled 0.3.0 candidate; "
                "source-local build artifacts are not accepted"
            )
        if not ARTIFACT.is_file():
            raise FileNotFoundError(f"Configured Linux package candidate does not exist: {ARTIFACT}")
        if CHECKSUM is None or not CHECKSUM.is_file():
            raise FileNotFoundError(f"Candidate SHA-256 sidecar is missing: {CHECKSUM}")

    def test_checksum_and_manifest_are_self_consistent(self) -> None:
        digest = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
        self.assertIn(digest, CHECKSUM.read_text(encoding="utf-8"))
        with tarfile.open(ARTIFACT, "r:gz") as archive:
            names = archive.getnames()
            root = next(name for name in names if name.endswith("/release_metadata.json"))
            metadata = json.loads(archive.extractfile(root).read())
        self.assertEqual(metadata["artifact"], ARTIFACT.name)
        self.assertEqual(metadata["artifact_version"], "0.3.0")
        self.assertEqual(metadata["application_payload_version"], "0.3.0")
        self.assertTrue(metadata["native_runtime_bundled"])
        self.assertEqual(metadata["python_abi"], "cp313")
        self.assertIn("Psycopg binary", metadata["runtime_mode"])

    def test_archive_has_linux_payload_only(self) -> None:
        required = {
            "launcher/ProgTrack",
            "launcher/launcher.py",
            "launcher/progtrack.desktop",
            "ProgTrack.v.0.3.0.py",
            "Resources/Seed/progtrack_seed.ptdb",
            "icons/ui/manifest.json",
            "launcher/requirements-linux-bundled.txt",
            "release_metadata.json",
            "linux_component_inventory.json",
            "runtime/bin/python3",
            "runtime/lib/python3.13/site-packages/PyQt6",
            "runtime/lib/python3.13/site-packages/psycopg_binary",
            "fonts/matplotlib",
        }
        with tarfile.open(ARTIFACT, "r:gz") as archive:
            names = {Path(name).relative_to(Path(name).parts[0]).as_posix() for name in archive.getnames()}
            self.assertTrue(required.issubset(names))
            self.assertFalse(any(name.casefold().endswith((".exe", ".dll", ".pyd")) for name in names))
            for name in names:
                lowered = name.casefold()
                self.assertFalse(lowered.endswith((".exe", ".dll", ".pyd")), name)
                self.assertFalse(lowered.startswith("tests/") or lowered.startswith("test/"), name)
                self.assertNotIn("progtrackdata/", lowered)
                self.assertFalse(lowered.startswith("source/"), name)
            mode = archive.getmember(next(n for n in archive.getnames() if n.endswith("/launcher/ProgTrack"))).mode
            self.assertTrue(mode & 0o111)


if __name__ == "__main__":
    unittest.main()
