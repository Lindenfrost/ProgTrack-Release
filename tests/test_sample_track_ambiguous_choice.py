import unittest

from Plugins.Sample_Track.sample_track_widget import _ambiguous_animal_choices


class _App:
    animals = {
        "Luna | Species A | 01.01.2020": {
            "_base_name": "Luna",
            "id": "A-20-1",
            "species": "Species A",
        },
        "Luna | Species B | 02.02.2021": {
            "_base_name": "Luna",
            "id": "B-21-2",
            "species": "Species B",
        },
        "Sol | Species A | 01.01.2020": {
            "_base_name": "Sol",
            "id": "A-20-3",
            "species": "Species A",
        },
    }
    archived = {}


class SampleTrackAmbiguousChoiceTests(unittest.TestCase):
    def test_duplicate_short_name_choices_use_name_and_public_id(self):
        self.assertEqual(
            _ambiguous_animal_choices(_App(), "Luna"),
            {
                "Luna (A-20-1)": "Luna | Species A | 01.01.2020",
                "Luna (B-21-2)": "Luna | Species B | 02.02.2021",
            },
        )


if __name__ == "__main__":
    unittest.main()
