"""Structural checks for the bounded pre-junction collision seam (#186/#195)."""

from __future__ import annotations

import unittest

from Plugins.Heritage_Track.pedigree_router import PedigreeRouter


class CollisionRecoveryContractTest(unittest.TestCase):
    def setUp(self):
        self.router = PedigreeRouter()

    @staticmethod
    def _labels(positions):
        return {node: f"{node} deliberately long label" for node in positions}

    def _pairs(self, positions, labels):
        return self.router._collision_pairs(
            positions,
            self.router.node_obstacles(positions, labels, True),
            self.router.marker_obstacles(positions),
        )

    def test_internal_partner_spacing_changes_members_without_translating_block(self):
        positions = {"Mate-A": (0.0, 0.0), "Mate-B": (0.1, 0.0)}
        families = {"family": {"mother": "Mate-A", "father": "Mate-B", "children": []}}
        labels = self._labels(positions)
        before_center = sum(point[0] for point in positions.values()) / 2.0
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                positions, families, labels, set(), True, preserve_y=True
            )
        )
        self.assertEqual(self._pairs(positions, labels), [])
        self.assertAlmostEqual(
            sum(point[0] for point in positions.values()) / 2.0,
            before_center,
        )
        self.assertLess(positions["Mate-A"][0], positions["Mate-B"][0])

    def test_sibling_branches_move_as_uniform_groups_and_keep_parent_fixed(self):
        positions = {
            "Parent": (0.0, 3.0),
            "Sibling-A": (0.0, 0.0),
            "Sibling-B": (0.1, 0.0),
            "A-child": (0.0, -3.0),
            "B-child": (0.1, -3.0),
        }
        families = {
            "siblings": {"mother": "Parent", "children": ["Sibling-A", "Sibling-B"]},
            "a-branch": {"mother": "Sibling-A", "children": ["A-child"]},
            "b-branch": {"mother": "Sibling-B", "children": ["B-child"]},
        }
        labels = self._labels(positions)
        parent_before = positions["Parent"]
        a_delta_before = positions["A-child"][0] - positions["Sibling-A"][0]
        b_delta_before = positions["B-child"][0] - positions["Sibling-B"][0]
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                positions, families, labels, {"Parent"}, True, preserve_y=True
            )
        )
        self.assertEqual(self._pairs(positions, labels), [])
        self.assertEqual(positions["Parent"], parent_before)
        self.assertAlmostEqual(
            positions["A-child"][0] - positions["Sibling-A"][0], a_delta_before
        )
        self.assertAlmostEqual(
            positions["B-child"][0] - positions["Sibling-B"][0], b_delta_before
        )

    def test_disconnected_components_can_translate_but_protected_conflict_is_rejected(self):
        positions = {"A": (0.0, 0.0), "B": (0.1, 0.0)}
        families = {
            "a-family": {"mother": "A", "children": ["A-child"]},
            "b-family": {"mother": "B", "children": ["B-child"]},
        }
        positions.update({"A-child": (0.0, -3.0), "B-child": (0.1, -3.0)})
        labels = self._labels(positions)
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                positions, families, labels, set(), True, preserve_y=True
            )
        )
        self.assertEqual(self._pairs(positions, labels), [])

        locked = {"A": (0.0, 0.0), "B": (0.1, 0.0)}
        self.assertFalse(
            self.router._resolve_obstacle_collisions(
                locked,
                {"family": {"mother": "A", "father": "B", "children": []}},
                self._labels(locked),
                {"A", "B"},
                True,
                preserve_y=True,
            )
        )
        self.assertIn("A/B", self.router._last_collision_recovery_diagnostic)

    def test_more_than_six_independent_conflicts_use_one_shared_budget(self):
        positions = {
            f"Node-{index:02d}": ((index // 2) * 20.0 + (index % 2) * 0.05, 0.0)
            for index in range(14)
        }
        families = {
            f"family-{index:02d}": {"mother": node, "children": []}
            for index, node in enumerate(positions)
        }
        labels = self._labels(positions)
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                positions, families, labels, set(), True, preserve_y=True
            )
        )
        self.assertEqual(self._pairs(positions, labels), [])

    def test_multi_child_continuing_branch_keeps_each_branch_vector(self):
        positions = {
            "Parent": (0.0, 3.0),
            "Child-A": (0.0, 0.0), "Child-B": (0.1, 0.0), "Child-C": (0.2, 0.0),
            "A-desc": (0.0, -3.0), "B-desc": (0.1, -3.0), "C-desc": (0.2, -3.0),
        }
        families = {
            "siblings": {"mother": "Parent", "children": ["Child-A", "Child-B", "Child-C"]},
            "a-branch": {"mother": "Child-A", "children": ["A-desc"]},
            "b-branch": {"mother": "Child-B", "children": ["B-desc"]},
            "c-branch": {"mother": "Child-C", "children": ["C-desc"]},
        }
        labels = self._labels(positions)
        vectors = {
            root: positions[desc][0] - positions[root][0]
            for root, desc in (("Child-A", "A-desc"), ("Child-B", "B-desc"), ("Child-C", "C-desc"))
        }
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                positions, families, labels, {"Parent"}, True, preserve_y=True
            )
        )
        self.assertEqual(self._pairs(positions, labels), [])
        for root, desc in (("Child-A", "A-desc"), ("Child-B", "B-desc"), ("Child-C", "C-desc")):
            self.assertAlmostEqual(positions[desc][0] - positions[root][0], vectors[root])

    def test_branch_groups_are_root_specific_when_origin_has_multiple_children(self):
        positions = {
            "P1": (-1.0, 3.0), "P2": (1.0, 3.0),
            "A": (-0.5, 0.0), "B": (0.5, 0.0), "M": (0.7, 0.0),
            "C": (0.6, -3.0),
        }
        families = {
            "origin": {"mother": "P1", "father": "P2", "children": ["A", "B"]},
            "continuing": {"mother": "B", "father": "M", "children": ["C"]},
        }
        partner_blocks = {node: {node} for node in positions}
        partner_blocks["B"] = {"B", "M"}
        partner_blocks["M"] = {"B", "M"}
        groups = self.router._branch_movement_groups(
            positions, families, partner_blocks, requested_nodes={"A", "B"}
        )
        b_alternatives = groups["B"]
        self.assertTrue(any({"B", "M", "C"} <= group for group in b_alternatives))
        self.assertTrue(all("P1" not in group and "P2" not in group for group in b_alternatives))
        self.assertTrue(all("B" not in group for group in groups["A"] if len(group) > 1))

    def test_second_endpoint_uses_its_own_directional_group_boundary(self):
        positions = {"wide": (0.0, 0.0), "narrow": (0.1, 0.0)}
        families = {
            "wide-family": {"mother": "wide", "children": []},
            "narrow-family": {"mother": "narrow", "children": []},
        }
        labels = {"wide": "W" * 80, "narrow": "N"}
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                positions, families, labels, set(), True, preserve_y=True,
            )
        )
        self.assertEqual(self._pairs(positions, labels), [])
        self.assertLess(positions["wide"][0], positions["narrow"][0])

    def test_shared_descendant_is_cut_only_for_sibling_roots_of_same_origin(self):
        positions = {
            "P1": (-1.0, 3.0), "P2": (1.0, 3.0),
            "A": (-0.5, 0.0), "B": (0.5, 0.0), "X": (0.0, -3.0),
        }
        families = {
            "origin": {"mother": "P1", "father": "P2", "children": ["A", "B"]},
            "a-branch": {"mother": "A", "children": ["X"]},
            "b-branch": {"mother": "B", "children": ["X"]},
        }
        blocks = {node: {node} for node in positions}
        groups = self.router._branch_movement_groups(
            positions, families, blocks, requested_nodes={"A", "B"}
        )
        self.assertTrue(all("X" not in group for group in groups["A"]))
        self.assertTrue(all("X" not in group for group in groups["B"]))

    def test_nested_requested_roots_keep_their_own_origin_family_alternatives(self):
        positions = {
            "G": (0.0, 6.0), "P": (0.0, 3.0),
            "A": (0.0, 0.0), "X": (0.0, -3.0),
        }
        families = {
            "g-parent": {"mother": "G", "children": ["P"]},
            "p-parent": {"mother": "P", "children": ["A"]},
            "a-branch": {"mother": "A", "children": ["X"]},
        }
        blocks = {node: {node} for node in positions}
        groups = self.router._branch_movement_groups(
            positions, families, blocks, requested_nodes={"P", "A"}
        )
        self.assertTrue(any({"A", "X"} <= group for group in groups["A"]))

    def test_terminal_sibling_cohort_widens_around_foreign_obstacle(self):
        positions = {
            "P": (0.0, 3.0),
            "Left": (-0.05, 0.0), "Middle": (0.0, 0.0), "Right": (0.05, 0.0),
            "Foreign": (0.0, -3.0),
        }
        families = {
            "siblings": {"mother": "P", "children": ["Left", "Middle", "Right"]},
            "foreign": {"mother": "Foreign", "children": []},
        }
        labels = self._labels(positions)
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                positions, families, labels, {"P"}, True, preserve_y=True,
            )
        )
        self.assertEqual(self._pairs(positions, labels), [])
        self.assertLess(positions["Left"][0], positions["Middle"][0])
        self.assertLess(positions["Middle"][0], positions["Right"][0])

    def test_contained_marker_label_and_permuted_input_are_deterministic(self):
        positions = {"Long label": (0.0, 0.0), "Short": (0.05, 0.0)}
        families = {
            "long-family": {"mother": "Long label", "children": []},
            "short-family": {"mother": "Short", "children": []},
        }
        labels = {"Long label": "A label that contains the other marker", "Short": "S"}
        first = dict(positions)
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                first, families, labels, set(), True, preserve_y=True
            )
        )
        second = dict(reversed(list(positions.items())))
        reversed_families = dict(reversed(list(families.items())))
        self.assertTrue(
            self.router._resolve_obstacle_collisions(
                second, reversed_families, dict(reversed(list(labels.items()))), set(), True,
                preserve_y=True,
            )
        )
        self.assertEqual(first, second)
        self.assertEqual(self._pairs(first, labels), [])

    def test_257_node_branch_uses_common_recovery_for_a_real_cross_row_collision(self):
        positions = {"Node-000": (0.0, 0.0), "Node-001": (0.0, 0.8)}
        for index in range(2, 257):
            positions[f"Node-{index:03d}"] = (float(index * 5), float(index * 3))
        families = {
            f"family-{index:03d}": {"mother": node, "children": []}
            for index, node in enumerate(positions)
        }
        labels = {node: node for node in positions}
        adjusted = self.router._arrange_nodes(
            positions, families, labels, set(), True, preserve_y=True
        )
        self.assertEqual(self._pairs(adjusted, labels), [])


if __name__ == "__main__":
    unittest.main()
