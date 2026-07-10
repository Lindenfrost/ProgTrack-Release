import json
import unittest
from copy import deepcopy
from pathlib import Path

from Plugins.Heritage_Track.layout_pipeline import LayoutPipeline, parse_complete_birth_date_ordinal
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine
from Plugins.Heritage_Track.pedigree_router import PedigreeRouter, RoutePlan


REPO_ROOT = Path(__file__).resolve().parents[1]


def _eldarion_fixture(*, birthdate_height_layout=True):
    parent_map = {
        "Eldarion": {"egg_donor": "Arwen", "sperm_donor": "Aragorn II"},
        "Placeholder": {"egg_donor": "Arwen", "sperm_donor": "Aragorn II"},
        "Arwen": {"egg_donor": "Celebrían", "sperm_donor": "Elrond"},
        "Celebrían": {"egg_donor": "Galadriel", "sperm_donor": "Celeborn"},
        "Elrond": {"egg_donor": "Elwing", "sperm_donor": "Eärendil"},
        "Aragorn II": {"egg_donor": "Taylor", "sperm_donor": "Aragorn I"},
        "Aragorn I": {"egg_donor": "Jessica", "sperm_donor": "Isildur"},
    }
    nodes = set(parent_map)
    for parents in parent_map.values():
        nodes.update(parents.values())

    engine = PedigreeEngine(
        {node: {} for node in nodes},
        lambda node, _record: parent_map.get(node, {}),
    )
    engine.build()
    families = {
        "family::arwen::aragorn_ii": {
            "mother": "Arwen",
            "father": "Aragorn II",
            "children": ["Eldarion", "Placeholder"],
        },
        "family::celebrian::elrond": {
            "mother": "Celebrían",
            "father": "Elrond",
            "children": ["Arwen"],
        },
        "family::galadriel::celeborn": {
            "mother": "Galadriel",
            "father": "Celeborn",
            "children": ["Celebrían"],
        },
        "family::elwing::earendil": {
            "mother": "Elwing",
            "father": "Eärendil",
            "children": ["Elrond"],
        },
        "family::taylor::aragorn_i": {
            "mother": "Taylor",
            "father": "Aragorn I",
            "children": ["Aragorn II"],
        },
        "family::jessica::isildur": {
            "mother": "Jessica",
            "father": "Isildur",
            "children": ["Aragorn I"],
        },
    }
    dates = {
        "Eldarion": "01.03.2026",
        "Placeholder": "01.01.1900",
        "Arwen": "23.02.2015",
        "Aragorn II": "11.02.2021",
        "Celebrían": "06.05.2007",
        "Elrond": "04.02.2012",
        "Galadriel": "17.06.2005",
        "Celeborn": "29.08.2004",
        "Elwing": "08.01.2008",
        "Eärendil": "14.02.2010",
        "Taylor": "14.06.2018",
        "Aragorn I": "07.02.2018",
        "Jessica": "06.07.2015",
        "Isildur": "23.02.2015",
    }
    birth_ordinals = {
        node: parse_complete_birth_date_ordinal(value)
        for node, value in dates.items()
    }
    positions = LayoutPipeline().compute_positions(
        nodes=nodes,
        levels={},
        engine=engine,
        families=families,
        birth_ordinal_by_node=birth_ordinals,
        birthdate_height_layout=birthdate_height_layout,
    )
    return parent_map, families, positions


