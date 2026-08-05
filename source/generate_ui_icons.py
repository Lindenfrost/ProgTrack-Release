"""Generate the SVG-first ProgTrack UI icon set and 64px PNG previews.

The editable SVG sources live outside the release repository in
``Q:/GitHub/Graphics/SVG/UI``.  PNG derivatives are retained only in
``Q:/GitHub/Graphics/UI`` for artwork review and backup.  The release package
uses SVG files directly and does not receive PNG fallbacks.

Several semantic IDs intentionally share one canonical SVG.  Their aliases
are declared in ``SHARED_ICON_ALIASES`` and are never emitted as duplicate
files.  Run this script after changing an icon definition; it also removes
stale alias SVG/PNG files from the graphics backup folder.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QByteArray, QSize
from PyQt6.QtGui import QGuiApplication, QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer


SVG_ROOT = Path(r"Q:\GitHub\Graphics\SVG\UI")
BACKUP_PNG_ROOT = Path(r"Q:\GitHub\Graphics\UI")
SIZE = 64


# Semantic IDs in the manifest remain distinct, but these artwork files are
# deliberately shared.  Keeping one canonical file prevents visual drift and
# makes the relationship explicit to the generator and asset review tools.
SHARED_ICON_ALIASES: dict[str, str] = {
    "role_breeding": "pedigree_symbol",
    "status_partner": "role_partner",
    "medi_current_sick": "status_sick",
    "medi_current_abnormal": "status_abnormal",
    "status_warning": "status_abnormal",
}

# pedigree_symbol is an existing editable master rather than a generated body
# in ICONS.  It still receives a PNG review derivative below.
EXISTING_CANONICAL_SVGS = ("pedigree_symbol",)


def _svg(body: str) -> str:
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" viewBox="0 0 64 64">
  <style>.line{{fill:none;stroke:#25364a;stroke-width:4;stroke-linecap:round;stroke-linejoin:round}}.light{{fill:#f7fafc;stroke:#25364a;stroke-width:4;stroke-linejoin:round}}.blue{{fill:#2f80ed;stroke:#25364a;stroke-width:4;stroke-linejoin:round}}.pink{{fill:#ee83ba;stroke:#25364a;stroke-width:4;stroke-linejoin:round}}.green{{fill:#4caf82;stroke:#25364a;stroke-width:4;stroke-linejoin:round}}.amber{{fill:#f4b942;stroke:#25364a;stroke-width:4;stroke-linejoin:round}}.red{{fill:#df5b5b;stroke:#25364a;stroke-width:4;stroke-linejoin:round}}.purple{{fill:#9b7ad0;stroke:#25364a;stroke-width:4;stroke-linejoin:round}}</style>
  {body}
</svg>'''


ICONS: dict[str, str] = {
    "action_add": _svg('<circle class="blue" cx="32" cy="32" r="23"/><path d="M32 20v24M20 32h24" stroke="#fff" stroke-width="5" stroke-linecap="round"/>'),
    "action_edit": _svg('<path class="blue" d="M14 45l4-13L42 8l10 10-24 24z"/><path d="M37 13l10 10M14 45l11-1" class="line"/><path d="M18 32l14 14" class="line"/>'),
    "action_edit_role": _svg('<rect class="purple" x="10" y="11" width="36" height="42" rx="6"/><circle cx="28" cy="25" r="6" fill="#fff"/><path d="M18 42c2-8 18-8 20 0" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round"/><path d="M43 39l8-8 5 5-8 8-7 2z" class="amber"/>'),
    "measure_blood": _svg('<path class="red" d="M32 8C25 20 17 27 17 38a15 15 0 0 0 30 0C47 27 39 20 32 8z"/><path d="M32 25v20M22 35h20" stroke="#fff" stroke-width="4" stroke-linecap="round"/>'),
    "measure_urine": _svg('<path class="amber" d="M32 8C25 20 17 27 17 38a15 15 0 0 0 30 0C47 27 39 20 32 8z"/><path d="M22 43c5 5 15 5 20 0" class="line"/>'),
    "measure_sperm": _svg('<circle class="purple" cx="25" cy="25" r="11"/><path d="M34 33c12 2 15 9 6 15-6 4-10 0-6-4 3-3 9 0 7 5" class="line"/><circle cx="25" cy="25" r="4" fill="#fff"/>'),
    "measure_weight": _svg('<path class="green" d="M11 50h42l-4-29H15z"/><path d="M24 21a8 8 0 0 1 16 0" class="line"/><path d="M32 33l7 8M32 33v12" class="line"/><circle cx="32" cy="33" r="3" fill="#fff"/>'),
    "action_archive": _svg('<path class="amber" d="M10 23h44v30H10z"/><path class="light" d="M7 15h50v10H7z"/><path d="M25 35h14" class="line"/>'),
    "action_restore": _svg('<path class="green" d="M50 32a18 18 0 1 1-6-13"/><path d="M50 12v14H36" fill="none" stroke="#25364a" stroke-width="5" stroke-linecap="round" stroke-linejoin="round"/><path d="M32 23v18M24 33l8 8 8-8" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'),
    "action_delete": _svg('<path class="red" d="M17 21h30l-3 32H20z"/><path d="M13 17h38M25 12h14M26 29v16M38 29v16" class="line"/>'),
    "action_refresh": _svg('<path class="blue" d="M49 25a19 19 0 0 0-33-7"/><path d="M15 18h13V7" class="line"/><path class="green" d="M15 39a19 19 0 0 0 33 7"/><path d="M49 46H36v11" class="line"/>'),
    "action_settings": _svg('<path class="light" d="M32 9l5 5 7-1 3 7 7 3-1 7 5 5-5 5 1 7-7 3-3 7-7-1-5 5-5-5-7 1-3-7-7-3 1-7-5-5 5-5-1-7 7-3 3-7 7 1z"/><circle class="blue" cx="32" cy="32" r="9"/>'),
    "toggle_expand": _svg('<circle class="light" cx="32" cy="32" r="23"/><path d="m27 20 13 12-13 12" class="line"/>'),
    "toggle_collapse": _svg('<circle class="light" cx="32" cy="32" r="23"/><path d="m20 27 12 13 12-13" class="line"/>'),
    "control_increment": _svg('<circle class="light" cx="32" cy="32" r="23"/><path d="M32 43V21M22 31l10-10 10 10" class="line"/>'),
    "control_decrement": _svg('<circle class="light" cx="32" cy="32" r="23"/><path d="M32 21v22M22 33l10 10 10-10" class="line"/>'),
    "heritage_placeholder": _svg('<circle class="light" cx="32" cy="32" r="22" stroke-dasharray="5 4"/><circle cx="32" cy="24" r="7" fill="#b7c0cb"/><path d="M20 45c3-10 21-10 24 0" fill="#b7c0cb"/>'),
    "role_female": _svg('<circle class="pink" cx="30" cy="25" r="13"/><path d="M30 38v16M22 46h16" class="line"/>'),
    "role_male": _svg('<circle class="blue" cx="25" cy="37" r="13"/><path d="M35 27 51 11M39 11h12v12" class="line"/>'),
    "role_offspring": _svg('<circle class="pink" cx="22" cy="25" r="9"/><circle class="blue" cx="41" cy="31" r="8"/><path d="M12 48c2-10 18-10 20 0M33 50c2-8 14-8 16 0" class="line"/>'),
    "role_partner": _svg('<circle class="pink" cx="23" cy="32" r="13"/><circle class="blue" cx="41" cy="32" r="13"/><path d="M29 32h6" class="line"/>'),
    "role_experimental": _svg('<path class="purple" d="M25 9h14M28 9v17L16 47a7 7 0 0 0 6 9h20a7 7 0 0 0 6-9L36 26V9"/><path d="M20 43h24" stroke="#fff" stroke-width="4"/><circle cx="34" cy="36" r="3" fill="#fff"/>'),
    "status_pregnant": _svg('<circle class="pink" cx="32" cy="32" r="22"/><circle cx="29" cy="29" r="8" fill="#fff"/><circle cx="39" cy="39" r="4" fill="#fff"/><path d="m22 43 6 6 14-17" fill="none" stroke="#25364a" stroke-width="4" stroke-linecap="round"/>'),
    "status_possible": _svg('<circle cx="32" cy="32" r="22" fill="#ee81bb" stroke="#25364a" stroke-width="4"/><path d="M24 26c0-5 3.5-8 8.5-8 5.5 0 8.5 3.2 8.5 7.5 0 4-2.3 6.2-5.4 8.4-2.7 1.9-4.1 3.3-4.1 7.1" fill="none" stroke="#ffffff" stroke-width="5" stroke-linecap="round"/><circle cx="31.5" cy="49" r="3" fill="#ffffff"/>'),
    "status_offspring": _svg('<circle class="light" cx="23" cy="25" r="10"/><circle class="pink" cx="41" cy="34" r="8"/><path d="M12 49c2-10 19-10 22 0M34 52c2-8 14-8 16 0" class="line"/>'),
    "status_not_pregnant": _svg('<circle cx="32" cy="32" r="22" fill="#ee81bb" stroke="#25364a" stroke-width="4"/><path d="M22 32H42" fill="none" stroke="#ffffff" stroke-width="6" stroke-linecap="round"/>'),
    "status_sick": _svg('<circle class="red" cx="32" cy="32" r="22"/><path d="M32 18v28M18 32h28" stroke="#fff" stroke-width="6" stroke-linecap="round"/>'),
    "status_abnormal": _svg('<path class="amber" d="M32 9 57 53H7z"/><path d="M32 24v14M32 45v1" class="line"/>'),
    "medi_ever_sick": _svg('<circle class="light" cx="27" cy="30" r="20"/><path d="M27 18v24M15 30h24" stroke="#df5b5b" stroke-width="6" stroke-linecap="round"/><circle class="blue" cx="47" cy="47" r="12"/><path d="M47 40v8l5 3" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round"/>'),
    "medi_ever_abnormal": _svg('<path class="light" d="M27 8 50 49H4z"/><path d="M27 22v13M27 42v1" class="line"/><circle class="blue" cx="47" cy="47" r="12"/><path d="M47 40v8l5 3" fill="none" stroke="#fff" stroke-width="3" stroke-linecap="round"/>'),
    "status_deceased": _svg('<circle class="light" cx="32" cy="32" r="22"/><path d="M32 15v34M21 25h22M21 40h22" class="line"/>'),
    "account_lord": _svg('<path class="amber" d="M12 21l10 8 10-16 10 16 10-8-4 28H16z"/><path d="M16 52h32" class="line"/><circle cx="22" cy="29" r="3" fill="#fff"/><circle cx="32" cy="23" r="3" fill="#fff"/><circle cx="42" cy="29" r="3" fill="#fff"/>'),
    "account_user": _svg('<circle class="blue" cx="32" cy="23" r="11"/><path class="blue" d="M13 54c2-16 36-16 38 0z"/>'),
    "account_guest_locked": _svg('<rect class="light" x="15" y="28" width="34" height="25" rx="4"/><path d="M22 28v-7a10 10 0 0 1 20 0v7" class="line"/><circle cx="32" cy="40" r="3" fill="#25364a"/><path d="M32 43v5" class="line"/>'),
    "network_insert_symbol": _svg('<circle class="amber" cx="32" cy="32" r="22"/><circle cx="24" cy="27" r="3" fill="#25364a"/><circle cx="40" cy="27" r="3" fill="#25364a"/><path d="M21 38c6 8 16 8 22 0" class="line"/>'),
    "flow_freezer": _svg('<circle class="blue" cx="32" cy="32" r="22"/><path d="M32 14v36M16 23l32 18M16 41l32-18" stroke="#fff" stroke-width="4" stroke-linecap="round"/><circle cx="32" cy="32" r="4" fill="#fff"/>'),
    "status_ok": _svg('<circle class="green" cx="32" cy="32" r="22"/><path d="m20 33 8 8 17-19" fill="none" stroke="#fff" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/>'),
}


def render(svg: str, target: Path) -> None:
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG for {target.name}")
    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()
    if not image.save(str(target), "PNG"):
        raise RuntimeError(f"Could not save {target}")


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])
    del app
    for root in (SVG_ROOT, BACKUP_PNG_ROOT):
        root.mkdir(parents=True, exist_ok=True)

    # Remove old generated aliases so a previous run cannot leave duplicate
    # artwork beside the canonical file.
    for alias in SHARED_ICON_ALIASES:
        (SVG_ROOT / f"{alias}.svg").unlink(missing_ok=True)
        (BACKUP_PNG_ROOT / f"{alias}.png").unlink(missing_ok=True)

    for identifier, svg in ICONS.items():
        svg_path = SVG_ROOT / f"{identifier}.svg"
        backup_png = BACKUP_PNG_ROOT / f"{identifier}.png"
        svg_path.write_text(svg + "\n", encoding="utf-8")
        render(svg, backup_png)

    for identifier in EXISTING_CANONICAL_SVGS:
        svg_path = SVG_ROOT / f"{identifier}.svg"
        if not svg_path.is_file():
            raise FileNotFoundError(f"Missing canonical SVG master: {svg_path}")
        render(svg_path.read_text(encoding="utf-8"), BACKUP_PNG_ROOT / f"{identifier}.png")

    print(f"Generated {len(ICONS)} SVG definitions and {len(ICONS) + len(EXISTING_CANONICAL_SVGS)} PNG previews.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
