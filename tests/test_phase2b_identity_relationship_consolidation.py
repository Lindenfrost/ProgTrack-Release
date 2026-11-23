from __future__ import annotations

import json
from pathlib import Path
import unittest
import zipfile

from Plugins.core.animal_relationships import (
    RelationshipConflict,
    plan_symmetric_relationship,
    relationship_status_icon,
)
from Plugins.core.resource_catalogs import ordered_species_for_display


ROOT = Path(__file__).resolve().parents[1]


class IdentityRelationshipConsolidationTest(unittest.TestCase):
    def test_unknown_species_is_presented_last_without_mutating_input(self):
        configured = ["Callitrix jacchus", "Unknown species", "Mus musculus"]
        shown = ordered_species_for_display(configured)
        self.assertEqual(
            shown,
            ["Callitrix jacchus", "Mus musculus", "Unknown species"],
        )
        self.assertEqual(
            configured,
            ["Callitrix jacchus", "Unknown species", "Mus musculus"],
        )
        for language in ("en", "de", "it", "ru"):
            messages = json.loads(
                (ROOT / "lang" / f"messages_{language}.json")
                .read_text(encoding="utf-8")
            )
            self.assertTrue(messages["species.unknown"])

    def test_partner_link_create_remove_and_reassign_are_symmetric(self):
        records = {
            "A": {"name": "A", "rolle": "partner_animal"},
            "B": {"name": "B", "rolle": "breeding_animal"},
            "C": {"name": "C", "rolle": "breeding_animal"},
        }
        subject, counterparts = plan_symmetric_relationship(
            records, "A", records["A"], field="partner_von",
            target_reference="B", can_edit=True,
        )
        records["A"] = subject
        records.update(counterparts)
        self.assertEqual(records["A"]["partner_von"], "B")
        self.assertEqual(records["B"]["verpaart_mit"], "A")
        self.assertEqual(
            relationship_status_icon(records, "A", records["A"]),
            "status.partner",
        )
        subject, counterparts = plan_symmetric_relationship(
            records, "A", records["A"], field="partner_von",
            target_reference="C", can_edit=True,
        )
        records["A"] = subject
        records.update(counterparts)
        self.assertEqual(records["B"]["verpaart_mit"], "")
        self.assertEqual(records["C"]["verpaart_mit"], "A")
        subject, counterparts = plan_symmetric_relationship(
            records, "A", records["A"], field="partner_von",
            target_reference="", can_edit=True,
        )
        records["A"] = subject
        records.update(counterparts)
        self.assertEqual(records["A"]["partner_von"], "")
        self.assertEqual(records["C"]["verpaart_mit"], "")

    def test_breeding_links_are_distinct_guarded_and_conflict_safe(self):
        records = {
            "A": {"name": "A", "rolle": "breeding_animal"},
            "B": {"name": "B", "rolle": "breeding_animal"},
            "C": {"name": "C", "rolle": "breeding_animal", "verpaart_mit": "B"},
        }
        records["B"]["verpaart_mit"] = "C"
        with self.assertRaises(RelationshipConflict):
            plan_symmetric_relationship(
                records, "A", records["A"], field="verpaart_mit",
                target_reference="B", can_edit=True,
            )
        self.assertNotIn("verpaart_mit", records["A"])
        self.assertEqual(records["B"]["verpaart_mit"], "C")
        with self.assertRaises(PermissionError):
            plan_symmetric_relationship(
                records, "A", records["A"], field="partner_von",
                target_reference="B", can_edit=False,
            )
        subject, counterparts = plan_symmetric_relationship(
            {"A": records["A"], "B": {"name": "B", "rolle": "breeding_animal"}},
            "A", records["A"], field="verpaart_mit",
            target_reference="B", can_edit=True,
        )
        linked = {"A": subject, **counterparts}
        self.assertEqual(
            relationship_status_icon(linked, "A", linked["A"]),
            "role.breeding",
        )
        self.assertNotIn("partner_von", linked["A"])

    def test_canonical_seed_has_only_reciprocal_relationships(self):
        with zipfile.ZipFile(
            ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb"
        ) as package:
            animals = {}
            for line in package.read("records/animals.jsonl").decode("utf-8").splitlines():
                row = json.loads(line)
                animals[row["ipid"]] = row["payload"]
        linked = 0
        for key, record in animals.items():
            for field in ("partner_von", "verpaart_mit"):
                target = str(record.get(field) or "")
                if not target:
                    continue
                linked += 1
                self.assertIn(target, animals)
                self.assertTrue(any(
                    str(animals[target].get(other) or "") == key
                    for other in ("partner_von", "verpaart_mit")
                ))
        self.assertGreater(linked, 0)

    def test_archived_reference_and_restricted_import_are_side_effect_free(self):
        # The service consumes one combined active/archive mapping; restoring an
        # animal therefore does not rewrite or weaken its stable relationship.
        records = {
            "Active": {
                "name": "Active", "rolle": "breeding_animal",
                "verpaart_mit": "Archived",
            },
            "Archived": {
                "name": "Archived", "rolle": "partner_animal",
                "partner_von": "Active", "archived": True,
            },
        }
        self.assertEqual(
            relationship_status_icon(records, "Active", records["Active"]),
            "status.partner",
        )
        before = json.loads(json.dumps(records))
        with self.assertRaises(PermissionError):
            # Import/adaptor callers have no implicit relationship authority;
            # they must pass the same explicit permission decision as the UI.
            plan_symmetric_relationship(
                records,
                "Active",
                records["Active"],
                field="verpaart_mit",
                target_reference="",
                can_edit=False,
            )
        self.assertEqual(records, before)


if __name__ == "__main__":
    unittest.main()
