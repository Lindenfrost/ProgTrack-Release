import unittest

from Plugins.core.project_visibility import (
    ANIMAL_WELFARE_ROLE,
    animal_matches_name_filter,
    animal_visible_by_project_scope,
    diff_project_associated_users,
    visible_projects_for_user,
)
from Plugins.Master_Track.permissions import can


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
