import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Plugins.core import platform_helpers


REPO_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_LAUNCHER = REPO_ROOT / "source" / "launcher" / "windows" / "launcher.py"


class LinuxSourceCompatibilityPreflightTest(unittest.TestCase):
    def test_default_export_directory_falls_back_when_desktop_is_missing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            documents = home / "Documents"
            documents.mkdir()

            with patch.object(platform_helpers, "_qt_writable_location", return_value=None):
                self.assertEqual(platform_helpers.default_export_directory(home=home), documents)

    def test_default_export_directory_prefers_existing_desktop_fallback(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            desktop = home / "Desktop"
            desktop.mkdir()
            documents = home / "Documents"
            documents.mkdir()

            with patch.object(platform_helpers, "_qt_writable_location", return_value=None):
                self.assertEqual(platform_helpers.default_export_directory(home=home), desktop)

    def test_default_save_path_uses_export_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)

            with patch.object(platform_helpers, "_qt_writable_location", return_value=None):
                self.assertEqual(
                    platform_helpers.default_save_path("report.pdf", home=home),
                    str(home / "report.pdf"),
                )

    def test_referenced_pdf_icon_exists_with_exact_case(self):
        pdf_icon = REPO_ROOT / "icons" / "file_pdf.png"

        self.assertTrue(platform_helpers.exact_case_path_exists(pdf_icon))
        self.assertFalse(platform_helpers.exact_case_path_exists(REPO_ROOT / "icons" / "file_pdg.png"))

    def test_medi_track_uses_existing_pdf_icon_reference(self):
        source = (REPO_ROOT / "Plugins" / "Medi_Track" / "medi_track_widget.py").read_text(encoding="utf-8")

        self.assertIn("file_pdf.png", source)
        self.assertNotIn("file_pdg.png", source)

    def test_frozen_launcher_sets_windows_qt_platform_only_on_windows(self):
        source = WINDOWS_LAUNCHER.read_text(encoding="utf-8")

        self.assertIn('if sys.platform.startswith("win"):', source)
        self.assertIn('os.environ.setdefault("QT_QPA_PLATFORM", "windows")', source)

    def test_launcher_discovers_highest_progtrack_version(self):
        spec = importlib.util.spec_from_file_location(
            "progtrack_launcher_test", WINDOWS_LAUNCHER
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            for version in ("0.1.1", "0.1.2", "0.1.10"):
                (root / f"ProgTrack.v.{version}.py").touch()
            self.assertEqual(
                "ProgTrack.v.0.1.10.py",
                module.discover_default_script(root).name,
            )


if __name__ == "__main__":
    unittest.main()
