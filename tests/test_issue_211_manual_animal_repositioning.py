"""Regression coverage for Heritage Track issue #211.

The repository ignores the local ``tests/`` workspace by design, but this
focused test remains useful for the 0.2.3 implementation and manual-gate
record.  It reuses the current position-cache integration fixture so the
checks exercise the real widget, router, render publication, and persistence
boundary together.
"""

import unittest
from types import SimpleNamespace



def _position_widget_fixture():
    """Load the shared setup lazily so pytest does not collect its tests here."""
    from tests.test_heritage_position_cache import HeritagePositionWidgetTest
    return HeritagePositionWidgetTest


class HeritageManualAnimalRepositioningTest(unittest.TestCase):
    """Only the three #211 checks; do not inherit the 45-case cache suite.

    The shared setup is intentionally reused as a fixture, but test methods
    from ``HeritagePositionWidgetTest`` must not be collected a second time.
    """

    def setUp(self):
        _position_widget_fixture().setUp(self)

    def tearDown(self):
        _position_widget_fixture().tearDown(self)

    def saved(self, key=None):
        return self.plugin.store.get_position_cache_entry(
            "guest", key or self.widget._active_position_cache_key
        )

    def test_manual_animal_overlap_is_published_and_persisted(self):
        widget = self.widget
        moved = "C"
        target = "M"
        candidate = {
            node: point
            for node, point in widget.node_positions.items()
            if not widget._is_family_node(node)
        }
        candidate[moved] = candidate[target]

        self.assertTrue(
            widget.refresh_graph(
                keep_view=True,
                position_candidate=candidate,
                manual_animal_position_override=True,
            )
        )
        self.assertEqual(widget.node_positions[moved], widget.node_positions[target])
        saved = self.saved()
        self.assertEqual(
            (saved["positions"][moved]["x"], saved["positions"][moved]["y"]),
            widget.node_positions[moved],
        )

    def test_zoom_boundary_drag_uses_display_coordinates_when_xdata_is_missing(self):
        widget = self.widget
        widget.canvas.draw()
        start = widget.node_positions["C"]
        start_px, start_py = widget.ax.transData.transform(start)
        widget._on_mouse_press(
            SimpleNamespace(
                button=1,
                inaxes=widget.ax,
                xdata=start[0],
                ydata=start[1],
                x=start_px,
                y=start_py,
                dblclick=False,
            )
        )

        moved_px = start_px + 80.0
        moved_py = start_py + 40.0
        widget._on_mouse_move(
            SimpleNamespace(
                button=1,
                inaxes=None,
                xdata=None,
                ydata=None,
                x=moved_px,
                y=moved_py,
                dblclick=False,
            )
        )
        expected_data = widget.ax.transData.inverted().transform(
            (moved_px, moved_py)
        )
        self.assertTrue(widget.is_dragging)
        self.assertAlmostEqual(
            widget.temp_positions["C"][0],
            float(expected_data[0]),
            places=6,
        )
        self.assertAlmostEqual(
            widget.temp_positions["C"][1],
            float(expected_data[1]),
            places=6,
        )
        widget._on_mouse_release(
            SimpleNamespace(
                button=1,
                inaxes=None,
                xdata=None,
                ydata=None,
                x=moved_px,
                y=moved_py,
            )
        )
        saved_point = self.saved()["positions"]["C"]
        self.assertEqual(
            widget.node_positions["C"],
            (saved_point["x"], saved_point["y"]),
        )

    def test_display_fallback_is_finite_and_independent_of_stale_xdata(self):
        widget = self.widget
        widget.canvas.draw()
        point = (123.0, 234.0)
        event = SimpleNamespace(
            inaxes=None,
            x=point[0],
            y=point[1],
            xdata=None,
            ydata=None,
        )
        actual = widget._event_data_position(event)
        expected = widget.ax.transData.inverted().transform(point)
        self.assertEqual(actual, (float(expected[0]), float(expected[1])))
