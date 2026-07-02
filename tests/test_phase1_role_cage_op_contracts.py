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


if __name__ == "__main__":
    unittest.main()
