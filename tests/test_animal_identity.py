import unittest

from Plugins.core.animal_identity import (
    animal_base_name,
    animal_identity_key,
    animal_identity_label,
    identity_conflict,
    normalize_birth_date,
    record_identity_tuple,
    resolve_animal_reference_text,
    split_animal_identity_key,
)


class AnimalIdentityTest(unittest.TestCase):
    def test_birth_date_is_normalized_for_identity_keys(self):
        self.assertEqual(normalize_birth_date("1.2.2021", required=True), "01.02.2021")
        self.assertEqual(normalize_birth_date("2021-02-01", required=True), "01.02.2021")

    def test_identity_key_uses_name_species_and_birth_date(self):
        key = animal_identity_key("Luna", "Callithrix jacchus", "12.03.2021")

        self.assertEqual(key, "Luna | Callithrix jacchus | 12.03.2021")
        self.assertEqual(split_animal_identity_key(key), ("Luna", "Callithrix jacchus", "12.03.2021"))

    def test_same_name_and_species_are_allowed_when_birth_differs(self):
        animals = {
            "Luna | Macaca mulatta | 01.01.2020": {
                "_base_name": "Luna",
                "species": "Macaca mulatta",
                "birth_date": "01.01.2020",
            }
        }

        self.assertFalse(identity_conflict("Luna", "Macaca mulatta", "02.01.2020", animals))
        self.assertTrue(identity_conflict("Luna", "Macaca mulatta", "01.01.2020", animals))

    def test_identity_display_falls_back_to_base_name_for_full_keys(self):
        key = "Luna | Macaca mulatta | 01.01.2020"

        self.assertEqual(animal_base_name(key), "Luna")
        self.assertEqual(animal_identity_label(key, {"species": "Macaca mulatta", "birth_date": "1.1.2020"}), key)

    def test_record_tuple_reads_identity_fields_from_record_or_key(self):
        self.assertEqual(
            record_identity_tuple(
                "Luna | Macaca mulatta | 01.01.2020",
                {"species": "", "birth_date": ""},
            ),
            ("luna", "macaca mulatta", "01.01.2020"),
        )

    def test_resolve_animal_reference_text_blocks_ambiguous_short_names(self):
        animals = {
            "Luna | Macaca mulatta | 01.01.2020": {
                "name": "Luna",
                "species": "Macaca mulatta",
                "birth_date": "01.01.2020",
            },
            "Luna | Macaca mulatta | 02.01.2020": {
                "name": "Luna",
                "species": "Macaca mulatta",
                "birth_date": "02.01.2020",
            },
        }

        key, _record, status = resolve_animal_reference_text("Luna", animals)
        self.assertEqual(key, "")
        self.assertEqual(status, "ambiguous")

        key, _record, status = resolve_animal_reference_text(
            "Luna | Macaca mulatta | 01.01.2020",
            animals,
        )
        self.assertEqual(key, "Luna | Macaca mulatta | 01.01.2020")
        self.assertEqual(status, "resolved")

    def test_resolve_animal_reference_text_can_use_species_to_disambiguate(self):
        animals = {
            "Luna | Macaca mulatta | 01.01.2020": {
                "name": "Luna",
                "species": "Macaca mulatta",
                "birth_date": "01.01.2020",
            },
            "Luna | Papio hamadryas | 01.01.2020": {
                "name": "Luna",
                "species": "Papio hamadryas",
                "birth_date": "01.01.2020",
            },
        }

        key, _record, status = resolve_animal_reference_text(
            "Luna",
            animals,
            target_species="Papio hamadryas",
        )

        self.assertEqual(key, "Luna | Papio hamadryas | 01.01.2020")
        self.assertEqual(status, "resolved")


if __name__ == "__main__":
    unittest.main()
