from __future__ import annotations

import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "ProgTrack.v.0.3.0.py"


class Issue244VisualStyleCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("progtrack_issue_244", MAIN)
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    def make_dialog_harness(self, roles, custom_definitions=None):
        module = self.module
        harness = module.StyleSettingsDialog.__new__(module.StyleSettingsDialog)
        harness.parent_app = SimpleNamespace(
            _active_custom_event_definitions_for_role=lambda _role, mode="edit": (
                list(custom_definitions or [])
            ),
        )
        harness.messages = {
            "event.surgery": "Surgery",
            "event.fsh": "FSH",
            "event.progesterone": "Progesterone",
            "event.biopsy": "Biopsy",
        }
        harness._role_definitions = roles
        harness._custom_event_definitions = list(custom_definitions or [])
        return harness

    def test_catalog_deduplicates_role_events_and_includes_symbol_metadata(self):
        custom = {
            "id": "custom-event:biopsy-v1",
            "stable_id": "custom-event:biopsy-v1",
            "event_type": "biopsy",
            "name": "Biopsy",
            "render_mode": "symbol",
            "default_marker": "D",
            "default_color": "#123456",
            "active": True,
        }
        dialog = self.make_dialog_harness(
            [
                {"value": "donor", "event_recipe": {"available_events": ["surgery", "fsh"]}},
                {"value": "offspring", "event_recipe": {"available_events": ["surgery", "progesterone"]}},
            ],
            [custom],
        )

        catalog = dialog._build_event_style_catalog()
        by_type = {entry["event_type"]: entry for entry in catalog}

        self.assertEqual(set(by_type), {"biopsy", "fsh", "progesterone", "surgery"})
        self.assertEqual(by_type["surgery"]["render_mode"], "line")
        self.assertEqual(by_type["fsh"]["render_mode"], "symbol")
        self.assertEqual(by_type["progesterone"]["render_mode"], "symbol")
        self.assertEqual(by_type["biopsy"]["default_marker"], "D")
        self.assertEqual(len([entry for entry in catalog if entry["event_type"] == "surgery"]), 1)

    def test_normalizer_migrates_custom_definition_ids_to_event_type(self):
        host = self.module.ProgTrackApp.__new__(self.module.ProgTrackApp)
        host._custom_event_definitions = lambda include_retired=True: [
            {
                "id": "custom-event:biopsy-v1",
                "stable_id": "custom-event:biopsy-stable",
                "event_type": "biopsy",
            }
        ]

        normalized = host._normalize_style_settings(
            {
                "custom_events": {
                    "custom-event:biopsy-v1": {"color": "#abcdef", "marker": "D"},
                }
            }
        )

        self.assertNotIn("custom-event:biopsy-v1", normalized["custom_events"])
        self.assertEqual(
            normalized["custom_events"]["biopsy"],
            {"color": "#abcdef", "marker": "D"},
        )

    def test_appearance_uses_canonical_override_and_builtin_symbol_defaults(self):
        host = self.module.ProgTrackApp.__new__(self.module.ProgTrackApp)
        host.custom_event_styles = {
            "progesterone": {"color": "#123456", "marker": "s"}
        }
        host._custom_event_definition_for_type = lambda _event_type: {}

        appearance = host._custom_event_appearance("progesterone")

        self.assertEqual(appearance["color"], "#123456")
        self.assertEqual(appearance["marker"], "s")
        self.assertEqual(appearance["render_mode"], "symbol")
        self.assertTrue(appearance["configured"])

    def test_issue_303_builtin_defaults_feed_catalog_and_plot_resolver(self):
        definitions = [
            {
                "event_type": "birth",
                "name": "Birth",
                "default_color": "#000000",
                "active": True,
            },
            {
                "event_type": "embryo_transfer",
                "name": "Embryo transfer",
                "default_color": "#000000",
                "active": True,
            },
        ]
        dialog = self.make_dialog_harness(
            [
                {
                    "value": "female",
                    "event_recipe": {
                        "available_events": ["birth", "embryo_transfer"]
                    },
                }
            ],
            definitions,
        )

        catalog = {
            entry["event_type"]: entry
            for entry in dialog._build_event_style_catalog()
        }
        expected = {"birth": "#ead700", "embryo_transfer": "#2feade"}
        for event_type, color in expected.items():
            with self.subTest(event_type=event_type, surface="catalog"):
                self.assertEqual(catalog[event_type]["default_color"], color)

        host = self.module.ProgTrackApp.__new__(self.module.ProgTrackApp)
        host.custom_event_styles = {}
        definitions_by_type = {
            item["event_type"]: item for item in definitions
        }
        host._custom_event_definition_for_type = (
            lambda event_type: definitions_by_type.get(event_type)
        )
        for event_type, color in expected.items():
            with self.subTest(event_type=event_type, surface="plot resolver"):
                appearance = host._custom_event_appearance(event_type)
                self.assertEqual(appearance["color"], color)
        self.assertEqual(
            self.module.event_style_metadata("surgery")["default_color"],
            "#0000FF",
        )

    def test_issue_303_reset_to_defaults_uses_catalog_defaults(self):
        class ColorButton:
            def __init__(self):
                self.properties = {}

            def setStyleSheet(self, _stylesheet):
                pass

            def setProperty(self, key, value):
                self.properties[key] = value

            def property(self, key):
                return self.properties.get(key)

        definitions = [
            {"event_type": "birth", "default_color": "#000000"},
            {"event_type": "embryo_transfer", "default_color": "#000000"},
        ]
        style_dialog = self.make_dialog_harness(
            [
                {
                    "value": "female",
                    "event_recipe": {
                        "available_events": ["birth", "embryo_transfer"]
                    },
                }
            ],
            definitions,
        )
        catalog = {
            entry["event_type"]: entry
            for entry in style_dialog._build_event_style_catalog()
        }
        reset_dialog = self.module.StyleSettingsDialog.__new__(
            self.module.StyleSettingsDialog
        )
        reset_dialog.parent_app = SimpleNamespace(
            _get_default_style_settings=lambda: {}
        )
        reset_dialog.color_buttons = {}
        reset_dialog.marker_combos = {}
        buttons = {key: ColorButton() for key in catalog}
        reset_dialog._custom_event_style_controls = {
            key: (catalog[key], button, None)
            for key, button in buttons.items()
        }

        reset_dialog._reset_to_defaults()

        self.assertEqual(buttons["birth"].property("color"), "#ead700")
        self.assertEqual(
            buttons["embryo_transfer"].property("color"), "#2feade"
        )

    def test_issue_303_explicit_user_overrides_remain_effective(self):
        host = self.module.ProgTrackApp.__new__(self.module.ProgTrackApp)
        host._custom_event_definitions = lambda include_retired=True: []
        normalized = host._normalize_style_settings(
            {
                "custom_events": {
                    "birth": {"color": "#123456"},
                    "embryo_transfer": {"color": "#654321"},
                }
            }
        )
        host.custom_event_styles = normalized["custom_events"]
        host._custom_event_definition_for_type = lambda _event_type: None

        self.assertEqual(
            host._custom_event_appearance("birth")["color"], "#123456"
        )
        self.assertEqual(
            host._custom_event_appearance("embryo_transfer")["color"],
            "#654321",
        )
        self.assertEqual(
            normalized["custom_events"],
            {
                "birth": {"color": "#123456"},
                "embryo_transfer": {"color": "#654321"},
            },
        )

    def test_appearance_does_not_read_removed_event_attribute_fallbacks(self):
        host = self.module.ProgTrackApp.__new__(self.module.ProgTrackApp)
        host.custom_event_styles = {}
        host.progesterone_color = self.module.QColor("#123456")
        host.progesterone_marker = "D"
        host._custom_event_definition_for_type = lambda _event_type: {}

        appearance = host._custom_event_appearance("progesterone")

        self.assertEqual(appearance["color"], "#008000")
        self.assertEqual(appearance["marker"], "v")

    def test_style_migration_maps_old_event_fields_once_to_canonical_keys(self):
        host = self.module.ProgTrackApp.__new__(self.module.ProgTrackApp)
        host._custom_event_definitions = lambda include_retired=True: []

        normalized = host._normalize_style_settings({
            "op_color": "#abcdef",
            "fsh_color": "#123456",
            "fsh_marker": "D",
        })

        self.assertNotIn("op_color", normalized)
        self.assertNotIn("fsh_color", normalized)
        self.assertEqual(normalized["custom_events"]["surgery"]["color"], "#abcdef")
        self.assertEqual(normalized["custom_events"]["fsh"], {
            "color": "#123456",
            "marker": "D",
        })


if __name__ == "__main__":
    unittest.main()
