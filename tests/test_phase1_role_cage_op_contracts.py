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

    def test_movement_history_hides_ipids_in_body_and_keeps_tooltips(self):
        source = (ROOT / "Plugins" / "Cage__Track" / "cage_track_widget.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("occupant_widget.setToolTip(occupant_id)", source)
        self.assertIn("mates = \", \".join(animal_base_name(mate) for mate in mate_ids)", source)
        self.assertIn("item4.setToolTip", source)
        self.assertNotIn('layout.addWidget(QLabel(f"IPID: {occupant_id}"))', source)
        self.assertNotIn('f"{animal_base_name(mate)} ({mate})"', source)


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

    def test_surgery_planner_y_axis_uses_short_names(self):
        source = (ROOT / "Plugins/Surgery_Planner/surgery_planner.py").read_text(encoding="utf-8")

        self.assertIn("self.ax.set_yticklabels([animal_base_name(n) for n in names])", source)
        self.assertNotIn("self.ax.set_yticklabels(names)", source)


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

    def test_sidebar_import_buttons_require_matching_data_permissions(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn('can_import_research_data = can_import and mt.can("core.edit_animal_research_data")', source)
        self.assertIn('can_import_measurements = can_import and mt.can("core.edit_animal_measurements")', source)
        self.assertIn("self.btn_load_blood.setEnabled(can_import_research_data)", source)
        self.assertIn("self.btn_load_urine.setEnabled(can_import_research_data)", source)
        self.assertIn("self.btn_load_weights.setEnabled(can_import_measurements)", source)
        self.assertIn("self.btn_load_sperm.setEnabled(can_import_research_data and steroid_active)", source)
        self.assertNotIn("self.btn_load_sperm.setEnabled(can_create and steroid_active)", source)

    def test_sperm_import_assigns_sperm_donor_role_to_new_unknown_animals(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn('if self._animal_role_value(a) == Role.UNKNOWN.value:', source)
        self.assertIn('a["rolle"] = Role.SAMENSP.value', source)


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

        self.assertIn('"core.manage_animal_roles"', source)
        self.assertIn("def _merge_user_messages", source)
        self.assertIn("def _save_role_label_overrides", source)
        self.assertIn('APP_BASE_DIR / "Plugins" / "core" / "user_lang"', source)

    def test_custom_role_tabs_are_dynamic_and_all_tab_is_last(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("def _custom_category_roles", source)
        self.assertIn("def _all_category_tab_index", source)
        self.assertIn("def _rebuild_category_tabs", source)
        self.assertIn('self._category_custom_role_values = custom_values', source)
        self.assertIn("custom_idx = idx - 6", source)
        self.assertNotIn("if idx == 6:", source)
        self.assertNotIn("self.category_tab.setTabVisible(6, True)", source)
        self.assertNotIn("self.category_tab.setTabText(6", source)

    def test_role_consumers_use_core_role_normalization(self):
        files = [
            ROOT / "Plugins" / "Surgery_Planner" / "surgery_planner.py",
            ROOT / "Plugins" / "Projects_Track" / "project_track_tab.py",
            ROOT / "Plugins" / "Heritage_Track" / "heritage_track_widget.py",
            ROOT / "Plugins" / "Heritage_Track" / "heritage_store.py",
            ROOT / "Plugins" / "Cage__Track" / "cage_track_widget.py",
            ROOT / "Plugins" / "Flow_Track" / "flow_track_widget.py",
        ]

        for path in files:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("canonical_role_value", source)

    def test_main_and_flow_track_do_not_branch_on_raw_role_values(self):
        files = [
            ROOT / "ProgTrack.v.0.1.1.py",
            ROOT / "Plugins" / "Flow_Track" / "flow_track_widget.py",
        ]
        forbidden_patterns = [
            "get('rolle') == Role.",
            'get("rolle") == Role.',
            "get('rolle') != Role.",
            'get("rolle") != Role.',
            "get('rolle') in (Role.",
            'get("rolle") in (Role.',
            "get('rolle') not in (Role.",
            'get("rolle") not in (Role.',
        ]

        for path in files:
            with self.subTest(path=path.name):
                source = path.read_text(encoding="utf-8")
                self.assertIn("def _animal_role_value", source)
                for pattern in forbidden_patterns:
                    self.assertNotIn(pattern, source)

    def test_flow_track_exports_use_desktop_helper_and_overview_flow_animals_only(self):
        source = (ROOT / "Plugins" / "Flow_Track" / "flow_track_widget.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("from Plugins.core.platform_helpers import default_save_path", source)
        self.assertIn("str(default_save_path(f\"flow_track_", source)
        self.assertIn("def _flow_tracked_animals", source)
        self.assertIn("for animal_name in self._flow_tracked_animals()", source)
        overview_source = source[
            source.index("def _create_all_animals_overview"):
            source.index("def _create_egg_donors_overview")
        ]
        self.assertIn("'Embryos created'", overview_source)
        self.assertIn("'Transferred'", overview_source)
        self.assertIn("'Implanted'", overview_source)
        self.assertIn("'Cryopreserved'", overview_source)
        self.assertNotIn("'Embryos Donated'", overview_source)
        self.assertNotIn("'Embryos Received'", overview_source)
        self.assertNotIn("'Frozen'", overview_source)

    def test_flow_track_dialogs_are_scrollable_and_screen_bounded(self):
        source = (ROOT / "Plugins" / "Flow_Track" / "flow_track_widget.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("def _set_scrollable_dialog_layout", source)
        self.assertIn("QtWidgets.QScrollArea(dialog)", source)
        self.assertIn("dialog.setMaximumSize(max_width, max_height)", source)
        self.assertIn("dialog.setSizeGripEnabled(True)", source)
        self.assertNotIn("dialog.setLayout(layout)", source)

    def test_edit_animal_empty_lists_keep_headers_top_aligned(self):
        source = (ROOT / "ProgTrack.v.0.1.1.py").read_text(encoding="utf-8")

        self.assertIn("layout.setAlignment(Qt.AlignmentFlag.AlignTop)", source)
        self.assertIn("row_layout.setAlignment(Qt.AlignmentFlag.AlignTop)", source)
        self.assertIn("flayout.setAlignment(Qt.AlignmentFlag.AlignTop)", source)
        self.assertIn("ev_flayout.setAlignment(Qt.AlignmentFlag.AlignTop)", source)

    def test_embryo_track_loads_heavy_dependencies_lazily(self):
        source = (ROOT / "Plugins" / "Embryo_Track" / "embryo_track.py").read_text(
            encoding="utf-8"
        )

        top_imports = source.split("# Configure logging")[0]
        self.assertNotIn("import pandas as pd", top_imports)
        self.assertNotIn("import numpy as np", top_imports)
        self.assertNotIn("from scipy.optimize import curve_fit", top_imports)
        self.assertIn("def _ensure_numeric_deps", source)
        self.assertIn("def _ensure_pandas", source)
        self.assertIn("_ensure_numeric_deps()", source)
        self.assertIn("_ensure_pandas()", source)

    def test_sample_track_uses_sex_field_instead_of_role_id_for_sample_sex(self):
        source = (ROOT / "Plugins" / "Sample_Track" / "sample_track_widget.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("def _record_sex_value", source)
        self.assertIn("def _species_abbreviation", source)
        self.assertIn('_species_abbreviation(rec.get("species", ""))', source)
        self.assertIn("sex_value = _record_sex_value(rec) or self._sex_cb.currentText()", source)
        self.assertIn('else _record_sex_value(rec)', source)
        self.assertNotIn('else rec.get("rolle", "")', source)
        self.assertNotIn("rec.get('rolle', '')", source)

    def test_sample_track_pdf_omits_duplicate_name_column_and_sizes_ipid(self):
        source = (ROOT / "Plugins" / "Sample_Track" / "sample_track_widget.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('static_headers = ["IPID", "Species", "ID", "Sex", "Birth", "Death", "Unit"]', source)
        self.assertIn('static_headers = ["No.", "IPID", "Species", "ID", "Sex", "Date", "Unit"]', source)
        self.assertIn("max_ipid_len = max", source)
        self.assertIn("ipid_col_width = max(45*mm", source)
        self.assertIn("fixed_width_by_header", source)
        self.assertNotIn('static_headers = ["IPID", "Name"', source)
        self.assertNotIn('static_headers = ["No.", "IPID", "Name"', source)

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
