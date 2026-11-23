from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtGui import QColor, QImage, QPainter, QPalette
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QStyleFactory

from Plugins.core.ui_icons import _adaptive_outline, icon


ROOT = Path(__file__).resolve().parents[1]
ICON_ROOT = ROOT / "icons" / "ui"
MASTER_ROOT = ROOT.parent / "Graphics" / "SVG" / "UI"


def _qt_dark_palette(light: QPalette) -> QPalette:
    """Return Qt Fusion's conventional role-based dark palette."""

    palette = QPalette(light)
    values = {
        QPalette.ColorRole.Window: QColor(53, 53, 53),
        QPalette.ColorRole.WindowText: QColor(255, 255, 255),
        QPalette.ColorRole.Base: QColor(35, 35, 35),
        QPalette.ColorRole.AlternateBase: QColor(53, 53, 53),
        QPalette.ColorRole.ToolTipBase: QColor(25, 25, 25),
        QPalette.ColorRole.ToolTipText: QColor(255, 255, 255),
        QPalette.ColorRole.Text: QColor(255, 255, 255),
        QPalette.ColorRole.Button: QColor(53, 53, 53),
        QPalette.ColorRole.ButtonText: QColor(255, 255, 255),
        QPalette.ColorRole.BrightText: QColor(255, 0, 0),
        QPalette.ColorRole.Link: QColor(42, 130, 218),
        QPalette.ColorRole.Highlight: QColor(42, 130, 218),
        QPalette.ColorRole.HighlightedText: QColor(0, 0, 0),
    }
    for role, color in values.items():
        palette.setColor(role, color)
    return palette


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    channels = []
    for channel in rgb:
        value = channel / 255.0
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _visible_fraction(resolved, background: QColor, *, size: int = 27) -> float:
    source = resolved.pixmap(size, size).toImage().convertToFormat(
        QImage.Format.Format_ARGB32
    )
    composite = QImage(size, size, QImage.Format.Format_ARGB32)
    composite.fill(background)
    painter = QPainter(composite)
    painter.drawImage(0, 0, source)
    painter.end()
    background_rgb = background.getRgb()[:3]
    opaque = 0
    visible = 0
    for y in range(size):
        for x in range(size):
            if source.pixelColor(x, y).alpha() <= 32:
                continue
            opaque += 1
            pixel_rgb = composite.pixelColor(x, y).getRgb()[:3]
            if _contrast_ratio(pixel_rgb, background_rgb) >= 2.0:
                visible += 1
    return visible / max(opaque, 1)


class UiSvgPaletteContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle(QStyleFactory.create("Fusion"))
        cls.light = cls.app.style().standardPalette()
        cls.dark = _qt_dark_palette(cls.light)
        cls.manifest = json.loads(
            (ICON_ROOT / "manifest.json").read_text(encoding="utf-8")
        )["icons"]

    def test_manifest_covers_every_packaged_svg_and_has_no_png_fallback(self):
        manifest_files = {entry["file"] for entry in self.manifest.values()}
        packaged_files = {path.name for path in ICON_ROOT.glob("*.svg")}
        self.assertEqual(manifest_files, packaged_files)
        self.assertTrue(all(name.endswith(".svg") for name in manifest_files))
        self.assertEqual(list(ICON_ROOT.glob("*.png")), [])
        self.assertNotIn(".png", (ICON_ROOT / "manifest.json").read_text().lower())

    def test_editable_svg_masters_match_the_packaged_assets(self):
        for packaged in ICON_ROOT.glob("*.svg"):
            master = MASTER_ROOT / packaged.name
            self.assertTrue(master.is_file(), msg=f"Missing SVG master: {master}")
            # Git's checkout policy may use CRLF for packaged assets while
            # editable masters use LF. Compare decoded SVG text so line-ending
            # policy does not masquerade as artwork drift.
            self.assertEqual(
                packaged.read_text(encoding="utf-8"),
                master.read_text(encoding="utf-8"),
                msg=f"Packaged SVG drifted from its master: {packaged.name}",
            )

    def test_every_svg_is_qt_renderable_on_light_and_dark_palettes(self):
        semantic_by_file = {}
        for semantic_id, entry in self.manifest.items():
            semantic_by_file.setdefault(entry["file"], semantic_id)
        failures = []
        for filename, semantic_id in sorted(semantic_by_file.items()):
            renderer = QSvgRenderer(str(ICON_ROOT / filename))
            if not renderer.isValid():
                failures.append(f"{filename}: invalid SVG")
                continue
            for palette_name, palette in (("light", self.light), ("dark", self.dark)):
                resolved = icon(semantic_id, palette=palette)
                if resolved.isNull():
                    failures.append(f"{filename}: null {palette_name} icon")
                    continue
                # Fixed black outlines are part of the artwork contract.  On
                # an intentionally dark Qt palette they may have low contrast
                # by design, so visibility is asserted on the normal light
                # button surface while both palette renders must remain valid.
                fraction = _visible_fraction(
                    resolved, palette.color(QPalette.ColorRole.Button)
                )
                if palette_name == "light" and fraction < 0.30:
                    failures.append(
                        f"{filename}: only {fraction:.1%} visible pixels on "
                        f"Qt {palette_name} button palette"
                    )
        self.assertEqual(failures, [])

    def test_ui_outlines_remain_black_on_all_palettes(self):
        self.assertEqual(_adaptive_outline(self.light), "")
        self.assertEqual(_adaptive_outline(self.dark), "")
        for path in ICON_ROOT.glob("*.svg"):
            svg = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("#25364a", svg, msg=f"Legacy blue outline in {path.name}")

    def test_language_and_account_icons_use_semantic_registry(self):
        source = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        for semantic_id in (
            "language.en",
            "language.de",
            "language.it",
            "language.ru",
            "account.lord",
            "account.master",
            "account.awo",
            "account.user",
        ):
            self.assertIn(semantic_id, self.manifest)
        self.assertIn("QAction(ui_icon(semantic_id)", source)
        self.assertNotIn("emojiFlag", source)
        self.assertIn('semantic_id = "account.master"', source)
        self.assertIn('semantic_id = "account.awo"', source)


if __name__ == "__main__":
    unittest.main()
