import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _class_method_counts(relative_path: str, class_name: str) -> dict[str, int]:
    tree = ast.parse(_read(relative_path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            counts: dict[str, int] = {}
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    counts[child.name] = counts.get(child.name, 0) + 1
            return counts
    raise AssertionError(f"{class_name} not found in {relative_path}")


class Phase1CodeHygieneContractsTest(unittest.TestCase):
    def test_shadowed_methods_removed_from_high_risk_plugins(self):
        animal_reports = _class_method_counts(
            "Plugins/Animal_Reports/animal_reports.py",
            "AnimalReportsWidget",
        )
        self.assertEqual(1, animal_reports.get("_load_animal_list"))
        self.assertNotIn("_export_report", animal_reports)
        self.assertNotIn("_add_measurement", animal_reports)
        self.assertNotIn("_add_procedure", animal_reports)

        surgery = _class_method_counts(
            "Plugins/Surgery_Planner/surgery_planner.py",
            "GanttWidget",
        )
        self.assertEqual(1, surgery.get("_parse_weekdays"))
        self.assertEqual(1, surgery.get("_on_calendar_date_clicked"))

        heritage = _class_method_counts(
            "Plugins/Heritage_Track/heritage_track_widget.py",
            "HeritageTrackPlugin",
        )
        self.assertEqual(1, heritage.get("_ensure_parent_placeholders"))

    def test_no_bare_except_in_runtime_sources(self):
        paths = [
            ROOT / "ProgTrack.v.0.1.1.py",
            ROOT / "source" / "launcher.py",
            *sorted((ROOT / "Plugins").rglob("*.py")),
        ]
        offenders: list[str] = []
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        self.assertEqual([], offenders)

    def test_animal_reports_legacy_todo_actions_are_not_exposed(self):
        source = _read("Plugins/Animal_Reports/animal_reports.py")

        self.assertNotIn("TODO: Implement actual PDF export", source)
        self.assertNotIn("clicked.connect(self._add_measurement)", source)
        self.assertNotIn("clicked.connect(self._add_procedure)", source)
        self.assertIn("Use the main Reports tab PDF export.", source)
        self.assertIn("Add measurements in the animal editor or import workflow.", source)
        self.assertIn("Add procedures in the animal editor or surgery workflow.", source)

    def test_project_audit_failures_are_logged_not_suppressed(self):
        source = _read("Plugins/Projects_Track/project_track_tab.py")

        self.assertIn("def _audit_project_action", source)
        self.assertIn("Project audit failed", source)
        self.assertNotIn("except Exception: pass", source)

    def test_job_editor_does_not_write_role_baseline_into_job_bundle(self):
        source = _read("Plugins/Master_Track/dialogs.py")

        self.assertNotIn("job_perms | user_baseline", source)
        self.assertNotIn("ROLE_BASELINES", source)
        self.assertIn("cb.setChecked(perm in job_perms)", source)

    def test_small_static_findings_stay_closed(self):
        self.assertNotIn("# noqa: duplicate", _read("Plugins/Medi_Track/medi_track_widget.py"))
        self.assertNotIn("Legend placeholder", _read("Plugins/Cage__Track/cage_track_widget.py"))

        flow_source = _read("Plugins/Flow_Track/flow_track_widget.py")
        self.assertNotIn("TODO: implement sorting", flow_source)
        self.assertIn("def _transfer_sort_key", flow_source)
        self.assertIn(
            "sorted(transfer_rows, key=lambda row: row[0], reverse=True)",
            flow_source,
        )
        self.assertIn("datetime.min", flow_source)


if __name__ == "__main__":
    unittest.main()
