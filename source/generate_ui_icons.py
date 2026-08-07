"""Validate and synchronize ProgTrack's SVG-only semantic UI icon set.

Editable vector masters live in ``Q:/GitHub/Graphics/SVG/UI``.  This tool
copies only SVG files into the release's ``icons/ui`` package and renders
optional 64 px PNG review previews outside the release repository.  Those
previews are artwork-review aids, never runtime assets or fallbacks.

The manifest is authoritative: every packaged SVG must be referenced by at
least one semantic ID, every referenced master must exist, and the release UI
folder must contain no PNG files.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QRectF
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = REPOSITORY_ROOT / "icons" / "ui"
MANIFEST_PATH = PACKAGE_ROOT / "manifest.json"
SVG_ROOT = Path(r"Q:\GitHub\Graphics\SVG\UI")
REVIEW_PNG_ROOT = Path(r"Q:\GitHub\Graphics\UI")
PREVIEW_SIZE = 64


def _manifest_files() -> set[str]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    entries = payload.get("icons", {})
    files = {
        str(entry.get("file") or "")
        for entry in entries.values()
        if isinstance(entry, dict)
    }
    if not files or "" in files or any(not name.endswith(".svg") for name in files):
        raise ValueError("Every UI manifest entry must reference one SVG file")
    return files


def _renderer(path: Path) -> QSvgRenderer:
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        raise ValueError(f"Qt cannot render SVG master: {path}")
    return renderer


def _render_preview(source: Path, target: Path) -> None:
    renderer = _renderer(source)
    image = QImage(
        PREVIEW_SIZE,
        PREVIEW_SIZE,
        QImage.Format.Format_ARGB32_Premultiplied,
    )
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, PREVIEW_SIZE, PREVIEW_SIZE))
    painter.end()
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"Could not write review preview: {target}")


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])
    del app
    expected = _manifest_files()
    PACKAGE_ROOT.mkdir(parents=True, exist_ok=True)
    REVIEW_PNG_ROOT.mkdir(parents=True, exist_ok=True)

    missing = sorted(name for name in expected if not (SVG_ROOT / name).is_file())
    if missing:
        raise FileNotFoundError(f"Missing canonical SVG masters: {missing}")

    stale_package = sorted(
        path.name for path in PACKAGE_ROOT.glob("*.svg") if path.name not in expected
    )
    if stale_package:
        raise ValueError(f"Unregistered packaged UI SVGs: {stale_package}")

    for png in PACKAGE_ROOT.glob("*.png"):
        png.unlink()

    for name in sorted(expected, key=str.casefold):
        source = SVG_ROOT / name
        _renderer(source)
        shutil.copyfile(source, PACKAGE_ROOT / name)
        _render_preview(source, REVIEW_PNG_ROOT / f"{Path(name).stem}.png")

    stale_previews = [
        path
        for path in REVIEW_PNG_ROOT.glob("*.png")
        if f"{path.stem}.svg" not in expected
    ]
    for path in stale_previews:
        path.unlink()

    print(
        f"Synchronized {len(expected)} SVG masters; "
        "the packaged UI folder contains no PNG fallback."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
