"""Block 4 documentation, localization, manifest, and release-truth checks."""

from __future__ import annotations

import hashlib
import html.parser
import json
import re
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LANG = ROOT / "lang"
PLUGIN_ROOT = ROOT / "Plugins"
WINDOWS_ARCHIVE = ROOT / "source" / "launcher" / "windows" / "release" / "ProgTrack-0.2.1.zip"


class _LinkCollector(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []
        self.images: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag.lower() == "a" and attrs_dict.get("href"):
            self.links.append(str(attrs_dict["href"]))
        if tag.lower() == "img" and attrs_dict.get("src"):
            self.images.append(str(attrs_dict["src"]))


class Block4DocumentationReleaseTest(unittest.TestCase):
    def test_catalogs_have_exact_key_and_placeholder_parity(self) -> None:
        catalogs = {
            path.name: json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(LANG.glob("messages_*.json"))
        }
        self.assertEqual(set(catalogs), {"messages_de.json", "messages_en.json", "messages_it.json", "messages_ru.json"})
        reference = catalogs["messages_en.json"]
        reference_keys = set(reference)
        for filename, catalog in catalogs.items():
            self.assertEqual(set(catalog), reference_keys, filename)
            for key in reference_keys:
                expected = sorted(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", str(reference[key])))
                actual = sorted(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", str(catalog[key])))
                self.assertEqual(actual, expected, f"placeholder mismatch: {filename}:{key}")
        # A removed key must be rejected by the same contract.
        probe = dict(catalogs["messages_it.json"])
        probe.pop(next(iter(reference_keys)))
        self.assertNotEqual(set(probe), reference_keys)

    def test_manifests_are_release_versioned_and_documented(self) -> None:
        manifests = sorted(PLUGIN_ROOT.glob("*/manifest.json"))
        self.assertEqual(len(manifests), 13)
        # Plugin manifests carry their own compatibility metadata.  Most
        # plugins remain at the 0.2.1 contract while Heritage Track was
        # independently revised to 0.2.3; derive the expected value from the
        # documented inventory instead of retaining the obsolete release-wide
        # 0.2.1 assumption.
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        documented_versions = {}
        for line in readme.splitlines():
            match = re.match(r"\| ([^|]+) \| `([^`]+)` \|", line)
            if match:
                documented_versions[match.group(1).strip()] = match.group(2)
        for path in manifests:
            data = json.loads(path.read_text(encoding="utf-8"))
            expected_version = documented_versions.get(data["display_name"])
            self.assertIsNotNone(expected_version, path.as_posix())
            self.assertEqual(data["version"], expected_version, path.as_posix())
            minimum = tuple(int(part) for part in data["min_progtrack_version"].split("."))
            self.assertEqual(len(minimum), 3, path.as_posix())
            self.assertLessEqual(minimum, (0, 3, 0), path.as_posix())
            self.assertTrue(data["features"])
            self.assertEqual(data["features"], data["capabilities"])
            self.assertTrue(data["docs"], path.as_posix())
            for link in data["docs"]:
                target = str(link).split("#", 1)[0]
                self.assertTrue((path.parent / target).resolve().is_file(), f"{path}:{link}")
            module_name = str(data["entry_point"]).split(".", 1)[0]
            self.assertTrue((path.parent / f"{module_name}.py").is_file(), path.as_posix())

    def test_readme_inventory_matches_every_manifest(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("| Plugin | Version | Kind | Purpose |", text)
        documented_versions = {}
        for line in text.splitlines():
            match = re.match(r"\| ([^|]+) \| `([^`]+)` \|", line)
            if match:
                documented_versions[match.group(1).strip()] = match.group(2)
        display_names = {
            json.loads(path.read_text(encoding="utf-8"))["display_name"]
            for path in PLUGIN_ROOT.glob("*/manifest.json")
        }
        for display_name in display_names:
            row = next((line for line in text.splitlines() if line.startswith(f"| {display_name} |")), "")
            self.assertRegex(row, rf"\| `{re.escape(documented_versions[display_name])}` \|", display_name)
        self.assertIn("organizational unit or workgroup", text)
        self.assertIn("Animal Reports", text)

    def test_localized_guides_have_valid_local_links_and_scope_terms(self) -> None:
        legacy_manual_symbols = "♀♂⚤➕✏🫥📈📉🎈⚖📦⤴🗑👶🐾💡"
        expected_plugins = {
            # The English guide keeps the established user-facing singular "Project Track" label; the manifest inventory uses "Projects Track". Both refer to the same shipped plugin.
            "en": ["Steroid Track", "Master Track", "Animal Reports", "Medi Track", "Surgery Planner (OP Planner)", "Embryo Tracker", "Flow Track", "Heritage Track", "Cage Track", "Sample Track", "Project Track", "Network Track"],
            "de": ["Steroid Track", "Master Track", "Tierberichte", "Medi Track", "OP-Planner", "Embryo Track", "Flow Track", "Heritage Track", "Käfig-Übersicht", "Sample Track", "Projekt Track", "Network Track"],
            "it": ["Steroid Track", "Master Track", "Report animali", "Medi Track", "Pianificatore operazioni", "Embryo Track", "Flow Track", "Heritage Track", "Gestione Gabbie", "Sample Track", "Project Track", "Network Track"],
            "ru": ["Steroid Track", "Master Track", "Отчеты о животных", "Medi Track", "Планировщик операций", "Embryo Track", "Flow Track", "Heritage Track", "Клетки", "Sample Track", "Project Track", "Network Track"],
        }
        for path in sorted((ROOT / "manual").glob("ProgTrack_User_Guide - *.html")):
            parser = _LinkCollector()
            text = path.read_text(encoding="utf-8")
            parser.feed(text)
            self.assertNotIn("\ufffd", text, path.name)
            for symbol in legacy_manual_symbols:
                self.assertNotIn(symbol, text, f"legacy manual symbol {symbol!r}: {path.name}")
            lang = path.stem.rsplit(" - ", 1)[-1]
            for plugin in expected_plugins[lang]:
                self.assertIn(plugin, text, f"{plugin} missing from {path.name}")
            for source in parser.images:
                target = (path.parent / source.split("#", 1)[0]).resolve()
                self.assertTrue(target.is_file(), f"{path.name}:{source}")
            for href in parser.links:
                if href.startswith(("http:", "https:", "mailto:", "#")):
                    continue
                target_text, _, fragment = href.partition("#")
                target = (path.parent / target_text).resolve()
                self.assertTrue(target.is_file(), f"{path.name}:{href}")
                if fragment:
                    target_html = target.read_text(encoding="utf-8", errors="replace")
                    self.assertRegex(target_html, rf"(?:id|name)=['\"]{re.escape(fragment)}['\"]", f"{path.name}:{href}")
            self.assertRegex(text, r"Reports|Animal Reports")
            for removed_label in (
                "<strong>Begriffe:</strong>",
                "<strong>Terminology:</strong>",
                "<strong>Terminologia:</strong>",
                "<strong>Термины:</strong>",
            ):
                self.assertNotIn(removed_label, text, f"removed terminology paragraph remains in {path.name}")

    def test_windows_archive_contains_current_documentation_and_manifests(self) -> None:
        self.assertTrue(WINDOWS_ARCHIVE.is_file(), "build the local Windows artifact before release acceptance")
        digest = hashlib.sha256(WINDOWS_ARCHIVE.read_bytes()).hexdigest()
        checksum = WINDOWS_ARCHIVE.with_name(WINDOWS_ARCHIVE.name + ".sha256")
        self.assertIn(digest, checksum.read_text(encoding="utf-8"))
        with zipfile.ZipFile(WINDOWS_ARCHIVE) as archive:
            names = set(archive.namelist())
            root = "ProgTrack-0.2.1/"
            required = {
                root + "README.md", root + "ProgTrack.v.0.2.1.py", root + "lang/messages_en.json",
                root + "manual/ProgTrack_User_Guide - en.html", root + "Plugins/Animal_Reports/manifest.json",
                root + "Plugins/Surgery_Planner/manifest.json", root + "Resources/Seed/progtrack_seed.ptdb",
            }
            self.assertTrue(required.issubset(names))
            packaged_readme = archive.read(root + "README.md").decode("utf-8")
            self.assertIn("| Plugin | Version | Kind | Purpose |", packaged_readme)
            packaged_en = json.loads(archive.read(root + "lang/messages_en.json"))
            packaged_de = json.loads(archive.read(root + "lang/messages_de.json"))
            self.assertEqual(set(packaged_en), set(packaged_de))
            for name in ("Animal_Reports", "Cage__Track", "Flow_Track", "Surgery_Planner"):
                manifest = json.loads(archive.read(root + f"Plugins/{name}/manifest.json"))
                self.assertEqual(manifest["version"], "0.2.1")
                self.assertEqual(manifest["min_progtrack_version"], "0.2.1")


if __name__ == "__main__":
    unittest.main()
