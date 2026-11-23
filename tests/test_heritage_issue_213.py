"""Focused regressions for Heritage Track issue #213."""

from __future__ import annotations

import unittest

from Plugins.Heritage_Track.pedigree_router import (
    PedigreeRouter,
    Rect,
    _OwnedSegment,
)


class HeritageIssue213Test(unittest.TestCase):
    def setUp(self) -> None:
        self.router = PedigreeRouter()

    @staticmethod
    def _segment(
        family_id: str,
        index: int,
        segment: tuple[tuple[float, float], tuple[float, float]],
    ) -> _OwnedSegment:
        return _OwnedSegment(
            family_id,
            "Shared",
            index,
            segment,
            is_terminal=index == 1,
        )

    def test_shared_parent_horizontal_shoulder_overlap_is_not_exempt(self) -> None:
        first_path = [(0.0, 1.0), (2.0, 1.0), (2.0, 0.0)]
        segments = [
            self._segment("family_a", 0, ((0.0, 1.0), (2.0, 1.0))),
            self._segment("family_a", 1, ((2.0, 1.0), (2.0, 0.0))),
            self._segment("family_b", 0, ((1.0, 1.0), (2.0, 1.0))),
            self._segment("family_b", 1, ((2.0, 1.0), (2.0, 0.0))),
        ]

        gaps, problems = self.router._find_crossing_gaps(
            segments,
            {"Shared": (2.0, 0.0)},
        )

        self.assertEqual(gaps, {})
        self.assertEqual(
            problems,
            ["family_a/family_b: different families share a segment"],
        )
        self.assertFalse(
            self.router._is_shared_parent_port_join(
                segments[0],
                segments[2],
                "overlap",
                None,
                first_path,
                segments,
            )
        )

    def test_shared_parent_terminal_vertical_overlap_remains_exempt(self) -> None:
        first_path = [(0.0, 1.0), (2.0, 1.0), (2.0, 0.0)]
        segments = [
            self._segment("family_a", 0, ((0.0, 1.0), (2.0, 1.0))),
            self._segment("family_a", 1, ((2.0, 1.0), (2.0, 0.0))),
            self._segment("family_b", 0, ((4.0, 2.0), (2.0, 2.0))),
            self._segment("family_b", 1, ((2.0, 2.0), (2.0, 0.0))),
        ]

        gaps, problems = self.router._find_crossing_gaps(
            segments,
            {"Shared": (2.0, 0.0)},
        )

        self.assertEqual(gaps, {})
        self.assertEqual(problems, [])

    def test_label_only_lanes_are_used_after_marker_safe_lanes_are_exhausted(self) -> None:
        positions = {
            "A": (0.0, 0.0),
            "B": (10.0, 0.0),
            "C": (4.0, 0.0),
            "D": (14.0, 0.0),
            "Child A": (3.0, 4.0),
            "Child B": (11.0, 4.0),
        }
        families = {
            "family_a": {"mother": "A", "father": "B", "children": ["Child A"]},
            "family_b": {"mother": "C", "father": "D", "children": ["Child B"]},
        }
        # The regular initial solver should keep both base candidates clear.
        # The two late rail alternatives for family_b are deliberately covered
        # only by presentation rectangles. There are no animal markers there.
        obstacles = {
            "A": Rect(-0.3, 0.3, -0.3, 0.3),
            "B": Rect(9.7, 10.3, -0.3, 0.3),
            "C": Rect(3.7, 4.3, -0.3, 0.3),
            "D": Rect(13.7, 14.3, -0.3, 0.3),
            "Child A": Rect(2.7, 3.3, 3.7, 4.3),
            "Child B": Rect(10.7, 11.3, 3.7, 4.3),
            "label-slab": Rect(-1.0, 15.0, 0.0, 3.0),
            "label-lower": Rect(-1.0, 15.0, 3.02, 3.20),
            "label-upper": Rect(-1.0, 15.0, 3.50, 3.72),
        }

        junctions = self.router._place_junctions(
            positions,
            families,
            obstacles,
            chronological=False,
            focused=True,
        )

        self.assertEqual(set(junctions), set(families))
        self.assertNotEqual(junctions["family_a"][1], junctions["family_b"][1])
        # The automatic single-child clearance floor may change which legal
        # label-only lane is selected.  Assert the semantic rule (a label may
        # be crossed when no foreign marker is hit), not one fixture-specific
        # Y interval.
        self.assertTrue(
            any(
                name.startswith("label-")
                and rect.contains(junctions["family_b"], margin=0.04)
                for name, rect in obstacles.items()
            )
        )
        self.assertFalse(
            self.router.marker_obstacles(positions)["Child B"].contains(
                junctions["family_b"], margin=0.04
            )
        )

    def test_marker_remains_hard_when_label_safe_alternative_is_unavailable(self) -> None:
        positions = {
            "A": (0.0, 0.0),
            "B": (10.0, 0.0),
            "C": (4.0, 0.0),
            "D": (14.0, 0.0),
            "Child A": (3.0, 4.0),
            "Child B": (11.0, 4.0),
            "Foreign": (11.0, 3.59),
        }
        families = {
            "family_a": {"mother": "A", "father": "B", "children": ["Child A"]},
            "family_b": {"mother": "C", "father": "D", "children": ["Child B"]},
        }
        obstacles = {
            "A": Rect(-0.3, 0.3, -0.3, 0.3),
            "B": Rect(9.7, 10.3, -0.3, 0.3),
            "C": Rect(3.7, 4.3, -0.3, 0.3),
            "D": Rect(13.7, 14.3, -0.3, 0.3),
            "Child A": Rect(2.7, 3.3, 3.7, 4.3),
            "Child B": Rect(10.7, 11.3, 3.7, 4.3),
            "label-slab": Rect(-1.0, 15.0, 0.0, 3.0),
            "Foreign": Rect(10.7, 11.3, 3.40, 3.78),
            "label-lower": Rect(-1.0, 15.0, 3.02, 3.20),
        }

        junctions = self.router._place_junctions(
            positions,
            families,
            obstacles,
            chronological=False,
            focused=True,
        )

        marker = self.router.marker_obstacles(positions)["Foreign"]
        self.assertFalse(marker.contains(junctions["family_b"], margin=0.04))


if __name__ == "__main__":
    unittest.main()
