import unittest

from Plugins.core.animal_status import (
    DECEASED_STATUS_SYMBOL,
    compact_death_status,
    compact_status_with_death_priority,
    has_death_date,
    status_summary_with_death_priority,
)


MESSAGES = {
    "status.normal": "Normal",
    "status.sick": "Sick",
    "status.abnormal": "Abnormal",
    "status.in_experiment": "In Experiment",
    "status.deceased": "Deceased",
}


class AnimalStatusTest(unittest.TestCase):
    def test_death_date_detection_uses_death_date_and_legacy_sterbedatum(self):
        self.assertTrue(has_death_date({"death_date": "01.02.2024"}))
        self.assertTrue(has_death_date({"sterbedatum": "01.02.2024"}))
        self.assertFalse(has_death_date({"death_date": "  ", "sterbedatum": ""}))
        self.assertFalse(has_death_date({}))

    def test_compact_death_status_overrides_transient_markers(self):
        record = {
            "death_date": "01.02.2024",
            "sick": True,
            "abnormal_current": True,
            "in_experiment": True,
        }

        self.assertEqual(compact_status_with_death_priority(record, "+! ■"), DECEASED_STATUS_SYMBOL)

    def test_compact_death_status_preserves_genotype(self):
        record = {"death_date": "01.02.2024", "genotype": "WT"}

        self.assertEqual(compact_death_status(record), "WT ✝")
        self.assertEqual(compact_status_with_death_priority(record, "+! ■"), "WT ✝")

    def test_compact_status_is_unchanged_for_living_animals(self):
        self.assertEqual(compact_status_with_death_priority({"sick": True}, "+"), "+")

    def test_human_summary_death_overrides_flags_and_special_status(self):
        record = {
            "death_date": "01.02.2024",
            "sick": True,
            "abnormal_current": True,
            "in_experiment": True,
            "special_status": "Needs review",
        }

        self.assertEqual(
            status_summary_with_death_priority(record, MESSAGES, projects_track_active=True),
            "Deceased",
        )

    def test_human_summary_keeps_current_behavior_for_living_animals(self):
        record = {
            "sick": True,
            "abnormal_current": True,
            "in_experiment": True,
            "special_status": "Needs review",
        }

        self.assertEqual(
            status_summary_with_death_priority(record, MESSAGES, projects_track_active=True),
            "Sick, Abnormal, In Experiment — Needs review",
        )

    def test_human_summary_respects_project_track_activity_for_experiment_status(self):
        record = {"in_experiment": True}

        self.assertEqual(status_summary_with_death_priority(record, MESSAGES), "Normal")
        self.assertEqual(
            status_summary_with_death_priority(record, MESSAGES, projects_track_active=True),
            "In Experiment",
        )


if __name__ == "__main__":
    unittest.main()
