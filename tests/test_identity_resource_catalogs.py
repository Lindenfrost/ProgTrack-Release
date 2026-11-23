"""Tests for static resource catalog files and current identity helpers.

Identity/lifecycle conventions are backend-owned in Phase 3 (#246). These
tests deliberately exercise only the still-supported text resources and pure
ID helper functions; they do not read or write the retired conventions JSON.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from Plugins.core.identity_conventions import (
    pattern_from_components,
    preview_next_generated_id,
)
from Plugins.core.resource_catalogs import (
    read_catalog,
    read_genotypes,
    write_catalog,
    write_genotypes,
)


class IdentityResourceCatalogTests(unittest.TestCase):
    def test_text_catalog_round_trips_unique_values_in_original_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_catalog(root, "death_causes", ["Cause A", "cause a", "Cause B"])

            self.assertEqual(
                read_catalog(root, "death_causes"),
                ["Cause A", "Cause B"],
            )

    def test_genotype_text_catalog_round_trips_and_deduplicates_per_species(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_genotypes(root, {"Species A": ["WT", "wt", "Mut"]})

            self.assertEqual(
                read_genotypes(root),
                {"Species A": ["WT", "Mut"]},
            )

    def test_optional_id_components_do_not_leave_extra_separators(self):
        self.assertEqual(pattern_from_components(["species"]), "{species}")
        self.assertEqual(
            pattern_from_components(["species", "", "count", "", ""]),
            "{species}_{sequence:04d}",
        )

    def test_id_preview_uses_next_counts_without_mutating_backend_payload(self):
        conventions = {
            "animal_id_components": [
                "species", "year_short", "count_year", "count_absolute",
                "sex", "name", "origin",
            ],
            "animal_id_pattern": (
                "{species}_{year2}_{sequence:04d}_{absolute_sequence:04d}_"
                "{sex}_{name}_{origin}"
            ),
            "yearly_sequences": {"26": 12},
            "absolute_sequence": 99,
        }
        before = json.loads(json.dumps(conventions))

        preview = preview_next_generated_id(
            conventions,
            name="Luna",
            species="Callithrix jacchus",
            birth_date="01.02.2026",
            sex="Female",
            origin="DPZ",
        )

        self.assertEqual(preview, "cj_26_0013_0100_F_Luna_DPZ")
        self.assertEqual(conventions, before)

    def test_id_preview_shows_placeholders_for_missing_descriptive_fields(self):
        conventions = {
            "animal_id_components": ["year_short", "count_year", "sex", "name"],
            "animal_id_pattern": "{year2}_{sequence:04d}_{sex}_{name}",
            "yearly_sequences": {"26": 4},
            "absolute_sequence": 10,
        }

        self.assertEqual(
            preview_next_generated_id(
                conventions,
                birth_date="2026",
                sex="Male",
            ),
            "26_0005_M_XXXX",
        )


if __name__ == "__main__":
    unittest.main()
