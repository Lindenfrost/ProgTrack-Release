import json
import unittest
from pathlib import Path

from matplotlib.figure import Figure

from Plugins.Cage__Track.cage_track_widget import CageTrackWidget, ProjectColorDialog


ROOT = Path(__file__).resolve().parents[1]


class _DrawNameDummy:
    def __init__(self):
        self.ax = Figure().add_subplot(111)
        self._project_rgba_cache = {}

    def _cached_project_rgba(self, project_color):
        return CageTrackWidget._cached_project_rgba(self, project_color)


class CageTrackCaretakerColorContractTest(unittest.TestCase):
    def test_default_preview_colors_are_deterministic_without_storing_custom_colors(self):
        colors = ProjectColorDialog.default_preview_colors(["Project B", "Project A"])

        self.assertEqual(["Project A", "Project B"], sorted(colors))
        self.assertNotEqual(colors["Project A"], colors["Project B"])

    def test_animal_name_without_project_has_no_background_box(self):
        widget = _DrawNameDummy()

        CageTrackWidget._draw_animal_name(widget, 1.0, 1.0, "No project", "#000000", zorder=1)

        self.assertIsNone(widget.ax.texts[0].get_bbox_patch())

    def test_animal_name_with_project_has_background_box(self):
        widget = _DrawNameDummy()

        CageTrackWidget._draw_animal_name(widget, 1.0, 1.0, "Project animal", "#ff0000", zorder=1)

        self.assertIsNotNone(widget.ax.texts[0].get_bbox_patch())


class OpPlannerReadOnlyContractTest(unittest.TestCase):
    def test_main_app_op_planner_opens_with_view_permission(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("'op_planner_action': 'op_scheduler.view'", source)
        self.assertIn("if not self._master_can('op_scheduler.view'):", source)
        self.assertIn("GanttWidget(animals=animals_for_planner, messages=self.messages, parent=self)", source)

    def test_surgery_planner_has_view_only_permission_state(self):
        source = (ROOT / "Plugins/Surgery_Planner/surgery_planner.py").read_text(encoding="utf-8")

        self.assertIn("def _apply_permission_state(self)", source)
        self.assertIn("editable = self._can_edit_schedule()", source)
        self.assertIn("export_btn.setEnabled(self._can('op_scheduler.view'))", source)
        self.assertIn("checkbox.setEnabled(self._can_edit_schedule())", source)


class RoleSidebarControlsContractTest(unittest.TestCase):
    def test_sidebar_import_buttons_follow_role_dialog_blocks(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("import_capabilities_for_blocks", source)
        self.assertIn("def _role_import_capabilities", source)
        self.assertIn("def _apply_sidebar_button_visibility_for_category", source)
        self.assertIn('self.btn_load_sperm.setVisible(caps["sperm"])', source)
        self.assertNotIn("self.btn_load_sperm.setVisible(steroid_active)", source)

    def test_all_tab_keeps_role_edit_and_refreshes_for_selection(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn('"button.sidebar.edit_role"', source)
        self.assertIn("self.btn_edit.clicked.connect(self._on_edit_in_all_tab)", source)
        self.assertIn("self.btn_edit_animal.setVisible(True)", source)
        self.assertIn(
            "self._apply_sidebar_button_visibility_for_category(self.category_tab.currentIndex())",
            source,
        )


class RoleSetupContractTest(unittest.TestCase):
    def test_role_setup_hides_internal_id_and_preset_columns(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("self.role_table.setColumnHidden(4, True)", source)
        self.assertIn("self.role_table.setColumnHidden(5, True)", source)
        self.assertNotIn("New custom roles use an emoji", source)
        self.assertIn("def _make_role_block_preset_combo", source)
        self.assertIn("def _exec_custom_role_blocks_dialog", source)
        self.assertIn("default_dialog_blocks", source)
        self.assertIn("def _category_tab_tooltips", source)
        self.assertIn("self._category_tab_tooltips()", source)

    def test_role_setup_uses_user_language_overrides_and_manager_permission(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn('{"lord", "master", "manager"}', source)
        self.assertIn("def _merge_user_messages", source)
        self.assertIn("def _save_role_label_overrides", source)
        self.assertIn('APP_BASE_DIR / "Plugins" / "core" / "user_lang"', source)

    def test_role_consumers_use_core_role_normalization(self):
        files = [
            ROOT / "Plugins" / "Surgery_Planner" / "surgery_planner.py",
            ROOT / "Plugins" / "Projects_Track" / "project_track_tab.py",
            ROOT / "Plugins" / "Heritage_Track" / "heritage_track_widget.py",
            ROOT / "Plugins" / "Heritage_Track" / "heritage_store.py",
            ROOT / "Plugins" / "Cage__Track" / "cage_track_widget.py",
        ]

        for path in files:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("canonical_role_value", source)

    def test_sample_progtrack_data_uses_internal_role_ids(self):
        data = json.loads((ROOT / "progtrack_daten.json").read_text(encoding="utf-8"))
        legacy_values = {
            "Spenderin",
            "Amme",
            "Samenspender",
            "Nachkomme",
            "Partnertier",
            "Zuchttier",
            "Versuchstier",
            "Unbekannt",
        }
        expected_values = {
            "egg_cell_donor",
            "surrogate",
            "sperm_donor",
            "offspring",
            "partner_animal",
            "breeding_animal",
            "experimental_animal",
            "unknown",
        }

        roles = {
            rec.get("rolle")
            for section in ("animals", "archived_animals")
            for rec in data.get(section, {}).values()
            if isinstance(rec, dict)
        }

        self.assertFalse(roles & legacy_values)
        self.assertTrue(roles <= expected_values)


if __name__ == "__main__":
    unittest.main()
