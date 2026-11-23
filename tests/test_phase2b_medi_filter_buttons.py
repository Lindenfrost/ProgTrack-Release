import json
import os
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QSizePolicy

from Plugins.Medi_Track.medi_track_widget import MediTrackWidget


ROOT = Path(__file__).resolve().parents[1]
FILTERS = {
    MediTrackWidget.FILTER_SICK: ("medi_track.filter.current_sick", "Currently Sick"),
    MediTrackWidget.FILTER_EVER_SICK: ("medi_track.filter.ever_sick", "Ever Sick"),
    MediTrackWidget.FILTER_ABNORMAL: ("medi_track.filter.current_abnormal", "Currently Abnormal"),
    MediTrackWidget.FILTER_EVER_ABNORMAL: ("medi_track.filter.ever_abnormal", "Ever Abnormal"),
    MediTrackWidget.FILTER_IN_EXPERIMENT: ("medi_track.filter.current_experiment", "Currently in experiment"),
    MediTrackWidget.FILTER_EVER_EXPERIMENT: ("medi_track.filter.ever_experiment", "Was in experiment"),
}


class MediFilterButtonPresentationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt_app = QApplication.instance() or QApplication([])

    @staticmethod
    def messages(lang):
        return json.loads((ROOT / "lang" / f"messages_{lang}.json").read_text(encoding="utf-8"))

    def make_widget(self, lang="en"):
        return MediTrackWidget(
            SimpleNamespace(),
            SimpleNamespace(),
            self.messages(lang),
            lang,
        )

    def test_non_all_filters_are_icon_only_compact_and_localized(self):
        widget = self.make_widget()
        all_button = widget._filter_btns[MediTrackWidget.FILTER_ALL]
        self.assertEqual(all_button.text(), "All")
        self.assertEqual(all_button.toolTip(), "")
        self.assertGreater(all_button.sizeHint().width(), 0)
        self.assertTrue(all_button.height() > 0)

        for filter_key, (message_key, fallback) in FILTERS.items():
            button = widget._filter_btns[filter_key]
            expected = self.messages("en").get(message_key, fallback)
            self.assertEqual(button.text(), "", filter_key)
            self.assertEqual(button.toolTip(), expected, filter_key)
            self.assertEqual(button.accessibleName(), expected, filter_key)
            self.assertFalse(button.icon().isNull(), filter_key)
            self.assertEqual(
                button.sizePolicy().horizontalPolicy(),
                QSizePolicy.Policy.Fixed,
            )
            self.assertEqual(button.minimumWidth(), 0)
            self.assertLess(button.sizeHint().width(), all_button.sizeHint().width())
            self.assertEqual(button.height(), all_button.height(), filter_key)

    def test_language_refresh_updates_tooltips_without_restoring_filter_text(self):
        widget = self.make_widget("en")
        german = self.messages("de")
        widget.update_language(german, "de")
        all_button = widget._filter_btns[MediTrackWidget.FILTER_ALL]
        self.assertEqual(all_button.text(), german["medi_track.filter.all"])
        self.assertEqual(all_button.toolTip(), "")
        self.assertTrue(all_button.height() > 0)
        for filter_key, (message_key, fallback) in FILTERS.items():
            button = widget._filter_btns[filter_key]
            expected = german.get(message_key, fallback)
            self.assertEqual(button.text(), "", filter_key)
            self.assertEqual(button.toolTip(), expected, filter_key)
            self.assertEqual(button.accessibleName(), expected, filter_key)
            self.assertEqual(button.minimumWidth(), 0)
            self.assertEqual(button.height(), all_button.height(), filter_key)

    def test_offscreen_filter_row_render(self):
        widget = self.make_widget()
        widget.resize(900, 500)
        widget.show()
        self.qt_app.processEvents()
        evidence = ROOT.parent / "Issue Tracker" / "Tests" / "_phase2b_ui_evidence_2026-08-12_medi_filter_buttons_compact.png"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        self.assertTrue(widget.grab().save(str(evidence)))


if __name__ == "__main__":
    unittest.main()
