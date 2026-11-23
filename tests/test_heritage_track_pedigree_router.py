import json
import math
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from Plugins.Heritage_Track.layout_pipeline import LayoutPipeline, parse_complete_birth_date_ordinal
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine
from Plugins.Heritage_Track.pedigree_router import (
    PedigreeRouter,
    Rect,
    RoutePlan,
    _OwnedSegment,
)
from Plugins.Heritage_Track.display_context import FrozenRoutePlan
from Plugins.Heritage_Track.ghost_strategies import VisibleFamilyCompletenessGhostStrategy
from tests.heritage_test_support import reported_subtest


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
    def test_set_backed_child_collection_has_deterministic_order(self):
        self.assertEqual(
            PedigreeRouter._children({"children": {"beta", "Alpha", "gamma"}}),
            ["Alpha", "beta", "gamma"],
        )

    def test_large_sparse_path_runs_final_horizontal_clearance(self):
        """The >256-node performance path keeps its hard label obstacle rule."""
        router = PedigreeRouter()
        children = [f"Child {index:03d}" for index in range(255)]
        nodes = {"Mother", "Father", *children}
        positions = {node: (0.0, 0.0) for node in nodes}
        families = {
            "family": {
                "mother": "Mother",
                "father": "Father",
                "children": children,
            }
        }
        labels = {node: node for node in nodes}
        adjusted = router._arrange_nodes(
            positions,
            families,
            labels,
            set(),
            False,
            preserve_y=False,
        )
        obstacles = router.node_obstacles(adjusted, labels, False)
        ordered = sorted(obstacles.items(), key=lambda item: item[1].left)
        overlaps = []
        for index, (first, first_rect) in enumerate(ordered):
            for second, second_rect in ordered[index + 1 :]:
                if second_rect.left >= first_rect.right:
                    break
                if first_rect.top > second_rect.bottom and second_rect.top > first_rect.bottom:
                    overlaps.append((first, second))
        self.assertEqual(overlaps, [])

    def test_visible_family_completeness_adds_only_missing_coparent(self):
        strategy = VisibleFamilyCompletenessGhostStrategy(
            families={
                "isildur": {
                    "mother": "Madison",
                    "father": "Elros",
                    "children": ["Isildur"],
                },
                "elros_origin": {
                    "mother": "Elwing",
                    "father": "Earendil",
                    "children": ["Elros"],
                },
            }
        )

        ghosts = strategy.find_ghosts(
            {"Elros", "Isildur"},
            engine=None,
            archived_animals={"Madison"},
        )

        self.assertEqual(ghosts, {"Madison"})

    def test_one_sided_visible_ancestry_puts_founder_outward(self):
        positions = {
            "Ancestor dam": (-6.0, 0.0),
            "Ancestor sire": (-2.0, 0.0),
            "Founder": (-1.0, 4.0),
            "Continuing": (1.0, 4.8),
            "Focus": (0.0, 8.0),
        }
        families = {
            "origin": {
                "mother": "Ancestor dam",
                "father": "Ancestor sire",
                "children": ["Continuing"],
            },
            "focus": {
                "mother": "Continuing",
                "father": "Founder",
                "children": ["Focus"],
            },
        }
        y_before = {node: point[1] for node, point in positions.items()}
        router = PedigreeRouter()

        changed = router._orient_isolated_pairs_by_visible_ancestry(
            positions,
            families,
            {node: node for node in positions},
            {
                "Continuing": {"Continuing", "Founder"},
                "Founder": {"Continuing", "Founder"},
            },
            show_inbreeding=False,
            chronological=True,
        )

        self.assertTrue(changed)
        self.assertLess(positions["Continuing"][0], positions["Founder"][0])
        self.assertLess(
            abs(positions["Continuing"][0] + 4.0),
            abs(positions["Founder"][0] + 4.0),
        )
        self.assertEqual(
            {node: point[1] for node, point in positions.items()}, y_before
        )

    def test_visible_ancestry_orientation_is_depth_aware_and_input_stable(self):
        base_positions = {
            "Ancestor dam": (-6.0, 0.0),
            "Ancestor sire": (-2.0, 0.0),
            "Founder": (-1.0, 4.0),
            "Continuing": (1.0, 4.0),
            "Focus": (0.0, 8.0),
        }
        focus_family = {
            "focus": {
                "mother": "Continuing",
                "father": "Founder",
                "children": ["Focus"],
            }
        }
        visible_families = {
            "origin": {
                "mother": "Ancestor dam",
                "father": "Ancestor sire",
                "children": ["Continuing"],
            },
            **focus_family,
        }
        blocks = {
            "Continuing": {"Continuing", "Founder"},
            "Founder": {"Continuing", "Founder"},
        }
        labels = {node: node for node in base_positions}
        router = PedigreeRouter()

        clipped = deepcopy(base_positions)
        self.assertFalse(
            router._orient_isolated_pairs_by_visible_ancestry(
                clipped,
                focus_family,
                labels,
                blocks,
                show_inbreeding=False,
            )
        )
        self.assertEqual(clipped, base_positions)

        forward = deepcopy(base_positions)
        reverse = dict(reversed(list(base_positions.items())))
        router._orient_isolated_pairs_by_visible_ancestry(
            forward,
            visible_families,
            labels,
            blocks,
            show_inbreeding=False,
        )
        router._orient_isolated_pairs_by_visible_ancestry(
            reverse,
            dict(reversed(list(visible_families.items()))),
            dict(reversed(list(labels.items()))),
            dict(reversed(list(blocks.items()))),
            show_inbreeding=False,
        )
        self.assertEqual(forward, reverse)

    def test_visible_ancestry_orientation_follows_primary_lineage_above_focus(self):
        base_positions = {
            "Ancestor dam": (-6.0, 0.0),
            "Ancestor sire": (-2.0, 0.0),
            "Founder": (-1.0, 4.0),
            "Continuing": (1.0, 4.0),
            "Intermediate": (0.0, 8.0),
            "Descendant mate": (2.0, 8.0),
            "Selected": (1.0, 12.0),
            "Ghost dam": (14.0, 0.0),
            "Ghost sire": (18.0, 0.0),
            "Ghost founder": (19.0, 4.0),
            "Ghost continuing": (21.0, 4.0),
            "Ghost child": (20.0, 8.0),
        }
        families = {
            "origin": {
                "mother": "Ancestor dam",
                "father": "Ancestor sire",
                "children": ["Continuing"],
            },
            "lineage pair": {
                "mother": "Continuing",
                "father": "Founder",
                "children": ["Intermediate"],
            },
            "selected family": {
                "mother": "Intermediate",
                "father": "Descendant mate",
                "children": ["Selected"],
            },
            "ghost origin": {
                "mother": "Ghost dam",
                "father": "Ghost sire",
                "children": ["Ghost continuing"],
            },
            "ghost pair": {
                "mother": "Ghost continuing",
                "father": "Ghost founder",
                "children": ["Ghost child"],
            },
        }
        blocks = {
            "Continuing": {"Continuing", "Founder"},
            "Founder": {"Continuing", "Founder"},
            "Intermediate": {"Intermediate", "Descendant mate"},
            "Descendant mate": {"Intermediate", "Descendant mate"},
            "Ghost continuing": {"Ghost continuing", "Ghost founder"},
            "Ghost founder": {"Ghost continuing", "Ghost founder"},
        }
        labels = {node: node for node in base_positions}
        router = PedigreeRouter()

        forward = deepcopy(base_positions)
        ghost_before = {
            node: forward[node]
            for node in ("Ghost continuing", "Ghost founder")
        }
        self.assertTrue(
            router._orient_isolated_pairs_by_visible_ancestry(
                forward,
                families,
                labels,
                blocks,
                show_inbreeding=False,
                focus_nodes={"Selected"},
            )
        )
        self.assertLess(forward["Continuing"][0], forward["Founder"][0])
        self.assertEqual(
            {
                node: forward[node]
                for node in ("Ghost continuing", "Ghost founder")
            },
            ghost_before,
        )

        reverse = dict(reversed(list(base_positions.items())))
        router._orient_isolated_pairs_by_visible_ancestry(
            reverse,
            dict(reversed(list(families.items()))),
            dict(reversed(list(labels.items()))),
            dict(reversed(list(blocks.items()))),
            show_inbreeding=False,
            focus_nodes={"Selected"},
        )
        self.assertEqual(forward, reverse)

        clipped = deepcopy(base_positions)
        clipped_families = {
            key: value for key, value in families.items() if key != "origin"
        }
        self.assertFalse(
            router._orient_isolated_pairs_by_visible_ancestry(
                clipped,
                clipped_families,
                labels,
                blocks,
                show_inbreeding=False,
                focus_nodes={"Selected"},
            )
        )
        self.assertEqual(clipped, base_positions)

    def test_one_sided_orientation_is_not_a_hard_sex_order_and_respects_collision_veto(self):
        positions = {
            "Ancestor dam": (2.0, 0.0),
            "Ancestor sire": (6.0, 0.0),
            "Founder": (1.0, 4.0),
            "Continuing": (-1.0, 4.0),
            "Focus": (0.0, 8.0),
        }
        families = {
            "origin": {
                "mother": "Ancestor dam",
                "father": "Ancestor sire",
                "children": ["Continuing"],
            },
            "focus": {
                "mother": "Continuing",
                "father": "Founder",
                "children": ["Focus"],
            },
        }
        blocks = {
            "Continuing": {"Continuing", "Founder"},
            "Founder": {"Continuing", "Founder"},
        }
        router = PedigreeRouter()
        self.assertTrue(
            router._orient_isolated_pairs_by_visible_ancestry(
                positions,
                families,
                {node: node for node in positions},
                blocks,
                show_inbreeding=False,
            )
        )
        self.assertGreater(positions["Continuing"][0], positions["Founder"][0])

        blocked = {
            "Ancestor dam": (-6.0, 0.0),
            "Ancestor sire": (-2.0, 0.0),
            "Founder": (-1.0, 4.0),
            "Continuing": (1.0, 4.0),
            "Focus": (0.0, 8.0),
            "Blocker": (-2.5, 3.0),
        }
        blocked_families = {
            "origin": {
                "mother": "Ancestor dam",
                "father": "Ancestor sire",
                "children": ["Continuing"],
            },
            "focus": {
                "mother": "Continuing",
                "father": "Founder",
                "children": ["Focus"],
            },
        }
        blocked_before = deepcopy(blocked)
        self.assertFalse(
            router._orient_isolated_pairs_by_visible_ancestry(
                blocked,
                blocked_families,
                {node: node for node in blocked},
                blocks,
                show_inbreeding=False,
            )
        )
        self.assertEqual(blocked, blocked_before)

    def test_both_visible_origins_orient_inward_but_multi_mate_hub_is_preserved(self):
        positions = {
            "A dam": (-6.0, 0.0),
            "A sire": (-4.0, 0.0),
            "B dam": (4.0, 0.0),
            "B sire": (6.0, 0.0),
            "A": (1.0, 4.0),
            "B": (-1.0, 4.0),
            "Child": (0.0, 8.0),
        }
        families = {
            "a_origin": {"mother": "A dam", "father": "A sire", "children": ["A"]},
            "b_origin": {"mother": "B dam", "father": "B sire", "children": ["B"]},
            "child": {"mother": "A", "father": "B", "children": ["Child"]},
        }
        router = PedigreeRouter()
        router._orient_isolated_pairs_by_visible_ancestry(
            positions,
            families,
            {node: node for node in positions},
            {"A": {"A", "B"}, "B": {"A", "B"}},
            show_inbreeding=False,
        )
        self.assertLess(positions["A"][0], positions["B"][0])

        multi_positions = {
            "Old dam": (-3.0, 0.0),
            "Old sire": (-1.0, 0.0),
            "Hub": (0.0, 4.0),
            "Mate A": (-2.0, 4.0),
            "Mate B": (2.0, 4.0),
            "Child A": (-1.0, 8.0),
            "Child B": (1.0, 8.0),
        }
        multi_families = {
            "origin": {"mother": "Old dam", "father": "Old sire", "children": ["Hub"]},
            "a": {"mother": "Mate A", "father": "Hub", "children": ["Child A"]},
            "b": {"mother": "Mate B", "father": "Hub", "children": ["Child B"]},
        }
        before = deepcopy(multi_positions)
        block = {
            node: {"Hub", "Mate A", "Mate B"}
            for node in ("Hub", "Mate A", "Mate B")
        }
        self.assertFalse(
            router._orient_isolated_pairs_by_visible_ancestry(
                multi_positions,
                multi_families,
                {node: node for node in multi_positions},
                block,
                show_inbreeding=False,
            )
        )
        self.assertEqual(multi_positions, before)

    def test_terminal_sibling_is_opposite_continuing_ancestor_of_focus(self):
        positions = {
            "Dam": (-2.0, 0.0),
            "Sire": (2.0, 0.0),
            "Terminal": (2.5, 4.0),
            "Continuing": (4.0, 4.0),
            "Mate": (6.0, 4.0),
            "Selected descendant": (5.0, 8.0),
        }
        families = {
            "siblings": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Terminal", "Continuing"],
            },
            "descendants": {
                "mother": "Continuing",
                "father": "Mate",
                "children": ["Selected descendant"],
            },
        }
        router = PedigreeRouter()
        continuing_offset = (
            positions["Selected descendant"][0] - positions["Continuing"][0]
        )

        weights = router._compact_focused_terminal_sibling_fans(
            positions,
            families,
            {node: node for node in positions},
            {"Selected descendant"},
            {
                "Continuing": {"Continuing", "Mate"},
                "Mate": {"Continuing", "Mate"},
            },
        )

        parent_center = (
            positions["Dam"][0] + positions["Sire"][0]
        ) / 2.0
        self.assertTrue(weights)
        self.assertGreater(positions["Continuing"][0], parent_center)
        self.assertLess(positions["Terminal"][0], parent_center)
        self.assertAlmostEqual(
            positions["Selected descendant"][0] - positions["Continuing"][0],
            continuing_offset,
        )

    def test_mixed_sibling_branch_roles_are_structural_and_get_distinct_lanes(self):
        positions = {
            "Dam": (-2.0, 0.0),
            "Sire": (2.0, 0.0),
            "Terminal A": (-1.0, 4.0),
            "Terminal B": (1.0, 4.0),
            "Continuing": (3.0, 4.0),
            "Mate": (5.0, 4.0),
            "Selected descendant": (4.0, 8.0),
        }
        families = {
            "siblings": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Terminal A", "Terminal B", "Continuing"],
            },
            "descendants": {
                "mother": "Continuing",
                "father": "Mate",
                "children": ["Selected descendant"],
            },
        }
        router = PedigreeRouter()

        roles = router._classify_visible_branch_roles(positions, families)
        self.assertEqual(roles["Continuing"], "continuing")
        self.assertEqual(roles["Terminal A"], "terminal")
        self.assertEqual(roles["Terminal B"], "terminal")

        self.assertTrue(
            router._stagger_focused_terminal_siblings(
                positions,
                families,
                {node: node for node in positions},
                {"Selected descendant"},
            )
        )
        self.assertNotEqual(positions["Terminal A"][1], positions["Terminal B"][1])

    def test_parent_corridor_expands_layout_while_route_stays_canonical(self):
        positions = {
            "Father": (0.0, 0.0),
            "Mother": (4.0, 0.0),
            "Child": (2.0, 4.0),
            "Blocker": (0.05, 2.0),
        }
        families = {
            "family": {
                "mother": "Mother",
                "father": "Father",
                "children": ["Child"],
            }
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(
            positions,
            families,
            labels=labels,
            focus_nodes={"Child"},
        )

        self.assertEqual(len(plan.route_segments("family", "Father")), 2)
        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])

    def test_family_junction_keeps_parent_midpoint_despite_foreign_label(self):
        positions = {
            "Mother": (0.0, 0.0),
            "Father": (6.0, 0.0),
            "Child": (0.0, 4.0),
            "Foreign": (1.68, 2.08),
        }
        families = {
            "family": {
                "mother": "Mother",
                "father": "Father",
                "children": ["Child"],
            }
        }
        labels = {node: node for node in positions}
        labels["Foreign"] = "03.07.2015"
        router = PedigreeRouter()
        obstacles = router.node_obstacles(positions, labels, False)

        junction = router._place_junctions(
            positions,
            families,
            obstacles,
        )["family"]

        parent_midpoint = 3.0
        self.assertAlmostEqual(junction[0], parent_midpoint)
        self.assertGreater(junction[0], positions["Mother"][0])
        self.assertLess(junction[0], positions["Father"][0])

    def test_multi_child_family_junction_repair_never_shifts_parent_midpoint(self):
        positions = {
            "Mother": (0.0, 0.0),
            "Father": (10.0, 0.0),
            "Child A": (1.0, 5.0),
            "Child B": (8.0, 5.0),
            "Foreign": (2.5, 4.0),
        }
        families = {
            "family": {
                "mother": "Mother",
                "father": "Father",
                "children": ["Child A", "Child B"],
            }
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()
        original_animal_positions = dict(positions)
        junctions = router._place_junctions(
            positions,
            families,
            router.node_obstacles(positions, labels, False),
        )
        parent_midpoint = (
            positions["Mother"][0] + positions["Father"][0]
        ) / 2.0
        initial_x = junctions["family"][0]
        self.assertTrue(
            router._family_route_marker_hit_details(positions, families, junctions)
        )

        router._repair_family_junction_route_markers(
            positions, families, junctions
        )

        self.assertAlmostEqual(junctions["family"][0], parent_midpoint)
        self.assertAlmostEqual(initial_x, junctions["family"][0])
        self.assertEqual(positions, original_animal_positions)
        self.assertGreater(junctions["family"][0], positions["Mother"][0])
        self.assertLess(junctions["family"][0], positions["Father"][0])

    def test_disconnected_family_components_are_packed_without_internal_changes(self):
        positions = {
            "Father A": (-2.0, 0.0),
            "Mother A": (2.0, 0.0),
            "Child A": (0.0, 4.0),
            "Father B": (98.0, 0.0),
            "Mother B": (102.0, 0.0),
            "Child B": (100.0, 4.0),
        }
        families = {
            "a": {"mother": "Mother A", "father": "Father A", "children": ["Child A"]},
            "b": {"mother": "Mother B", "father": "Father B", "children": ["Child B"]},
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, families, labels=labels)

        xs = [point[0] for point in plan.animal_positions.values()]
        self.assertLess(max(xs) - min(xs), 15.0)
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])

    def test_family_junction_stays_between_parents_and_child_route_is_direct(self):
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
        parent_xs = sorted(
            (plan.animal_positions["Dam"][0], plan.animal_positions["Sire"][0])
        )
        self.assertGreater(junction[0], parent_xs[0])
        self.assertLess(junction[0], parent_xs[1])
        self.assertAlmostEqual(junction[0], parent_midpoint)
        for parent in ("Dam", "Sire"):
            segments = plan.route_segments("family", parent)
            self.assertGreaterEqual(len(segments), 2)
            self.assertEqual(segments[0][0][1], segments[0][1][1])
            self.assertEqual(segments[-1][0][0], segments[-1][1][0])
        child_segments = plan.route_segments("family", "Child")
        self.assertEqual(child_segments, [(junction, plan.animal_positions["Child"])])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])

    def test_sole_child_uses_parent_midpoint_in_both_layout_modes(self):
        positions = {
            "Dam": (-2.0, 0.0),
            "Sire": (2.0, 0.0),
            "Only child": (3.0, 4.0),
        }
        families = {
            "family": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Only child"],
            }
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        for mode in ("partner_normalized", "chronological"):
            with reported_subtest(self, "sole-child route geometry", mode=mode):
                plan = router.plan(
                    positions,
                    families,
                    labels=labels,
                    focus_nodes={"Only child"},
                    vertical_layout_mode=mode,
                )
                junction = plan.family_positions["family"]
                child = plan.animal_positions["Only child"]
                self.assertAlmostEqual(
                    junction[0],
                    (plan.animal_positions["Dam"][0]
                     + plan.animal_positions["Sire"][0]) / 2.0,
                )
                child_segments = plan.route_segments("family", "Only child")
                self.assertEqual(len(child_segments), 1)
                self.assertGreaterEqual(
                    math.dist(*child_segments[0]),
                    router._single_child_leg_clearance(
                        chronological=mode == "chronological"
                    ) - 1e-7,
                )
                self.assertTrue(plan.draw_segments("family", "Only child"))
                for parent in ("Dam", "Sire"):
                    self.assertLessEqual(
                        len(plan.route_segments("family", parent)), 2
                    )
                self.assertEqual(
                    router.validate_plan(plan, families, labels=labels), []
                )

    def test_mode_specific_sole_child_floor_and_same_row_parent_route(self):
        """Chronological geometry accepts a collapsed vertical parent entry."""
        positions = {
            "Dam": (-2.0, 0.0),
            "Sire": (2.0, 0.0),
            "Only child": (0.0, 4.0),
        }
        families = {
            "family": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Only child"],
            }
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        partner_plan = router.plan(
            positions,
            families,
            labels=labels,
            focus_nodes={"Only child"},
            vertical_layout_mode="partner_normalized",
        )
        chronological_plan = router.plan(
            positions,
            families,
            labels=labels,
            focus_nodes={"Only child"},
            vertical_layout_mode="chronological",
        )
        self.assertEqual(partner_plan.vertical_layout_mode, "partner_normalized")
        self.assertEqual(chronological_plan.vertical_layout_mode, "chronological")
        self.assertGreaterEqual(
            math.dist(
                partner_plan.family_positions["family"],
                partner_plan.animal_positions["Only child"],
            ),
            2.0 - 1e-7,
        )
        self.assertGreaterEqual(
            math.dist(
                chronological_plan.family_positions["family"],
                chronological_plan.animal_positions["Only child"],
            ),
            1.0 - 1e-7,
        )

        same_row = RoutePlan(
            animal_positions={
                "Dam": (-2.0, 0.0),
                "Sire": (2.0, 0.0),
                "Only child": (0.0, 2.0),
            },
            family_positions={"family": (0.0, 0.0)},
            family_members={"family": {"Dam", "Sire", "Only child"}},
            routes={
                "family": {
                    "Dam": [(0.0, 0.0), (-2.0, 0.0)],
                    "Sire": [(0.0, 0.0), (2.0, 0.0)],
                    "Only child": [(0.0, 0.0), (0.0, 2.0)],
                }
            },
            vertical_layout_mode="chronological",
        )
        self.assertEqual(router.validate_plan(same_row, families, labels=labels), [])

        round_trip = FrozenRoutePlan.from_route_plan(same_row).to_mutable()
        self.assertEqual(round_trip.vertical_layout_mode, "chronological")
        self.assertEqual(router.validate_plan(round_trip, families, labels=labels), [])

    def test_edge_near_sole_child_uses_same_axis_boundary_for_placement_and_validation(self):
        """A child just inside a parent interval must not self-reject its knot."""
        positions = {
            "Dam": (10.9027, 0.0),
            "Sire": (15.6267, 0.0),
            # Deliberately inside the interval but inside the old 0.08
            # validator inset. This is a generic geometry boundary witness,
            # not a seed-specific coordinate rule.
            "Only child": (10.9139, 4.0),
        }
        families = {
            "family": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Only child"],
            }
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        for mode in ("partner_normalized", "chronological"):
            with reported_subtest(self, "edge-near sole-child geometry", mode=mode):
                plan = router.plan(
                    positions,
                    families,
                    labels=labels,
                    focus_nodes={"Only child"},
                    vertical_layout_mode=mode,
                )
                self.assertEqual(
                    router.validate_plan(plan, families, labels=labels), []
                )
                junction = plan.family_positions["family"]
                self.assertAlmostEqual(
                    junction[0],
                    (plan.animal_positions["Dam"][0]
                     + plan.animal_positions["Sire"][0]) / 2.0,
                )

    def test_karl_wilhelm_sibling_branches_are_symmetric_and_deterministic(self):
        generation_y = {
            "Jutta": 0.0,
            "Dieter": 0.0,
            "Konrad II": 3.6,
            "Heike": 3.6,
            "Rita": 3.6,
            "Erika II": 3.6,
            "Karl": 7.2,
            "Wilhelm": 7.2,
            "Dori": 10.8,
            "Nori": 10.8,
            "Ori": 10.8,
        }
        positions = {
            node: (float(index), generation_y[node])
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
        self.assertLess((karl_x - origin_x) * (wilhelm_x - origin_x), 0.0)
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
        self.assertLess(min(dori_x, nori_x), karl_family_x)
        self.assertGreater(max(dori_x, nori_x), karl_family_x)
        self.assertLessEqual(
            abs(plan.animal_positions["Ori"][0] - wilhelm_family_x),
            1.5,
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
            self.assertEqual(child_path[0], (junction, plan.animal_positions[child]))

        for family_id, parents in (
            ("origin", ("Dam", "Sire")),
            ("consanguine", ("Dam", "Daughter")),
        ):
            parent_xs = [plan.animal_positions[parent][0] for parent in parents]
            self.assertGreater(plan.family_positions[family_id][0], min(parent_xs))
            self.assertLess(plan.family_positions[family_id][0], max(parent_xs))
            self.assertAlmostEqual(plan.family_positions[family_id][0], sum(parent_xs) / 2.0)
            for parent in parents:
                segments = plan.route_segments(family_id, parent)
                # Parent/offspring pairings retain the same canonical entry:
                # horizontal at the knot and vertical at the parent.
                self.assertGreaterEqual(len(segments), 2)
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
        self.assertLessEqual(
            abs(((sister_x + brother_x) / 2.0) - origin_x), 0.5
        )
        self.assertLessEqual(abs(origin_x - mating_x), 0.75)
        self.assertGreater(sister_y, plan.animal_positions["Dam"][1])
        self.assertGreater(plan.animal_positions["Child"][1], sister_y)
        for sibling in ("Sister", "Brother"):
            self.assertEqual(len(plan.route_segments("origin", sibling)), 1)
        child_segments = plan.route_segments("consanguine", "Child")
        self.assertEqual(len(child_segments), 1)
        self.assertLessEqual(
            abs(child_segments[0][0][0] - child_segments[0][1][0]), 0.75
        )

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
            child_segments = plan.route_segments(family_id, child)
            self.assertEqual(len(child_segments), 1)
            self.assertEqual(child_segments[0], (junction, child_position))

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
            parents = [
                node for node in (family.get("mother"), family.get("father"))
                if node in plan.animal_positions
            ]
            if len(parents) == 2:
                self.assertAlmostEqual(
                    plan.family_positions[family_id][0],
                    sum(plan.animal_positions[node][0] for node in parents) / 2.0,
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
            with reported_subtest(
                self,
                "Eldarion birthdate-height layout",
                birthdate_height_layout=birthdate_height_layout,
            ):
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
                for family_id, family in families.items():
                    parents = [
                        parent
                        for parent in (family.get("mother"), family.get("father"))
                        if parent in plan.animal_positions
                    ]
                    children = [
                        child
                        for child in family.get("children", [])
                        if child in plan.animal_positions
                    ]
                    if not parents or not children:
                        continue
                    with reported_subtest(
                        self,
                        "Eldarion family geometry",
                        family_id=family_id,
                    ):
                        junction_x = plan.family_positions[family_id][0]
                        if len(parents) == 2:
                            parent_xs = sorted(
                                plan.animal_positions[parent][0]
                                for parent in parents
                            )
                            self.assertGreater(junction_x, parent_xs[0])
                            self.assertLess(junction_x, parent_xs[1])
                            self.assertAlmostEqual(junction_x, sum(parent_xs) / 2.0)
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

    def test_label_projection_keeps_existing_left_to_right_order(self):
        """Collision resolution must not fix overlap by swapping animals."""
        router = PedigreeRouter()
        positions = {"Alpha": (1.0, 0.0), "Beta": (-1.0, 0.0)}
        labels = {
            "Alpha": "Alpha very long display label",
            "Beta": "Beta very long display label",
        }

        router._solve_horizontal_constraints(positions, {}, labels, True)

        self.assertGreater(positions["Alpha"][0], positions["Beta"][0])
        obstacles = router.node_obstacles(positions, labels, True)
        self.assertLessEqual(obstacles["Beta"].right, obstacles["Alpha"].left)

    def test_widget_has_cached_hit_test_index_for_dense_graphs(self):
        source = (REPO_ROOT / "Plugins" / "Heritage_Track" / "heritage_track_widget.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("self._hit_grid", source)
        self.assertIn("self._rebuild_hit_test_index()", source)
        self.assertIn("cell_radius", source)

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
            movable_nodes={"Protected blocker"},
        )

        self.assertEqual(plan.animal_positions["Protected blocker"], (1.0, 2.0))
        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(plan, families, labels=labels), [])

    def test_automatic_route_recovery_clears_foreign_marker_without_gap_exemption(self):
        positions = {
            "Dam": (-2.0, 0.0),
            "Sire": (2.0, 0.0),
            "Child": (3.0, 4.0),
            "Blocker": (1.1, 2.45),
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

        plan = router.plan(positions, families, labels=labels)

        problems = router.validate_plan(plan, families, labels=labels)
        self.assertFalse(
            any("foreign animal marker" in problem for problem in problems),
            problems,
        )
        self.assertEqual(plan.unresolved, [])
        self.assertEqual(
            router._route_marker_hit_details(
                plan.animal_positions,
                families,
                labels,
                show_inbreeding=True,
                chronological=False,
            ),
            [],
        )

    def test_family_junction_marker_recovery_uses_vertical_corridor_when_parent_x_is_narrow(self):
        positions = {
            "Dam": (-0.35, 0.0),
            "Sire": (0.35, 0.0),
            "Child": (8.0, 5.0),
            "Foreign marker": (4.0, 4.125),
        }
        families = {
            "family": {
                "mother": "Dam",
                "father": "Sire",
                "children": ["Child"],
            }
        }
        router = PedigreeRouter()
        family_positions = {"family": (0.0, 3.25)}
        before_animals = dict(positions)
        before_hits = router._family_route_marker_hit_details(
            positions, families, family_positions
        )
        self.assertTrue(before_hits)

        changed = router._repair_family_junction_route_markers(
            positions, families, family_positions, chronological=True
        )

        self.assertTrue(changed)
        self.assertEqual(positions, before_animals)
        self.assertLessEqual(abs(family_positions["family"][0]), 0.22)
        self.assertNotAlmostEqual(family_positions["family"][1], 3.25)
        self.assertGreater(family_positions["family"][1], 0.0)
        self.assertLess(family_positions["family"][1], 5.0)
        self.assertEqual(
            router._family_route_marker_hit_details(
                positions, families, family_positions
            ),
            [],
        )

    def test_route_recovery_translates_terminal_sibling_cohort_before_splitting_it(self):
        positions = {
            "Parent A": (-4.0, 8.0),
            "Parent B": (4.0, 8.0),
            "Sibling A": (-3.0, 4.0),
            "Sibling B": (0.0, 4.5),
            "Sibling C": (3.0, 5.0),
            "Route parent A": (-1.0, 10.0),
            "Route parent B": (1.0, 10.0),
            "Target": (0.0, 12.0),
        }
        families = {
            "siblings": {
                "mother": "Parent A",
                "father": "Parent B",
                "children": ["Sibling A", "Sibling B", "Sibling C"],
            },
            "route": {
                "mother": "Route parent A",
                "father": "Route parent B",
                "children": ["Target"],
            },
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        # Keep the geometry witness at the recovery boundary deterministic:
        # the generic collision is present precisely while the middle
        # terminal sibling remains in its original route corridor.
        def witness_hits(candidate, *_args, **_kwargs):
            if abs(candidate["Sibling B"][0]) < 0.1:
                return [("route", "Target", "Sibling B", 0)]
            return []

        cohorts = router._visible_sibling_cohorts(positions, families)
        self.assertEqual(len(cohorts), 1)
        self.assertTrue(cohorts[0][1], "the visible sibling cohort has no in-scope descendants")
        before = dict(positions)
        with patch.object(router, "_route_marker_hit_details", side_effect=witness_hits):
            changed = router._repair_canonical_route_marker_collisions(
                positions,
                families,
                labels,
                set(),
                False,
                preserve_y=True,
            )

        self.assertTrue(changed)
        siblings = ("Sibling A", "Sibling B", "Sibling C")
        shifts = [positions[node][0] - before[node][0] for node in siblings]
        self.assertNotAlmostEqual(shifts[0], 0.0)
        self.assertAlmostEqual(shifts[0], shifts[1])
        self.assertAlmostEqual(shifts[1], shifts[2])
        for node in siblings:
            self.assertEqual(positions[node][1], before[node][1])
        self.assertEqual(
            [positions[node][0] for node in siblings],
            sorted(positions[node][0] for node in siblings),
        )

    def test_route_recovery_can_widen_a_sibling_cohort_without_scattering_it(self):
        positions = {
            "Parent A": (-4.0, 8.0),
            "Parent B": (4.0, 8.0),
            "Sibling A": (-3.0, 4.0),
            "Sibling B": (0.0, 4.5),
            "Sibling C": (3.0, 5.0),
            "Route parent A": (-1.0, 10.0),
            "Route parent B": (1.0, 10.0),
            "Target": (0.0, 12.0),
        }
        families = {
            "siblings": {
                "mother": "Parent A",
                "father": "Parent B",
                "children": ["Sibling A", "Sibling B", "Sibling C"],
            },
            "route": {
                "mother": "Route parent A",
                "father": "Route parent B",
                "children": ["Target"],
            },
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        def witness_hits(candidate, *_args, **_kwargs):
            if candidate["Sibling B"][0] - candidate["Sibling A"][0] < 3.5:
                return [("route", "Target", "Sibling A", 0)]
            return []

        before = dict(positions)
        with patch.object(router, "_route_marker_hit_details", side_effect=witness_hits):
            changed = router._repair_canonical_route_marker_collisions(
                positions,
                families,
                labels,
                set(),
                False,
                preserve_y=True,
            )

        self.assertTrue(changed)
        siblings = ("Sibling A", "Sibling B", "Sibling C")
        self.assertGreaterEqual(
            positions["Sibling B"][0] - positions["Sibling A"][0], 3.5
        )
        self.assertLess(
            positions["Sibling A"][0],
            positions["Sibling B"][0],
        )
        self.assertLess(
            positions["Sibling B"][0],
            positions["Sibling C"][0],
        )
        self.assertNotEqual(positions["Sibling A"][0], before["Sibling A"][0])
        self.assertNotEqual(positions["Sibling C"][0], before["Sibling C"][0])
        for node in siblings:
            self.assertEqual(positions[node][1], before[node][1])

    def test_route_recovery_can_expand_sibling_cohort_beyond_viewport_to_clear_marker(self):
        positions = {
            "Parent A": (-4.0, 8.0),
            "Parent B": (4.0, 8.0),
            "Sibling A": (-3.0, 4.0),
            "Sibling B": (0.0, 4.5),
            "Sibling C": (3.0, 5.0),
            "Route parent A": (-1.0, 10.0),
            "Route parent B": (1.0, 10.0),
            "Target": (0.0, 12.0),
        }
        families = {
            "siblings": {
                "mother": "Parent A",
                "father": "Parent B",
                "children": ["Sibling A", "Sibling B", "Sibling C"],
            },
            "route": {
                "mother": "Route parent A",
                "father": "Route parent B",
                "children": ["Target"],
            },
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        # Model a route that stays blocked until a cohesive reflow needs more
        # horizontal room than a normal viewport can show at readable scale.
        # The route-clearance rule is data-space based; the viewport must not
        # cap the automatic layout or force a foreign-marker crossing.
        def witness_hits(candidate, *_args, **_kwargs):
            span = candidate["Sibling C"][0] - candidate["Sibling A"][0]
            if span < 20.0:
                return [("route", "Target", "Sibling B", 0)]
            return []

        before = dict(positions)
        with patch.object(router, "_route_marker_hit_details", side_effect=witness_hits):
            changed = router._repair_canonical_route_marker_collisions(
                positions,
                families,
                labels,
                set(),
                False,
                preserve_y=True,
            )

        self.assertTrue(changed)
        siblings = ("Sibling A", "Sibling B", "Sibling C")
        self.assertGreaterEqual(
            positions["Sibling C"][0] - positions["Sibling A"][0], 20.0
        )
        self.assertEqual(
            [positions[node][0] for node in siblings],
            sorted(positions[node][0] for node in siblings),
        )
        for node in siblings:
            self.assertEqual(positions[node][1], before[node][1])

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

    def test_shared_endpoint_crossing_away_from_marker_gets_a_gap(self):
        router = PedigreeRouter()
        segments = [
            _OwnedSegment("family_a", "Shared", 0, ((0.0, 0.0), (2.0, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((1.0, -1.0), (1.0, 1.0))),
        ]

        gaps, problems = router._find_crossing_gaps(
            segments,
            {"Shared": (2.0, 0.0)},
        )

        self.assertEqual(problems, [])
        self.assertEqual(gaps, {("family_b", "Shared", 0): [(1.0, 0.0)]})

    def test_shared_endpoint_marker_crossing_is_exempt(self):
        router = PedigreeRouter()
        segments = [
            _OwnedSegment("family_a", "Shared", 0, ((0.0, 0.0), (2.0, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((2.0, 0.0), (3.0, 1.0))),
        ]

        gaps, problems = router._find_crossing_gaps(
            segments,
            {"Shared": (2.0, 0.0)},
        )

        self.assertEqual(gaps, {})
        self.assertEqual(problems, [])

    def test_shared_endpoint_overlap_must_stay_inside_marker(self):
        router = PedigreeRouter()
        outside = [
            _OwnedSegment("family_a", "Shared", 0, ((1.5, 0.0), (2.4, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((1.7, 0.0), (2.5, 0.0))),
        ]
        inside = [
            _OwnedSegment("family_a", "Shared", 0, ((1.9, 0.0), (2.1, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((1.95, 0.0), (2.05, 0.0))),
        ]

        _outside_gaps, outside_problems = router._find_crossing_gaps(
            outside,
            {"Shared": (2.0, 0.0)},
        )
        inside_gaps, inside_problems = router._find_crossing_gaps(
            inside,
            {"Shared": (2.0, 0.0)},
        )

        self.assertEqual(inside_gaps, {})
        self.assertEqual(inside_problems, [])
        self.assertEqual(
            outside_problems,
            ["family_a/family_b: different families share a segment"],
        )

    def test_shared_endpoint_without_position_reports_missing_geometry(self):
        router = PedigreeRouter()
        segments = [
            _OwnedSegment("family_a", "Shared", 0, ((0.0, 0.0), (2.0, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((1.0, -1.0), (1.0, 1.0))),
        ]

        gaps, problems = router._find_crossing_gaps(segments, {})

        self.assertEqual(gaps, {})
        self.assertEqual(
            problems,
            ["family_a/family_b: shared animal endpoint Shared has missing geometry"],
        )

    def test_crossing_gap_preserves_horizontal_family_shoulder(self):
        plan = RoutePlan(
            animal_positions={"Partner": (2.0, 0.0), "Foreign": (1.0, 1.0)},
            family_positions={"partners": (0.0, 0.0), "foreign": (1.0, -1.0)},
            family_members={
                "partners": {"Partner"},
                "foreign": {"Foreign"},
            },
            routes={
                "partners": {"Partner": [(0.0, 0.0), (2.0, 0.0)]},
                "foreign": {
                    "Foreign": [(1.0, -1.0), (1.0, -0.5), (1.0, 1.0)]
                },
            },
        )
        router = PedigreeRouter()

        router.recompute_line_gaps(
            plan,
            animal_gap_obstacles={},
            junction_gap_obstacles={},
        )

        self.assertNotIn(
            ("partners", "Partner", 0),
            plan.line_crossing_gaps,
        )
        self.assertEqual(
            plan.line_crossing_gaps.get(("foreign", "Foreign", 1)),
            [(1.0, 0.0)],
        )
        self.assertEqual(plan.unresolved, [])

    def test_gap_radius_is_pixel_constant_for_horizontal_and_vertical_routes(self):
        pixel_scale = (20.0, 5.0)
        radius_pixels = 2.75
        for endpoint, path in (
            ("Horizontal", [(0.0, 0.0), (10.0, 0.0)]),
            ("Vertical", [(0.0, 0.0), (0.0, 10.0)]),
        ):
            with reported_subtest(self, "route gap radius", endpoint=endpoint):
                plan = RoutePlan(
                    animal_positions={endpoint: path[-1]},
                    family_positions={"family": path[0]},
                    family_members={"family": {endpoint}},
                    routes={"family": {endpoint: path}},
                    crossing_gaps={
                        ("family", endpoint, 0): [
                            (
                                (path[0][0] + path[1][0]) / 2.0,
                                (path[0][1] + path[1][1]) / 2.0,
                            )
                        ]
                    },
                )
                drawn = plan.draw_segments(
                    "family",
                    endpoint,
                    gap_radius_pixels=radius_pixels,
                    pixel_scale=pixel_scale,
                )
                self.assertEqual(len(drawn), 2)
                first_end = drawn[0][1]
                second_start = drawn[1][0]
                pixel_gap = math.hypot(
                    (second_start[0] - first_end[0]) * pixel_scale[0],
                    (second_start[1] - first_end[1]) * pixel_scale[1],
                )
                self.assertAlmostEqual(pixel_gap, radius_pixels * 2.0, places=6)

    def test_foreign_family_knot_uses_supplied_pixel_calibrated_obstacle(self):
        plan = RoutePlan(
            animal_positions={"Child": (4.0, 0.0)},
            family_positions={"source": (0.0, 0.0), "foreign": (2.0, 0.0)},
            family_members={"source": {"Child"}, "foreign": set()},
            routes={"source": {"Child": [(0.0, 0.0), (4.0, 0.0)]}},
        )
        router = PedigreeRouter()
        router.recompute_line_gaps(
            plan,
            animal_gap_obstacles={},
            junction_gap_obstacles={
                "@source": Rect(-0.1, 0.1, -0.1, 0.1),
                "@foreign": Rect(1.8, 2.2, -0.25, 0.25),
            },
        )

        self.assertTrue(plan.crossing_gaps.get(("source", "Child", 0)))
        self.assertEqual(plan.unresolved, [])

    def test_gap_recompute_replaces_stale_masks_after_geometry_move(self):
        positions = {"Animal A": (2.0, 0.0), "Animal B": (1.0, 1.0)}
        routes = {
            "family_a": {"Animal A": [(0.0, 0.0), (2.0, 0.0)]},
            "family_b": {"Animal B": [(1.0, -1.0), (1.0, 1.0)]},
        }
        plan = RoutePlan(
            animal_positions=positions,
            family_positions={"family_a": (0.0, 0.0), "family_b": (1.0, -1.0)},
            family_members={"family_a": {"Animal A"}, "family_b": {"Animal B"}},
            routes=routes,
            crossing_gaps={("stale", "route", 0): [(99.0, 99.0)]},
        )
        router = PedigreeRouter()

        router.recompute_line_gaps(
            plan, labels={node: node for node in positions}
        )
        self.assertIn(("family_b", "Animal B", 0), plan.crossing_gaps)
        self.assertNotIn(("stale", "route", 0), plan.crossing_gaps)

        # Simulate a completed family/group translation: both the junction and
        # its owned route move, then the old crossing mask must disappear.
        plan.family_positions["family_b"] = (3.0, -1.0)
        plan.animal_positions["Animal B"] = (3.0, 1.0)
        plan.routes["family_b"]["Animal B"] = [(3.0, -1.0), (3.0, 1.0)]
        router.recompute_line_gaps(
            plan, labels={node: node for node in plan.animal_positions}
        )
        self.assertNotIn(("family_b", "Animal B", 0), plan.crossing_gaps)

    def test_view_scale_recompute_reuses_structural_line_crossings(self):
        plan = RoutePlan(
            animal_positions={"A": (2.0, 0.0), "B": (1.0, 1.0)},
            family_positions={"fa": (0.0, 0.0), "fb": (1.0, -1.0)},
            family_members={"fa": {"A"}, "fb": {"B"}},
            routes={
                "fa": {"A": [(0.0, 0.0), (2.0, 0.0)]},
                "fb": {"B": [(1.0, -1.0), (1.0, 1.0)]},
            },
        )
        router = PedigreeRouter()
        router.recompute_line_gaps(plan)
        expected = deepcopy(plan.line_crossing_gaps)
        self.assertTrue(plan.line_crossings_ready)
        self.assertTrue(expected)

        with patch.object(
            router,
            "_find_crossing_gaps",
            wraps=router._find_crossing_gaps,
        ) as crossing_scan:
            router.recompute_line_gaps(
                plan,
                animal_gap_obstacles=router.marker_obstacles(
                    plan.animal_positions,
                    half_width=0.1,
                    half_height=0.1,
                ),
                junction_gap_obstacles={},
                recompute_crossings=False,
            )

        crossing_scan.assert_not_called()
        self.assertEqual(plan.line_crossing_gaps, expected)
        self.assertTrue(plan.crossing_gaps)

    def test_automatic_family_knot_clears_foreign_child_route_marker(self):
        positions = {
            "Dam": (-2.0, 0.0),
            "Sire": (2.0, 0.0),
            "Child": (3.0, 4.0),
            "Blocker": (1.1, 2.45),
        }
        families = {
            "family": {"mother": "Dam", "father": "Sire", "children": ["Child"]}
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(
            positions,
            families,
            labels=labels,
        )

        self.assertEqual(plan.crossing_gaps, {})
        self.assertAlmostEqual(
            plan.family_positions["family"][0],
            (plan.animal_positions["Dam"][0] + plan.animal_positions["Sire"][0]) / 2.0,
        )
        blocker = router.marker_obstacles(plan.animal_positions)["Blocker"]
        self.assertTrue(
            all(
                not blocker.intersects(segment)
                for segment in plan.draw_segments("family", "Child")
            )
        )
        self.assertEqual(plan.unresolved, [])
        problems = router.validate_plan(plan, families, labels=labels)
        self.assertEqual(problems, [])

    def test_long_label_uses_halo_without_creating_a_route_gap(self):
        positions = {
            "Child": (4.0, 4.0),
            "Long label": (0.0, 2.0),
        }
        plan = RoutePlan(
            animal_positions=positions,
            family_positions={"family": (0.0, 0.0)},
            family_members={"family": {"Child"}},
            routes={"family": {"Child": [(0.0, 0.0), (4.0, 4.0)]}},
        )
        labels = {
            "Child": "Child",
            "Long label": "A deliberately very long animal name",
        }
        router = PedigreeRouter()
        label_rect = router.node_obstacles(positions, labels, True)["Long label"]
        self.assertTrue(label_rect.intersects(((0.0, 0.0), (4.0, 4.0))))

        router.recompute_line_gaps(
            plan,
            labels=labels,
            animal_gap_obstacles=router.marker_obstacles(
                positions, half_width=0.2, half_height=0.2
            ),
        )

        self.assertNotIn(("family", "Child", 0), plan.crossing_gaps)
        self.assertEqual(
            plan.draw_segments("family", "Child"),
            [((0.0, 0.0), (4.0, 4.0))],
        )

    def test_unrelated_nodes_are_packed_into_a_compact_grid(self):
        positions = {f"Node {index}": (float(index) * 4.0, 0.0) for index in range(15)}
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        plan = router.plan(positions, {}, labels=labels)

        xs = [point[0] for point in plan.animal_positions.values()]
        ys = {point[1] for point in plan.animal_positions.values()}
        self.assertEqual(len(set(plan.animal_positions.values())), len(positions))
        self.assertGreaterEqual(len(ys), 3)
        self.assertLess(max(xs) - min(xs), 12.0)
        self.assertEqual(plan.unresolved, [])

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
            with reported_subtest(
                self,
                "routing-warning localization",
                path=path.name,
            ):
                messages = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("heritage_track.status.routing_warning", messages)


if __name__ == "__main__":
    unittest.main()
