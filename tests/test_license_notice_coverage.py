from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class LicenseNoticeCoverageTest(unittest.TestCase):
    def _assert_direct_files_are_named(self, directory: Path, notice: Path) -> None:
        notice_text = notice.read_text(encoding="utf-8")
        actual = {path.name for path in directory.iterdir() if path.is_file() and path.name != notice.name}
        missing = sorted(name for name in actual if name not in notice_text)
        self.assertFalse(missing, f"{notice}: unlisted files: {missing}")

    def test_area_notices_cover_direct_assets(self) -> None:
        self._assert_direct_files_are_named(ROOT / "icons", ROOT / "icons/LICENSE_NOTICE.md")
        self._assert_direct_files_are_named(
            ROOT / "Plugins/Resources", ROOT / "Plugins/Resources/LICENSE_NOTICE.md"
        )
        self._assert_direct_files_are_named(ROOT / "manual", ROOT / "manual/LICENSE_NOTICE.md")
        self._assert_direct_files_are_named(
            ROOT / "Plugins/PdG_converter/data",
            ROOT / "Plugins/PdG_converter/data/LICENSE_NOTICE.md",
        )

    def test_ui_manifest_and_linux_font_notice_are_governed(self) -> None:
        icon_notice = (ROOT / "icons/LICENSE_NOTICE.md").read_text(encoding="utf-8")
        ui_files = {path.name for path in (ROOT / "icons/ui").iterdir() if path.is_file()}
        missing_ui = sorted(name for name in ui_files if name not in icon_notice)
        self.assertFalse(missing_ui, f"icons/LICENSE_NOTICE.md: unlisted UI files: {missing_ui}")

        font_notice = ROOT / "source/launcher/linux/fonts/LICENSE_NOTICE.md"
        self.assertTrue(font_notice.is_file())
        font_text = font_notice.read_text(encoding="utf-8")
        for phrase in ("Basil K. Malyshev", "STI Pub Companies", "Bitstream", "SIL Open Font License"):
            self.assertIn(phrase, font_text)
        third_party_font_license = ROOT / "third_party_licenses/LICENSE_COMPUTER_MODERN_BAKOMA.txt"
        self.assertTrue(third_party_font_license.is_file())
        self.assertIn("Basil K. Malyshev", third_party_font_license.read_text(encoding="utf-8"))
        packager = (ROOT / "source/launcher/linux/package_linux_release.py").read_text(encoding="utf-8")
        self.assertIn("FONT_LICENSE_NOTICE", packager)
        self.assertIn('"fonts/LICENSE_NOTICE.md"', packager)


if __name__ == "__main__":
    unittest.main()
