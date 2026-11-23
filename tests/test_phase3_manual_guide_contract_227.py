from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUIDES = {
    "en": "manual/ProgTrack_User_Guide - en.html",
    "de": "manual/ProgTrack_User_Guide - de.html",
    "it": "manual/ProgTrack_User_Guide - it.html",
    "ru": "manual/ProgTrack_User_Guide - ru.html",
}


class Phase3ManualGuideContract227Test(unittest.TestCase):
    def test_each_localized_guide_has_account_table_and_ordered_client_steps(self):
        for language, relative_path in GUIDES.items():
            with self.subTest(language=language):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                anchor = 'id="postgresql-client-setup"'
                start = source.find(anchor)
                self.assertGreaterEqual(start, 0, f"{language} guide lacks setup section")
                next_heading = source.find("<h4", start + len(anchor))
                section = source[start:next_heading if next_heading >= 0 else None]
                self.assertIn("<table", section, f"{language} guide lacks account table")
                self.assertIn("<ol", section, f"{language} guide lacks ordered client steps")


if __name__ == "__main__":
    unittest.main()
