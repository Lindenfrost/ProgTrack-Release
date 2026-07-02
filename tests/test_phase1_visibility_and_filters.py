import unittest
from pathlib import Path

from Plugins.core.project_visibility import (
    ANIMAL_WELFARE_ROLE,
    animal_matches_name_filter,
    animal_visible_by_project_scope,
    diff_project_associated_users,
    visible_projects_for_user,
)
from Plugins.Master_Track.permissions import can


REPO_ROOT = Path(__file__).resolve().parents[1]


PROJECTS = {
    "Alpha": {
        "summary": {
            "contact1_login": "alice",
            "contacts_other_logins": ["bob"],
        },
        "iacuc": {"welfare_login": "welfare"},
        "assoc_users": {"staff_logins": ["keeper"]},
    },
    "Beta": {
        "summary": {"contact1_login": "charlie"},
        "assoc_users": {"staff_logins": []},
    },
}


class Phase1VisibilityAndFiltersTest(unittest.TestCase):
    def test_visible_projects_are_limited_to_associated_users(self):
        unrestricted, projects = visible_projects_for_user(PROJECTS, "keeper", "user")

        self.assertFalse(unrestricted)
        self.assertEqual(projects, {"Alpha"})

    def test_guest_or_missing_user_sees_no_projects(self):
        unrestricted, projects = visible_projects_for_user(PROJECTS, None, "guest")

        self.assertFalse(unrestricted)
        self.assertEqual(projects, set())
        self.assertFalse(animal_visible_by_project_scope({"project": ""}, unrestricted, projects))

    def test_logged_in_user_without_project_association_still_restricted(self):
        unrestricted, projects = visible_projects_for_user(PROJECTS, "unassociated", "user")

        self.assertFalse(unrestricted)
        self.assertEqual(projects, set())

    def test_animal_welfare_full_visibility_is_permission_based(self):
        unrestricted, projects = visible_projects_for_user(PROJECTS, "welfare", ANIMAL_WELFARE_ROLE)

        self.assertFalse(unrestricted)
        self.assertEqual(projects, {"Alpha"})

        unrestricted, projects = visible_projects_for_user(
            PROJECTS,
            "welfare",
            ANIMAL_WELFARE_ROLE,
            can_view_all_projects=True,
        )

        self.assertTrue(unrestricted)
        self.assertEqual(projects, {"Alpha", "Beta"})
        self.assertTrue(can(ANIMAL_WELFARE_ROLE, [], [], [], "project.view"))
        self.assertTrue(can(ANIMAL_WELFARE_ROLE, [], [], [], "project.view_all"))
        self.assertTrue(can(ANIMAL_WELFARE_ROLE, [], [], [], "reports.view"))
        self.assertFalse(can(ANIMAL_WELFARE_ROLE, [], [], [], "master.create_users"))
        self.assertFalse(can(ANIMAL_WELFARE_ROLE, [], [], [], "master.view_audit"))

    def test_animal_scope_hides_unassociated_or_projectless_animals(self):
        self.assertTrue(animal_visible_by_project_scope({"project": "Alpha"}, False, {"Alpha"}))
        self.assertFalse(animal_visible_by_project_scope({"project": "Beta"}, False, {"Alpha"}))
        self.assertFalse(animal_visible_by_project_scope({"project": ""}, False, {"Alpha"}))
        self.assertTrue(animal_visible_by_project_scope({"project": "Beta"}, True, set()))

    def test_main_all_animals_tab_bypasses_project_scope_in_sidebar(self):
        source = (REPO_ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("show_all_animals_tab = cat == self.messages[\"sidebar.filter.all\"]", source)
        self.assertIn("def _visible_in_animal_sidebar", source)
        self.assertIn("if show_all_animals_tab:", source)
        self.assertIn("return animal_visible_by_project_scope(data, unrestricted_projects, visible_projects)", source)
        self.assertIn("if not _visible_in_animal_sidebar(data):", source)

    def test_reports_use_stable_animal_key_not_sidebar_display_text(self):
        source = (REPO_ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("user_data = item.data(Qt.ItemDataRole.UserRole)", source)
        self.assertIn("if isinstance(user_data, str) and user_data in self.animals:", source)
        self.assertIn("animal_name = getattr(self, 'report_current_animal', None)", source)
        self.assertIn("# Use the report's tracked animal as source of truth.", source)

    def test_name_filter_matches_key_base_and_display_names_case_insensitively(self):
        animal = {"name": "Mila-01", "_base_name": "Mila", "display_name": "Mila ♀"}

        self.assertTrue(animal_matches_name_filter("Mila|Macaca", animal, "mil"))
        self.assertTrue(animal_matches_name_filter("Mila|Macaca", animal, "MACACA"))
        self.assertFalse(animal_matches_name_filter("Mila|Macaca", animal, "rhesus"))
        self.assertTrue(animal_matches_name_filter("Mila|Macaca", animal, ""))

    def test_project_association_diff_marks_added_and_removed_users(self):
        before = {"assoc_users": {"staff_logins": ["alice", "bob"]}}
        after = {"assoc_users": {"staff_logins": ["bob", "charlie"]}}

        self.assertEqual(diff_project_associated_users(before, after), {"alice", "charlie"})


if __name__ == "__main__":
    unittest.main()
