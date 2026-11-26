from pathlib import Path
import json
import unittest
from html.parser import HTMLParser


ROOT = Path(__file__).resolve().parents[1]
GUIDES = {
    "en": "manual/ProgTrack_User_Guide - en.html",
    "de": "manual/ProgTrack_User_Guide - de.html",
    "it": "manual/ProgTrack_User_Guide - it.html",
    "ru": "manual/ProgTrack_User_Guide - ru.html",
}
RETEST = ROOT / "tests/manual tests phase 3a.md"


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class TableRowExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.row = None
        self.cell = None
        self.cell_parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"}:
            self.cell = tag
            self.cell_parts = []

    def handle_data(self, data):
        if self.cell:
            self.cell_parts.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell == tag:
            self.row.append(" ".join(" ".join(self.cell_parts).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


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

    def test_each_guide_has_field_by_field_windows_and_ubuntu_example(self):
        field_keys = (
            "backend.profile.postgresql",
            "backend.postgresql.host",
            "backend.postgresql.port",
            "backend.postgresql.database",
            "backend.postgresql.user",
            "backend.postgresql.password",
            "backend.postgresql.sslmode",
            "backend.postgresql.server_name",
            "backend.postgresql.ca_file",
            "backend.postgresql.client_cert",
            "backend.postgresql.client_key",
            "backend.postgresql.client_key_passphrase",
            "backend.postgresql.timeout",
            "backend.postgresql.pool_min",
            "backend.postgresql.pool_max",
            "backend.storage.root",
            "backend.storage.credentials",
            "backend.storage.username",
            "backend.storage.password",
        )
        action_keys = (
            "backend.postgresql.refresh_databases",
            "backend.postgresql.use_database",
            "backend.postgresql.test",
        )
        shared_values = (
            "172.16.9.77",
            "5432",
            "progtrack_test_023",
            "progtrack_app",
            "verify-full",
            "ProgTrack-server",
            "progtrack-ca.crt",
            "smb_app",
            "10",
            "Pool minimum",
            "Pool maximum",
        )
        for language, relative_path in GUIDES.items():
            with self.subTest(language=language):
                source = (ROOT / relative_path).read_text(encoding="utf-8")
                messages = json.loads((ROOT / f"lang/messages_{language}.json").read_text(encoding="utf-8"))
                start = source.find('id="postgresql-client-setup"')
                heading = source.find("<h5", start)
                table_start = source.find('<table class="basic">', heading)
                table_end = source.find("</table>", table_start) + len("</table>")
                self.assertGreaterEqual(table_start, 0, f"{language} guide lacks the example table")
                table = source[table_start:table_end]
                section_end = source.find("<h4", heading)
                section = source[heading:section_end if section_end >= 0 else None]
                parser = TextExtractor()
                parser.feed(table)
                table_text = " ".join(" ".join(parser.parts).split())
                rows = TableRowExtractor()
                rows.feed(table)
                table_rows = rows.rows
                self.assertIn("Windows", table_text)
                self.assertIn("Ubuntu", table_text)
                exact_row_keys = (
                    "backend.postgresql.host",
                    "backend.postgresql.port",
                    "backend.postgresql.database",
                    "backend.postgresql.user",
                    "backend.postgresql.password",
                    "backend.postgresql.sslmode",
                    "backend.postgresql.server_name",
                    "backend.postgresql.ca_file",
                    "backend.postgresql.timeout",
                    "backend.postgresql.pool_min",
                    "backend.postgresql.pool_max",
                    "backend.storage.root",
                    "backend.storage.credentials",
                    "backend.storage.username",
                    "backend.storage.password",
                )
                for key in exact_row_keys:
                    self.assertIn(messages[key], [row[0] for row in table_rows], f"{language} first-column field label differs from UI: {key}")
                for key in (
                    "backend.postgresql.client_cert",
                    "backend.postgresql.client_key",
                    "backend.postgresql.client_key_passphrase",
                ):
                    self.assertTrue(any(messages[key] in row[0] for row in table_rows), f"{language} mTLS field label differs from UI: {key}")
                for value in shared_values:
                    self.assertIn(value, table_text, f"{language} example lacks {value}")
                for value in ("/srv/progtrack-managed", "documents", "config-assets"):
                    self.assertIn(value, section, f"{language} server-storage description lacks {value}")
                self.assertIn("progtrack_admin", section)
                for key in field_keys:
                    self.assertIn(messages[key], table_text, f"{language} table lacks UI label {key}")
                section_parser = TextExtractor()
                section_parser.feed(section)
                section_text = " ".join(" ".join(section_parser.parts).split())
                for key in action_keys:
                    self.assertIn(messages[key], section_text, f"{language} setup steps omit UI action {key}")
                self.assertIn(r"\172.16.9.77\progtrack-managed", table_text)
                self.assertIn("/mnt/progtrack", table_text)
                self.assertIn("mTLS", table_text)

    def test_phase_3a_uses_the_manual_as_the_fresh_client_value_source(self):
        retest = RETEST.read_text(encoding="utf-8")
        self.assertNotIn("## Shared server profile used by this retest", retest)
        self.assertIn("only source for the example connection values", retest)
        self.assertIn("P3A-01 must follow its Windows column", retest)
        self.assertIn("P3A-03 must follow its Ubuntu column", retest)
        self.assertIn("compare every visible backend dialog label and control", retest.casefold())
        self.assertIn("| #227 Localized PostgreSQL/SMB manuals and IT walkthrough | P3A-01, P3A-03, P3A-09 |", retest)


if __name__ == "__main__":
    unittest.main()
