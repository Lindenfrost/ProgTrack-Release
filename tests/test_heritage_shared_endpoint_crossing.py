"""Regression coverage for Heritage shared-endpoint route classification."""

from __future__ import annotations

import math
import unittest

from Plugins.Heritage_Track.pedigree_router import (
    PedigreeRouter,
    _OwnedSegment,
)


class HeritageSharedEndpointCrossingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.router = PedigreeRouter()

    def test_crossing_away_from_shared_marker_gets_a_gap(self):
        segments = [
            _OwnedSegment("family_a", "Shared", 0, ((0.0, 0.0), (2.0, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((1.0, -1.0), (1.0, 1.0))),
        ]

        gaps, problems = self.router._find_crossing_gaps(
            segments,
            {"Shared": (2.0, 0.0)},
        )

        self.assertEqual(problems, [])
        self.assertEqual(gaps, {("family_b", "Shared", 0): [(1.0, 0.0)]})

    def test_crossing_at_shared_marker_is_exempt(self):
        segments = [
            _OwnedSegment("family_a", "Shared", 0, ((0.0, 0.0), (2.0, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((2.0, 0.0), (3.0, 1.0))),
        ]

        gaps, problems = self.router._find_crossing_gaps(
            segments,
            {"Shared": (2.0, 0.0)},
        )

        self.assertEqual(gaps, {})
        self.assertEqual(problems, [])

    def test_shared_overlap_must_stay_inside_marker(self):
        outside = [
            _OwnedSegment("family_a", "Shared", 0, ((1.5, 0.0), (2.4, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((1.7, 0.0), (2.5, 0.0))),
        ]
        inside = [
            _OwnedSegment("family_a", "Shared", 0, ((1.9, 0.0), (2.1, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((1.95, 0.0), (2.05, 0.0))),
        ]

        outside_gaps, outside_problems = self.router._find_crossing_gaps(
            outside,
            {"Shared": (2.0, 0.0)},
        )
        inside_gaps, inside_problems = self.router._find_crossing_gaps(
            inside,
            {"Shared": (2.0, 0.0)},
        )

        self.assertEqual(outside_gaps, {})
        self.assertEqual(
            outside_problems,
            ["family_a/family_b: different families share a segment"],
        )
        self.assertEqual(inside_gaps, {})
        self.assertEqual(inside_problems, [])

    def test_missing_shared_marker_is_reported(self):
        segments = [
            _OwnedSegment("family_a", "Shared", 0, ((0.0, 0.0), (2.0, 0.0))),
            _OwnedSegment("family_b", "Shared", 0, ((1.0, -1.0), (1.0, 1.0))),
        ]

        gaps, problems = self.router._find_crossing_gaps(segments, {})

        self.assertEqual(gaps, {})
        self.assertEqual(
            problems,
            ["family_a/family_b: shared animal endpoint Shared has missing geometry"],
        )

        _gaps, nonfinite_problems = self.router._find_crossing_gaps(
            segments,
            {"Shared": (math.nan, 0.0)},
        )
        self.assertEqual(nonfinite_problems, problems)


if __name__ == "__main__":
    unittest.main()
