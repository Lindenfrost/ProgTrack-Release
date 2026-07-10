import json
import math
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

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


def _papio_branch_fixture():
    rows = [
        [("AdÃ¨le", 14.455), ("Boldog", 15.855)],
        [("Balcmeg", 0.0), ("Othrod", 11.2), ("BÃ©atrice", 12.558), ("Gorgol", 13.958)],
        [("Orcobal", 9.8), ("Camille", 10.484), ("GrishnÃ¡kh", 11.884)],
        [
            ("Gorbag", 1.4),
            ("Delphine", 8.395),
            ("Orc-chieftain", 8.4),
            ("Shagrat", 9.795),
            ("Lugdush", 11.899),
        ],
        [
            ("Muzgash", 7.0),
            ("Lagduf", 7.699),
            ("Ã‰lodie", 9.099),
            ("HÃ©lÃ¨ne", 10.499),
            ("Snaga I", 12.6),
        ],
        [("Lug", 4.2), ("Florence", 5.6), ("Ufthak", 7.0), ("Isabelle", 8.4), ("UglÃºk", 9.8)],
        [("GisÃ¨le", 3.5), ("Naglur-Danlo", 4.9), ("Josette", 6.3), ("MauhÃºr", 7.7), ("Snaga II", 14.0)],
        [("Gothmog", 2.8), ("Lurz", 5.6)],
    ]
    positions = {
        node: (x, float(level * 2))
        for level, row in enumerate(rows)
        for node, x in row
    }
    families = {
        "founders": {"mother": "AdÃ¨le", "father": "Boldog", "children": ["Balcmeg", "Gorgol", "Othrod"]},
        "gorgol": {"mother": "BÃ©atrice", "father": "Gorgol", "children": ["GrishnÃ¡kh", "Orcobal"]},
        "split": {"mother": "Camille", "father": "GrishnÃ¡kh", "children": ["Gorbag", "Lugdush", "Orc-chieftain", "Shagrat"]},
        "shagrat": {"mother": "Delphine", "father": "Shagrat", "children": ["Lagduf", "Muzgash", "Snaga I"]},
        "ufthak": {"mother": "Florence", "father": "Ufthak", "children": ["Naglur-Danlo"]},
        "naglur": {"mother": "GisÃ¨le", "father": "Naglur-Danlo", "children": ["Gothmog"]},
        "lugdush": {"mother": "HÃ©lÃ¨ne", "father": "Lugdush", "children": ["UglÃºk"]},
        "ugluk": {"mother": "Isabelle", "father": "UglÃºk", "children": ["MauhÃºr", "Snaga II"]},
        "mauhur": {"mother": "Josette", "father": "MauhÃºr", "children": ["Lurz"]},
        "lagduf": {"mother": "Ã‰lodie", "father": "Lagduf", "children": ["Lug", "Ufthak"]},
    }
    return positions, families


