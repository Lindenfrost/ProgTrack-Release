import json
import re
import unittest
from pathlib import Path

from Plugins.Master_Track.permissions import (
    ALL_PERMISSIONS,
    INTERNAL_PERMISSIONS,
    ROLE_LORD,
    ROLE_MASTER,
    ROLE_USER,
    can,
)


ROOT = Path(__file__).resolve().parents[1]


class MasterTrackPermissionCatalogTest(unittest.TestCase):
    def test_identity_is_catalogued_and_master_toggle_is_internal_lord_only(self):
        catalog = set(ALL_PERMISSIONS)

        self.assertIn("core.edit_animal_identity", catalog)
        self.assertNotIn("toggle_master_track", catalog)
        self.assertIn("toggle_master_track", INTERNAL_PERMISSIONS)
        self.assertFalse(can(ROLE_MASTER, [], [], [], "toggle_master_track"))
        self.assertTrue(can(ROLE_LORD, [], [], [], "toggle_master_track"))
        self.assertFalse(can(ROLE_USER, [], ["toggle_master_track"], [], "toggle_master_track"))

    def test_permission_labels_cover_full_catalog_in_all_languages(self):
        labels = json.loads(
            (ROOT / "Plugins/Master_Track/permissions_labels.json").read_text(encoding="utf-8")
        )
        catalog = set(ALL_PERMISSIONS)

        self.assertEqual({"en", "de", "it", "ru"}, set(labels))
        for lang, lang_labels in labels.items():
            with self.subTest(lang=lang):
                self.assertFalse(catalog - set(lang_labels))
                self.assertNotIn("toggle_master_track", lang_labels)

    def test_jobs_json_contains_only_catalogued_permissions(self):
        jobs = json.loads((ROOT / "Plugins/Master_Track/jobs.json").read_text(encoding="utf-8"))
        catalog = set(ALL_PERMISSIONS)

        unknown = {
            permission
            for permissions in jobs.values()
            for permission in permissions
            if permission not in catalog
        }
        self.assertEqual(set(), unknown)

    def test_manager_job_has_phase1_visibility_and_role_setup_permissions(self):
        jobs = json.loads((ROOT / "Plugins/Master_Track/jobs.json").read_text(encoding="utf-8"))
        manager_permissions = set(jobs.get("manager", []))

        self.assertIn("project.view_all", manager_permissions)
        self.assertIn("core.manage_animal_roles", manager_permissions)

    def test_checked_permission_strings_are_in_catalog(self):
        catalog = set(ALL_PERMISSIONS)
        source_paths = [ROOT / "ProgTrack.v.0.1.1.py", *list((ROOT / "Plugins").rglob("*.py"))]
        patterns = (
            re.compile(r"_master_can\(\s*['\"]([^'\"]+)['\"]"),
            re.compile(r"\.can\(\s*['\"]([^'\"]+)['\"]"),
            re.compile(r"_can\(\s*['\"]([^'\"]+)['\"]"),
        )
        used = set()

        for path in source_paths:
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                for match in pattern.finditer(text):
                    permission = match.group(1)
                    if "." in permission or permission == "toggle_master_track":
                        used.add(permission)

        self.assertFalse(used - catalog - set(INTERNAL_PERMISSIONS))

    def test_master_disabled_fallback_paths_are_centralized(self):
        core = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")
        projects = (ROOT / "Plugins/Projects_Track/project_track_tab.py").read_text(encoding="utf-8")
        surgery = (ROOT / "Plugins/Surgery_Planner/surgery_planner.py").read_text(encoding="utf-8")
        sample = (ROOT / "Plugins/Sample_Track/sample_track_widget.py").read_text(encoding="utf-8")

        self.assertIn("if mt is None:\n            return True", core)
        self.assertIn("if \"master_track\" in getattr(self, '_disabled_plugins', set()):", core)
        self.assertIn("getattr(self._app, '_master_can', None)", projects)
        self.assertIn("getattr(parent, '_master_can', None)", surgery)
        self.assertIn('if "master_track" in disabled:', sample)

    def test_edit_user_dialog_is_scrollable_and_direct_overrides_collapsed(self):
        dialogs = (ROOT / "Plugins" / "Master_Track" / "dialogs.py").read_text(encoding="utf-8")

        self.assertIn("scroll = QScrollArea(self)", dialogs)
        self.assertIn("outer_layout.addWidget(scroll, 1)", dialogs)
        self.assertIn("outer_layout.addWidget(buttons)", dialogs)
        self.assertIn("override_group = _CollapsibleSection(", dialogs)
        self.assertIn("collapsed=True", dialogs)
        self.assertIn('f"master_track.job.{job_name}"', dialogs)
        self.assertIn('job_name.replace("_", " ").title()', dialogs)


if __name__ == "__main__":
    unittest.main()
