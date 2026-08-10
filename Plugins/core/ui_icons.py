"""Semantic SVG UI icon registry with exact text fallbacks.

The packaged artwork stays vector-only.  Qt renders a palette-aware in-memory
pixmap from the SVG when the canonical navy outline would not contrast with
the active widget palette.  This is deliberately not a PNG fallback: the SVG
master remains the only runtime asset and is re-rendered for the active Qt
palette.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QImage, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer


ICON_ROOT = Path(__file__).resolve().parents[2] / "icons" / "ui"
MANIFEST_PATH = ICON_ROOT / "manifest.json"
DISPLAY_SCALE = 1.5
CANONICAL_OUTLINE = QColor("#25364a")
CANONICAL_LIGHT_FILL = QColor("#f7fafc")
MIN_OUTLINE_CONTRAST = 3.0
LEGACY_ROLE_ICON_ALIASES = {
    "\u2640": "role.female",
    "\u2642": "role.male",
    "\U0001f476": "role.offspring",
    "\U0001f43e": "role.partner",
    "\u26a4": "role.breeding",
    "\U0001f4a1": "role.experimental",
    # Early Role Setup builds could persist the shortened semantic text that
    # was visible underneath the SVG picker.  Keep those records rendering
    # while the next authorized Role Setup save writes the canonical value.
    "role.fem": "role.female",
    "role.mal": "role.male",
    "role.off": "role.offspring",
    "role.par": "role.partner",
    "role.bre": "role.breeding",
}



@lru_cache(maxsize=1)
def manifest() -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = raw.get("icons", {}) if isinstance(raw, dict) else {}
    return entries if isinstance(entries, dict) else {}


def _relative_luminance(color: QColor) -> float:
    channels = []
    for value in (color.redF(), color.greenF(), color.blueF()):
        channels.append(
            value / 12.92
            if value <= 0.04045
            else ((value + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first: QColor, second: QColor) -> float:
    first_luminance = _relative_luminance(first)
    second_luminance = _relative_luminance(second)
    lighter = max(first_luminance, second_luminance)
    darker = min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


def _effective_palette(palette: QPalette | None) -> QPalette | None:
    if palette is not None:
        return palette
    app = QGuiApplication.instance()
    return app.palette() if app is not None else None


def _adaptive_outline(palette: QPalette | None) -> str:
    """Return a palette outline only when the canonical navy is unreadable.

    UI icons occur on buttons, tabs, labels, and list rows, so all common Qt
    surface roles are considered.  The normal light palette keeps the source
    artwork byte-for-byte; a dark or high-contrast palette receives the most
    legible Qt text-role colour.
    """

    active = _effective_palette(palette)
    if active is None:
        return ""
    surfaces = (
        active.color(QPalette.ColorRole.Button),
        active.color(QPalette.ColorRole.Window),
        active.color(QPalette.ColorRole.Base),
        active.color(QPalette.ColorRole.AlternateBase),
    )
    canonical_score = min(
        _contrast_ratio(CANONICAL_OUTLINE, surface) for surface in surfaces
    )
    if canonical_score >= MIN_OUTLINE_CONTRAST:
        return ""
    # A pure light text colour would contrast with the dark surface but would
    # disappear inside the many fixed light-filled controls.  Include that
    # fill in the score and prefer Qt's palette highlight/link colour when it
    # provides balanced contrast on both sides.
    comparison_colors = surfaces + (CANONICAL_LIGHT_FILL,)
    candidates = (
        active.color(QPalette.ColorRole.Highlight),
        active.color(QPalette.ColorRole.Link),
        active.color(QPalette.ColorRole.ButtonText),
        active.color(QPalette.ColorRole.WindowText),
        active.color(QPalette.ColorRole.Text),
    )
    candidate = max(
        candidates,
        key=lambda color: min(
            _contrast_ratio(color, comparison) for comparison in comparison_colors
        ),
    )
    candidate_score = min(_contrast_ratio(candidate, surface) for surface in surfaces)
    return candidate.name() if candidate_score > canonical_score else ""


@lru_cache(maxsize=128)
def _rendered_icon(filename: str, outline: str) -> QIcon:
    path = ICON_ROOT / filename
    if not filename or not path.is_file():
        return QIcon()
    if not outline:
        return QIcon(str(path))
    try:
        svg = path.read_text(encoding="utf-8")
    except OSError:
        return QIcon()
    # All editable UI masters use this one canonical outline token.  Replacing
    # it in memory retains every fill and semantic colour from the source SVG.
    svg = svg.replace("#25364a", outline).replace("#25364A", outline)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        return QIcon()
    resolved = QIcon()
    for size in (64, 128):
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        resolved.addPixmap(QPixmap.fromImage(image))
    return resolved


def canonical_icon_value(value: object) -> str:
    raw = str(value or "").strip()
    return LEGACY_ROLE_ICON_ALIASES.get(raw, raw)


def resolve_icon_path(icon_value: object) -> Path | None:
    """Resolve a semantic ID or safe SVG value within the UI asset root."""
    value = canonical_icon_value(icon_value)
    entry = manifest().get(value, {})
    filename = str(entry.get("file", "")).strip() if isinstance(entry, dict) else ""
    if not filename:
        candidate = value[4:].strip() if value.startswith("svg:") else value
        if Path(candidate).name != candidate or Path(candidate).suffix.casefold() != ".svg":
            return None
        filename = candidate
    path = ICON_ROOT / Path(filename).name
    if not path.is_file() or path.suffix.casefold() != ".svg":
        return None
    return path


def icon(semantic_id: str, *, palette: QPalette | None = None) -> QIcon:
    path = resolve_icon_path(semantic_id)
    filename = path.name if path is not None else ""
    return _rendered_icon(filename, _adaptive_outline(palette))


def fallback_text(semantic_id: str, default: str = "") -> str:
    value = canonical_icon_value(semantic_id)
    return str(manifest().get(value, {}).get("fallback", default))


def apply_icon(widget: Any, semantic_id: str, *, fallback: str = "") -> bool:
    palette = widget.palette() if hasattr(widget, "palette") else None
    resolved = icon(semantic_id, palette=palette)
    if not resolved.isNull() and hasattr(widget, "setIcon"):
        widget.setIcon(resolved)
        # Keep all semantic SVGs consistently legible.  Call sites may still
        # provide a larger context-specific size, but the registry never
        # leaves the default 16px rendering in place.
        if hasattr(widget, "iconSize") and hasattr(widget, "setIconSize"):
            try:
                current = widget.iconSize()
                # Explicitly sized controls (30px sidebar buttons, for
                # example) are already at the enlarged target and must not
                # grow again when translations are refreshed.
                if current.width() <= 24 and current.height() <= 24:
                    width = max(1, int(round(current.width() * DISPLAY_SCALE)))
                    height = max(1, int(round(current.height() * DISPLAY_SCALE)))
                    widget.setIconSize(QSize(width, height))
            except (AttributeError, TypeError, RuntimeError):
                pass
        return True
    text = fallback_text(semantic_id, fallback)
    if text and hasattr(widget, "setText") and not widget.text():
        widget.setText(text)
    return False