class HeritageTrackPedigreeRouterTest(unittest.TestCase):
    def test_family_junction_is_midpoint_and_only_child_may_enter_straight(self):
        positions = {"Dam": (-2.0, 0.0), "Sire": (2.0, 0.0), "Child": (3.0, 4.0)}
        families = {
            "family": {"mother": "Dam", "father": "Sire", "children": ["Child"]}
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)

        junction = plan.family_positions["family"]
        parent_midpoint = (
            plan.animal_positions["Dam"][0] + plan.animal_positions["Sire"][0]
        ) / 2.0
        self.assertAlmostEqual(junction[0], parent_midpoint)
        for parent in ("Dam", "Sire"):
            segments = plan.route_segments("family", parent)
            self.assertGreaterEqual(len(segments), 2)
            self.assertEqual(segments[0][0][1], segments[0][1][1])
            self.assertEqual(segments[-1][0][0], segments[-1][1][0])
        child_segments = plan.route_segments("family", "Child")
        self.assertEqual(child_segments, [(junction, plan.animal_positions["Child"])])
        self.assertAlmostEqual(child_segments[0][0][0], child_segments[0][1][0])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])

    def test_karl_wilhelm_sibling_branches_are_symmetric_and_deterministic(self):
        positions = {
            node: (float(index), 0.0)
            for index, node in enumerate(
                (
                    "Erika II",
                    "Ori",
                    "Wilhelm",
                    "Karl",
                    "Dori",
                    "Nori",
                    "Rita",
                    "Heike",
                    "Konrad II",
                    "Jutta",
                    "Dieter",
                )
            )
        }
        families = {
            "origin": {
                "mother": "Heike",
                "father": "Konrad II",
                "children": ["Karl", "Wilhelm"],
            },
            "karl_branch": {
                "mother": "Rita",
                "father": "Karl",
                "children": ["Dori", "Nori"],
            },
            "wilhelm_branch": {
                "mother": "Erika II",
                "father": "Wilhelm",
                "children": ["Ori"],
            },
            "konrad_origin": {
                "mother": "Jutta",
                "father": "Dieter",
                "children": ["Konrad II"],
            },
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)
        reversed_plan = router.plan(
            dict(reversed(list(positions.items()))),
            dict(reversed(list(families.items()))),
            labels=dict(reversed(list(labels.items()))),
        )

        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])
        self.assertEqual(plan.animal_positions, reversed_plan.animal_positions)
        self.assertEqual(plan.family_positions, reversed_plan.family_positions)
        self.assertEqual(plan.routes, reversed_plan.routes)
        self.assertEqual(plan.crossing_gaps, reversed_plan.crossing_gaps)
        self.assertEqual(plan.unresolved, reversed_plan.unresolved)

        origin_x = plan.family_positions["origin"][0]
        karl_x, karl_y = plan.animal_positions["Karl"]
        wilhelm_x, wilhelm_y = plan.animal_positions["Wilhelm"]
        self.assertAlmostEqual(karl_y, wilhelm_y)
        self.assertAlmostEqual(karl_x + wilhelm_x, 2.0 * origin_x)
        self.assertLess((karl_x - origin_x) * (wilhelm_x - origin_x), 0.0)

        karl_family_x = plan.family_positions["karl_branch"][0]
        wilhelm_family_x = plan.family_positions["wilhelm_branch"][0]
        self.assertGreater((karl_family_x - origin_x) * (karl_x - origin_x), 0.0)
        self.assertGreater((wilhelm_family_x - origin_x) * (wilhelm_x - origin_x), 0.0)
        self.assertGreater(
            (plan.animal_positions["Rita"][0] - karl_x) * (karl_x - origin_x),
            0.0,
        )
        self.assertGreater(
            (plan.animal_positions["Erika II"][0] - wilhelm_x)
            * (wilhelm_x - origin_x),
            0.0,
        )

        dori_x, dori_y = plan.animal_positions["Dori"]
        nori_x, nori_y = plan.animal_positions["Nori"]
        self.assertAlmostEqual(dori_y, nori_y)
        self.assertAlmostEqual(dori_x + nori_x, 2.0 * karl_family_x)
        self.assertAlmostEqual(
            plan.animal_positions["Ori"][0],
            wilhelm_family_x,
        )

    def test_parent_offspring_pairing_keeps_generations_and_canonical_routes(self):
        positions = {
            "Dam": (-2.0, 0.0),
            "Sire": (2.0, 0.0),
            "Daughter": (0.0, 2.0),
            "Child": (0.0, 4.0),
        }
        families = {
            "origin": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Daughter"],
            },
            "consanguine": {
                "mother": "Dam",
                "father": "Daughter",
                "children": ["Child"],
            },
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)

        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])
        dam_y = plan.animal_positions["Dam"][1]
        self.assertAlmostEqual(dam_y, plan.animal_positions["Sire"][1])
        self.assertGreater(plan.animal_positions["Daughter"][1], dam_y)
        self.assertGreater(
            plan.animal_positions["Child"][1],
            plan.animal_positions["Daughter"][1],
        )

        for family_id, child in (("origin", "Daughter"), ("consanguine", "Child")):
            junction = plan.family_positions[family_id]
            child_path = plan.route_segments(family_id, child)
            self.assertEqual(len(child_path), 1)
            self.assertAlmostEqual(junction[0], plan.animal_positions[child][0])
            self.assertAlmostEqual(child_path[0][0][0], child_path[0][1][0])

        for family_id, parents in (
            ("origin", ("Dam", "Sire")),
            ("consanguine", ("Dam", "Daughter")),
        ):
            parent_xs = [plan.animal_positions[parent][0] for parent in parents]
            self.assertAlmostEqual(
                plan.family_positions[family_id][0],
                sum(parent_xs) / 2.0,
            )
            for parent in parents:
                segments = plan.route_segments(family_id, parent)
                self.assertEqual(len(segments), 2)
                self.assertAlmostEqual(segments[0][0][1], segments[0][1][1])
                self.assertAlmostEqual(segments[-1][0][0], segments[-1][1][0])

    def test_sibling_pairing_stays_centered_and_legible(self):
        positions = {
            "Dam": (-2.0, 0.0),
            "Sire": (2.0, 0.0),
            "Sister": (-3.0, 2.0),
            "Brother": (0.5, 2.0),
            "Child": (4.0, 4.0),
        }
        families = {
            "origin": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Sister", "Brother"],
            },
            "consanguine": {
                "mother": "Sister",
                "father": "Brother",
                "children": ["Child"],
            },
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)

        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])
        origin_x = plan.family_positions["origin"][0]
        mating_x = plan.family_positions["consanguine"][0]
        sister_x, sister_y = plan.animal_positions["Sister"]
        brother_x, brother_y = plan.animal_positions["Brother"]
        self.assertAlmostEqual(sister_y, brother_y)
        self.assertAlmostEqual(sister_x + brother_x, 2.0 * origin_x)
        self.assertAlmostEqual(origin_x, mating_x)
        self.assertGreater(sister_y, plan.animal_positions["Dam"][1])
        self.assertGreater(plan.animal_positions["Child"][1], sister_y)
        for sibling in ("Sister", "Brother"):
            self.assertEqual(len(plan.route_segments("origin", sibling)), 1)
        child_segments = plan.route_segments("consanguine", "Child")
        self.assertEqual(len(child_segments), 1)
        self.assertAlmostEqual(child_segments[0][0][0], child_segments[0][1][0])

    def test_multi_mate_hub_uses_distinct_family_slots(self):
        positions = {
            "Hub": (0.0, 0.0),
            "Mate A": (1.0, 0.0),
            "Mate B": (2.0, 0.0),
            "Child A": (3.0, 2.0),
            "Child B": (4.0, 2.0),
        }
        families = {
            "family_a": {
                "mother": "Mate A",
                "father": "Hub",
                "children": ["Child A"],
            },
            "family_b": {
                "mother": "Mate B",
                "father": "Hub",
                "children": ["Child B"],
            },
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)

        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])
        self.assertEqual(len(set(plan.animal_positions.values())), len(positions))
        self.assertNotEqual(
            plan.family_positions["family_a"],
            plan.family_positions["family_b"],
        )
        for family_id, child in (("family_a", "Child A"), ("family_b", "Child B")):
            junction = plan.family_positions[family_id]
            child_position = plan.animal_positions[child]
            self.assertAlmostEqual(junction[0], child_position[0])
            child_segments = plan.route_segments(family_id, child)
            self.assertEqual(len(child_segments), 1)
            self.assertAlmostEqual(child_segments[0][0][0], child_segments[0][1][0])

    def test_real_finwe_multi_mate_and_large_sibling_fan_has_no_knots(self):
        positions = {
            "Finwe": (0.0, 0.0),
            "Miriel": (-2.0, 0.0),
            "Indis": (2.0, 0.0),
            "Feanor": (-1.0, 2.0),
            "Findis": (0.0, 2.0),
            "Fingolfin": (1.0, 2.0),
            "Irime": (2.0, 2.0),
            "Finarfin": (3.0, 2.0),
        }
        families = {
            "miriel_finwe": {
                "mother": "Miriel",
                "father": "Finwe",
                "children": ["Feanor"],
            },
            "indis_finwe": {
                "mother": "Indis",
                "father": "Finwe",
                "children": ["Findis", "Fingolfin", "Irime", "Finarfin"],
            },
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)

        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])
        self.assertNotEqual(plan.family_positions["miriel_finwe"], plan.family_positions["indis_finwe"])
        self.assertEqual(len(plan.route_segments("miriel_finwe", "Feanor")), 1)
        for child in families["indis_finwe"]["children"]:
            self.assertEqual(len(plan.route_segments("indis_finwe", child)), 1)

    def test_real_balanced_papio_branches_are_vertical_compact_and_conflict_free(self):
        positions, families = _papio_branch_fixture()
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)

        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])
        xs = [x for x, _y in plan.animal_positions.values()]
        ys = [y for _x, y in plan.animal_positions.values()]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        self.assertLess(width, height * 2.0)
        for family_id, family in families.items():
            children = family["children"]
            if len(children) == 1:
                child = children[0]
                self.assertAlmostEqual(
                    plan.family_positions[family_id][0],
                    plan.animal_positions[child][0],
                )

    def test_corrupt_parent_cycle_is_finite_and_deterministic(self):
        positions = {
            "A": (-1.0, 2.0),
            "B": (1.0, 2.0),
            "X": (-3.0, 0.0),
            "Y": (3.0, 0.0),
            "C": (0.0, 4.0),
        }
        families = {
            "a_origin": {"mother": "B", "father": "X", "children": ["A"]},
            "b_origin": {"mother": "A", "father": "Y", "children": ["B"]},
            "offspring": {"mother": "A", "father": "B", "children": ["C"]},
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        first = router.plan(positions, families, labels=labels)
        second = router.plan(
            dict(reversed(list(positions.items()))),
            dict(reversed(list(families.items()))),
            labels=dict(reversed(list(labels.items()))),
        )

        self.assertEqual(set(first.animal_positions), set(positions))
        self.assertTrue(all(math.isfinite(value) for point in first.all_points() for value in point))
        self.assertEqual(first.animal_positions, second.animal_positions)
        self.assertEqual(first.family_positions, second.family_positions)
        self.assertEqual(first.routes, second.routes)
        self.assertEqual(first.crossing_gaps, second.crossing_gaps)
        self.assertEqual(first.unresolved, second.unresolved)
        self.assertEqual(
            router.validate_plan(first, families, labels=labels),
            router.validate_plan(second, families, labels=labels),
        )

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
                sibling_segments = [
                    plan.route_segments("family::arwen::aragorn_ii", child)
                    for child in ("Eldarion", "Placeholder")
                ]
                self.assertTrue(all(len(segments) == 1 for segments in sibling_segments))
                self.assertTrue(
                    any(
                        segment[0][0] != segment[1][0]
                        and segment[0][1] != segment[1][1]
                        for segments in sibling_segments
                        for segment in segments
                    )
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
            "family_a": {"mother": "", "father": "", "children": ["Animal A"]},
            "family_b": {"mother": "", "father": "", "children": ["Animal B"]},
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

        self.assertEqual(len(drawn), 2)
        self.assertAlmostEqual(drawn[0][1][1], -0.1)
        self.assertAlmostEqual(drawn[1][0][1], 0.1)
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

    def test_production_plan_bounds_candidate_work_and_skips_full_validation(self):
        _parent_map, families, positions = _eldarion_fixture()
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        with patch.object(
            router,
            "validate_plan",
            side_effect=AssertionError("production plan must not run full diagnostics"),
        ), patch.object(router, "_score_path", wraps=router._score_path) as score_path:
            plan = router.plan(positions, families, labels=labels)

        self.assertEqual(plan.unresolved, [])
        self.assertLess(score_path.call_count, 600)

    def test_drag_redraw_reuses_artists_instead_of_allocating_each_move(self):
        source = (REPO_ROOT / "Plugins" / "Heritage_Track" / "heritage_track_widget.py").read_text(
            encoding="utf-8"
        )
        drag_method = source.split("    def _redraw_dragged_nodes", 1)[1].split(
            "    def _finish_drag_blit", 1
        )[0]

        self.assertIn("set_offsets", source)
        self.assertIn("self.ax.draw_artist", drag_method)
        self.assertNotIn("self.ax.scatter", drag_method)
        self.assertNotIn("self.ax.text", drag_method)

    def test_routing_warning_language_key_exists(self):
        for path in (REPO_ROOT / "lang").glob("messages_*.json"):
            with self.subTest(path=path.name):
                messages = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("heritage_track.status.routing_warning", messages)


if __name__ == "__main__":
    unittest.main()
