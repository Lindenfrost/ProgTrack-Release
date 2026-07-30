"""Semantic UI icon registry with exact text fallbacks."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from PyQt6.QtGui import QIcon


ICON_ROOT = Path(__file__).resolve().parents[2] / "icons" / "ui"
MANIFEST_PATH = ICON_ROOT / "manifest.json"


@lru_cache(maxsize=1)
def manifest() -> dict[str, dict[str, str]]:
    try:
        raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = raw.get("icons", {}) if isinstance(raw, dict) else {}
    return entries if isinstance(entries, dict) else {}


def icon(semantic_id: str) -> QIcon:
    entry = manifest().get(semantic_id, {})
    filename = str(entry.get("file", ""))
    path = ICON_ROOT / filename
    return QIcon(str(path)) if filename and path.is_file() else QIcon()


def fallback_text(semantic_id: str, default: str = "") -> str:
    return str(manifest().get(semantic_id, {}).get("fallback", default))


def apply_icon(widget: Any, semantic_id: str, *, fallback: str = "") -> bool:
    resolved = icon(semantic_id)
    if not resolved.isNull() and hasattr(widget, "setIcon"):
        widget.setIcon(resolved)
        return True
    text = fallback_text(semantic_id, fallback)
    if text and hasattr(widget, "setText") and not widget.text():
        widget.setText(text)
    return False
