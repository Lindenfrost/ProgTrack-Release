import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from matplotlib.backend_bases import MouseButton, MouseEvent
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]


class PlotsEmptyDoubleClickTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "progtrack_issue_234", ROOT / "ProgTrack.v.0.3.0.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(cls.module)

    @staticmethod
    def event(*, button=1, dblclick=False, inaxes=True):
        return SimpleNamespace(
            button=button,
            dblclick=dblclick,
            inaxes=object() if inaxes else None,
        )

    def test_left_double_click_anywhere_inside_any_axes_clears_selection(self):
        helper = self.module.ProgTrackApp._plot_double_click_clears_selection

        figure = Figure(figsize=(8, 3))
        first_axes, second_axes = figure.subplots(1, 2)
        for axes in (first_axes, second_axes):
            axes.plot([0, 1], [0, 1], marker="o", label="series")
            axes.legend()
        canvas = FigureCanvasAgg(figure)
        canvas.draw()

        # Actual Matplotlib events over data and empty chart background in
        # separate subplots must both qualify; artist hit-testing is irrelevant.
        for axes, point in (
            (first_axes, (0.5, 0.5)),  # line
            (second_axes, (0.0, 0.0)),  # marker
            (second_axes, (0.8, 0.2)),  # empty chart background
        ):
            x, y = axes.transData.transform(point)
            event = MouseEvent(
                "button_press_event", canvas, x, y,
                button=MouseButton.LEFT, dblclick=True,
            )
            self.assertIs(event.inaxes, axes)
            self.assertTrue(helper(event))

        inside = first_axes.transData.transform((0.8, 0.2))
        self.assertFalse(helper(MouseEvent(
            "button_press_event", canvas, *inside,
            button=MouseButton.LEFT, dblclick=False,
        )))
        self.assertFalse(helper(MouseEvent(
            "button_press_event", canvas, *inside,
            button=MouseButton.RIGHT, dblclick=True,
        )))
        outside = MouseEvent(
            "button_press_event", canvas, -5, -5,
            button=MouseButton.LEFT, dblclick=True,
        )
        self.assertIsNone(outside.inaxes)
        self.assertFalse(helper(outside))

    def test_plot_selection_clear_is_queued_after_event_dispatch(self):
        helper = self.module.ProgTrackApp._queue_plot_selection_clear
        clear_selection = Mock()
        queued_callbacks = []
        with patch.object(
            self.module.QTimer,
            "singleShot",
            side_effect=lambda delay, callback: queued_callbacks.append(
                (delay, callback)
            ),
        ):
            helper(clear_selection)
        clear_selection.assert_not_called()
        self.assertEqual(len(queued_callbacks), 1)
        delay, callback = queued_callbacks[0]
        self.assertEqual(delay, 0)
        self.assertIs(callback, clear_selection)
        callback()
        clear_selection.assert_called_once_with()

    def test_plot_selection_clear_runs_on_the_next_qt_event_loop_turn(self):
        app = (
            self.module.QtWidgets.QApplication.instance()
            or self.module.QtWidgets.QApplication([])
        )

        dispatching = {"active": False}
        cleared = []

        def clear_selection():
            self.assertFalse(
                dispatching["active"],
                "clearing selection must not replace the canvas inside its event callback",
            )
            cleared.append(True)

        # Model the Matplotlib button-press callback: the plot canvas must still
        # exist until the callback has returned, then Qt may process the queued
        # selection reset and replace it safely.
        dispatching["active"] = True
        self.module.ProgTrackApp._queue_plot_selection_clear(clear_selection)
        self.assertEqual(cleared, [])
        dispatching["active"] = False

        app.processEvents()
        self.assertEqual(cleared, [True])

    def test_plot_click_handler_classifies_empty_double_click_before_pan(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        press_start = source.index("        def _plot_press_is_empty(event)")
        press_end = source.index("        # ------------------------\n        # 7.18.11 Scroll zoom handler", press_start)
        press_helper = source[press_start:press_end]
        self.assertIn('getattr(self, "_annotation", None)', press_helper)
        self.assertIn('getattr(self, "_highlight_scatter", None)', press_helper)
        self.assertIn('getattr(self, "sperm_overlay_dots", [])', press_helper)

        start = source.index("        def on_click(event):")
        end = source.index("        # ------------------------\n        # 7.18.14 Button release", start)
        handler = source[start:end]

        clear_classification = handler.index(
            "if self._plot_double_click_clears_selection(event):"
        )
        empty_pan_classification = handler.index(
            "empty_press = _plot_press_is_empty(event)"
        )
        clear_queue = handler.index(
            "self._queue_plot_selection_clear(self._unselect_all_plots)"
        )
        cleanup = handler.index("if getattr(self, '_highlight_scatter', None):")
        self.assertLess(clear_classification, clear_queue)
        self.assertLess(clear_queue, empty_pan_classification)
        self.assertLess(clear_queue, cleanup)
        pan_block = handler.index("_pan_active = True")
        self.assertLess(clear_queue, pan_block)
        self.assertIn("_plot_double_click_clears_selection", handler)
        self.assertIn("_plot_press_is_empty(event)", handler)
        self.assertIn("_pan_active = False", handler[:clear_queue])


if __name__ == "__main__":
    unittest.main()