class HeritageTrackPedigreeRouterTest(unittest.TestCase):
    def test_eldarion_fixture_has_owned_routes_without_geometry_conflicts(self):
        router = PedigreeRouter()
        for birthdate_height_layout in (False, True):
            with self.subTest(birthdate_height_layout=birthdate_height_layout):
                parent_map, families, positions = _eldarion_fixture(
                    birthdate_height_layout=birthdate_height_layout
                )
                original_parent_map = deepcopy(parent_map)
                labels = {node: node for node in positions}

                plan = router.plan(positions, families, labels=labels, show_inbreeding=True)

                self.assertEqual(parent_map, original_parent_map)
                self.assertEqual(plan.unresolved, [])
                self.assertEqual(
                    set(plan.routes["family::arwen::aragorn_ii"]),
                    {"Arwen", "Aragorn II", "Eldarion", "Placeholder"},
                )
                self.assertEqual(
                    set(plan.routes["family::jessica::isildur"]),
                    {"Jessica", "Isildur", "Aragorn I"},
                )
                self.assertEqual(
                    router.validate_plan(plan, families, labels=labels, show_inbreeding=True),
                    [],
                )

    def test_router_is_deterministic_for_reordered_input(self):
        _parent_map, families, positions = _eldarion_fixture()
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        first = router.plan(positions, families, labels=labels)
        second = router.plan(
            dict(reversed(list(positions.items()))),
            dict(reversed(list(families.items()))),
            labels=dict(reversed(list(labels.items()))),
        )

        self.assertEqual(first.animal_positions, second.animal_positions)
        self.assertEqual(first.family_positions, second.family_positions)
        self.assertEqual(first.routes, second.routes)
        self.assertEqual(first.crossing_gaps, second.crossing_gaps)
        self.assertEqual(first.unresolved, second.unresolved)

    def test_protected_manual_node_is_not_moved_and_routes_avoid_it(self):
        positions = {
            "Dam": (0.0, 0.0),
            "Sire": (2.0, 0.0),
            "Child": (1.0, 4.0),
            "Protected blocker": (1.0, 2.0),
        }
        families = {
            "family": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Child"],
            }
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(
            positions,
            families,
            labels=labels,
            protected_nodes={"Protected blocker"},
        )

        self.assertEqual(plan.animal_positions["Protected blocker"], (1.0, 2.0))
        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])

    def test_crossing_gap_splits_lower_priority_route(self):
        positions = {"Animal A": (2.0, 0.0), "Animal B": (1.0, 1.0)}
        families = {
            "family_a": {"mother": "Animal A", "father": "", "children": []},
            "family_b": {"mother": "Animal B", "father": "", "children": []},
        }
        routes = {
            "family_a": {"Animal A": [(0.0, 0.0), (2.0, 0.0)]},
            "family_b": {"Animal B": [(1.0, -1.0), (1.0, 1.0)]},
        }
        ungapped = RoutePlan(
            animal_positions=positions,
            family_positions={"family_a": (0.0, 0.0), "family_b": (1.0, -1.0)},
            family_members={"family_a": {"Animal A"}, "family_b": {"Animal B"}},
            routes=routes,
        )
        router = PedigreeRouter()

        self.assertIn(
            "family_a/family_b: crossing lacks a visible gap",
            router.validate_plan(ungapped, families, labels=positions),
        )

        gapped = RoutePlan(
            animal_positions=positions,
            family_positions=ungapped.family_positions,
            family_members=ungapped.family_members,
            routes=routes,
            crossing_gaps={("family_b", "Animal B", 0): [(1.0, 0.0)]},
        )
        drawn = gapped.draw_segments("family_b", "Animal B", gap_radius=0.1)

        self.assertEqual(drawn, [((1.0, -1.0), (1.0, -0.1)), ((1.0, 0.1), (1.0, 1.0))])
        self.assertEqual(router.validate_plan(gapped, families, labels=positions), [])

    def test_collapsed_family_routes_only_visible_parents(self):
        positions = {"Dam": (0.0, 0.0), "Sire": (2.0, 0.0)}
        families = {"collapsed": {"mother": "Dam", "father": "Sire", "children": []}}
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)

        self.assertEqual(set(plan.routes["collapsed"]), {"Dam", "Sire"})
        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])

    def test_widget_rendering_and_highlighting_share_route_plan(self):
        source = (REPO_ROOT / "Plugins" / "Heritage_Track" / "heritage_track_widget.py").read_text(
            encoding="utf-8"
        )

        self.assertIn("route_plan.draw_segments(family_id, parent)", source)
        self.assertIn("route_plan.draw_segments(family_id, child)", source)
        self.assertIn("self._route_plan.draw_segments", source)
        self.assertNotIn("self.ax.plot([px, px]", source)

    def test_routing_warning_language_key_exists(self):
        for path in (REPO_ROOT / "lang").glob("messages_*.json"):
            with self.subTest(path=path.name):
                messages = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("heritage_track.status.routing_warning", messages)


if __name__ == "__main__":
    unittest.main()
