"""Semantic SVG UI icon registry with exact text fallbacks.

The packaged artwork stays vector-only.  UI masters use a fixed black outline
and are rendered unchanged on every Qt palette.  This is deliberately not a
PNG fallback: the SVG master remains the only runtime asset.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QIcon, QPalette


ICON_ROOT = Path(__file__).resolve().parents[2] / "icons" / "ui"
MANIFEST_PATH = ICON_ROOT / "manifest.json"
DISPLAY_SCALE = 1.5
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


def _adaptive_outline(_palette: QPalette | None) -> str:
    """Keep the canonical black outline independent of the Qt palette.

    Older builds replaced a dark-blue outline with the active palette's
    highlight colour on dark themes.  That made the same icon appear blue on
    another workstation.  The UI contract now requires stable black outlines,
    so no palette-dependent replacement is performed.
    """

    return ""


@lru_cache(maxsize=128)
def _rendered_icon(filename: str, _outline: str) -> QIcon:
    path = ICON_ROOT / filename
    if not filename or not path.is_file():
        return QIcon()
    # SVG is the single runtime asset.  Do not recolour it from palette data;
    # its black contour must remain identical in the README, picker, and app.
    return QIcon(str(path))


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
                if current.width() < 24 and current.height() < 24:
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
