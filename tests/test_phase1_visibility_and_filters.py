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
        self.assertFalse(can(ANIMAL_WELFARE_ROLE, [], [], [], "project.view_all"))
        self.assertTrue(can(ANIMAL_WELFARE_ROLE, [], [], [], "reports.view"))
        self.assertFalse(can(ANIMAL_WELFARE_ROLE, [], [], [], "master.create_users"))
        self.assertFalse(can(ANIMAL_WELFARE_ROLE, [], [], [], "master.view_audit"))
        self.assertTrue(can("user", ["manager"], [], [], "project.view_all"))
        self.assertTrue(can("user", ["manager"], [], [], "core.manage_animal_roles"))

    def test_animal_scope_hides_unassociated_or_projectless_animals(self):
        self.assertTrue(animal_visible_by_project_scope({"project": "Alpha"}, False, {"Alpha"}))
        self.assertFalse(animal_visible_by_project_scope({"project": "Beta"}, False, {"Alpha"}))
        self.assertFalse(animal_visible_by_project_scope({"project": ""}, False, {"Alpha"}))
        self.assertTrue(animal_visible_by_project_scope({"project": "Beta"}, True, set()))

    def test_main_all_animals_tab_bypasses_project_scope_in_sidebar(self):
        source = (REPO_ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("show_all_animals_tab = idx == all_idx", source)
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

    def test_reports_guard_logout_and_file_exports_by_report_view_permission(self):
        source = (REPO_ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("def _prepare_reports_for_user_context_change", source)
        self.assertIn("self._prepare_reports_for_user_context_change()", source)
        self.assertIn("self.report_year_combo.blockSignals(True)", source)
        self.assertIn("self.report_month_combo.blockSignals(True)", source)
        self.assertIn("can_reports_export = self._master_can('reports.view')", source)
        self.assertNotIn("print_action.setEnabled(can_reports_export)", source)
        self.assertIn("pdf_export_action.setEnabled(can_reports_export)", source)
        self.assertEqual(
            source.count('pdf_export_action = QAction(self.messages.get("menu.file.export_pdf"'),
            2,
        )
        self.assertIn("if not self._master_can('reports.view'):", source)
        self.assertIn("if date > death_date:", source)
        self.assertIn("return \"\"", source)

        animal_reports = (REPO_ROOT / "Plugins" / "Animal_Reports" / "animal_reports.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("header_top = page_height - 0.6*cm", animal_reports)
        self.assertIn("topMargin=6.8*cm", animal_reports)

    def test_startup_menu_and_heritage_archived_marker_contracts(self):
        source = (REPO_ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("menubar.setVisible(False)", source)
        self.assertIn("self.menuBar().setVisible(True)", source)
        self.assertIn("and name not in self.archived", source)

    def test_name_filter_matches_visible_names_by_prefix_only(self):
        animal = {"name": "Mila-01", "_base_name": "Mila", "display_name": "Mila ♀"}

        self.assertTrue(animal_matches_name_filter("Mila|Macaca", animal, "mil"))
        self.assertFalse(animal_matches_name_filter("Mila|Macaca", animal, "ila"))
        self.assertFalse(animal_matches_name_filter("Mila|Macaca", animal, "MACACA"))
        self.assertFalse(animal_matches_name_filter("Mila|Macaca", animal, "rhesus"))
        self.assertTrue(animal_matches_name_filter("Mila|Macaca", animal, ""))

    def test_project_association_diff_marks_added_and_removed_users(self):
        before = {"assoc_users": {"staff_logins": ["alice", "bob"]}}
        after = {"assoc_users": {"staff_logins": ["bob", "charlie"]}}

        self.assertEqual(diff_project_associated_users(before, after), {"alice", "charlie"})

    def test_project_track_refresh_save_feedback_and_welfare_role_filter(self):
        source = (REPO_ROOT / "Plugins" / "Projects_Track" / "project_track_tab.py").read_text(
            encoding="utf-8"
        )
        plugin_source = (REPO_ROOT / "Plugins" / "Projects_Track" / "ProjectsTrack_plugin.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("self._refresh_project_btn = QPushButton", source)
        self.assertIn("top_btn_row.addWidget(self._new_project_btn, 5)", source)
        self.assertIn("top_btn_row.addWidget(self._refresh_project_btn, 1)", source)
        self.assertIn("def _on_refresh_clicked(self):", source)
        self.assertIn("role_filter='animal_welfare_officer'", source)
        self.assertIn("if self._role_filter and ud.get('role') != self._role_filter:", source)
        self.assertIn('"project.info.saved"', source)
        self.assertIn("def _cache_file_for_current_user", plugin_source)
        self.assertIn("'project_assignment_cache'", plugin_source)
        self.assertIn("'project.view_all'", plugin_source)
        self.assertIn("self.cache_file = self._cache_file_for_current_user()", plugin_source)


if __name__ == "__main__":
    unittest.main()
