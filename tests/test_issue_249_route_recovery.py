"""Focused regression contracts for Heritage Track issue #249."""

import unittest
from unittest.mock import patch

from Plugins.Heritage_Track.pedigree_router import (
    PedigreeRouter,
    RoutePlan,
    _canonical_endpoint_path,
    _foreign_marker_hits,
)


class Issue249RouteRecoveryTest(unittest.TestCase):
    def test_automatic_knot_clears_route_without_a_false_gap(self):
        positions = {
            "Dam": (-2.0, 0.0), "Sire": (2.0, 0.0),
            "Child": (3.0, 4.0), "Blocker": (1.1, 2.45),
        }
        families = {
            "family": {"mother": "Dam", "father": "Sire", "children": ["Child"]}
        }
        router = PedigreeRouter()
        plan = router.plan(
            positions, families, labels={node: node for node in positions},
        )

        midpoint = (plan.animal_positions["Dam"][0] + plan.animal_positions["Sire"][0]) / 2.0
        self.assertAlmostEqual(plan.family_positions["family"][0], midpoint)
        self.assertEqual(plan.crossing_gaps, {})
        self.assertEqual(plan.unresolved, [])
        self.assertEqual(router.validate_plan(
            plan, families, labels={node: node for node in positions},
        ), [])

    def test_explicit_manual_knot_remains_authoritative(self):
        positions = {
            "Dam": (-2.0, 0.0), "Sire": (2.0, 0.0),
            "Child": (3.0, 4.0), "Blocker": (1.1, 2.45),
        }
        families = {
            "family": {"mother": "Dam", "father": "Sire", "children": ["Child"]}
        }
        router = PedigreeRouter()
        manual_knot = (-0.5, 1.5)
        plan = router.plan(
            positions, families, labels={node: node for node in positions},
            protected_nodes=set(positions),
            manual_family_positions={"family": manual_knot},
        )

        self.assertEqual(plan.family_positions["family"], manual_knot)
        self.assertEqual(plan.animal_positions, positions)
        self.assertTrue(_foreign_marker_hits(
            "Child", plan.routes["family"]["Child"],
            router.marker_obstacles(plan.animal_positions),
        ))
        self.assertFalse(any(
            "foreign animal marker Blocker" in problem
            for problem in router.validate_plan(
                plan, families, labels={node: node for node in positions},
            )
        ))

    def test_two_parent_automatic_knot_tracks_either_parent_in_both_modes(self):
        families = {
            "family": {"mother": "Dam", "father": "Sire", "children": ["Child"]}
        }
        positions = {"Dam": (-4.0, 0.0), "Sire": (4.0, 0.0), "Child": (0.0, 6.0)}
        labels = {node: node for node in positions}
        for mode in ("partner_normalized", "chronological"):
            for moving_parent, new_x in (("Dam", -2.0), ("Sire", 6.0)):
                with self.subTest(mode=mode, moving_parent=moving_parent):
                    router = PedigreeRouter()
                    before = router.plan(
                        positions, families, labels=labels,
                        protected_nodes=set(positions),
                        vertical_layout_mode=mode,
                    )
                    moved = dict(positions)
                    moved[moving_parent] = (new_x, moved[moving_parent][1])
                    after = router.plan(
                        moved, families, labels=labels,
                        protected_nodes=set(moved),
                        vertical_layout_mode=mode,
                    )
                    expected = (moved["Dam"][0] + moved["Sire"][0]) / 2.0
                    self.assertAlmostEqual(before.family_positions["family"][0], 0.0)
                    self.assertAlmostEqual(after.family_positions["family"][0], expected)
                    self.assertNotEqual(
                        before.family_positions["family"][0],
                        after.family_positions["family"][0],
                    )
                    self.assertEqual(after.routes["family"]["Dam"][0], after.family_positions["family"])
                    self.assertEqual(after.routes["family"]["Sire"][0], after.family_positions["family"])
                    self.assertEqual(after.routes["family"]["Child"][0], after.family_positions["family"])
                    self.assertFalse(any(
                        "automatic junction X differs" in problem
                        for problem in router.validate_plan(after, families, labels=labels)
                    ))
                    stale = after.family_positions["family"]
                    after.family_positions["family"] = (expected + 0.25, stale[1])
                    self.assertTrue(any(
                        "automatic junction X differs" in problem
                        for problem in router.validate_plan(after, families, labels=labels)
                    ))
                    after.family_positions["family"] = stale

                    manual = router.plan(
                        moved, families, labels=labels,
                        protected_nodes=set(moved),
                        manual_family_positions={"family": (expected + 0.5, 2.0)},
                        vertical_layout_mode=mode,
                    )
                    self.assertAlmostEqual(manual.family_positions["family"][0], expected + 0.5)
                    self.assertFalse(any(
                        "automatic junction X differs" in problem
                        for problem in router.validate_plan(manual, families, labels=labels)
                    ))

    def test_real_marker_witnesses_match_preview_paths_and_validator(self):
        positions = {
            "Dam": (-4.0, 0.0), "Sire": (4.0, 0.0),
            "Child A": (-2.0, 4.0), "Child B": (-1.2, 3.0),
            "Other family child": (-1.0, 3.0),
            "Horizontal blocker": (-2.0, 1.5),
            "Vertical blocker": (-4.0, 0.7),
        }
        families = {
            "family": {
                "mother": "Dam", "father": "Sire",
                "children": ["Child A", "Child B"],
            },
            "other": {"mother": "Other family child", "children": ["Not visible"]},
        }
        junctions = {"family": (0.0, 1.5)}
        router = PedigreeRouter()
        labels = {node: node for node in positions}
        parents = {"Dam", "Sire"}
        routes = {
            endpoint: _canonical_endpoint_path(
                junctions["family"], positions[endpoint], parent=endpoint in parents,
            )
            for endpoint in ("Dam", "Sire", "Child A", "Child B")
        }
        plan = RoutePlan(
            animal_positions=dict(positions), family_positions=junctions,
            family_members={"family": set(routes)}, routes={"family": routes},
            display_mode="focused",
        )
        before = dict(positions)
        preview = router._family_route_marker_hit_details(
            positions, families, junctions,
        )
        final = [
            ("family", endpoint, foreign, segment)
            for endpoint, path in routes.items()
            for segment, foreign in _foreign_marker_hits(
                endpoint, path, router.marker_obstacles(plan.animal_positions),
            )
        ]
        self.assertEqual(sorted(preview), sorted(final))
        for witness in (
            ("family", "Child A", "Child B", 0),
            ("family", "Child A", "Other family child", 0),
            ("family", "Dam", "Horizontal blocker", 0),
            ("family", "Dam", "Vertical blocker", 1),
        ):
            self.assertIn(witness, preview)
        for vertical_mode in ("partner_normalized", "chronological"):
            plan.vertical_layout_mode = vertical_mode
            problems = router.validate_plan(plan, families, labels=labels)
            for _family, endpoint, foreign, _segment in preview:
                self.assertIn(
                    f"family: route to {endpoint} intersects foreign animal marker {foreign}",
                    problems,
                )
        reversed_positions = dict(reversed(list(positions.items())))
        self.assertEqual(preview, router._family_route_marker_hit_details(
            reversed_positions, families, junctions,
        ))
        self.assertEqual(positions, before)

    def test_preview_uses_the_same_repaired_junction_as_final_routing(self):
        positions = {
            "Dam": (-0.35, 0.0), "Sire": (0.35, 0.0),
            "Child": (8.0, 5.0), "Foreign marker": (4.0, 4.125),
        }
        families = {
            "family": {"mother": "Dam", "father": "Sire", "children": ["Child"]}
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()
        raw_junction = {"family": (0.0, 3.25)}
        self.assertTrue(router._family_route_marker_hit_details(
            positions, families, raw_junction
        ))

        with patch.object(
            router, "_place_junctions",
            side_effect=lambda *_args, **_kwargs: dict(raw_junction),
        ):
            final_junctions = router._automatic_route_junctions(
                positions, families, labels,
                show_inbreeding=True, chronological=True,
            )
            preview_hits = router._route_marker_hit_details(
                positions, families, labels,
                show_inbreeding=True, chronological=True,
            )

        self.assertNotEqual(final_junctions, raw_junction)
        self.assertEqual(preview_hits, router._family_route_marker_hit_details(
            positions, families, final_junctions
        ))
        self.assertEqual(preview_hits, [])

    def test_recovery_can_cross_the_old_fixed_x_limit(self):
        positions = {
            "Dam": (-4.0, 0.0), "Sire": (4.0, 0.0),
            "Child": (2.0, 4.0), "Blocker": (1.0, 10.0),
            "Distant": (80.0, 10.0),
        }
        families = {
            "family": {"mother": "Dam", "father": "Sire", "children": ["Child"]}
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()

        def witness_hits(candidate, *_args, **_kwargs):
            return ([('family', 'Child', 'Blocker', 0)]
                    if candidate['Blocker'][0] < 30.0 else [])

        with patch.object(router, "_route_marker_hit_details", side_effect=witness_hits):
            changed = router._repair_canonical_route_marker_collisions(
                positions, families, labels, set(), False, preserve_y=True
            )

        self.assertTrue(changed)
        self.assertGreater(positions["Blocker"][0] - 1.0, 20.48)
        self.assertEqual(positions["Blocker"][1], 10.0)

    def test_two_independent_hits_are_repaired_in_one_family(self):
        positions = {
            "Dam": (-4.0, 0.0), "Sire": (4.0, 0.0),
            "Child A": (-2.0, 4.0), "Child B": (2.0, 4.0),
            "Blocker A": (-1.0, 10.0), "Blocker B": (1.0, 10.0),
        }
        families = {
            "family": {
                "mother": "Dam", "father": "Sire",
                "children": ["Child A", "Child B"],
            }
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()
        before = dict(positions)

        def witness_hits(candidate, *_args, **_kwargs):
            hits = []
            if abs(candidate["Blocker A"][0] + 1.0) <= 0.5:
                hits.append(("family", "Child A", "Blocker A", 0))
            if abs(candidate["Blocker B"][0] - 1.0) <= 0.5:
                hits.append(("family", "Child B", "Blocker B", 0))
            return hits

        with patch.object(router, "_route_marker_hit_details", side_effect=witness_hits):
            changed = router._repair_canonical_route_marker_collisions(
                positions, families, labels, set(), False, preserve_y=True
            )

        self.assertTrue(changed)
        self.assertEqual(witness_hits(positions), [])
        self.assertEqual(
            {node: point[1] for node, point in positions.items()},
            {node: point[1] for node, point in before.items()},
        )

    def test_bounded_plateau_is_detached_until_both_blocks_clear(self):
        positions = {
            "Dam": (-4.0, 0.0), "Sire": (4.0, 0.0),
            "Child A": (-2.0, 4.0), "Child B": (2.0, 4.0),
            "Blocker A": (-1.0, 10.0), "Blocker B": (1.0, 10.0),
        }
        families = {
            "family": {
                "mother": "Dam", "father": "Sire",
                "children": ["Child A", "Child B"],
            }
        }
        labels = {node: node for node in positions}
        router = PedigreeRouter()
        before = dict(positions)

        def witness_hits(candidate, *_args, **_kwargs):
            if (abs(candidate["Blocker A"][0] + 1.0) > 0.5
                    and abs(candidate["Blocker B"][0] - 1.0) > 0.5):
                return []
            return [
                ("family", "Child A", "Blocker A", 0),
                ("family", "Child B", "Blocker B", 0),
            ]

        with patch.object(router, "_route_marker_hit_details", side_effect=witness_hits):
            changed = router._repair_canonical_route_marker_collisions(
                positions, families, labels, set(), False, preserve_y=True
            )

        self.assertTrue(changed)
        self.assertEqual(witness_hits(positions), [])
        self.assertNotEqual(positions["Blocker A"][0], before["Blocker A"][0])
        self.assertNotEqual(positions["Blocker B"][0], before["Blocker B"][0])
        self.assertEqual(
            {node: point[1] for node, point in positions.items()},
            {node: point[1] for node, point in before.items()},
        )

    def test_locked_plateau_rolls_back_every_intermediate_move(self):
        positions = {
            "Dam": (-4.0, 0.0), "Sire": (4.0, 0.0),
            "Child A": (-2.0, 4.0), "Child B": (2.0, 4.0),
            "Blocker A": (-1.0, 10.0), "Blocker B": (1.0, 10.0),
        }
        families = {
            "family": {
                "mother": "Dam", "father": "Sire",
                "children": ["Child A", "Child B"],
            }
        }
        router = PedigreeRouter()
        before = dict(positions)

        def witness_hits(candidate, *_args, **_kwargs):
            if (abs(candidate["Blocker A"][0] + 1.0) > 0.5
                    and abs(candidate["Blocker B"][0] - 1.0) > 0.5):
                return []
            return [
                ("family", "Child A", "Blocker A", 0),
                ("family", "Child B", "Blocker B", 0),
            ]

        with patch.object(router, "_route_marker_hit_details", side_effect=witness_hits):
            changed = router._repair_canonical_route_marker_collisions(
                positions, families, {node: node for node in positions},
                {"Blocker B", "Child A", "Child B", "Dam", "Sire"}, False,
                preserve_y=True,
            )

        self.assertFalse(changed)
        self.assertEqual(positions, before)
        self.assertIn("exhausted", router._last_route_recovery_stop_reason)
