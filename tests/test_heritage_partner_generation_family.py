"""Focused regression tests for Heritage Track issue #157.

The tests stay at the deterministic pedigree/layout boundary and do not open a
native window.  They cover hard parent-before-child levels, safe relaxation of
partner rows, unresolved cycles, canonical family IDs, and one-parent
singletons.
"""

from __future__ import annotations

import unittest

from Plugins.Heritage_Track.layout_pipeline import GroupGrouper, family_node_id
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackWidget
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine


def _engine(parent_map: dict[str, dict[str, str]]) -> PedigreeEngine:
    animals = {name: {} for name in parent_map}

    def lookup(name: str, _record: dict) -> dict[str, str]:
        return dict(parent_map.get(name, {}))

    engine = PedigreeEngine(animals, lookup)
    engine.build()
    return engine


class HeritagePartnerGenerationFamilyTest(unittest.TestCase):
    def test_indirect_partner_chain_never_breaks_parent_order(self):
        # A is an ancestor of B, while the partner graph connects them only
        # through X/Y.  A component-wide same-row push would collapse this
        # topology; safe local alignment leaves the A->B edge strict.
        parent_map = {
            "A": {},
            "X": {},
            "Y": {},
            "Z": {},
            "B": {"egg_donor": "A", "sperm_donor": "Z"},
            "C": {"egg_donor": "A", "sperm_donor": "X"},
            "D": {"egg_donor": "X", "sperm_donor": "Y"},
            "E": {"egg_donor": "Y", "sperm_donor": "B"},
        }
        engine = _engine(parent_map)
        levels = engine.compute_levels(set(engine.all_nodes))

        self.assertEqual(engine.get_level_diagnostics(set(engine.all_nodes)), ())
        for parent, child in engine.iter_edges(set(engine.all_nodes)):
            self.assertLess(levels[parent], levels[child], (parent, child, levels))
        self.assertLess(levels["A"], levels["B"])

    def test_cycle_is_reported_and_not_a_valid_level_assignment(self):
        parent_map = {
            "A": {"egg_donor": "B"},
            "B": {"sperm_donor": "A"},
        }
        engine = _engine(parent_map)
        levels = engine.compute_levels(set(engine.all_nodes))
        diagnostics = engine.get_level_diagnostics(set(engine.all_nodes))

        self.assertEqual(set(levels), {"A", "B"})
        self.assertTrue(diagnostics)
        self.assertTrue(any("unresolved generation order" in item for item in diagnostics))

    def test_family_ids_are_canonical_and_one_parent_is_a_singleton(self):
        parent_map = {
            "Dam": {},
            "Sire": {},
            "Full": {"egg_donor": "Dam", "sperm_donor": "Sire"},
            "Half": {"egg_donor": "Dam"},
        }
        engine = _engine(parent_map)
        nodes = set(engine.all_nodes)
        groups, _by_node = GroupGrouper(nodes, engine).group()

        full = next(group for group in groups.values() if group["members"] == ["Full"])
        half = next(group for group in groups.values() if group["members"] == ["Half"])
        self.assertEqual(full["origin_family"], family_node_id("Dam", "Sire"))
        self.assertTrue(full["origin_family"].startswith("__family__::"))
        self.assertEqual(half["origin_family"], "")
        self.assertEqual(HeritageTrackWidget._family_node_id(None, "", "Sire"), "")

    def test_reordered_parent_records_have_identical_levels_and_diagnostics(self):
        first = {
            "Root": {},
            "Dam": {},
            "Child": {"egg_donor": "Dam", "sperm_donor": "Root"},
            "Grandchild": {"egg_donor": "Child"},
        }
        second = {
            "Grandchild": {"egg_donor": "Child"},
            "Child": {"sperm_donor": "Root", "egg_donor": "Dam"},
            "Dam": {},
            "Root": {},
        }
        engine_a = _engine(first)
        engine_b = _engine(second)
        nodes = set(engine_a.all_nodes)
        self.assertEqual(
            engine_a.compute_levels(nodes),
            engine_b.compute_levels(set(engine_b.all_nodes)),
        )
        self.assertEqual(
            engine_a.get_level_diagnostics(nodes),
            engine_b.get_level_diagnostics(set(engine_b.all_nodes)),
        )


if __name__ == "__main__":
    unittest.main()
