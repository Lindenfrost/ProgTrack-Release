"""Focused tests for Heritage Track issue #206 initial framing."""

from __future__ import annotations

import copy
import unittest
from types import SimpleNamespace

from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackWidget


class HeritageInitialViewportTest(unittest.TestCase):
    @staticmethod
    def _widget(*, axes=(600.0, 300.0)) -> HeritageTrackWidget:
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget._pedigree_router = SimpleNamespace(
            _estimated_label_width=lambda label: 1.0,
        )
        widget._get_node_obstacle_label = lambda node, record: str(node)
        widget._get_node_record = lambda node: {}
        widget._effective_axes_pixels = lambda: axes
        widget._canonical_selection_ids = ("selected",)
        widget._canonicalize_selection = lambda: ("selected",)
        return widget

    @staticmethod
    def _inside(bounds, point) -> bool:
        return (
            bounds[0][0] <= point[0] <= bounds[0][1]
            and bounds[1][0] <= point[1] <= bounds[1][1]
        )

    def test_large_graph_framing_prioritizes_terminal_descendants(self) -> None:
        widget = self._widget()
        positions = {
            "selected": (40.0, 0.0),
            "terminal-a": (48.0, 0.0),
            "terminal-b": (52.0, 0.0),
            "terminal-c": (56.0, 0.0),
            "distant-left": (-100.0, -100.0),
            "distant-right": (200.0, 100.0),
        }
        before = copy.deepcopy(positions)

        bounds = widget._compute_view_bounds(
            positions,
            selected_nodes=["selected"],
            terminal_nodes=["terminal-a", "terminal-b", "terminal-c"],
        )

        self.assertEqual(positions, before)
        self.assertTrue(self._inside(bounds, positions["selected"]))
        self.assertEqual(
            sum(self._inside(bounds, positions[node]) for node in (
                "terminal-a", "terminal-b", "terminal-c"
            )),
            3,
        )
        self.assertFalse(self._inside(bounds, positions["distant-left"]))
        self.assertFalse(self._inside(bounds, positions["distant-right"]))

    def test_large_selection_uses_the_same_terminal_objective(self) -> None:
        widget = self._widget()
        selected = [f"selected-{index}" for index in range(9)]
        positions = {
            node: (40.0 + index, 0.0)
            for index, node in enumerate(selected)
        }
        positions.update({
            "terminal-a": (50.0, 0.0),
            "terminal-b": (53.0, 0.0),
            "terminal-c": (56.0, 0.0),
            "distant-left": (-100.0, -100.0),
            "distant-right": (200.0, 100.0),
        })

        bounds = widget._compute_view_bounds(
            positions,
            selected_nodes=selected,
            terminal_nodes=["terminal-a", "terminal-b", "terminal-c"],
        )

        self.assertTrue(all(self._inside(bounds, positions[node]) for node in selected))
        self.assertTrue(all(self._inside(bounds, positions[node]) for node in (
            "terminal-a", "terminal-b", "terminal-c"
        )))

    def test_terminal_descendants_are_resolved_from_family_structure(self) -> None:
        positions = {
            "selected": (0.0, 0.0),
            "parent": (-2.0, 1.0),
            "child-a": (0.0, 3.0),
            "child-b": (2.0, 3.0),
            "grandchild": (0.0, 6.0),
            "__family__::selected::parent": (-1.0, 1.5),
            "__family__::selected::child-a": (0.0, 4.5),
        }
        families = {
            "family-1": {
                "mother": "selected",
                "father": "parent",
                "children": ["child-a", "child-b"],
            },
            "family-2": {
                "mother": "child-a",
                "father": "parent",
                "children": ["grandchild"],
            },
        }

        terminals = HeritageTrackWidget._terminal_descendants_for_selection(
            ["selected"], positions, families
        )

        self.assertEqual(terminals, {"child-b", "grandchild"})

    def test_framing_is_invariant_under_input_order(self) -> None:
        widget = self._widget()
        positions = {
            "selected": (40.0, 0.0),
            "terminal-a": (48.0, 0.0),
            "terminal-b": (52.0, 0.0),
            "terminal-c": (56.0, 0.0),
            "distant-left": (-100.0, -100.0),
            "distant-right": (200.0, 100.0),
        }
        first = widget._compute_view_bounds(
            positions,
            selected_nodes=["selected"],
            terminal_nodes=["terminal-a", "terminal-b", "terminal-c"],
        )
        second = widget._compute_view_bounds(
            dict(reversed(list(positions.items()))),
            selected_nodes=["selected"],
            terminal_nodes=["terminal-c", "terminal-b", "terminal-a"],
        )

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
