"""Keep canonical plugin display names stable across supported locales (#238)."""

from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
LOCALES = ("en", "de", "it", "ru")

PLUGIN_LABELS = {
    "Animal Reports": ("menu.tools.animal_reports",),
    "Medi Track": ("menu.tools.medi_track", "tab.medi_track"),
    "Project Track": ("menu.tools.projects_track", "tab.project_track"),
    "Cage Track": ("menu.tools.cage_track", "tab.cage_track"),
    "Heritage Track": (
        "menu.tools.heritage_track",
        "tab.heritage_track",
        "heritage_track.title",
    ),
    "Flow Track": ("menu.tools.flow_track", "tab.flow_track"),
    "Network Track": ("menu.tools.network_track",),
    "Embryo Track": ("menu.tools.embryo_tracker",),
    "Sample Track": ("menu.tools.sample_track",),
    "Steroid Track": ("menu.tools.steroid_track",),
}

PLUGIN_MANIFESTS = {
    "Animal Reports": "Animal_Reports",
    "Medi Track": "Medi_Track",
    "Project Track": "Projects_Track",
    "Cage Track": "Cage__Track",
    "Heritage Track": "Heritage_Track",
    "Flow Track": "Flow_Track",
    "Network Track": "Network_Track",
    "Embryo Track": "Embryo_Track",
    "Sample Track": "Sample_Track",
    "Steroid Track": "Steroid_track",
}

PERMISSION_NAMESPACES = {
    "__namespace.network": "Network Track",
    "__namespace.heritage": "Heritage Track",
    "__namespace.medi_track": "Medi Track",
    "__namespace.cage": "Cage Track",
    "__namespace.project": "Project Track",
    "__namespace.reports": "Animal Reports",
    "__namespace.embryo_track": "Embryo Track",
    "__namespace.sample_track": "Sample Track",
    "__namespace.flow_track": "Flow Track",
}


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class Issue238PluginNameTests(unittest.TestCase):
    def test_menu_navigation_and_plugin_title_keys_use_canonical_names(self):
        for locale in LOCALES:
            messages = _load_json(ROOT / "lang" / f"messages_{locale}.json")
            with self.subTest(locale=locale):
                for expected, keys in PLUGIN_LABELS.items():
                    for key in keys:
                        with self.subTest(key=key):
                            self.assertEqual(messages.get(key), expected)

                # This is localized prose, but the referenced plugin name is not.
                self.assertIn("Embryo Track", messages["embryo_track.error.missing_scipy"])

    def test_plugin_manifests_use_canonical_display_names(self):
        for expected, directory in PLUGIN_MANIFESTS.items():
            manifest = _load_json(ROOT / "Plugins" / directory / "manifest.json")
            with self.subTest(plugin=directory):
                self.assertEqual(manifest["display_name"], expected)
                self.assertEqual(manifest["name"], directory)

    def test_master_track_permission_category_names_use_canonical_names(self):
        labels = _load_json(ROOT / "Plugins" / "Master_Track" / "permissions_labels.json")
        self.assertEqual(set(labels), set(LOCALES))
        for locale in LOCALES:
            with self.subTest(locale=locale):
                for key, expected in PERMISSION_NAMESPACES.items():
                    self.assertEqual(labels[locale].get(key), expected, key)

    def test_animal_reports_window_title_fallback_is_the_plugin_name(self):
        source = (ROOT / "Plugins" / "Animal_Reports" / "animal_reports.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("plugin.animal_reports.title', 'Animal Report'", source)
        self.assertIn("plugin.animal_reports.title', 'Animal Reports'", source)


if __name__ == "__main__":
    unittest.main()
