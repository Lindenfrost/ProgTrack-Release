"""Focused regression tests for Heritage Track issue #160.

These tests exercise the selection contract without opening a native window:
the widget normalizes Core/Heritage selections once, the immutable context
records the same scope/mode, and the router accepts the explicit mode rather
than recomputing it from a differently shaped collection.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from Plugins.Heritage_Track.display_context import DisplayContextBuilder
from Plugins.Heritage_Track.display_strategies import DisplaySetStrategy
from Plugins.Heritage_Track.heritage_track_widget import HeritageTrackWidget
from Plugins.Heritage_Track.pedigree_router import (
    LAYOUT_MODE_FOCUSED,
    LAYOUT_MODE_OVERVIEW,
    PedigreeRouter,
)


class _Engine:
    all_nodes = set()
    parent_to_children = {}

    def compute_levels(self, nodes):
        return {node: 0 for node in nodes}


class _EchoStrategy(DisplaySetStrategy):
    def compute(self, engine, selected, max_generations=999, exclude_archived=False, archived_set=None):
        return set(selected)


class HeritageSelectionModeScopeTest(unittest.TestCase):
    def _widget(self):
        records = {
            "Alpha | Callithrix jacchus | 01.01.2020 | DPZ": {
                "name": "Alpha", "id": "CJ-001", "ipid": "Alpha | Callithrix jacchus | 01.01.2020 | DPZ",
                "species": "Callithrix jacchus",
            },
            "Beta | Callithrix jacchus | 02.01.2020 | DPZ": {
                "name": "Beta", "id": "CJ-002", "ipid": "Beta | Callithrix jacchus | 02.01.2020 | DPZ",
                "species": "Callithrix jacchus",
            },
        }
        app = SimpleNamespace(
            selected_animals=[],
            _selected_heritage_only=[],
            animals=records,
            archived={},
        )
        widget = HeritageTrackWidget.__new__(HeritageTrackWidget)
        widget.app = app
        widget.plugin = SimpleNamespace(_all_identity_records=lambda: records)
        widget.layout_mode = LAYOUT_MODE_FOCUSED
        widget._canonical_selection_ids = ()
        return widget, app

    def test_canonical_selection_merges_aliases_deduplicates_and_sorts(self):
        widget, _app = self._widget()
        alpha = "Alpha | Callithrix jacchus | 01.01.2020 | DPZ"
        beta = "Beta | Callithrix jacchus | 02.01.2020 | DPZ"
        self.assertEqual(
            widget._canonicalize_selection([" beta ", "CJ-001", beta, alpha, "CJ-002"]),
            (alpha, beta),
        )

    def test_selection_thresholds_are_based_on_unique_canonical_values(self):
        widget, _app = self._widget()
        for count, expected in ((0, LAYOUT_MODE_OVERVIEW), (1, LAYOUT_MODE_FOCUSED), (5, LAYOUT_MODE_FOCUSED),
                                (8, LAYOUT_MODE_FOCUSED), (9, LAYOUT_MODE_OVERVIEW)):
            values = [f"animal-{index}" for index in range(count)]
            duplicated = values + values[:2]
            canonical = widget._canonicalize_selection(duplicated)
            self.assertEqual(len(canonical), count)
            self.assertEqual(widget._layout_mode_for_selection(list(canonical)), expected)

    def test_cache_key_is_invariant_to_selection_permutation(self):
        widget, _app = self._widget()
        key_a = widget._render_cache_key(["CJ-002", "CJ-001"], False, display_mode=LAYOUT_MODE_FOCUSED)
        key_b = widget._render_cache_key(["CJ-001", "CJ-002"], False, display_mode=LAYOUT_MODE_FOCUSED)
        self.assertEqual(key_a, key_b)

    def test_context_carries_canonical_scope_and_explicit_mode(self):
        builder = DisplayContextBuilder(
            engine=_Engine(),
            settings={"max_generations": 2},
            display_strategy=_EchoStrategy(),
        )
        context = builder.build(
            ["B", "A", "B"],
            display_mode=LAYOUT_MODE_OVERVIEW,
            selection_type="selected",
        )
        self.assertEqual(context.canonical_selection, ("A", "B"))
        self.assertEqual(context.display_mode, LAYOUT_MODE_OVERVIEW)
        self.assertEqual(context.selection_type, "selected")

    def test_router_preserves_explicit_mode_even_when_focus_set_is_misleading(self):
        router = PedigreeRouter()
        positions = {"A": (0.0, 0.0), "B": (2.0, 0.0)}
        focused = router.plan(
            positions,
            {},
            focus_nodes=set(),
            display_mode=LAYOUT_MODE_FOCUSED,
        )
        overview = router.plan(
            positions,
            {},
            focus_nodes={"A"},
            display_mode=LAYOUT_MODE_OVERVIEW,
        )
        self.assertEqual(focused.display_mode, LAYOUT_MODE_FOCUSED)
        self.assertEqual(overview.display_mode, LAYOUT_MODE_OVERVIEW)


if __name__ == "__main__":
    unittest.main()
