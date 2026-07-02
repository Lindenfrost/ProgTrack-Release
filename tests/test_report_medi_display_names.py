import unittest

from Plugins.Animal_Reports.animal_reports import AnimalReportsWidget
from Plugins.Medi_Track.medi_track_widget import _display_animal_name


class ReportAndMediDisplayNameTests(unittest.TestCase):
    def test_animal_reports_name_field_uses_short_name_when_record_name_is_ipid(self):
        key = "Luna | Macaca mulatta | 01.02.2024"
        record = {"name": key, "ipid": key, "species": "Macaca mulatta"}

        display = AnimalReportsWidget._display_animal_name(object(), key, record)

        self.assertEqual(display, "Luna")

    def test_medi_track_name_field_uses_short_name_when_record_name_is_ipid(self):
        key = "Luna | Macaca mulatta | 01.02.2024"
        record = {"name": key, "ipid": key, "species": "Macaca mulatta"}

        self.assertEqual(_display_animal_name(key, record), "Luna")

    def test_display_name_field_is_preserved_when_it_is_a_clear_name(self):
        key = "Luna | Macaca mulatta | 01.02.2024"
        record = {"display_name": "Luna II", "name": key, "ipid": key}

        report_display = AnimalReportsWidget._display_animal_name(object(), key, record)

        self.assertEqual(report_display, "Luna II")
        self.assertEqual(_display_animal_name(key, record), "Luna II")


if __name__ == "__main__":
    unittest.main()
