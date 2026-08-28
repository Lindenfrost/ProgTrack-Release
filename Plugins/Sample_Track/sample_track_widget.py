# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Sample Track organ and biological sample widget.

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtCore import Qt, QSettings, QSize, QStringListModel, QTimer
from Plugins.core.backend_store import BackendJsonStore
from PyQt6.QtGui import QColor, QPainter, QFont, QTransform, QIcon
from PyQt6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
    QMessageBox, QPushButton, QScrollArea, QScrollBar, QSizePolicy, QSpinBox,
    QInputDialog,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget, QCompleter,
)
from Plugins.core.animal_identity import (
    animal_base_name,
    animal_identity_label,
    resolve_animal_reference_text,
    split_animal_identity_key,
)
from Plugins.core.platform_helpers import default_export_directory
from Plugins.core.ui_icons import apply_icon

logger = logging.getLogger(__name__)

PLUGIN_DIR = Path(__file__).parent
DATE_FORMAT = "%d.%m.%Y"


def _record_sex_value(record: Optional[Dict[str, Any]]) -> str:
    raw = str((record or {}).get("sex", "")).strip()
    lowered = raw.lower()
    if lowered in ("male", "männlich", "maennlich"):
        return "Male"
    if lowered in ("female", "weiblich"):
        return "Female"
    if raw in ("Male", "Female", "Undefined"):
        return raw
    return ""


def _species_abbreviation(species: str) -> str:
    words = [part for part in str(species or "").strip().split() if part]
    if len(words) >= 2:
        return "".join(word[0].lower() for word in words if word[0].isalnum())
    if words:
        return words[0][:2].lower()
    return ""

# ---------------------------------------------------------------------------
# Column-width constants shared by row widgets AND headers (px)
# Changing a value here aligns both header and data rows automatically.
# ---------------------------------------------------------------------------
_W1_NAME     = 100   # Tab 1 — animal name
_W1_SPECIES  =  70   # Tab 1 — species
_W1_ID       = 100   # Tab 1 — animal ID
_W1_SEX      =  72   # Tab 1 — sex (row line-edits + header combo)
_W1_UNIT     =  90   # Tab 1 — unit column  (header widget + row spacer/label)
_W1_DATE_GRP = 120   # Tab 1 — birth/death boxes AND age-from/age-to containers
_W1_SAVE     =  55   # Tab 1 — save button

_W2_SAMPLENO =  70   # Tab 2 — sample number
_W2_NAME     = 100   # Tab 2 — animal name
_W2_SPECIES  =  65   # Tab 2 — species
_W2_ID       =  95   # Tab 2 — animal ID
_W2_SEX      =  72   # Tab 2 — sex (row line-edits + header combo)
_W2_UNIT     =  90   # Tab 2 — unit column
_W2_DATE_GRP = 120   # Tab 2 — collection-date boxes AND date-from/to containers
_W2_SAVE     =  55   # Tab 2 — save button

# ---------------------------------------------------------------------------
# Size constants for scroll areas
# ---------------------------------------------------------------------------

# Fixed height for rotated labels (shared constant)
_ROTATED_LABEL_FIXED_H = 58

def _fit_scroll_area_to_content(scroll_area: QScrollArea, content: QWidget, extra: int = 2) -> None:
    """Set scroll_area height from the content widget's current sizeHint."""
    content.ensurePolished()
    if content.layout() is not None:
        content.layout().activate()
    h = content.sizeHint().height() + 2 * scroll_area.frameWidth() + extra
    scroll_area.setFixedHeight(max(h, 1))


def _sync_widget_height(widget: QWidget) -> None:
    """Refresh geometry without freezing widget height."""
    if widget.layout() is not None:
        widget.layout().activate()
    widget.adjustSize()
    widget.updateGeometry()


# ---------------------------------------------------------------------------
# Organ/tissue list — from SampleTrack_tissues_and_abbreviations.xlsx Tab 1
# Abbreviations are language-independent (same in all languages)
# ---------------------------------------------------------------------------
ORGANS: List[Dict[str, str]] = [
    {"key": "grosshirn",    "name": "Gro\u00dfhirn",    "abbrev": "GH"},
    {"key": "mittelhirn",   "name": "Mittelhirn",       "abbrev": "MH"},
    {"key": "kleinhirn",    "name": "Kleinhirn",        "abbrev": "KH"},
    {"key": "stammhirn",    "name": "Stammhirn",        "abbrev": "SH"},
    {"key": "hypothalamus", "name": "Hypothalamus",     "abbrev": "HYT"},
    {"key": "hypophyse",    "name": "Hypophyse",        "abbrev": "HY"},
    {"key": "auge",         "name": "Auge",             "abbrev": "AU"},
    {"key": "schilddruese", "name": "Schilddr\u00fcse", "abbrev": "SD"},
    {"key": "luftroehre",   "name": "Luftr\u00f6hre",   "abbrev": "LR"},
    {"key": "speiseroehre", "name": "Speiser\u00f6hre", "abbrev": "SR"},
    {"key": "lunge",        "name": "Lunge",            "abbrev": "LU"},
    {"key": "thymus",       "name": "Thymus",           "abbrev": "THY"},
    {"key": "herz",         "name": "Herz",             "abbrev": "HE"},
    {"key": "blut",         "name": "Blut",             "abbrev": "BL"},
    {"key": "pankreas",     "name": "Pankreas",         "abbrev": "PA"},
    {"key": "gallenblase",  "name": "Gallenblase",      "abbrev": "GB"},
    {"key": "leber",        "name": "Leber",            "abbrev": "LE"},
    {"key": "magen",        "name": "Magen",            "abbrev": "MA"},
    {"key": "milz",         "name": "Milz",             "abbrev": "MI"},
    {"key": "duenndarm",    "name": "D\u00fcnndarm",    "abbrev": "D\u00dcD"},
    {"key": "dickdarm",     "name": "Dickdarm",         "abbrev": "DID"},
    {"key": "niere",        "name": "Niere",            "abbrev": "NI"},
    {"key": "nebenniere",   "name": "Nebenniere",       "abbrev": "NN"},
    {"key": "hoden",        "name": "Hoden",            "abbrev": "HO"},
    {"key": "uterus",       "name": "Uterus",           "abbrev": "UT"},
    {"key": "ovar",         "name": "Ovar",             "abbrev": "OV"},
    {"key": "knochenmark",  "name": "Knochenmark",      "abbrev": "KM"},
    {"key": "muskel",       "name": "Muskel",           "abbrev": "MU"},
    {"key": "haut",         "name": "Haut",             "abbrev": "HA"},
    {"key": "fett",         "name": "Fett",             "abbrev": "FE"},
    {"key": "blase",        "name": "Blase",            "abbrev": "BLA"},
    {"key": "harnleiter",   "name": "Harnleiter",       "abbrev": "HL"},
    {"key": "samenblase",   "name": "Samenblase",       "abbrev": "SB"},
    {"key": "lippe",        "name": "Lippe",            "abbrev": "LI"},
    {"key": "zunge",        "name": "Zunge",            "abbrev": "ZU"},
    {"key": "nebenhoden",   "name": "Nebenhoden",       "abbrev": "NH"},
    {"key": "penis",        "name": "Penis",            "abbrev": "PE"},
    {"key": "plazenta",     "name": "Plazenta",         "abbrev": "PLA"},
    {"key": "sternum",      "name": "Sternum",          "abbrev": "STR"},
    {"key": "kniegelenk",   "name": "Kniegelenk",       "abbrev": "KG"},
    {"key": "nabelschnur",  "name": "Nabelschnur",      "abbrev": "NSch"},
    {"key": "rueckenmark",  "name": "R\u00fcckenmark",  "abbrev": "RM"},
    {"key": "vagina",       "name": "Vagina",           "abbrev": "VA"},
    {"key": "bindegewebe",  "name": "Bindegewebe",      "abbrev": "BG"},
    {"key": "brustdruese",  "name": "Brustdr\u00fcse",  "abbrev": "BD"},
    {"key": "eileiter",     "name": "Eileiter",         "abbrev": "EI"},
    {"key": "fussgelenk",   "name": "Fu\u00dfgelenk",   "abbrev": "FG"},
    {"key": "handgelenk",   "name": "Handgelenk",       "abbrev": "HG"},
    {"key": "zwerchfell",   "name": "Zwerchfell",       "abbrev": "ZF"},
    {"key": "nerv",         "name": "Nerv",             "abbrev": "NV"},
    {"key": "ovidukt",      "name": "Ovidukt",          "abbrev": "OVD"},
    {"key": "knorpel",      "name": "Knorpel",          "abbrev": "KN"},
    {"key": "netzhaut",     "name": "Netzhaut",         "abbrev": "NE"},
    {"key": "prostata",     "name": "Prostata",         "abbrev": "PR"},
    {"key": "mandeln",      "name": "Mandeln",          "abbrev": "MAN"},
    {"key": "parotis",      "name": "Parotis",          "abbrev": "PAR"},
    {"key": "cornea",       "name": "Cornea",           "abbrev": "CO"},
    {"key": "brustwarze",   "name": "Brustwarze",       "abbrev": "BW"},
]

# Other sample types — from SampleTrack_tissues_and_abbreviations.xlsx Tab 2
OTHER_TYPES: List[Dict[str, str]] = [
    {"key": "tumor",    "name": "Tumor",       "abbrev": "TU"},
    {"key": "vollblut", "name": "Vollblut",    "abbrev": "VB"},
    {"key": "serum",    "name": "Serum",        "abbrev": "SE"},
    {"key": "plasma",   "name": "Plasma",       "abbrev": "PL"},
    {"key": "dna",      "name": "DNA",          "abbrev": "DNA"},
    {"key": "rna",      "name": "RNA",          "abbrev": "RNA"},
    {"key": "urin",     "name": "Urin",         "abbrev": "UR"},
    {"key": "kot",      "name": "Kot",          "abbrev": "KO"},
]

# ---------------------------------------------------------------------------
# Localised name / abbreviation lookup
# ---------------------------------------------------------------------------

_LOOKUPS: Dict[str, Any] = {}


def _load_lookups(lang: str = "en") -> None:
    """Load samples_<lang>.json from the plugin folder into _LOOKUPS.

    Falls back to samples_en.json, then to empty dict (English hardcoded
    values in ORGANS / OTHER_TYPES are used as ultimate fallback).
    """
    global _LOOKUPS
    path = PLUGIN_DIR / f"samples_{lang}.json"
    if not path.exists():
        path = PLUGIN_DIR / "samples_en.json"
    try:
        with open(path, encoding="utf-8") as fh:
            _LOOKUPS = json.load(fh)
        logger.debug("Sample_Track lookups loaded from %s", path)
    except Exception as exc:
        logger.warning("Sample_Track: could not load lookups from %s: %s", path, exc)
        _LOOKUPS = {}


def _organ_display(key: str) -> Tuple[str, str]:
    """Return (display_name, abbreviation) for an organ key.

    Lookup order: loaded JSON file → English hardcoded ORGANS fallback.
    Internal storage always uses the English key, not the display name.
    """
    organs = _LOOKUPS.get("organs", {})
    name: Optional[str] = organs.get("names", {}).get(key)
    abbrev: Optional[str] = organs.get("abbreviations", {}).get(key)
    if name is None or abbrev is None:
        for o in ORGANS:
            if o["key"] == key:
                name = name if name is not None else o["name"]
                abbrev = abbrev if abbrev is not None else o["abbrev"]
                break
        else:
            name = name or key
            abbrev = abbrev or key[:3]
    return name, abbrev


def _type_display(key: str) -> Tuple[str, str]:
    """Return (display_name, abbreviation) for a sample-type key.

    Lookup order: loaded JSON file → English hardcoded OTHER_TYPES fallback.
    Internal storage always uses the English key.
    """
    other = _LOOKUPS.get("other_types", {})
    name: Optional[str] = other.get("names", {}).get(key)
    abbrev: Optional[str] = other.get("abbreviations", {}).get(key)
    if name is None or abbrev is None:
        for t in OTHER_TYPES:
            if t["key"] == key:
                name = name if name is not None else t["name"]
                abbrev = abbrev if abbrev is not None else t.get("abbrev", t["name"][:3])
                break
        else:
            name = name or key
            abbrev = abbrev or key[:3]
    return name, abbrev


def _msg(messages: Dict, key: str, fallback: str) -> str:
    return messages.get(key, fallback) if isinstance(messages, dict) else fallback


def _animal_records(app) -> Dict[str, Dict[str, Any]]:
    animals = getattr(app, 'animals', {})
    archived = getattr(app, 'archived', {})
    merged: Dict[str, Dict[str, Any]] = {}
    if isinstance(animals, dict):
        merged.update(animals)
    if isinstance(archived, dict):
        merged.update(archived)
    return merged


def _resolve_animal_input(app, value: str) -> tuple[str, Dict[str, Any], str]:
    """Resolve user-entered animal text to an IPID key.

    Returns (key, record, error_code). error_code is "" on success,
    "missing" when the text cannot be resolved, and "ambiguous" when a short
    display name matches more than one animal.
    """
    text = str(value or "").strip()
    if not text:
        return "", {}, "missing"

    key, record, status = resolve_animal_reference_text(text, _animal_records(app))
    if status == "resolved":
        return key, dict(record), ""
    if status == "ambiguous":
        return "", {}, "ambiguous"

    if split_animal_identity_key(text) is not None:
        return text, {}, ""
    return "", {}, "missing"


def _display_animal_name(key: str, record: Optional[Dict[str, Any]] = None) -> str:
    return animal_base_name(key, record or {})


def _animal_choice_label(key: str, record: Dict[str, Any]) -> str:
    name = animal_base_name(key, record)
    animal_id = str(record.get("id") or key).strip()
    return f"{name} ({animal_id})"


def _ambiguous_animal_choices(app, value: str) -> Dict[str, str]:
    """Return Name (ID) labels mapped to stable IPID keys for a duplicate name."""
    folded = animal_base_name(value).casefold()
    choices: Dict[str, str] = {}
    for key, record in _animal_records(app).items():
        if animal_base_name(key, record).casefold() != folded:
            continue
        label = _animal_choice_label(key, record)
        if label in choices:
            label = f"{label} — {key}"
        choices[label] = key
    return dict(sorted(choices.items(), key=lambda item: item[0].casefold()))


def _choose_ambiguous_animal(parent, app, value: str, messages: Dict) -> str:
    choices = _ambiguous_animal_choices(app, value)
    if not choices:
        return ""
    label, accepted = QInputDialog.getItem(
        parent,
        _msg(messages, "sample_track.animal_choice.title", "Select animal"),
        _msg(
            messages,
            "sample_track.animal_choice.prompt",
            "More than one animal has this name. Select Name (ID):",
        ),
        list(choices),
        0,
        False,
    )
    return choices.get(label, "") if accepted else ""


def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s.strip(), DATE_FORMAT).date()
    except Exception:
        return None


def _str2bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "1", "yes")


def _is_organ_row_meaningful(data: Dict) -> bool:
    """Return True if a Tab 1 row has meaningful content."""
    # Has animal name OR at least one organ checked
    if data.get("animal_name", "").strip():
        return True
    return any(_str2bool(data.get(f"{o['key']}_checked", False)) for o in ORGANS)


def _is_other_row_meaningful(data: Dict) -> bool:
    """Return True if a Tab 2 row has meaningful content."""
    # Has animal name, sample number, OR at least one sample type checked
    if data.get("animal_name", "").strip():
        return True
    if data.get("sample_number", "").strip():
        return True
    return any(_str2bool(data.get(f"{t['key']}_checked", False)) for t in OTHER_TYPES)


# ---------------------------------------------------------------------------
# Backend persistence layer
# ---------------------------------------------------------------------------

class JsonStore:
    """Backend-owned sample rows.

    The path argument is retained only for source compatibility with older
    callers; it is deliberately ignored. Sample rows are backend-only.
    """

    def __init__(self, path: Path, backend=None, record_id: str = ""):
        if backend is None or not record_id:
            raise RuntimeError("Sample Track requires a backend record store.")
        self._store = BackendJsonStore(backend, "samples", record_id)

    def read(self) -> List[Dict[str, Any]]:
        value = self._store.load([])
        return value if isinstance(value, list) else []

    def write(self, rows: List[Dict[str, Any]]) -> None:
        try:
            self._store.save(rows)
        except Exception as exc:
            logger.error("Sample backend write failed: %s", exc)


# ---------------------------------------------------------------------------
# Rotated label widget
# ---------------------------------------------------------------------------

class RotatedLabel(QLabel):
    """Label with rotated text (90° counter‑clockwise). Fixed height, width from text length."""
    _FIXED_H = _ROTATED_LABEL_FIXED_H  # px — fits abbreviations up to ~8 chars incl. count suffix

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        fm_w = self.fontMetrics().height() + 6
        self.setFixedSize(fm_w, self._FIXED_H)

    def set_text(self, text: str):
        self.setText(text)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.rotate(-90)
        painter.translate(-self.height() / 2, -self.width() / 2)
        painter.drawText(0, 0, self.height(), self.width(),
                         Qt.AlignmentFlag.AlignCenter, self.text())
        painter.end()


class FilterToggleLabel(QWidget):
    """Clickable rotated header label used as a boolean filter toggle."""

    def __init__(self, text: str, tooltip: str = "", parent=None):
        super().__init__(parent)
        self._checked = False
        self._callback_toggled = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        self._lbl = RotatedLabel(text)
        if tooltip:
            self._lbl.setToolTip(tooltip)
            self.setToolTip(tooltip)
        layout.addWidget(self._lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.setFixedWidth(36)
        self._update_style()

    def set_checked(self, value: bool) -> None:
        self._checked = bool(value)
        self._update_style()

    def is_checked(self) -> bool:
        return self._checked

    def _update_style(self) -> None:
        if self._checked:
            self._lbl.setStyleSheet("color: black; font-weight: bold;")
        else:
            self._lbl.setStyleSheet("color: #888; font-weight: normal;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._checked = not self._checked
            self._update_style()
            if callable(self._callback_toggled):
                self._callback_toggled()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# EditLocationDialog
# ---------------------------------------------------------------------------

class EditLocationDialog(QDialog):
    def __init__(self, parent=None, messages=None, unit="", storage=""):
        super().__init__(parent)
        m = messages or {}
        self.setWindowTitle(_msg(m, "sample_track.row.action.edit_location", "Edit Location"))
        layout = QVBoxLayout(self)
        form = QHBoxLayout()
        form.addWidget(QLabel(_msg(m, "sample_track.sample_dialog.field.unit", "Unit:")))
        self.unit_le = QLineEdit(unit)
        form.addWidget(self.unit_le)
        layout.addLayout(form)
        form2 = QHBoxLayout()
        form2.addWidget(QLabel(_msg(m, "sample_track.sample_dialog.field.storage", "Storage Space:")))
        self.storage_le = QLineEdit(storage)
        form2.addWidget(self.storage_le)
        layout.addLayout(form2)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    def get_values(self) -> Tuple[str, str]:
        return self.unit_le.text().strip(), self.storage_le.text().strip()


# ---------------------------------------------------------------------------
# SampleDialog (Tab 1 organ)
# ---------------------------------------------------------------------------

class SampleDialog(QDialog):
    def __init__(self, parent=None, messages=None, organ_name="",
                 num_aliquots=1, aliquot_locations=None, comment="", warning=False):
        super().__init__(parent)
        m = messages or {}
        self._messages = m
        self.setWindowTitle(organ_name)
        self._result_action = None
        self._orig_aliquots = aliquot_locations or [{"unit": "", "storage": ""}]
        layout = QVBoxLayout(self)

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel(_msg(m, "sample_track.sample_dialog.field.num_aliquots", "Number of Aliquots:")))
        self.spin = QSpinBox()
        self.spin.setMinimum(0)
        self.spin.setValue(max(0, num_aliquots))
        spin_row.addWidget(self.spin)
        spin_row.addStretch()
        layout.addLayout(spin_row)

        self._aliquot_container = QWidget()
        self._aliquot_layout = QVBoxLayout(self._aliquot_container)
        self._aliquot_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._aliquot_container)
        self._aliquot_widgets: List[AliquotLocationWidget] = []

        locs = aliquot_locations or [{"unit": "", "storage": ""}]
        self._rebuild_aliquots(max(0, num_aliquots), locs)
        self.spin.valueChanged.connect(self._on_spin_changed)

        layout.addWidget(QLabel(_msg(m, "sample_track.sample_dialog.field.comment", "Comment:")))
        self.comment_te = QTextEdit(comment)
        self.comment_te.setMaximumHeight(80)
        layout.addWidget(self.comment_te)

        self.warning_cb = QCheckBox(_msg(m, "sample_track.sample_dialog.field.warning", "Warning"))
        self.warning_cb.setChecked(warning)
        layout.addWidget(self.warning_cb)

        btn_row = QHBoxLayout()
        self.delete_btn = QPushButton(_msg(m, "sample_track.sample_dialog.button.delete", "Delete"))
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.delete_btn)
        self.ok_btn = QPushButton(_msg(m, "sample_track.sample_dialog.button.ok", "OK"))
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

    def _rebuild_aliquots(self, n: int, existing_locs: list):
        for w in self._aliquot_widgets:
            self._aliquot_layout.removeWidget(w)
            w.deleteLater()
        self._aliquot_widgets.clear()
        for i in range(n):
            loc = existing_locs[i] if i < len(existing_locs) else {"unit": "", "storage": ""}
            w = AliquotLocationWidget(i + 1, self._messages, loc.get("unit", ""), loc.get("storage", ""))
            self._aliquot_layout.addWidget(w)
            self._aliquot_widgets.append(w)

    def _on_spin_changed(self, n: int):
        cur_locs = [w.get_values() for w in self._aliquot_widgets]
        self._rebuild_aliquots(n, cur_locs)

    def _on_ok(self):
        self._result_action = "ok"
        self.accept()

    def _on_delete(self):
        self._result_action = "delete"
        self.accept()

    def values(self):
        locs = [w.get_values() for w in self._aliquot_widgets]
        return (len(self._aliquot_widgets), locs,
                self.comment_te.toPlainText().strip(),
                self.warning_cb.isChecked())

    @property
    def action(self):
        return self._result_action


# ---------------------------------------------------------------------------
# AliquotLocationWidget
# ---------------------------------------------------------------------------

class AliquotLocationWidget(QGroupBox):
    def __init__(self, n: int, messages=None, unit="", storage="", parent=None):
        m = messages or {}
        title = _msg(m, "sample_track.sample_dialog.section.aliquot", "Aliquot {n}").replace("{n}", str(n))
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        row1 = QHBoxLayout()
        row1.addWidget(QLabel(_msg(m, "sample_track.sample_dialog.field.unit", "Unit:")))
        self.unit_le = QLineEdit(unit)
        row1.addWidget(self.unit_le)
        layout.addLayout(row1)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel(_msg(m, "sample_track.sample_dialog.field.storage", "Storage Space:")))
        self.storage_le = QLineEdit(storage)
        row2.addWidget(self.storage_le)
        layout.addLayout(row2)

    def get_values(self) -> Dict[str, str]:
        return {"unit": self.unit_le.text().strip(), "storage": self.storage_le.text().strip()}


# ---------------------------------------------------------------------------
# OtherSampleDialog (Tab 2)
# ---------------------------------------------------------------------------

class OtherSampleDialog(QDialog):
    def __init__(self, parent=None, messages=None, type_name="",
                 num_aliquots=1, aliquot_locations=None, comment="", warning=False):
        super().__init__(parent)
        m = messages or {}
        self.setWindowTitle(type_name)
        self._messages = m
        self._result_action = None
        self._orig_aliquots = aliquot_locations or [{"unit": "", "storage": ""}]
        layout = QVBoxLayout(self)

        spin_row = QHBoxLayout()
        spin_row.addWidget(QLabel(_msg(m, "sample_track.sample_dialog.field.num_aliquots", "Number of Aliquots:")))
        self.spin = QSpinBox()
        self.spin.setMinimum(0)
        self.spin.setValue(max(0, num_aliquots))
        spin_row.addWidget(self.spin)
        layout.addLayout(spin_row)

        self._aliquot_container = QWidget()
        self._aliquot_layout = QVBoxLayout(self._aliquot_container)
        self._aliquot_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._aliquot_container)
        self._aliquot_widgets: List[AliquotLocationWidget] = []

        locs = aliquot_locations or [{"unit": "", "storage": ""}]
        self._rebuild_aliquots(max(0, num_aliquots), locs)
        self.spin.valueChanged.connect(self._on_spin_changed)

        layout.addWidget(QLabel(_msg(m, "sample_track.sample_dialog.field.comment", "Comment:")))
        self.comment_te = QTextEdit(comment)
        self.comment_te.setMaximumHeight(80)
        layout.addWidget(self.comment_te)

        self.warning_cb = QCheckBox(_msg(m, "sample_track.sample_dialog.field.warning", "Warning"))
        self.warning_cb.setChecked(warning)
        layout.addWidget(self.warning_cb)

        btn_row = QHBoxLayout()
        self.delete_btn = QPushButton(_msg(m, "sample_track.sample_dialog.button.delete", "Delete"))
        self.delete_btn.clicked.connect(self._on_delete)
        btn_row.addWidget(self.delete_btn)
        self.ok_btn = QPushButton(_msg(m, "sample_track.sample_dialog.button.ok", "OK"))
        self.ok_btn.setDefault(True)
        self.ok_btn.clicked.connect(self._on_ok)
        btn_row.addWidget(self.ok_btn)
        layout.addLayout(btn_row)

    def _rebuild_aliquots(self, n: int, existing_locs: list):
        for w in self._aliquot_widgets:
            self._aliquot_layout.removeWidget(w)
            w.deleteLater()
        self._aliquot_widgets.clear()
        for i in range(n):
            loc = existing_locs[i] if i < len(existing_locs) else {"unit": "", "storage": ""}
            w = AliquotLocationWidget(i + 1, self._messages, loc.get("unit", ""), loc.get("storage", ""))
            self._aliquot_layout.addWidget(w)
            self._aliquot_widgets.append(w)

    def _on_spin_changed(self, n: int):
        cur_locs = [w.get_values() for w in self._aliquot_widgets]
        self._rebuild_aliquots(n, cur_locs)

    def _on_ok(self):
        self._result_action = "ok"
        self.accept()

    def _on_delete(self):
        self._result_action = "delete"
        self.accept()

    def values(self):
        locs = [w.get_values() for w in self._aliquot_widgets]
        return (len(self._aliquot_widgets), locs,
                self.comment_te.toPlainText().strip(),
                self.warning_cb.isChecked())

    @property
    def action(self):
        return self._result_action


# ---------------------------------------------------------------------------
# OrganLabel  (sample rows — no checkbox, styled abbreviation label only)
# ---------------------------------------------------------------------------

class OrganLabel(QWidget):
    """Compact display for sample rows: rotated abbreviation label, no checkbox.

    Style: grey = unchecked; bold black = checked; bold red = checked+warning.
    Clickable: first press triggers _callback_checked; subsequent presses open
    dialog via _callback_clicked_checked.  API is identical to OrganCheckbox so
    _build_organ_section/_build_type_section work unchanged.
    """

    def __init__(self, organ: Dict, parent=None):
        super().__init__(parent)
        self._organ = organ
        self._checked = False
        self._warning = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(0)
        self._lbl = RotatedLabel(organ["abbrev"])
        self._lbl.setToolTip(organ["name"])
        layout.addWidget(self._lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.setFixedWidth(36)
        self._callback_checked = None
        self._callback_clicked_checked = None
        self._update_style()

    def set_count(self, n: int):
        if n > 1:
            self._lbl.setToolTip(f"{self._organ['name']} \u2014 {n} aliquot(s)")
        else:
            self._lbl.setToolTip(self._organ["name"])

    def set_checked(self, v: bool):
        self._checked = v
        self._update_style()

    def set_warning(self, v: bool):
        self._warning = v
        self._update_style()

    def is_checked(self) -> bool:
        return self._checked

    def _update_style(self):
        if self._checked and self._warning:
            self._lbl.setStyleSheet("color: red; font-weight: bold;")
        elif self._checked:
            self._lbl.setStyleSheet("color: black; font-weight: bold;")
        else:
            self._lbl.setStyleSheet("color: #bbb; font-weight: normal;")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._checked:
                self._checked = True
                self._update_style()
                if self._callback_checked:
                    self._callback_checked()
            else:
                if self._callback_clicked_checked:
                    self._callback_clicked_checked()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# OrganCheckbox
# ---------------------------------------------------------------------------

class OrganCheckbox(QWidget):
    def __init__(self, organ: Dict, parent=None):
        super().__init__(parent)
        self._organ = organ
        self._checked = False
        self._warning = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(1)
        self._lbl = RotatedLabel(organ["abbrev"])
        self._lbl.setToolTip(organ["name"])
        layout.addWidget(self._lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._chk = QCheckBox()
        self._chk.setToolTip(organ["name"])
        self._chk.stateChanged.connect(self._on_state_changed)
        layout.addWidget(self._chk, alignment=Qt.AlignmentFlag.AlignHCenter)
        self._count_lbl = QLabel("")
        self._count_lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self._count_lbl.setStyleSheet("font-size: 9px; color: #444;")
        self._count_lbl.setFixedWidth(34)
        self._count_lbl.hide()
        layout.addWidget(self._count_lbl, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.setFixedWidth(36)
        self._callback_checked = None
        self._callback_clicked_checked = None

    def set_count(self, n: int):
        """Show '(n)' below the checkbox only when n > 1; hide for n <= 1."""
        if n > 1:
            self._count_lbl.setText(f"({n})")
            self._count_lbl.show()
            tt = f"{self._organ['name']} \u2014 {n} aliquot(s)"
        else:
            self._count_lbl.setText("")
            self._count_lbl.hide()
            tt = self._organ["name"]
        self._lbl.setToolTip(tt)
        self._chk.setToolTip(tt)

    def _on_state_changed(self, state):
        pass  # handled by mousePressEvent override on checkbox

    def set_checked(self, v: bool):
        self._checked = v
        self._chk.blockSignals(True)
        self._chk.setChecked(v)
        self._chk.blockSignals(False)
        self._update_style()

    def set_warning(self, v: bool):
        self._warning = v
        self._update_style()

    def is_checked(self) -> bool:
        return self._checked

    def _update_style(self):
        if self._warning and self._checked:
            self._chk.setStyleSheet("QCheckBox::indicator { background-color: #ff6666; }")
        else:
            self._chk.setStyleSheet("")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self._checked:
                # Simply check it
                self._checked = True
                self._chk.blockSignals(True)
                self._chk.setChecked(True)
                self._chk.blockSignals(False)
                self._update_style()
                if self._callback_checked:
                    self._callback_checked()
            else:
                # Open dialog
                if self._callback_clicked_checked:
                    self._callback_clicked_checked()
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# SampleRowWidget (Tab 1)
# ---------------------------------------------------------------------------

class SampleRowWidget(QFrame):
    def __init__(self, data: Dict, app, messages: Dict, organ_store: JsonStore,
                 all_rows_ref: list, is_saved: bool = False, parent=None):
        super().__init__(parent)
        self._app = app
        self._messages = messages
        self._store = organ_store
        self._all_rows = all_rows_ref
        self._data = data          # direct reference — NOT a copy
        self._saved = is_saved    # explicit saved state, not inferred from data
        self._persisted_key: Optional[str] = data.get("animal_name", "") if is_saved else None
        self._selected = False
        self._delete_callback = None
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(4, 2, 4, 2)
        self._main_layout.setSpacing(2)

        # Row 1: identity + unit column + vertical divider + organ checkboxes
        self._row1 = QWidget()
        self._row1_layout = QHBoxLayout(self._row1)
        self._row1_layout.setContentsMargins(0, 0, 0, 0)
        self._row1_layout.setSpacing(6)

        # Row 2: birth/death dates + save button
        self._row2 = QWidget()
        self._row2_layout = QHBoxLayout(self._row2)
        self._row2_layout.setContentsMargins(0, 0, 0, 0)
        self._row2_layout.setSpacing(6)

        self._main_layout.addWidget(self._row1)
        self._main_layout.addWidget(self._row2)

        self._build_identity_section()
        self._build_organ_section()
        self._apply_saved_state()
        
        # Sync height after layout is complete
        _sync_widget_height(self)

    # ---- identity section ----

    def _build_identity_section(self):
        m = self._messages

        # --- Row 1: name / species / ID / sex / unit-column ---
        self._name_le = QLineEdit(self._data.get("animal_name", ""))
        self._name_le.setPlaceholderText("Name")
        self._name_le.setFixedWidth(_W1_NAME)
        self._species_le = QLineEdit(self._data.get("species", ""))
        self._species_le.setPlaceholderText("Species")
        self._species_le.setFixedWidth(_W1_SPECIES)
        self._id_le = QLineEdit(self._data.get("id", ""))
        self._id_le.setPlaceholderText("ID")
        self._id_le.setFixedWidth(_W1_ID)
        self._sex_cb = QComboBox()
        self._sex_cb.addItems(["Male", "Female", "Undefined"])
        self._sex_cb.setFixedWidth(_W1_SEX)
        _saved_sex = self._data.get("sex", "")
        _cur_sex = _saved_sex if _saved_sex in ("Male", "Female", "Undefined") else "Undefined"
        self._sex_cb.setCurrentText(_cur_sex)
        self._sex_cb.setStyleSheet("QComboBox { color: #aaa; }" if _cur_sex == "Undefined" else "QComboBox { color: black; }")
        self._sex_cb.currentTextChanged.connect(
            lambda t, cb=self._sex_cb: cb.setStyleSheet(
                "QComboBox { color: #aaa; }" if t == "Undefined" else "QComboBox { color: black; }"))
        # Unit placeholder — same width as the header unit column
        self._unit_spacer = QWidget()
        self._unit_spacer.setFixedWidth(_W1_UNIT)

        # Read-only labels for row 1 (visible when saved)
        self._name_lbl = QLabel()
        self._name_lbl.setFixedWidth(_W1_NAME)
        self._name_lbl.setStyleSheet("font-weight: bold;")
        self._name_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._name_lbl.mousePressEvent = lambda e: self._show_context_menu()
        self._species_lbl = QLabel()
        self._species_lbl.setFixedWidth(_W1_SPECIES)
        self._id_lbl = QLabel()
        self._id_lbl.setFixedWidth(_W1_ID)
        self._sex_lbl = QLabel()
        self._sex_lbl.setFixedWidth(_W1_SEX)
        self._unit_lbl = QLabel()          # shows first checked organ's unit
        self._unit_lbl.setFixedWidth(_W1_UNIT)

        for w in [self._name_le, self._species_le, self._id_le,
                  self._sex_cb, self._unit_spacer,
                  self._name_lbl, self._species_lbl, self._id_lbl,
                  self._sex_lbl, self._unit_lbl]:
            self._row1_layout.addWidget(w, alignment=Qt.AlignmentFlag.AlignTop)

        # Vertical divider between identity and organ checkboxes
        _div = QFrame()
        _div.setFrameShape(QFrame.Shape.VLine)
        _div.setFrameShadow(QFrame.Shadow.Sunken)
        _div.setFixedWidth(4)
        self._row1_layout.addWidget(_div)

        # --- Row 2: birth date / death date / save button ---
        _birth_box = QWidget()
        _birth_box.setFixedWidth(_W1_DATE_GRP)
        _bh = QHBoxLayout(_birth_box)
        _bh.setContentsMargins(0, 0, 0, 0)
        _bh.setSpacing(2)
        _bh.addWidget(QLabel(_msg(m, "sample_track.field.birth", "Birth:")))
        self._birth_le = QLineEdit(self._data.get("birth_date", ""))
        self._birth_le.setPlaceholderText("DD.MM.YYYY")
        _bh.addWidget(self._birth_le)
        self._birth_box = _birth_box

        _death_box = QWidget()
        _death_box.setFixedWidth(_W1_DATE_GRP)
        _dh = QHBoxLayout(_death_box)
        _dh.setContentsMargins(0, 0, 0, 0)
        _dh.setSpacing(2)
        _dh.addWidget(QLabel(_msg(m, "sample_track.field.death", "Death:")))
        self._death_le = QLineEdit(self._data.get("death_date", ""))
        self._death_le.setPlaceholderText("DD.MM.YYYY")
        _dh.addWidget(self._death_le)
        self._death_box = _death_box

        self._birth_lbl = QLabel()
        self._birth_lbl.setFixedWidth(_W1_DATE_GRP)
        self._death_lbl = QLabel()
        self._death_lbl.setFixedWidth(_W1_DATE_GRP)

        self._save_btn = QPushButton(_msg(m, "sample_track.button.save", "Save"))
        self._save_btn.setFixedWidth(_W1_SAVE)
        self._save_btn.clicked.connect(self._on_save)

        self._cancel_btn = QPushButton(_msg(m, "sample_track.button.delete", "Delete"))
        self._cancel_btn.setFixedWidth(_W1_SAVE)
        self._cancel_btn.clicked.connect(self._on_cancel)

        for w in [self._birth_box, self._death_box, self._cancel_btn, self._save_btn,
                  self._birth_lbl, self._death_lbl]:
            self._row2_layout.addWidget(w)
        self._row2_layout.addStretch()

        self._setup_autocomplete()

    def _setup_autocomplete(self):
        names = []
        self._animal_display_to_key = {}
        for key, rec in _animal_records(self._app).items():
            display = animal_identity_label(key, rec)
            names.append(display)
            self._animal_display_to_key[display] = key
        model = QStringListModel(sorted(set(names)))
        comp = QCompleter(model, self._name_le)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.activated.connect(self._on_autocomplete)
        self._name_le.setCompleter(comp)

    def _on_autocomplete(self, display_name: str):
        records = _animal_records(self._app)
        rec = None
        key_used = getattr(self, '_animal_display_to_key', {}).get(display_name, display_name)
        for key, r in records.items():
            if key == key_used:
                rec = r
                break
        if rec is None:
            return
        # Just populate fields - duplicate check will happen on Save
        self._name_le.setText(key_used)
        self._name_le.setToolTip(animal_identity_label(key_used, rec))
        self._species_le.setText(rec.get('species', ''))
        self._id_le.setText(rec.get('id', ''))
        _sv = _record_sex_value(rec)
        self._sex_cb.setCurrentText(_sv if _sv in ('Male', 'Female', 'Undefined') else 'Undefined')
        self._birth_le.setText(rec.get('birth_date', ''))
        self._death_le.setText(rec.get('death_date', ''))

    def _on_save(self):
        raw_name = self._name_le.text().strip()
        name, rec, error_code = _resolve_animal_input(self._app, raw_name)
        if error_code == "ambiguous":
            name = _choose_ambiguous_animal(
                self, self._app, raw_name, self._messages)
            if name:
                rec = dict(_animal_records(self._app).get(name, {}))
                error_code = ""
        if error_code:
            message = (
                "This animal name is ambiguous. Please select the full IPID from autocomplete."
                if error_code == "ambiguous"
                else "Please select an existing animal or enter a valid full IPID."
            )
            QMessageBox.warning(self, "Animal identity", message)
            return
        existing = [r.get("animal_name", "") for r in self._all_rows if r is not self._data]
        if name in existing:
            QMessageBox.warning(
                self, "Duplicate",
                _msg(self._messages, "sample_track.error.duplicate_animal",
                     "An entry for this animal already exists."))
            return  # Keep row open for editing
        self._data["animal_name"] = name
        self._data["species"] = _species_abbreviation(
            (rec.get("species") if rec else "") or self._species_le.text().strip()
        )
        self._data["id"] = (rec.get("id") if rec else "") or self._id_le.text().strip()
        sex_value = _record_sex_value(rec) or self._sex_cb.currentText()
        self._data["sex"] = sex_value if sex_value in ("Male", "Female", "Undefined") else self._sex_cb.currentText()
        self._data["birth_date"] = (rec.get("birth_date") if rec else "") or self._birth_le.text().strip()
        self._data["death_date"] = (rec.get("death_date") if rec else "") or self._death_le.text().strip()
        self._saved = True
        self._apply_saved_state()
        self._save_to_store()
        # Audit
        mt = getattr(self._app, 'master_track', None)
        if mt and hasattr(mt, 'audit'):
            mt.audit("sample_track.add_row", name, "tab=organ_samples")

    def _on_cancel(self):
        logger.debug("_on_cancel (delete) clicked for organ sample row")
        reply = QMessageBox.question(
            self,
            _msg(self._messages, "sample_track.confirm.delete_sample", "Delete entry?"),
            _msg(self._messages, "sample_track.confirm.delete_sample", "Delete this entry?"))
        logger.debug(f"Dialog reply: {reply}")
        if reply == 1024:
            logger.debug("User confirmed delete, calling delete_row()")
            self.delete_row()

    def delete_row(self):
        """Delete this row from storage and remove from UI."""
        try:
            name = self._data.get("animal_name", "")
            logger.debug(f"delete_row called for animal: {name}")
            # Always purge from storage by animal_name (whether saved or not)
            rows = self._store.read()
            original_count = len(rows)
            rows = [r for r in rows if r.get("animal_name", "") != name]
            if len(rows) < original_count:
                logger.debug(f"Purging {original_count - len(rows)} rows from JSON")
                self._store.write(rows)
            mt = getattr(self._app, 'master_track', None)
            if mt and hasattr(mt, 'audit'):
                mt.audit("sample_track.delete_row", name or "<unsaved>", "tab=organ_samples")
            self._persisted_key = None
            # Request UI removal
            self._request_delete()
        except Exception as e:
            logger.error(f"Error in delete_row: {e}", exc_info=True)
            raise

    def _request_delete(self):
        logger.debug(f"_request_delete called (SampleRowWidget), callback callable={callable(self._delete_callback)}")
        if callable(self._delete_callback):
            try:
                self._delete_callback(self)
            except Exception as e:
                logger.error(f"Error in delete callback: {e}", exc_info=True)
                raise
        else:
            logger.warning("No delete callback set, trying parent fallback")
            p = self.parent()
            if p and hasattr(p, '_remove_row'):
                p._remove_row(self)

    def _apply_saved_state(self):
        editable = not self._saved
        self._name_le.setVisible(editable)
        self._species_le.setVisible(editable)
        self._id_le.setVisible(editable)
        self._sex_cb.setVisible(editable)
        self._unit_spacer.setVisible(editable)
        self._birth_box.setVisible(editable)
        self._death_box.setVisible(editable)
        self._cancel_btn.setVisible(editable)
        self._save_btn.setVisible(editable)
        self._name_lbl.setVisible(self._saved)
        self._species_lbl.setVisible(self._saved)
        self._id_lbl.setVisible(self._saved)
        self._sex_lbl.setVisible(self._saved)
        self._unit_lbl.setVisible(self._saved)
        self._birth_lbl.setVisible(self._saved)
        self._death_lbl.setVisible(self._saved)
        if self._saved:
            animal_key = self._data.get("animal_name", "")
            self._name_lbl.setText(_display_animal_name(animal_key, _animal_records(self._app).get(animal_key)))
            self._name_lbl.setToolTip(animal_identity_label(animal_key, _animal_records(self._app).get(animal_key)))
            self._species_lbl.setText(self._data.get("species", ""))
            self._id_lbl.setText(self._data.get("id", ""))
            self._sex_lbl.setText(self._data.get("sex", ""))
            bd = self._data.get("birth_date", "")
            dd = self._data.get("death_date", "")
            self._birth_lbl.setText(
                f"{_msg(self._messages, 'sample_track.field.birth', 'Birth:')} {bd}" if bd else "")
            self._death_lbl.setText(
                f"{_msg(self._messages, 'sample_track.field.death', 'Death:')} {dd}" if dd else "")
            _units_seen: List[str] = []
            for chk in self._organ_checkboxes:
                if chk.is_checked():
                    k = chk._organ['key']
                    try:
                        locs = json.loads(str(self._data.get(f"{k}_aliquot_locations", "[]")))
                        for _loc in (locs if isinstance(locs, list) else []):
                            _u = _loc.get("unit", "").strip()
                            if _u and _u not in _units_seen:
                                _units_seen.append(_u)
                    except Exception:
                        pass
            self._unit_lbl.setText(", ".join(_units_seen))
        if self._saved:
            has_dates = bool(self._data.get("birth_date", "").strip()) or \
                        bool(self._data.get("death_date", "").strip())
            self._row2.setVisible(has_dates)
        else:
            self._row2.setVisible(True)
        self._row1.adjustSize()
        self._row2.adjustSize()
        self._main_layout.activate()
        _sync_widget_height(self)
        QTimer.singleShot(0, self._request_parent_height_refresh)

    def _save_to_store(self):
        rows = self._store.read()
        name = self._data.get("animal_name", "")
        lookup_name = self._persisted_key if self._persisted_key is not None else name
        found = False
        for i, r in enumerate(rows):
            if r.get("animal_name") == lookup_name:
                rows[i] = self._data
                found = True
                break
        if not found:
            rows.append(self._data)
        rows.sort(key=lambda r: r.get("animal_name", "").lower())
        self._store.write(rows)
        self._persisted_key = name if name else None

    def _show_context_menu(self):
        from PyQt6.QtWidgets import QDialog as _QDialog, QVBoxLayout as _VL, QPushButton as _PB
        dlg = _QDialog(self)
        dlg.setWindowTitle(self._data.get("animal_name", ""))
        vl = _VL(dlg)

        btn_edit = _PB(_msg(self._messages, "sample_track.row.action.edit_entry", "Edit Entry"))
        btn_loc  = _PB(_msg(self._messages, "sample_track.row.action.edit_location", "Edit Location"))
        btn_del  = _PB(_msg(self._messages, "sample_track.row.action.delete_all_samples", "Clear samples"))

        def _do_edit():
            dlg.accept()
            self._saved = False
            # Do NOT set self._data["saved"]=False — would corrupt persisted data
            self._apply_saved_state()

        def _do_loc():
            dlg.accept()
            # Gather current unit/storage from first checked organ
            unit = ""
            storage = ""
            for chk in self._organ_checkboxes:
                if chk.is_checked():
                    k = chk._organ["key"]
                    try:
                        locs = json.loads(str(self._data.get(f"{k}_aliquot_locations", "[]")))
                        if locs and isinstance(locs, list) and locs[0]:
                            unit = locs[0].get("unit", "")
                            storage = locs[0].get("storage", "")
                    except Exception:
                        pass
                    break
            ed = EditLocationDialog(self, self._messages, unit, storage)
            if ed.exec():
                u, s = ed.get_values()
                for chk in self._organ_checkboxes:
                    if chk.is_checked():
                        k = chk._organ["key"]
                        # Update every aliquot location entry
                        try:
                            locs = json.loads(str(self._data.get(f"{k}_aliquot_locations", "[]")))
                            if not isinstance(locs, list) or not locs:
                                locs = [{}]
                        except Exception:
                            locs = [{}]
                        locs = [{"unit": u, "storage": s} for _ in locs]
                        self._data[f"{k}_aliquot_locations"] = json.dumps(locs)
                self._apply_saved_state()
                self._save_to_store()
                mt = getattr(self._app, 'master_track', None)
                if mt and hasattr(mt, 'audit'):
                    mt.audit("sample_track.edit_location",
                             self._data.get("animal_name", ""), "tab=organ_samples")

        def _do_del():
            logger.info("_do_del (Clear all samples) clicked")
            reply = QMessageBox.question(
                self,
                _msg(self._messages, "sample_track.confirm.delete_all_samples", "Clear all samples?"),
                _msg(self._messages, "sample_track.confirm.delete_all_samples", "Clear all samples?"))
            logger.info(f"Dialog reply value: {int(reply)}, match: {int(reply) == 1024}")
            if int(reply) == 1024:
                logger.info("User confirmed, calling _clear_all_organs()")
                dlg.accept()
                self._clear_all_organs()
                mt = getattr(self._app, 'master_track', None)
                if mt and hasattr(mt, 'audit'):
                    mt.audit("sample_track.clear_row_samples", self._data.get("animal_name", ""), "tab=organ_samples")
            else:
                logger.info("User cancelled or dialog rejected")
                dlg.reject()

        btn_edit.clicked.connect(_do_edit)
        btn_loc.clicked.connect(_do_loc)
        btn_del.clicked.connect(_do_del)
        for b in [btn_edit, btn_loc, btn_del]:
            vl.addWidget(b)
        dlg.exec()

    # ---- organ checkboxes ----

    def _build_organ_section(self):
        self._organ_checkboxes: List[OrganLabel] = []
        _organ_container = QWidget()
        _organ_hl = QHBoxLayout(_organ_container)
        _organ_hl.setContentsMargins(0, 0, 0, 0)
        _organ_hl.setSpacing(0)
        for organ in ORGANS:
            disp_name, disp_abbrev = _organ_display(organ["key"])
            chk = OrganLabel({"key": organ["key"], "name": disp_name, "abbrev": disp_abbrev})
            k = organ["key"]
            is_chk = _str2bool(self._data.get(f"{k}_checked", False))
            chk.set_checked(is_chk)
            chk.set_warning(_str2bool(self._data.get(f"{k}_warning", False)))
            if is_chk:
                chk.set_count(int(self._data.get(f"{k}_num_aliquots", 1) or 1))
            chk._callback_checked = lambda k=k, c=chk: self._on_organ_first_check(k, c)
            chk._callback_clicked_checked = lambda k=k, c=chk: self._on_organ_dialog(k, c)
            _organ_hl.addWidget(chk)
            self._organ_checkboxes.append(chk)
        _organ_hl.addStretch()
        self._organ_scroll_area = QScrollArea()
        self._organ_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._organ_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._organ_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._organ_scroll_area.setWidgetResizable(False)
        self._organ_scroll_area.setWidget(_organ_container)
        _fit_scroll_area_to_content(self._organ_scroll_area, _organ_container)
        self._organ_scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._organ_scroll_area.wheelEvent = lambda e: e.ignore()
        _right_col = QWidget()
        _right_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _right_vl = QVBoxLayout(_right_col)
        _right_vl.setContentsMargins(0, 0, 0, 0)
        _right_vl.setSpacing(0)
        _right_vl.addWidget(self._organ_scroll_area)
        self._row1_layout.addWidget(_right_col, 1)

    def _on_organ_first_check(self, organ_key: str, chk: OrganLabel):
        self._data[f"{organ_key}_checked"] = True
        self._data.setdefault(f"{organ_key}_num_aliquots", 1)
        self._data[f"{organ_key}_aliquot_locations"] = self._data.get(
            f"{organ_key}_aliquot_locations") or json.dumps([{"unit": "", "storage": ""}])
        self._data[f"{organ_key}_comment"] = self._data.get(f"{organ_key}_comment", "")
        self._data[f"{organ_key}_warning"] = False
        chk.set_count(int(self._data[f"{organ_key}_num_aliquots"] or 1))

    def _on_organ_dialog(self, organ_key: str, chk: OrganLabel):
        disp_name, _ = _organ_display(organ_key)
        num_al = int(self._data.get(f"{organ_key}_num_aliquots", 1) or 1)
        try:
            locs = json.loads(str(self._data.get(f"{organ_key}_aliquot_locations", "[]")))
            if not isinstance(locs, list) or not locs:
                locs = [{"unit": "", "storage": ""}]
        except Exception:
            locs = [{"unit": "", "storage": ""}]
        dlg = SampleDialog(
            self, self._messages, disp_name,
            num_aliquots=num_al,
            aliquot_locations=locs,
            comment=str(self._data.get(f"{organ_key}_comment", "")),
            warning=_str2bool(self._data.get(f"{organ_key}_warning", False)),
        )
        if dlg.exec():
            if dlg.action == "delete":
                reply = QMessageBox.question(
                    self,
                    _msg(self._messages, "sample_track.confirm.delete_sample_entry",
                         "Delete this sample entry?"),
                    _msg(self._messages, "sample_track.confirm.delete_sample_entry",
                         "Delete this sample entry?"))
                if reply == 1024:
                    self._data[f"{organ_key}_checked"] = False
                    self._data[f"{organ_key}_num_aliquots"] = 0
                    self._data[f"{organ_key}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
                    self._data[f"{organ_key}_comment"] = ""
                    self._data[f"{organ_key}_warning"] = False
                    chk.set_checked(False)
                    chk.set_warning(False)
                    chk.set_count(0)
                    self._save_to_store()
                    self._check_all_unchecked()
            elif dlg.action == "ok":
                num_al2, locs2, comment2, warning2 = dlg.values()
                if num_al2 == 0:
                    # Treat as tissue entry deletion
                    self._data[f"{organ_key}_checked"] = False
                    self._data[f"{organ_key}_num_aliquots"] = 0
                    self._data[f"{organ_key}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
                    self._data[f"{organ_key}_comment"] = ""
                    self._data[f"{organ_key}_warning"] = False
                    chk.set_checked(False)
                    chk.set_warning(False)
                    chk.set_count(0)
                    self._save_to_store()
                    self._check_all_unchecked()
                else:
                    self._data[f"{organ_key}_num_aliquots"] = num_al2
                    self._data[f"{organ_key}_aliquot_locations"] = json.dumps(locs2)
                    self._data[f"{organ_key}_comment"] = comment2
                    self._data[f"{organ_key}_warning"] = warning2
                    chk.set_warning(warning2)
                    chk.set_count(num_al2)
                    self._apply_saved_state()
                    self._save_to_store()
                    all_units = [loc.get("unit", "").strip() for loc in locs2 if loc.get("unit", "").strip()]
                    if all_units:
                        mt = getattr(self._app, 'master_track', None)
                        if mt and hasattr(mt, 'audit'):
                            mt.audit("sample_track.assign_unit",
                                     self._data.get("animal_name", ""),
                                     f"Organ sample assigned to {', '.join(all_units)}")

    def _clear_all_organs(self):
        logger.info(f"_clear_all_organs called for animal: {self._data.get('animal_name', '')}")
        for o in ORGANS:
            k = o["key"]
            self._data[f"{k}_checked"] = False
            self._data[f"{k}_num_aliquots"] = 0
            self._data[f"{k}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
            self._data[f"{k}_comment"] = ""
            self._data[f"{k}_warning"] = False
            logger.info(f"  Set {k}_num_aliquots to 0")
        for chk in self._organ_checkboxes:
            chk.set_checked(False)
            chk.set_warning(False)
            chk.set_count(0)
        self._apply_saved_state()
        logger.info("  Calling _save_to_store()")
        self._save_to_store()
        logger.info("  _save_to_store() completed")
        self._request_parent_height_refresh()
        # Force immediate visual update - update geometry and repaint
        self.adjustSize()
        self.updateGeometry()
        self.repaint()
        self.update()
        # Force parent refresh
        p = self.parent()
        if p:
            p.repaint()
            p.update()

    def _check_all_unchecked(self):
        """All aliquots zeroed — row stays; user must delete manually via Delete button."""
        pass  # intentional no-op: row deletion is user-initiated only

    def sizeHint(self):
        self._row1.adjustSize()
        self._row2.adjustSize()
        margins = self._main_layout.contentsMargins()
        # Use not isHidden() like SampleListWidget: isVisible() is False during
        # visibility transitions even when not explicitly hidden, causing 1px width.
        rows = [r for r in (self._row1, self._row2) if not r.isHidden()]
        rows_for_width = rows if rows else [self._row1, self._row2]
        w = (margins.left() + margins.right()
             + max((r.sizeHint().width() for r in rows_for_width), default=0)
             + 2 * self.frameWidth())
        h = (margins.top() + margins.bottom()
             + sum(r.sizeHint().height() for r in rows)
             + self._main_layout.spacing() * max(len(rows) - 1, 0)
             + 2 * self.frameWidth())
        return QSize(max(w, 100), max(h, 1))

    def minimumSizeHint(self):
        return self.sizeHint()

    def _request_parent_height_refresh(self):
        p = self.parent()
        while p is not None:
            if hasattr(p, '_update_list_height'):
                p._update_list_height()
                return
            p = p.parent()

    def get_data(self) -> Dict:
        return self._data

    def get_animal_name(self) -> str:
        return self._data.get("animal_name", "")

    def set_selected(self, v: bool):
        self._selected = v
        self.setStyleSheet("background-color: #cce8ff;" if v else "")


# ---------------------------------------------------------------------------
# OtherSampleRowWidget (Tab 2)
# ---------------------------------------------------------------------------

class OtherSampleRowWidget(QFrame):
    def __init__(self, data: Dict, app, messages: Dict, other_store: JsonStore,
                 all_rows_ref: list, is_saved: bool = False, parent=None):
        super().__init__(parent)
        self._app = app
        self._messages = messages
        self._store = other_store
        self._all_rows = all_rows_ref
        self._data = data          # direct reference — NOT a copy
        self._saved = is_saved    # explicit saved state, not inferred from data
        self._persisted_key: Optional[Tuple[str, str]] = (
            data.get("animal_name", ""), data.get("sample_number", "")
        ) if is_saved else None
        self._selected = False
        self._delete_callback = None
        self.setFrameShape(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._main_layout = QVBoxLayout(self)
        self._main_layout.setContentsMargins(4, 2, 4, 2)
        self._main_layout.setSpacing(2)

        # Row 1: identity + unit column + vertical divider + type checkboxes
        self._row1 = QWidget()
        self._row1_layout = QHBoxLayout(self._row1)
        self._row1_layout.setContentsMargins(0, 0, 0, 0)
        self._row1_layout.setSpacing(6)

        # Row 2: collection date + save button
        self._row2 = QWidget()
        self._row2_layout = QHBoxLayout(self._row2)
        self._row2_layout.setContentsMargins(0, 0, 0, 0)
        self._row2_layout.setSpacing(6)

        self._main_layout.addWidget(self._row1)
        self._main_layout.addWidget(self._row2)

        self._build_identity_section()
        self._build_type_section()
        self._apply_saved_state()
        
        # Sync height after layout is complete
        _sync_widget_height(self)

    def _build_identity_section(self):
        m = self._messages

        # --- Row 1: sample-no / name / species / ID / sex / unit-column ---
        self._sample_num_le = QLineEdit(self._data.get("sample_number", ""))
        self._sample_num_le.setPlaceholderText(
            _msg(m, "sample_track.header.sample_number", "Sample No."))
        self._sample_num_le.setFixedWidth(_W2_SAMPLENO)

        self._name_le = QLineEdit(self._data.get("animal_name", ""))
        self._name_le.setPlaceholderText("Name")
        self._name_le.setFixedWidth(_W2_NAME)
        self._species_le = QLineEdit(self._data.get("species", ""))
        self._species_le.setPlaceholderText("Species")
        self._species_le.setFixedWidth(_W2_SPECIES)
        self._id_le = QLineEdit(self._data.get("id", ""))
        self._id_le.setPlaceholderText("ID")
        self._id_le.setFixedWidth(_W2_ID)
        self._sex_cb = QComboBox()
        self._sex_cb.addItems(["Male", "Female", "Undefined"])
        self._sex_cb.setFixedWidth(_W2_SEX)
        _saved_sex2 = self._data.get("sex", "")
        _cur_sex2 = _saved_sex2 if _saved_sex2 in ("Male", "Female", "Undefined") else "Undefined"
        self._sex_cb.setCurrentText(_cur_sex2)
        self._sex_cb.setStyleSheet("QComboBox { color: #aaa; }" if _cur_sex2 == "Undefined" else "QComboBox { color: black; }")
        self._sex_cb.currentTextChanged.connect(
            lambda t, cb=self._sex_cb: cb.setStyleSheet(
                "QComboBox { color: #aaa; }" if t == "Undefined" else "QComboBox { color: black; }"))
        # Unit placeholder — same width as the header unit column
        self._unit_spacer = QWidget()
        self._unit_spacer.setFixedWidth(_W2_UNIT)

        # Read-only labels for row 1 (visible when saved)
        self._sample_num_lbl = QLabel(self._data.get("sample_number", ""))
        self._sample_num_lbl.setFixedWidth(_W2_SAMPLENO)
        self._sample_num_lbl.setStyleSheet("font-weight: bold;")
        self._sample_num_lbl.setCursor(Qt.CursorShape.PointingHandCursor)
        self._sample_num_lbl.mousePressEvent = lambda e: self._on_sample_num_click()
        self._name_lbl = QLabel(self._data.get("animal_name", ""))
        self._name_lbl.setFixedWidth(_W2_NAME)
        self._species_lbl = QLabel()
        self._species_lbl.setFixedWidth(_W2_SPECIES)
        self._id_lbl = QLabel()
        self._id_lbl.setFixedWidth(_W2_ID)
        self._sex_lbl = QLabel()
        self._sex_lbl.setFixedWidth(_W2_SEX)
        self._unit_lbl = QLabel()          # shows unit(s) from checked types
        self._unit_lbl.setFixedWidth(_W2_UNIT)

        for w in [self._sample_num_le, self._name_le, self._species_le,
                  self._id_le, self._sex_cb, self._unit_spacer,
                  self._sample_num_lbl, self._name_lbl, self._species_lbl,
                  self._id_lbl, self._sex_lbl, self._unit_lbl]:
            self._row1_layout.addWidget(w, alignment=Qt.AlignmentFlag.AlignTop)

        # Vertical divider between identity and type checkboxes
        _div = QFrame()
        _div.setFrameShape(QFrame.Shape.VLine)
        _div.setFrameShadow(QFrame.Shadow.Sunken)
        _div.setFixedWidth(4)
        self._row1_layout.addWidget(_div)

        # --- Row 2: collection date + save button ---
        _date_box = QWidget()
        _date_box.setFixedWidth(_W2_DATE_GRP)
        _cdh = QHBoxLayout(_date_box)
        _cdh.setContentsMargins(0, 0, 0, 0)
        _cdh.setSpacing(2)
        _cdh.addWidget(QLabel(_msg(m, "sample_track.field.collection_date", "Coll.:")))
        self._date_le = QLineEdit(self._data.get("collection_date", ""))
        self._date_le.setPlaceholderText("DD.MM.YYYY")
        _cdh.addWidget(self._date_le)
        self._date_box = _date_box

        self._date_lbl = QLabel()
        self._date_lbl.setFixedWidth(_W2_DATE_GRP)

        self._save_btn = QPushButton(_msg(m, "sample_track.button.save", "Save"))
        self._save_btn.setFixedWidth(_W2_SAVE)
        self._save_btn.clicked.connect(self._on_save)

        self._cancel_btn = QPushButton(_msg(m, "sample_track.button.delete", "Delete"))
        self._cancel_btn.setFixedWidth(_W2_SAVE)
        self._cancel_btn.clicked.connect(self._on_cancel)

        for w in [self._date_box, self._cancel_btn, self._save_btn, self._date_lbl]:
            self._row2_layout.addWidget(w)
        self._row2_layout.addStretch()

        self._setup_autocomplete()

    def _setup_autocomplete(self):
        names = []
        self._animal_display_to_key = {}
        for key, rec in _animal_records(self._app).items():
            display = animal_identity_label(key, rec)
            names.append(display)
            self._animal_display_to_key[display] = key
        model = QStringListModel(sorted(set(names)))
        comp = QCompleter(model, self._name_le)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.activated.connect(self._on_autocomplete)
        self._name_le.setCompleter(comp)

    def _on_autocomplete(self, display_name: str):
        records = _animal_records(self._app)
        rec = None
        key_used = getattr(self, '_animal_display_to_key', {}).get(display_name, display_name)
        for key, r in records.items():
            if key == key_used:
                rec = r
                break
        if rec is None:
            return
        self._name_le.setText(key_used)
        self._name_le.setToolTip(animal_identity_label(key_used, rec))
        self._species_le.setText(rec.get('species', ''))
        self._id_le.setText(rec.get('id', ''))
        _sv2 = _record_sex_value(rec)
        self._sex_cb.setCurrentText(_sv2 if _sv2 in ('Male', 'Female', 'Undefined') else 'Undefined')

    def _on_save(self):
        raw_name = self._name_le.text().strip()
        name, rec, error_code = _resolve_animal_input(self._app, raw_name)
        if error_code == "ambiguous":
            name = _choose_ambiguous_animal(
                self, self._app, raw_name, self._messages)
            if name:
                rec = dict(_animal_records(self._app).get(name, {}))
                error_code = ""
        if error_code:
            message = (
                "This animal name is ambiguous. Please select the full IPID from autocomplete."
                if error_code == "ambiguous"
                else "Please select an existing animal or enter a valid full IPID."
            )
            QMessageBox.warning(self, "Animal identity", message)
            return
        sn = self._sample_num_le.text().strip()
        if sn:
            for r in self._all_rows:
                if r is not self._data:
                    if r.get("animal_name") == name and r.get("sample_number") == sn:
                        QMessageBox.warning(
                            self, "Duplicate",
                            "Sample number already exists for this animal.")
                        return  # Keep row open for editing
        self._data["sample_number"] = sn
        self._data["animal_name"] = name
        self._data["species"] = _species_abbreviation(
            (rec.get("species") if rec else "") or self._species_le.text().strip()
        )
        self._data["id"] = (rec.get("id") if rec else "") or self._id_le.text().strip()
        sex_value = _record_sex_value(rec) or self._sex_cb.currentText()
        self._data["sex"] = sex_value if sex_value in ("Male", "Female", "Undefined") else self._sex_cb.currentText()
        self._data["collection_date"] = self._date_le.text().strip()
        self._saved = True
        self._apply_saved_state()
        self._save_to_store()
        self._persisted_key = (name, sn) if (name or sn) else None
        mt = getattr(self._app, 'master_track', None)
        if mt and hasattr(mt, 'audit'):
            mt.audit("sample_track.add_row", name,
                     f"tab=other_samples; sample_number={sn}")

    def _on_cancel(self):
        reply = QMessageBox.question(
            self,
            _msg(self._messages, "sample_track.confirm.delete_sample", "Delete entry?"),
            _msg(self._messages, "sample_track.confirm.delete_sample", "Delete this entry?"))
        if reply == 1024:
            self.delete_row()

    def delete_row(self):
        """Delete this row from storage and remove from UI."""
        try:
            name = self._data.get("animal_name", "")
            logger.info(f"delete_row called for animal: {name}")
            # Always purge from storage by animal_name (whether saved or not)
            rows = self._store.read()
            original_count = len(rows)
            rows = [r for r in rows if r.get("animal_name", "") != name]
            if len(rows) < original_count:
                logger.info(f"Purging {original_count - len(rows)} rows from JSON")
                self._store.write(rows)
            mt = getattr(self._app, 'master_track', None)
            if mt and hasattr(mt, 'audit'):
                mt.audit("sample_track.delete_row", name or "<unsaved>", "tab=other_samples")
            self._persisted_key = None
            # Request UI removal
            self._request_delete()
        except Exception as e:
            logger.error(f"Error in delete_row: {e}", exc_info=True)
            raise

    def _apply_saved_state(self):
        editable = not self._saved
        self._sample_num_le.setVisible(editable)
        self._name_le.setVisible(editable)
        self._species_le.setVisible(editable)
        self._id_le.setVisible(editable)
        self._sex_cb.setVisible(editable)
        self._unit_spacer.setVisible(editable)
        self._date_box.setVisible(editable)
        self._cancel_btn.setVisible(editable)
        self._save_btn.setVisible(editable)
        self._sample_num_lbl.setVisible(self._saved)
        self._name_lbl.setVisible(self._saved)
        self._species_lbl.setVisible(self._saved)
        self._id_lbl.setVisible(self._saved)
        self._sex_lbl.setVisible(self._saved)
        self._unit_lbl.setVisible(self._saved)
        self._date_lbl.setVisible(self._saved and bool(self._data.get("collection_date", "").strip()))
        if self._saved:
            self._sample_num_lbl.setText(self._data.get("sample_number", ""))
            animal_key = self._data.get("animal_name", "")
            self._name_lbl.setText(_display_animal_name(animal_key, _animal_records(self._app).get(animal_key)))
            self._name_lbl.setToolTip(animal_identity_label(animal_key, _animal_records(self._app).get(animal_key)))
            self._species_lbl.setText(self._data.get("species", ""))
            self._id_lbl.setText(self._data.get("id", ""))
            self._sex_lbl.setText(self._data.get("sex", ""))
            cd = self._data.get("collection_date", "")
            self._date_lbl.setText(
                f"{_msg(self._messages, 'sample_track.field.collection_date', 'Coll.:')} {cd}" if cd else "")
            _units_seen2: List[str] = []
            for chk in self._type_checkboxes:
                if chk.is_checked():
                    k = chk._organ["key"]
                    try:
                        locs = json.loads(str(self._data.get(f"{k}_aliquot_locations", "[]")))
                        for _loc in (locs if isinstance(locs, list) else []):
                            _u = _loc.get("unit", "").strip()
                            if _u and _u not in _units_seen2:
                                _units_seen2.append(_u)
                    except Exception:
                        pass
            self._unit_lbl.setText(", ".join(_units_seen2))
        if self._saved:
            has_date = bool(self._data.get("collection_date", "").strip())
            self._row2.setVisible(has_date)
        else:
            self._row2.setVisible(True)
        self._row1.adjustSize()
        self._row2.adjustSize()
        self._main_layout.activate()
        _sync_widget_height(self)
        QTimer.singleShot(0, self._request_parent_height_refresh)

    def _on_sample_num_click(self):
        self._show_context_menu()

    def _show_context_menu(self):
        from PyQt6.QtWidgets import QDialog as _QDialog, QVBoxLayout as _VL, QPushButton as _PB
        dlg = _QDialog(self)
        dlg.setWindowTitle(self._data.get("animal_name", ""))
        vl = _VL(dlg)

        btn_edit = _PB(_msg(self._messages, "sample_track.row.action.edit_entry", "Edit Entry"))
        btn_del  = _PB(_msg(self._messages, "sample_track.row.action.delete_all_samples", "Clear samples"))

        def _do_edit():
            dlg.accept()
            self._saved = False
            self._apply_saved_state()

        def _do_del():
            reply = QMessageBox.question(
                self,
                _msg(self._messages, "sample_track.confirm.delete_all_samples", "Clear all samples?"),
                _msg(self._messages, "sample_track.confirm.delete_all_samples", "Clear all samples?"))
            if reply == 1024:
                dlg.accept()
                self._clear_all_types()
                mt = getattr(self._app, 'master_track', None)
                if mt and hasattr(mt, 'audit'):
                    mt.audit("sample_track.clear_row_samples", self._data.get("animal_name", ""),
                             f"tab=other_samples; sample_number={self._data.get('sample_number', '')}")
            else:
                dlg.reject()

        btn_edit.clicked.connect(_do_edit)
        btn_del.clicked.connect(_do_del)
        for b in [btn_edit, btn_del]:
            vl.addWidget(b)
        dlg.exec()

    def _save_to_store(self):
        rows = self._store.read()
        name = self._data.get("animal_name", "")
        sn = self._data.get("sample_number", "")
        lookup_name, lookup_sn = self._persisted_key if self._persisted_key is not None else (name, sn)
        found = False
        for i, r in enumerate(rows):
            if r.get("animal_name") == lookup_name and r.get("sample_number") == lookup_sn:
                rows[i] = self._data
                found = True
                break
        if not found:
            rows.append(self._data)
        self._store.write(rows)
        self._persisted_key = (name, sn) if (name or sn) else None

    def _build_type_section(self):
        self._type_checkboxes: List[OrganLabel] = []
        _type_container = QWidget()
        _type_hl = QHBoxLayout(_type_container)
        _type_hl.setContentsMargins(0, 0, 0, 0)
        _type_hl.setSpacing(0)
        for t in OTHER_TYPES:
            disp_name, disp_abbrev = _type_display(t["key"])
            chk = OrganLabel({"key": t["key"], "name": disp_name, "abbrev": disp_abbrev})
            k = t["key"]
            is_chk = _str2bool(self._data.get(f"{k}_checked", False))
            chk.set_checked(is_chk)
            chk.set_warning(_str2bool(self._data.get(f"{k}_warning", False)))
            if is_chk:
                chk.set_count(int(self._data.get(f"{k}_num_aliquots", 1) or 1))
            chk._callback_checked = lambda k=k, c=chk: self._on_type_first_check(k, c)
            chk._callback_clicked_checked = lambda k=k, c=chk: self._on_type_dialog(k, c)
            _type_hl.addWidget(chk)
            self._type_checkboxes.append(chk)
        _type_hl.addStretch()
        self._organ_scroll_area = QScrollArea()
        self._organ_scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self._organ_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._organ_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._organ_scroll_area.setWidgetResizable(False)
        self._organ_scroll_area.setWidget(_type_container)
        _fit_scroll_area_to_content(self._organ_scroll_area, _type_container)
        self._organ_scroll_area.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._organ_scroll_area.wheelEvent = lambda e: e.ignore()
        _right_col = QWidget()
        _right_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        _right_vl = QVBoxLayout(_right_col)
        _right_vl.setContentsMargins(0, 0, 0, 0)
        _right_vl.setSpacing(0)
        _right_vl.addWidget(self._organ_scroll_area)
        self._row1_layout.addWidget(_right_col, 1)

    def _on_type_first_check(self, type_key: str, chk: OrganLabel):
        self._data[f"{type_key}_checked"] = True
        self._data[f"{type_key}_num_aliquots"] = 1
        self._data[f"{type_key}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
        self._data[f"{type_key}_comment"] = ""
        self._data[f"{type_key}_warning"] = False
        chk.set_count(1)

    def _on_type_dialog(self, type_key: str, chk: OrganLabel):
        disp_name, _ = _type_display(type_key)
        try:
            locs = json.loads(str(self._data.get(f"{type_key}_aliquot_locations", "[]")))
            if not isinstance(locs, list):
                locs = [{"unit": "", "storage": ""}]
        except Exception:
            locs = [{"unit": "", "storage": ""}]
        num = int(self._data.get(f"{type_key}_num_aliquots", 1) or 1)
        dlg = OtherSampleDialog(
            self, self._messages, disp_name,
            num_aliquots=num,
            aliquot_locations=locs,
            comment=str(self._data.get(f"{type_key}_comment", "")),
            warning=_str2bool(self._data.get(f"{type_key}_warning", False)),
        )
        if dlg.exec():
            if dlg.action == "delete":
                reply = QMessageBox.question(
                    self,
                    _msg(self._messages, "sample_track.confirm.delete_sample_entry",
                         "Delete this sample entry?"),
                    _msg(self._messages, "sample_track.confirm.delete_sample_entry",
                         "Delete this sample entry?"))
                if reply == 1024:
                    self._data[f"{type_key}_checked"] = False
                    self._data[f"{type_key}_num_aliquots"] = 0
                    self._data[f"{type_key}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
                    self._data[f"{type_key}_comment"] = ""
                    self._data[f"{type_key}_warning"] = False
                    chk.set_checked(False)
                    chk.set_warning(False)
                    chk.set_count(0)
                    self._save_to_store()
                    self._check_all_unchecked()
            elif dlg.action == "ok":
                num2, locs2, comment2, warning2 = dlg.values()
                if num2 == 0:
                    # Treat as type entry deletion
                    self._data[f"{type_key}_checked"] = False
                    self._data[f"{type_key}_num_aliquots"] = 0
                    self._data[f"{type_key}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
                    self._data[f"{type_key}_comment"] = ""
                    self._data[f"{type_key}_warning"] = False
                    chk.set_checked(False)
                    chk.set_warning(False)
                    chk.set_count(0)
                    self._save_to_store()
                    self._check_all_unchecked()
                else:
                    self._data[f"{type_key}_num_aliquots"] = num2
                    self._data[f"{type_key}_aliquot_locations"] = json.dumps(locs2)
                    self._data[f"{type_key}_comment"] = comment2
                    self._data[f"{type_key}_warning"] = warning2
                    chk.set_warning(warning2)
                    chk.set_count(num2)
                    self._save_to_store()
                    # Audit unit
                    all_units = [loc.get("unit", "").strip() for loc in locs2 if loc.get("unit", "").strip()]
                    if all_units:
                        unit_str = ", ".join(all_units)
                        mt = getattr(self._app, 'master_track', None)
                        if mt and hasattr(mt, 'audit'):
                            mt.audit("sample_track.assign_unit",
                                     self._data.get("animal_name", ""),
                                     f"Other sample assigned to {unit_str}")

    def _clear_all_types(self):
        for t in OTHER_TYPES:
            k = t["key"]
            self._data[f"{k}_checked"] = False
            self._data[f"{k}_num_aliquots"] = 0
            self._data[f"{k}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
            self._data[f"{k}_comment"] = ""
            self._data[f"{k}_warning"] = False
        for chk in self._type_checkboxes:
            chk.set_checked(False)
            chk.set_warning(False)
            chk.set_count(0)
        self._apply_saved_state()
        self._save_to_store()
        self._request_parent_height_refresh()
        # Force immediate visual update - update geometry and repaint
        self.adjustSize()
        self.updateGeometry()
        self.repaint()
        self.update()
        # Force parent refresh
        p = self.parent()
        if p:
            p.repaint()
            p.update()

    def _check_all_unchecked(self):
        """All aliquots zeroed — row stays; user must delete manually via Delete button."""
        pass  # intentional no-op: row deletion is user-initiated only

    def _request_delete(self):
        logger.debug(f"_request_delete called (OtherSampleRowWidget), callback callable={callable(self._delete_callback)}")
        if callable(self._delete_callback):
            try:
                self._delete_callback(self)
                logger.debug("Delete callback executed successfully")
            except Exception as e:
                logger.error(f"Error in delete callback: {e}", exc_info=True)
                raise
        else:
            logger.warning("No delete callback set, trying parent fallback")
            p = self.parent()
            if p and hasattr(p, '_remove_row'):
                p._remove_row(self)

    def sizeHint(self):
        self._row1.adjustSize()
        self._row2.adjustSize()
        margins = self._main_layout.contentsMargins()
        # Use not isHidden() like SampleListWidget: isVisible() is False during
        # visibility transitions even when not explicitly hidden, causing 1px width.
        rows = [r for r in (self._row1, self._row2) if not r.isHidden()]
        rows_for_width = rows if rows else [self._row1, self._row2]
        w = (margins.left() + margins.right()
             + max((r.sizeHint().width() for r in rows_for_width), default=0)
             + 2 * self.frameWidth())
        h = (margins.top() + margins.bottom()
             + sum(r.sizeHint().height() for r in rows)
             + self._main_layout.spacing() * max(len(rows) - 1, 0)
             + 2 * self.frameWidth())
        return QSize(max(w, 100), max(h, 1))

    def minimumSizeHint(self):
        return self.sizeHint()

    def _request_parent_height_refresh(self):
        p = self.parent()
        while p is not None:
            if hasattr(p, '_update_list_height'):
                p._update_list_height()
                return
            p = p.parent()

    def get_data(self) -> Dict:
        return self._data

    def get_animal_name(self) -> str:
        return self._data.get("animal_name", "")

    def set_selected(self, v: bool):
        self._selected = v
        self.setStyleSheet("background-color: #cce8ff;" if v else "")


# ---------------------------------------------------------------------------
# Unit visibility helpers
# ---------------------------------------------------------------------------

def _get_user_units(app) -> Optional[List[str]]:
    """Return list of user's unit strings, or None if MT absent/inactive."""
    mt = getattr(app, 'master_track', None)
    if mt is None:
        return None
    disabled = getattr(app, '_disabled_plugins', set())
    if "master_track" in disabled:
        return None
    if not getattr(mt, 'is_logged_in', False):
        return None
    try:
        rec = mt.user_db.get_user(mt.current_username)
        if rec is None:
            return []
        units = [u.strip() for u in rec.get("unit", "").split(",") if u.strip()]
        return units
    except Exception:
        return []


def _get_lord_all_units(app) -> Optional[List[str]]:
    """If logged-in user is a Lord, return empty list (Lord sees all). Else None."""
    mt = getattr(app, 'master_track', None)
    if mt is None:
        return None
    disabled = getattr(app, '_disabled_plugins', set())
    if "master_track" in disabled:
        return None
    if not getattr(mt, 'is_logged_in', False):
        return None
    try:
        rec = mt.user_db.get_user(mt.current_username)
        if rec and rec.get('role', '').lower() == 'lord':
            # Lord sees all - return empty list to signal Lord status
            return []
    except Exception:
        pass
    return None


def _organ_row_visible(data: Dict, user_units: Optional[List[str]], is_lord: bool = False) -> bool:
    """Tab 1 row visibility check — checks aliquot_locations only."""
    if user_units is None:
        return True
    if is_lord:
        return True
    any_checked = any(_str2bool(data.get(f"{o['key']}_checked", False)) for o in ORGANS)
    if not any_checked:
        return True
    # Collect all units from all checked organs first
    all_sample_units: List[str] = []
    has_checked_with_units = False
    for o in ORGANS:
        k = o["key"]
        if not _str2bool(data.get(f"{k}_checked", False)):
            continue
        sample_units: List[str] = []
        try:
            locs = json.loads(str(data.get(f"{k}_aliquot_locations", "[]")))
            for loc in (locs if isinstance(locs, list) else []):
                u = loc.get("unit", "").strip()
                if u and u not in sample_units:
                    sample_units.append(u)
        except Exception:
            pass
        if sample_units:
            has_checked_with_units = True
            all_sample_units.extend(sample_units)
    # If no checked organs have units assigned → visible to all
    if not has_checked_with_units:
        return True
    # Has units - only visible if user has matching units
    if len(user_units) == 0:
        return False
    return any(u in all_sample_units for u in user_units)


def _other_row_visible(data: Dict, user_units: Optional[List[str]], is_lord: bool = False) -> bool:
    """Tab 2 row visibility check."""
    if user_units is None:
        return True
    if is_lord:
        return True
    any_checked = any(_str2bool(data.get(f"{t['key']}_checked", False)) for t in OTHER_TYPES)
    if not any_checked:
        return True
    # Collect all units from all checked types first
    all_sample_units: List[str] = []
    has_checked_with_units = False
    for t in OTHER_TYPES:
        k = t["key"]
        if not _str2bool(data.get(f"{k}_checked", False)):
            continue
        try:
            locs = json.loads(str(data.get(f"{k}_aliquot_locations", "[]")))
        except Exception:
            locs = []
        sample_units = [loc.get("unit", "").strip() for loc in locs if loc.get("unit", "").strip()]
        if sample_units:
            has_checked_with_units = True
            all_sample_units.extend(sample_units)
    # If no checked types have units assigned → visible to all
    if not has_checked_with_units:
        return True
    # Has units - only visible if user has matching units
    if len(user_units) == 0:
        return False
    return any(u in all_sample_units for u in user_units)


# ---------------------------------------------------------------------------
# SampleListWidget (generic container)
# ---------------------------------------------------------------------------

class SampleListWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._rows: List[QFrame] = []
        self._height_changed_callback = None  # called after add/remove/clear

    def sizeHint(self):
        # Use isHidden() not isVisible(): isVisible() is False before parent window is shown,
        # causing height=0. isHidden() only reflects explicit hide() calls (e.g. from filters).
        vis = [r for r in self._rows if not r.isHidden()]
        rows_for_width = vis if vis else self._rows
        w = self._layout.contentsMargins().left() + self._layout.contentsMargins().right()
        if rows_for_width:
            w += max(r.sizeHint().width() for r in rows_for_width)
        h = self._layout.contentsMargins().top() + self._layout.contentsMargins().bottom()
        if vis:
            h += sum(r.sizeHint().height() for r in vis)
            h += self._layout.spacing() * (len(vis) - 1)
        return QSize(max(w, 100), max(h, 0))

    def minimumSizeHint(self):
        return QSize(100, 0)

    def _notify_height(self):
        if callable(self._height_changed_callback):
            self._height_changed_callback()

    def add_row(self, row_widget: QFrame):
        row_widget.setParent(self)
        row_widget.show()
        self._layout.addWidget(row_widget)
        self._rows.append(row_widget)
        self._layout.invalidate()
        self._layout.activate()
        row_widget.adjustSize()
        row_widget.updateGeometry()
        self.adjustSize()
        self.updateGeometry()
        self.update()
        QTimer.singleShot(0, self._notify_height)

    def _remove_row(self, row_widget: QFrame):
        if row_widget in self._rows:
            self._rows.remove(row_widget)
        row_widget.hide()
        self._layout.removeWidget(row_widget)
        row_widget.setParent(None)
        row_widget.deleteLater()
        self._layout.invalidate()
        self._layout.activate()
        self.adjustSize()
        self.updateGeometry()
        self.repaint()
        self._notify_height()
        QTimer.singleShot(0, self._notify_height)

    def clear_rows(self):
        for r in list(self._rows):
            self._layout.removeWidget(r)
            r.hide()
            r.deleteLater()
        self._rows.clear()
        self._layout.invalidate()
        self._layout.activate()
        self.adjustSize()
        self.updateGeometry()
        self.update()
        QTimer.singleShot(0, self._notify_height)


# ---------------------------------------------------------------------------
# Tab 1 — OrganSamplesTab
# ---------------------------------------------------------------------------

class OrganSamplesTab(QWidget):
    def __init__(self, app, messages: Dict, organ_store: JsonStore, parent=None):
        super().__init__(parent)
        self._app = app
        self._messages = messages
        self._store = organ_store
        self._row_data: List[Dict] = []
        self._row_widgets: List[SampleRowWidget] = []
        self._row_organ_scrolls: List[QScrollArea] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._build_header(layout)

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._list_widget = SampleListWidget()
        self._list_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self._list_widget._height_changed_callback = self._update_list_height
        self._list_scroll.setWidget(self._list_widget)
        # Ensure scroll area viewport allows proper scrolling
        self._list_scroll.viewport().setAutoFillBackground(False)
        layout.addWidget(self._list_scroll, 1)

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton(
            _msg(messages, "sample_track.button.add_sample", "Add Sample"))
        self._add_btn.clicked.connect(self._add_row)
        btn_row.addWidget(self._add_btn)
        self._pdf_btn = QPushButton(
            _msg(messages, "sample_track.button.export_pdf", "Export PDF"))
        self._pdf_btn.clicked.connect(self._export_pdf)
        btn_row.addWidget(self._pdf_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._load_from_store()

    def _update_list_height(self):
        # Ensure visible rows have their layouts activated for correct size calculation
        for w in self._row_widgets:
            if not w.isHidden():
                if hasattr(w, '_row1'):
                    w._row1.adjustSize()
                if hasattr(w, '_row2'):
                    w._row2.adjustSize()
                w.adjustSize()
        self._list_widget.layout().activate()
        hint = self._list_widget.sizeHint()
        h = max(hint.height(), 1)
        w = max(hint.width(), 1)
        # Set minimum width to prevent rows from shrinking below content
        self._list_widget.setMinimumWidth(w)
        # Don't set fixed height - let the scroll area handle overflow
        self._list_widget.setMinimumHeight(h)
        self._list_widget.updateGeometry()
        self._list_scroll.setMinimumHeight(120)
        self._list_scroll.updateGeometry()
        # Force immediate viewport update
        self._list_scroll.viewport().update()

    def _build_header(self, parent_layout):
        header_container = QWidget()
        header_vl = QVBoxLayout(header_container)
        header_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_vl.setContentsMargins(0, 0, 0, 0)
        header_vl.setSpacing(0)
        m = self._messages

        # "Filter by:" label sits directly above the fields (no wrapper widget)
        _filter_by_lbl = QLabel(_msg(m, "sample_track.header.filter_by", "Filter by:"))
        _filter_by_lbl.setStyleSheet("font-weight: bold; color: #555;")
        _filter_by_lbl.setContentsMargins(0, 0, 0, 0)
        _filter_by_lbl.setFixedHeight(14)
        header_vl.addWidget(_filter_by_lbl)

        # -- Row 1: Name / Species / ID / Sex / Unit  |  [right_col: organ scroll + sync bar] --
        row1 = QWidget()
        row1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hl = QHBoxLayout(row1)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)

        self._h_name = QLineEdit()
        self._h_name.setPlaceholderText(_msg(m, "sample_track.header.name", "Name"))
        self._h_name.setFixedWidth(_W1_NAME)
        self._h_species = QLineEdit()
        self._h_species.setPlaceholderText(_msg(m, "sample_track.header.species", "Species"))
        self._h_species.setFixedWidth(_W1_SPECIES)
        self._h_id = QLineEdit()
        self._h_id.setPlaceholderText(_msg(m, "sample_track.header.id", "ID"))
        self._h_id.setFixedWidth(_W1_ID)
        self._h_sex = QComboBox()
        self._h_sex.addItem(_msg(m, "sample_track.header.sex.select", "Select Sex"))
        self._h_sex.addItem("Male")
        self._h_sex.addItem("Female")
        self._h_sex.setFixedWidth(_W1_SEX)
        self._h_sex.setStyleSheet("QComboBox { color: #aaa; }")
        self._h_sex.currentIndexChanged.connect(
            lambda idx, cb=self._h_sex: cb.setStyleSheet(
                "QComboBox { color: #aaa; }" if idx == 0 else "QComboBox { color: black; }"))

        hl.addWidget(self._h_name, alignment=Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._h_species, alignment=Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._h_id, alignment=Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._h_sex, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Unit widget — same column as row unit labels
        self._unit_widget = self._create_unit_widget(_W1_UNIT)
        hl.addWidget(self._unit_widget, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Vertical divider
        _div = QFrame()
        _div.setFrameShape(QFrame.Shape.VLine)
        _div.setFrameShadow(QFrame.Shadow.Sunken)
        _div.setFixedWidth(4)
        hl.addWidget(_div)

        # Right column: organ filter scroll area + master sync scrollbar underneath
        _right_col = QWidget()
        _right_vl = QVBoxLayout(_right_col)
        _right_vl.setContentsMargins(0, 0, 0, 0)
        _right_vl.setSpacing(0)

        # Organ filter slots container
        _organ_filter_container = QWidget()
        _ofc_hl = QHBoxLayout(_organ_filter_container)
        _ofc_hl.setContentsMargins(0, 0, 0, 0)
        _ofc_hl.setSpacing(0)

        self._organ_filter_labels: Dict[str, FilterToggleLabel] = {}
        for organ in ORGANS:
            disp_name, disp_abbrev = _organ_display(organ["key"])
            slot = QWidget()
            slot.setFixedWidth(36)
            vl_s = QVBoxLayout(slot)
            vl_s.setContentsMargins(2, 2, 2, 2)
            vl_s.setSpacing(0)
            flt = FilterToggleLabel(disp_abbrev, disp_name)
            flt._callback_toggled = self._apply_filters
            vl_s.addWidget(flt, alignment=Qt.AlignmentFlag.AlignHCenter)
            _ofc_hl.addWidget(slot)
            self._organ_filter_labels[organ["key"]] = flt
        _ofc_hl.addStretch()

        self._header_organ_scroll = QScrollArea()
        self._header_organ_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._header_organ_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._header_organ_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._header_organ_scroll.setWidgetResizable(False)
        self._header_organ_scroll.setWidget(_organ_filter_container)
        _fit_scroll_area_to_content(self._header_organ_scroll, _organ_filter_container)
        _right_vl.addWidget(self._header_organ_scroll)

        # Master synchronisation scrollbar (controls all organ scroll areas)
        self._organ_sync_bar = QScrollBar(Qt.Orientation.Horizontal)
        self._organ_sync_bar.setFixedHeight(13)
        _right_vl.addWidget(self._organ_sync_bar)

        _right_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hl.addWidget(_right_col, 1)

        # Connect sync bar ↔ header organ scroll area; hide bar when content fits
        self._organ_sync_bar.setVisible(False)
        self._organ_sync_bar.valueChanged.connect(self._on_organ_sync_scroll)
        self._header_organ_scroll.horizontalScrollBar().rangeChanged.connect(
            lambda mn, mx: self._refresh_sync_bar())

        # -- "Filter by age" toggle button (collapsed by default) --
        _age_lbl_text = _msg(m, "sample_track.header.filter_by_age", "Filter by age:")
        self._age_toggle_btn = QPushButton(_age_lbl_text)
        self._age_toggle_btn.setFlat(True)
        self._age_toggle_btn.setCheckable(True)
        self._age_toggle_btn.setChecked(False)
        self._age_toggle_btn.setStyleSheet(
            "QPushButton { font-weight: bold; color: #555; text-align: left; "
            "border: none; padding: 0px; margin: 0px; }")
        self._age_toggle_btn.setFixedHeight(16)
        apply_icon(self._age_toggle_btn, "toggle.expand", fallback=_age_lbl_text)

        # -- Age fields row (hidden until toggle is expanded) --
        self._age_row = QWidget()
        self._age_row.setVisible(False)
        hl2 = QHBoxLayout(self._age_row)
        hl2.setContentsMargins(8, 0, 0, 0)
        hl2.setSpacing(2)

        # From group: [y] yr [m] mo [d] d  (leading ≥ moved to separator)
        self._h_age_from_y = QLineEdit()
        self._h_age_from_y.setPlaceholderText("0")
        self._h_age_from_y.setFixedWidth(30)
        self._h_age_from_y.setToolTip(_msg(m, "sample_track.header.age_from", "Minimum age") + " — years")
        hl2.addWidget(self._h_age_from_y)
        hl2.addWidget(QLabel("yr"))
        self._h_age_from_m = QLineEdit()
        self._h_age_from_m.setPlaceholderText("0")
        self._h_age_from_m.setFixedWidth(26)
        self._h_age_from_m.setToolTip(_msg(m, "sample_track.header.age_from", "Minimum age") + " — months")
        hl2.addWidget(self._h_age_from_m)
        hl2.addWidget(QLabel("mo"))
        self._h_age_from_d = QLineEdit()
        self._h_age_from_d.setPlaceholderText("0")
        self._h_age_from_d.setFixedWidth(26)
        self._h_age_from_d.setToolTip(_msg(m, "sample_track.header.age_from", "Minimum age") + " — days")
        hl2.addWidget(self._h_age_from_d)
        hl2.addWidget(QLabel("d"))

        # "≥ Age ≤" combined separator label (tight spacing, no gap)
        _age_sep = QLabel("≥ " + _msg(m, "sample_track.header.age_separator", "Age") + " ≤")
        _age_sep.setStyleSheet("font-weight: bold; color: #555; padding: 0 2px 0 4px;")
        hl2.addWidget(_age_sep)
        self._h_age_to_y = QLineEdit()
        self._h_age_to_y.setPlaceholderText("0")
        self._h_age_to_y.setFixedWidth(30)
        self._h_age_to_y.setToolTip(_msg(m, "sample_track.header.age_to", "Maximum age") + " — years")
        hl2.addWidget(self._h_age_to_y)
        hl2.addWidget(QLabel("yr"))
        self._h_age_to_m = QLineEdit()
        self._h_age_to_m.setPlaceholderText("0")
        self._h_age_to_m.setFixedWidth(26)
        self._h_age_to_m.setToolTip(_msg(m, "sample_track.header.age_to", "Maximum age") + " — months")
        hl2.addWidget(self._h_age_to_m)
        hl2.addWidget(QLabel("mo"))
        self._h_age_to_d = QLineEdit()
        self._h_age_to_d.setPlaceholderText("0")
        self._h_age_to_d.setFixedWidth(26)
        self._h_age_to_d.setToolTip(_msg(m, "sample_track.header.age_to", "Maximum age") + " — days")
        hl2.addWidget(self._h_age_to_d)
        hl2.addWidget(QLabel("d"))
        hl2.addStretch()

        # Wire toggle: show/hide age row and flip arrow
        def _on_age_toggle(checked: bool, _lbl=_age_lbl_text):
            self._age_row.setVisible(checked)
            self._age_toggle_btn.setText(_lbl)
            apply_icon(
                self._age_toggle_btn,
                "toggle.collapse" if checked else "toggle.expand",
                fallback=_lbl,
            )
            self._apply_filters()
        self._age_toggle_btn.toggled.connect(_on_age_toggle)

        header_vl.addWidget(row1)
        header_vl.addWidget(self._age_toggle_btn)
        header_vl.addWidget(self._age_row)
        parent_layout.addWidget(header_container)

        for w in [self._h_name, self._h_species, self._h_id]:
            w.textChanged.connect(self._apply_filters)
        self._h_sex.currentIndexChanged.connect(self._apply_filters)
        if isinstance(self._unit_widget, (QComboBox,)):
            self._unit_widget.currentIndexChanged.connect(self._apply_filters)
        for le in [self._h_age_from_y, self._h_age_from_m, self._h_age_from_d,
                   self._h_age_to_y, self._h_age_to_m, self._h_age_to_d]:
            le.textChanged.connect(self._apply_filters)

    def _get_stored_units(self) -> List[str]:
        """Collect all unique units from stored organ sample data (for Lord accounts)."""
        units: set = set()
        try:
            rows = self._store.read()
            for d in rows:
                for o in ORGANS:
                    k = o["key"]
                    if not _str2bool(d.get(f"{k}_checked", False)):
                        continue
                    try:
                        locs = json.loads(str(d.get(f"{k}_aliquot_locations", "[]")))
                        for loc in locs:
                            u = loc.get("unit", "").strip()
                            if u:
                                units.add(u)
                    except Exception:
                        pass
        except Exception:
            pass
        return sorted(units)

    def _create_unit_widget(self, width: int) -> QWidget:
        """Return a fixed-width unit filter widget (QComboBox or QLabel)."""
        m = self._messages
        lord_check = _get_lord_all_units(self._app)
        if lord_check is not None:
            # Lord: dropdown populated from units found in stored samples
            store_units = self._get_stored_units()
            cb = QComboBox()
            cb.addItem(_msg(m, "sample_track.header.unit.select", "Select Unit"))
            for u in store_units:
                cb.addItem(u)
            cb.setFixedWidth(width)
            return cb
        user_units = _get_user_units(self._app)
        if user_units is None:
            w = QLineEdit()
            w.setReadOnly(True)
            w.setFixedWidth(width)
            return w
        if len(user_units) == 0:
            w = QLabel("—")
            w.setFixedWidth(width)
            return w
        if len(user_units) == 1:
            w = QLabel(user_units[0])
            w.setFixedWidth(width)
            return w
        cb = QComboBox()
        cb.addItem(_msg(m, "sample_track.header.unit.select", "Select Unit"))
        for u in user_units:
            cb.addItem(u)
        cb.setFixedWidth(width)
        return cb

    def _load_from_store(self):
        self._list_widget.clear_rows()
        self._row_widgets.clear()
        self._row_organ_scrolls.clear()
        raw = self._store.read()
        # Load only meaningful rows
        self._row_data = [d for d in raw if _is_organ_row_meaningful(d)]
        self._row_data.sort(key=lambda r: r.get("animal_name", "").lower())
        for d in self._row_data:
            self._add_row_widget(d, is_saved=True)
        # Apply unit and other filters immediately on load
        self._apply_filters()
        QTimer.singleShot(0, self._update_list_height)
        QTimer.singleShot(0, self._refresh_sync_bar)

    def _add_row_widget(self, data: Dict, is_saved: bool = False) -> SampleRowWidget:
        w = SampleRowWidget(data, self._app, self._messages,
                            self._store, self._row_data, is_saved=is_saved)
        w._delete_callback = self._on_row_deleted
        self._list_widget.add_row(w)
        self._row_widgets.append(w)
        if hasattr(w, '_organ_scroll_area'):
            self._row_organ_scrolls.append(w._organ_scroll_area)
            w._organ_scroll_area.horizontalScrollBar().rangeChanged.connect(
                lambda mn, mx: self._refresh_sync_bar())
        QTimer.singleShot(0, self._update_list_height)
        QTimer.singleShot(0, self._refresh_sync_bar)
        return w

    def _on_row_deleted(self, row: SampleRowWidget):
        logger.debug(f"_on_row_deleted called for row: {row.get_animal_name()}")
        if row in self._row_widgets:
            self._row_widgets.remove(row)
            logger.debug("Row removed from _row_widgets")
        d = row.get_data()
        # Try to remove by identity first, then by matching animal_name
        found = False
        for i, rd in enumerate(self._row_data):
            if rd is d:
                del self._row_data[i]
                found = True
                logger.debug("Row data removed by identity match")
                break
        if not found:
            # Fallback: match by animal_name for unsaved rows
            name = d.get("animal_name", "")
            logger.debug(f"Identity match failed, trying name match: {name}")
            for i, rd in enumerate(self._row_data):
                if rd.get("animal_name", "") == name:
                    del self._row_data[i]
                    logger.debug("Row data removed by name match")
                    break
        if hasattr(row, '_organ_scroll_area') and row._organ_scroll_area in self._row_organ_scrolls:
            self._row_organ_scrolls.remove(row._organ_scroll_area)
        self._list_widget._remove_row(row)
        logger.debug("Row removed from _list_widget")
        self._update_list_height()
        self._refresh_sync_bar()
        self._list_scroll.viewport().update()
        self.update()
        QTimer.singleShot(0, self.refresh)

    def _refresh_sync_bar(self):
        """Recompute max overflow across header + all row scroll areas; update sync bar."""
        max_val = self._header_organ_scroll.horizontalScrollBar().maximum()
        for sa in self._row_organ_scrolls:
            max_val = max(max_val, sa.horizontalScrollBar().maximum())
        self._organ_sync_bar.setRange(0, max_val)
        self._organ_sync_bar.setVisible(max_val > 0)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._refresh_sync_bar)

    def _on_organ_sync_scroll(self, value: int):
        self._header_organ_scroll.horizontalScrollBar().setValue(value)
        for sa in self._row_organ_scrolls:
            sa.horizontalScrollBar().setValue(value)

    def _add_row(self):
        new_data: Dict = {"animal_name": "",
                          "species": "", "id": "", "sex": "",
                          "birth_date": "", "death_date": ""}
        self._row_data.append(new_data)
        w = self._add_row_widget(new_data, is_saved=False)
        w._name_le.setFocus()
        QTimer.singleShot(0, self._update_list_height)

    @staticmethod
    def _age_int(le: QLineEdit) -> int:
        try:
            return max(0, int(le.text().strip()))
        except (ValueError, AttributeError):
            return 0

    def _apply_filters(self):
        name_f = self._h_name.text().lower()
        species_f = self._h_species.text().lower()
        id_f = self._h_id.text().lower()
        _age_active = getattr(self, '_age_row', None) is not None and self._age_row.isVisible()
        from_y = self._age_int(self._h_age_from_y) if _age_active else 0
        from_m = self._age_int(self._h_age_from_m) if _age_active else 0
        from_d = self._age_int(self._h_age_from_d) if _age_active else 0
        age_from_set = _age_active and (from_y > 0 or from_m > 0 or from_d > 0)
        to_y = self._age_int(self._h_age_to_y) if _age_active else 0
        to_m = self._age_int(self._h_age_to_m) if _age_active else 0
        to_d = self._age_int(self._h_age_to_d) if _age_active else 0
        age_to_set = _age_active and (to_y > 0 or to_m > 0 or to_d > 0)
        lord_units = _get_lord_all_units(self._app)
        user_units = _get_user_units(self._app)
        active_organ_keys = [k for k, flt in self._organ_filter_labels.items() if flt.is_checked()]

        unit_filter = None
        if isinstance(self._unit_widget, QComboBox):
            idx = self._unit_widget.currentIndex()
            unit_filter = None if idx == 0 else self._unit_widget.currentText()

        for w in self._row_widgets:
            d = w.get_data()
            visible = True
            if name_f and name_f not in d.get("animal_name", "").lower():
                visible = False
            if species_f and species_f not in d.get("species", "").lower():
                visible = False
            if id_f and id_f not in d.get("id", "").lower():
                visible = False
            if self._h_sex.currentIndex() > 0 and d.get("sex", "") != self._h_sex.currentText():
                visible = False

            # Organ type filter (AND logic - show only if ALL selected organs match)
            if visible and active_organ_keys:
                if not all(_str2bool(d.get(f"{k}_checked", False)) for k in active_organ_keys):
                    visible = False

            # Age filter
            if visible and (age_from_set or age_to_set):
                try:
                    from dateutil.relativedelta import relativedelta as _rd
                    today = date.today()
                    birth = _parse_date(d.get("birth_date", ""))
                    death = _parse_date(d.get("death_date", ""))
                    ref = death or today
                    if birth:
                        if age_from_set:
                            earliest_birth = ref - _rd(years=from_y, months=from_m, days=from_d)
                            if birth > earliest_birth:
                                visible = False
                        if age_to_set and visible:
                            latest_birth = ref - _rd(years=to_y, months=to_m, days=to_d)
                            if birth < latest_birth:
                                visible = False
                    else:
                        visible = False
                except Exception:
                    pass

            # Unit visibility
            if visible:
                eff_units = lord_units if lord_units is not None else user_units
                is_lord = lord_units is not None
                if unit_filter and eff_units is not None and not is_lord:
                    eff_units = [unit_filter]
                row_visible = _organ_row_visible(d, eff_units, is_lord)
                if not row_visible:
                    visible = False

            w.setVisible(visible)
        QTimer.singleShot(0, self._update_list_height)

    def refresh(self):
        self._load_from_store()
        self._apply_filters()

    def _export_pdf(self):
        _export_tab_pdf(self, self._app, self._messages, "organ_samples",
                        self._row_widgets, self._store)


# ---------------------------------------------------------------------------
# Tab 2 — OtherSamplesTab
# ---------------------------------------------------------------------------

class OtherSamplesTab(QWidget):
    def __init__(self, app, messages: Dict, other_store: JsonStore, parent=None):
        super().__init__(parent)
        self._app = app
        self._messages = messages
        self._store = other_store
        self._row_data: List[Dict] = []
        self._row_widgets: List[OtherSampleRowWidget] = []
        self._row_organ_scrolls: List[QScrollArea] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self._build_header(layout)

        self._list_scroll = QScrollArea()
        self._list_scroll.setWidgetResizable(True)
        self._list_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._list_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._list_widget = SampleListWidget()
        self._list_widget.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self._list_widget._height_changed_callback = self._update_list_height
        self._list_scroll.setWidget(self._list_widget)
        # Ensure scroll area viewport allows proper scrolling
        self._list_scroll.viewport().setAutoFillBackground(False)
        layout.addWidget(self._list_scroll, 1)

        btn_row = QHBoxLayout()
        self._add_btn = QPushButton(
            _msg(messages, "sample_track.button.add_sample", "Add Sample"))
        self._add_btn.clicked.connect(self._add_row)
        btn_row.addWidget(self._add_btn)
        self._pdf_btn = QPushButton(
            _msg(messages, "sample_track.button.export_pdf", "Export PDF"))
        self._pdf_btn.clicked.connect(self._export_pdf)
        btn_row.addWidget(self._pdf_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._load_from_store()

    def _update_list_height(self):
        # Ensure visible rows have their layouts activated for correct size calculation
        for w in self._row_widgets:
            if not w.isHidden():
                if hasattr(w, '_row1'):
                    w._row1.adjustSize()
                if hasattr(w, '_row2'):
                    w._row2.adjustSize()
                w.adjustSize()
        self._list_widget.layout().activate()
        hint = self._list_widget.sizeHint()
        h = max(hint.height(), 1)
        w = max(hint.width(), 1)
        self._list_widget.setMinimumWidth(w)
        self._list_widget.setFixedHeight(h)
        self._list_widget.updateGeometry()
        self._list_scroll.updateGeometry()

    def _build_header(self, parent_layout):
        header_container = QWidget()
        header_vl = QVBoxLayout(header_container)
        header_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header_vl.setContentsMargins(0, 0, 0, 0)
        header_vl.setSpacing(0)
        m = self._messages

        # "Filter by:" label sits directly above the fields (no wrapper widget)
        _filter_by_lbl2 = QLabel(_msg(m, "sample_track.header.filter_by", "Filter by:"))
        _filter_by_lbl2.setStyleSheet("font-weight: bold; color: #555;")
        _filter_by_lbl2.setContentsMargins(0, 0, 0, 0)
        _filter_by_lbl2.setFixedHeight(14)
        header_vl.addWidget(_filter_by_lbl2)

        # -- Row 1: SampleNo / Name / Species / ID / Sex / Unit  |  [right_col: type scroll + sync bar] --
        row1 = QWidget()
        row1.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        hl = QHBoxLayout(row1)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(6)

        self._h_sampleno = QLineEdit()
        self._h_sampleno.setPlaceholderText(
            _msg(m, "sample_track.header.sample_number", "Sample No."))
        self._h_sampleno.setFixedWidth(_W2_SAMPLENO)
        self._h_name = QLineEdit()
        self._h_name.setPlaceholderText(_msg(m, "sample_track.header.name", "Name"))
        self._h_name.setFixedWidth(_W2_NAME)
        self._h_species = QLineEdit()
        self._h_species.setPlaceholderText(_msg(m, "sample_track.header.species", "Species"))
        self._h_species.setFixedWidth(_W2_SPECIES)
        self._h_id = QLineEdit()
        self._h_id.setPlaceholderText(_msg(m, "sample_track.header.id", "ID"))
        self._h_id.setFixedWidth(_W2_ID)
        self._h_sex = QComboBox()
        self._h_sex.addItem(_msg(m, "sample_track.header.sex.select", "Select Sex"))
        self._h_sex.addItem("Male")
        self._h_sex.addItem("Female")
        self._h_sex.setFixedWidth(_W2_SEX)
        self._h_sex.setStyleSheet("QComboBox { color: #aaa; }")
        self._h_sex.currentIndexChanged.connect(
            lambda idx, cb=self._h_sex: cb.setStyleSheet(
                "QComboBox { color: #aaa; }" if idx == 0 else "QComboBox { color: black; }"))

        hl.addWidget(self._h_sampleno, alignment=Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._h_name, alignment=Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._h_species, alignment=Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._h_id, alignment=Qt.AlignmentFlag.AlignVCenter)
        hl.addWidget(self._h_sex, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Unit widget — same column as row unit labels
        self._unit_widget = self._create_unit_widget(_W2_UNIT)
        hl.addWidget(self._unit_widget, alignment=Qt.AlignmentFlag.AlignVCenter)

        # Vertical divider
        _div = QFrame()
        _div.setFrameShape(QFrame.Shape.VLine)
        _div.setFrameShadow(QFrame.Shadow.Sunken)
        _div.setFixedWidth(4)
        hl.addWidget(_div)

        # Right column: type filter scroll area + master sync scrollbar underneath
        _right_col = QWidget()
        _right_vl = QVBoxLayout(_right_col)
        _right_vl.setContentsMargins(0, 0, 0, 0)
        _right_vl.setSpacing(0)

        # Type filter slots container
        _type_filter_container = QWidget()
        _tfc_hl = QHBoxLayout(_type_filter_container)
        _tfc_hl.setContentsMargins(0, 0, 0, 0)
        _tfc_hl.setSpacing(0)

        self._type_filter_labels: Dict[str, FilterToggleLabel] = {}
        for t in OTHER_TYPES:
            disp_name, disp_abbrev = _type_display(t["key"])
            slot = QWidget()
            slot.setFixedWidth(36)
            vl_s = QVBoxLayout(slot)
            vl_s.setContentsMargins(2, 2, 2, 2)
            vl_s.setSpacing(0)
            flt = FilterToggleLabel(disp_abbrev, disp_name)
            flt._callback_toggled = self._apply_filters
            vl_s.addWidget(flt, alignment=Qt.AlignmentFlag.AlignHCenter)
            _tfc_hl.addWidget(slot)
            self._type_filter_labels[t["key"]] = flt
        _tfc_hl.addStretch()

        self._header_organ_scroll = QScrollArea()
        self._header_organ_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._header_organ_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._header_organ_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._header_organ_scroll.setWidgetResizable(False)
        self._header_organ_scroll.setWidget(_type_filter_container)
        _fit_scroll_area_to_content(self._header_organ_scroll, _type_filter_container)
        _right_vl.addWidget(self._header_organ_scroll)

        # Master synchronisation scrollbar
        self._organ_sync_bar = QScrollBar(Qt.Orientation.Horizontal)
        self._organ_sync_bar.setFixedHeight(13)
        _right_vl.addWidget(self._organ_sync_bar)

        _right_col.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hl.addWidget(_right_col, 1)

        # Connect sync bar ↔ header type scroll area; hide bar when content fits
        self._organ_sync_bar.setVisible(False)
        self._organ_sync_bar.valueChanged.connect(self._on_organ_sync_scroll)
        self._header_organ_scroll.horizontalScrollBar().rangeChanged.connect(
            lambda mn, mx: self._refresh_sync_bar())

        # -- "Filter by date" toggle button (collapsed by default) --
        header_vl.setContentsMargins(2, 2, 2, 0)
        header_vl.setSpacing(0)
        _date_lbl_text = _msg(m, "sample_track.header.filter_by_date", "Filter by collection date:")
        self._date_toggle_btn = QPushButton(_date_lbl_text)
        self._date_toggle_btn.setFlat(True)
        self._date_toggle_btn.setCheckable(True)
        self._date_toggle_btn.setChecked(False)
        self._date_toggle_btn.setStyleSheet(
            "QPushButton { font-weight: bold; color: #555; text-align: left; "
            "border: none; padding: 1px 0px; }")
        apply_icon(self._date_toggle_btn, "toggle.expand", fallback=_date_lbl_text)

        # -- Date fields row (hidden until toggle expanded) --
        self._date_row = QWidget()
        self._date_row.setVisible(False)
        hl2 = QHBoxLayout(self._date_row)
        hl2.setContentsMargins(8, 0, 0, 0)
        hl2.setSpacing(2)

        self._h_date_from = QLineEdit()
        self._h_date_from.setPlaceholderText("DD.MM.YYYY")
        self._h_date_from.setFixedWidth(88)
        self._h_date_from.setToolTip(_msg(m, "sample_track.header.collection_date_from", "Collection date from"))
        hl2.addWidget(self._h_date_from)

        _date_sep = QLabel("≥ " + _msg(m, "sample_track.header.date_separator", "Date") + " ≤")
        _date_sep.setStyleSheet("font-weight: bold; color: #555; padding: 0 2px 0 4px;")
        hl2.addWidget(_date_sep)

        self._h_date_to = QLineEdit()
        self._h_date_to.setPlaceholderText("DD.MM.YYYY")
        self._h_date_to.setFixedWidth(88)
        self._h_date_to.setToolTip(_msg(m, "sample_track.header.collection_date_to", "Collection date to"))
        hl2.addWidget(self._h_date_to)
        hl2.addStretch()

        def _on_date_toggle(checked: bool, _lbl=_date_lbl_text):
            self._date_row.setVisible(checked)
            self._date_toggle_btn.setText(_lbl)
            apply_icon(
                self._date_toggle_btn,
                "toggle.collapse" if checked else "toggle.expand",
                fallback=_lbl,
            )
            self._apply_filters()
        self._date_toggle_btn.toggled.connect(_on_date_toggle)

        header_vl.addWidget(row1)
        header_vl.addWidget(self._date_toggle_btn)
        header_vl.addWidget(self._date_row)
        parent_layout.addWidget(header_container)

        for w in [self._h_sampleno, self._h_name, self._h_species, self._h_id,
                  self._h_date_from, self._h_date_to]:
            w.textChanged.connect(self._apply_filters)
        self._h_sex.currentIndexChanged.connect(self._apply_filters)
        if isinstance(self._unit_widget, QComboBox):
            self._unit_widget.currentIndexChanged.connect(self._apply_filters)

    def _get_stored_units(self) -> List[str]:
        """Collect all unique units from stored other-sample data (for Lord accounts)."""
        units: set = set()
        try:
            rows = self._store.read()
            for d in rows:
                for t in OTHER_TYPES:
                    k = t["key"]
                    if not _str2bool(d.get(f"{k}_checked", False)):
                        continue
                    try:
                        locs = json.loads(str(d.get(f"{k}_aliquot_locations", "[]")))
                        for loc in locs:
                            u = loc.get("unit", "").strip()
                            if u:
                                units.add(u)
                    except Exception:
                        pass
        except Exception:
            pass
        return sorted(units)

    def _create_unit_widget(self, width: int) -> QWidget:
        """Return a fixed-width unit filter widget (QComboBox or QLabel)."""
        m = self._messages
        lord_check = _get_lord_all_units(self._app)
        if lord_check is not None:
            store_units = self._get_stored_units()
            cb = QComboBox()
            cb.addItem(_msg(m, "sample_track.header.unit.select", "Select Unit"))
            for u in store_units:
                cb.addItem(u)
            cb.setFixedWidth(width)
            return cb
        user_units = _get_user_units(self._app)
        if user_units is None:
            w = QLineEdit()
            w.setReadOnly(True)
            w.setFixedWidth(width)
            return w
        if len(user_units) == 0:
            w = QLabel("—")
            w.setFixedWidth(width)
            return w
        if len(user_units) == 1:
            w = QLabel(user_units[0])
            w.setFixedWidth(width)
            return w
        cb = QComboBox()
        cb.addItem(_msg(m, "sample_track.header.unit.select", "Select Unit"))
        for u in user_units:
            cb.addItem(u)
        cb.setFixedWidth(width)
        return cb

    def _load_from_store(self):
        self._list_widget.clear_rows()
        self._row_widgets.clear()
        self._row_organ_scrolls.clear()
        raw = self._store.read()
        # Load only meaningful rows
        self._row_data = [d for d in raw if _is_other_row_meaningful(d)]
        for d in self._row_data:
            self._add_row_widget(d, is_saved=True)
        # Apply unit and other filters immediately on load
        self._apply_filters()
        QTimer.singleShot(0, self._update_list_height)
        QTimer.singleShot(0, self._refresh_sync_bar)

    def _add_row_widget(self, data: Dict, is_saved: bool = False) -> OtherSampleRowWidget:
        w = OtherSampleRowWidget(data, self._app, self._messages,
                                 self._store, self._row_data, is_saved=is_saved)
        w._delete_callback = self._on_row_deleted
        self._list_widget.add_row(w)
        self._row_widgets.append(w)
        if hasattr(w, '_organ_scroll_area'):
            self._row_organ_scrolls.append(w._organ_scroll_area)
            w._organ_scroll_area.horizontalScrollBar().rangeChanged.connect(
                lambda mn, mx: self._refresh_sync_bar())
        QTimer.singleShot(0, self._update_list_height)
        QTimer.singleShot(0, self._refresh_sync_bar)
        return w

    def _on_row_deleted(self, row: OtherSampleRowWidget):
        if row in self._row_widgets:
            self._row_widgets.remove(row)
        d = row.get_data()
        # Try to remove by identity first, then by matching animal_name+sample_number
        found = False
        for i, rd in enumerate(self._row_data):
            if rd is d:
                del self._row_data[i]
                found = True
                break
        if not found:
            # Fallback: match by animal_name and sample_number for unsaved rows
            name = d.get("animal_name", "")
            sn = d.get("sample_number", "")
            for i, rd in enumerate(self._row_data):
                if rd.get("animal_name", "") == name and rd.get("sample_number", "") == sn:
                    del self._row_data[i]
                    break
        if hasattr(row, '_organ_scroll_area') and row._organ_scroll_area in self._row_organ_scrolls:
            self._row_organ_scrolls.remove(row._organ_scroll_area)
        self._list_widget._remove_row(row)
        self._update_list_height()
        self._refresh_sync_bar()
        self._list_scroll.viewport().update()
        self.update()
        QTimer.singleShot(0, self.refresh)

    def _refresh_sync_bar(self):
        """Recompute max overflow across header + all row scroll areas; update sync bar."""
        max_val = self._header_organ_scroll.horizontalScrollBar().maximum()
        for sa in self._row_organ_scrolls:
            max_val = max(max_val, sa.horizontalScrollBar().maximum())
        self._organ_sync_bar.setRange(0, max_val)
        self._organ_sync_bar.setVisible(max_val > 0)

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._refresh_sync_bar)

    def _on_organ_sync_scroll(self, value: int):
        self._header_organ_scroll.horizontalScrollBar().setValue(value)
        for sa in self._row_organ_scrolls:
            sa.horizontalScrollBar().setValue(value)

    def _add_row(self):
        new_data: Dict = {"sample_number": "", "animal_name": "",
                          "species": "", "id": "", "sex": "", "collection_date": ""}
        self._row_data.append(new_data)
        w = self._add_row_widget(new_data, is_saved=False)
        w._sample_num_le.setFocus()
        QTimer.singleShot(0, self._update_list_height)

    def _apply_filters(self):
        sampleno_f = self._h_sampleno.text().lower()
        name_f = self._h_name.text().lower()
        species_f = self._h_species.text().lower()
        id_f = self._h_id.text().lower()
        _date_active = getattr(self, '_date_row', None) is not None and self._date_row.isVisible()
        date_from = _parse_date(self._h_date_from.text()) if _date_active else None
        date_to = _parse_date(self._h_date_to.text()) if _date_active else None
        lord_units = _get_lord_all_units(self._app)
        user_units = _get_user_units(self._app)
        active_type_keys = [k for k, flt in self._type_filter_labels.items() if flt.is_checked()]

        unit_filter = None
        if isinstance(self._unit_widget, QComboBox):
            idx = self._unit_widget.currentIndex()
            unit_filter = None if idx == 0 else self._unit_widget.currentText()

        for w in self._row_widgets:
            d = w.get_data()
            visible = True
            if sampleno_f and sampleno_f not in d.get("sample_number", "").lower():
                visible = False
            if name_f and name_f not in d.get("animal_name", "").lower():
                visible = False
            if species_f and species_f not in d.get("species", "").lower():
                visible = False
            if id_f and id_f not in d.get("id", "").lower():
                visible = False
            if self._h_sex.currentIndex() > 0 and d.get("sex", "") != self._h_sex.currentText():
                visible = False

            # Type filter (AND logic - show only if ALL selected types match)
            if visible and active_type_keys:
                if not all(_str2bool(d.get(f"{k}_checked", False)) for k in active_type_keys):
                    visible = False

            if visible and (date_from or date_to):
                cd = _parse_date(d.get("collection_date", ""))
                if cd:
                    if date_from and cd < date_from:
                        visible = False
                    if date_to and cd > date_to:
                        visible = False
            if visible:
                eff_units = lord_units if lord_units is not None else user_units
                is_lord = lord_units is not None
                if unit_filter and eff_units is not None and not is_lord:
                    eff_units = [unit_filter]
                if not _other_row_visible(d, eff_units, is_lord):
                    visible = False
            w.setVisible(visible)
        QTimer.singleShot(0, self._update_list_height)

    def refresh(self):
        self._load_from_store()
        self._apply_filters()

    def _export_pdf(self):
        _export_tab_pdf(self, self._app, self._messages, "other_samples",
                        self._row_widgets, self._store)

    def upsert_row(self, animal_name: str, collection_date: str,
                   type_key: str) -> None:
        """Create or update a row for the given animal/date, marking type_key checked."""
        rows = self._store.read()
        found = None
        for r in rows:
            if r.get("animal_name") == animal_name and r.get("collection_date") == collection_date:
                found = r
                break
        if found is None:
            app = self._app
            rec = (getattr(app, 'animals', {}).get(animal_name)
                   or getattr(app, 'archived', {}).get(animal_name, {}))
            # Progesterone (plasma) and urine samples always come from females
            sex_value = "Female" if type_key in ("plasma", "urin") else _record_sex_value(rec)
            new_row: Dict[str, Any] = {
                "sample_number": "",
                "animal_name": animal_name,
                    "species": _species_abbreviation(rec.get("species", "")),
                "id": rec.get("id", ""),
                "sex": sex_value,
                "collection_date": collection_date,
            }
            new_row[f"{type_key}_checked"] = True
            new_row[f"{type_key}_num_aliquots"] = 1
            new_row[f"{type_key}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
            rows.append(new_row)
            self._row_data.append(new_row)
            self._add_row_widget(new_row, is_saved=True)
        else:
            if not _str2bool(found.get(f"{type_key}_checked", False)):
                found[f"{type_key}_checked"] = True
                if not found.get(f"{type_key}_aliquot_locations"):
                    found[f"{type_key}_num_aliquots"] = 1
                    found[f"{type_key}_aliquot_locations"] = json.dumps([{"unit": "", "storage": ""}])
        self._store.write(rows)


# ---------------------------------------------------------------------------
# PDF export
# ---------------------------------------------------------------------------

def _export_tab_pdf(parent_widget, app, messages: Dict, tab_key: str,
                    row_widgets, store) -> None:
    m = messages
    path, _ = QFileDialog.getSaveFileName(
        parent_widget,
        _msg(m, "sample_track.pdf.export_title", "Save PDF"),
        str(default_export_directory()),
        _msg(m, "sample_track.pdf.export_filter", "PDF Files (*.pdf)"),
    )
    if not path:
        return
    if not path.lower().endswith(".pdf"):
        path += ".pdf"
    try:
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.lib import colors
        from reportlab.lib.units import mm
        from reportlab.platypus import (
            BaseDocTemplate, PageTemplate, Frame, Table, TableStyle,
            Paragraph, Spacer
        )
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.pdfgen.canvas import Canvas

        styles = getSampleStyleSheet()
        tab_label = _msg(m, f"sample_track.tab.{tab_key}", tab_key)
        title_text = _msg(m, "sample_track.pdf.title", "Sample Track — {tab}").replace("{tab}", tab_label)

        mt = getattr(app, 'master_track', None)
        user = ""
        if mt and getattr(mt, 'is_logged_in', False):
            username = getattr(mt, 'current_username', '')
            try:
                user_rec = mt.user_db.get_user(username)
                if user_rec:
                    user = user_rec.get('display_name', username)
                else:
                    user = username
            except Exception:
                user = username

        page_size = landscape(A4)
        page_width, page_height = page_size
        margin = 15 * mm
        usable_width = page_width - 2 * margin

        # Build table data - filter to only rows with aliquots
        visible_rows = [w for w in row_widgets if w.isVisible()]
        
        def _has_aliquots(data, items):
            """Check if row has at least one aliquot across any checked item."""
            for item in items:
                k = item["key"]
                if _str2bool(data.get(f"{k}_checked", False)):
                    num_aliquots = data.get(f"{k}_num_aliquots", 0)
                    if num_aliquots and num_aliquots > 0:
                        return True
            return False
        
        if tab_key == "organ_samples":
            visible_rows = [w for w in visible_rows if _has_aliquots(w.get_data(), ORGANS)]
        else:
            visible_rows = [w for w in visible_rows if _has_aliquots(w.get_data(), OTHER_TYPES)]
        
        if not visible_rows:
            data_rows = []
        else:
            # Build full data with all columns first
            if tab_key == "organ_samples":
                # Static headers for animal identity and sample metadata.
                static_headers = ["IPID", "Species", "ID", "Sex", "Birth", "Death", "Unit"]
                # Use plain abbreviations for organ headers
                organ_labels = [_organ_display(o["key"])[1] for o in ORGANS]
                all_headers = static_headers + organ_labels
                
                full_data_rows = []
                for w in visible_rows:
                    d = w.get_data()
                    # Collect all units from all checked organs
                    all_units = set()
                    for o in ORGANS:
                        k = o["key"]
                        if _str2bool(d.get(f"{k}_checked", False)):
                            try:
                                locs_str = d.get(f"{k}_aliquot_locations", "[]")
                                locs = json.loads(locs_str) if isinstance(locs_str, str) else locs_str
                                for loc in locs:
                                    unit = loc.get("unit", "").strip()
                                    if unit:
                                        all_units.add(unit)
                            except Exception:
                                pass
                    unit_str = ", ".join(sorted(all_units)) if all_units else ""
                    row_cells = [
                        d.get("animal_name", ""),
                        d.get("species", ""),
                        d.get("id", ""), d.get("sex", ""),
                        d.get("birth_date", ""), d.get("death_date", ""),
                        unit_str,
                    ]
                    for o in ORGANS:
                        k = o["key"]
                        if _str2bool(d.get(f"{k}_checked", False)):
                            # Use num_aliquots field directly
                            num_aliquots = d.get(f"{k}_num_aliquots", 0)
                            warning = d.get(f"{k}_warning", False)
                            if warning:
                                # Warning indicator with exclamation mark
                                row_cells.append(f"{num_aliquots}!")
                            else:
                                row_cells.append(str(num_aliquots))
                        else:
                            row_cells.append("")
                    full_data_rows.append(row_cells)
            else:
                # Static headers for animal identity and sample metadata.
                static_headers = ["No.", "IPID", "Species", "ID", "Sex", "Date", "Unit"]
                # Use plain abbreviations for sample type headers
                type_labels = [_type_display(t["key"])[0] for t in OTHER_TYPES]
                all_headers = static_headers + type_labels
                
                full_data_rows = []
                for w in visible_rows:
                    d = w.get_data()
                    # Collect all units from all checked types
                    all_units = set()
                    for t in OTHER_TYPES:
                        k = t["key"]
                        if _str2bool(d.get(f"{k}_checked", False)):
                            try:
                                locs_str = d.get(f"{k}_aliquot_locations", "[]")
                                locs = json.loads(locs_str) if isinstance(locs_str, str) else locs_str
                                for loc in locs:
                                    unit = loc.get("unit", "").strip()
                                    if unit:
                                        all_units.add(unit)
                            except Exception:
                                pass
                    unit_str = ", ".join(sorted(all_units)) if all_units else ""
                    row_cells = [
                        d.get("sample_number", ""),
                        d.get("animal_name", ""),
                        d.get("species", ""), d.get("id", ""),
                        d.get("sex", ""), d.get("collection_date", ""),
                        unit_str,
                    ]
                    for t in OTHER_TYPES:
                        k = t["key"]
                        if _str2bool(d.get(f"{k}_checked", False)):
                            # Use num_aliquots field directly
                            num_aliquots = d.get(f"{k}_num_aliquots", 0)
                            warning = d.get(f"{k}_warning", False)
                            if warning:
                                # Warning indicator with exclamation mark
                                row_cells.append(f"{num_aliquots}!")
                            else:
                                row_cells.append(str(num_aliquots))
                        else:
                            row_cells.append("")
                    full_data_rows.append(row_cells)
            
            # Determine which columns have data (non-empty values)
            def _col_has_data(col_idx, rows):
                for row in rows:
                    val = row[col_idx] if col_idx < len(row) else ""
                    if val and str(val).strip():
                        return True
                return False
            
            # Find indices of columns with data
            data_col_indices = [i for i in range(len(all_headers)) if _col_has_data(i, full_data_rows)]
            
            # Build filtered headers and data rows
            headers = [all_headers[i] for i in data_col_indices]
            data_rows = [headers]
            for full_row in full_data_rows:
                filtered_row = [full_row[i] for i in data_col_indices]
                data_rows.append(filtered_row)

        def _header_footer(canvas: Canvas, doc):
            canvas.saveState()
            # Header on each page
            canvas.setFont('Helvetica-Bold', 10)
            canvas.drawString(margin, page_height - margin - 10, title_text)
            canvas.setFont('Helvetica', 8)
            header_y = page_height - margin - 22
            date_str = f"{_msg(m, 'sample_track.pdf.export_date', 'Date:')} {datetime.now().strftime(DATE_FORMAT + ' %H:%M')}"
            canvas.drawString(margin, header_y, date_str)
            if user:
                user_str = f"{_msg(m, 'sample_track.pdf.exported_by', 'Exported by:')} {user}"
                canvas.drawString(margin, header_y - 12, user_str)
            # Page number
            canvas.setFont('Helvetica', 8)
            page_num_text = f"Page {doc.page}"
            canvas.drawRightString(page_width - margin, margin, page_num_text)
            canvas.restoreState()

        doc = BaseDocTemplate(
            path,
            pagesize=page_size,
            leftMargin=margin,
            rightMargin=margin,
            topMargin=margin + 20,  # Extra space for header
            bottomMargin=margin + 15,  # Space for footer
        )
        # Position frame to respect topMargin (leave room for header) and bottomMargin
        top_margin = margin + 90
        bottom_margin = margin + 15
        frame_height = page_height - top_margin - bottom_margin
        frame = Frame(
            margin, bottom_margin,
            usable_width, frame_height,
            id='table_frame'
        )
        template = PageTemplate(id='sample_template', frames=frame, onPage=_header_footer)
        doc.addPageTemplates([template])

        story = []

        # Calculate ID column width based on visible rows
        if visible_rows:
            max_id_len = max(len(str(d.get("id", ""))) for w in visible_rows for d in [w.get_data()])
            id_col_width = max(10*mm, min(25*mm, (max_id_len * 2.5 + 5)*mm))
            max_ipid_len = max(len(str(d.get("animal_name", ""))) for w in visible_rows for d in [w.get_data()])
            ipid_col_width = max(45*mm, min(85*mm, (max_ipid_len * 1.7 + 8)*mm))
        else:
            id_col_width = 15*mm
            ipid_col_width = 45*mm

        if not visible_rows:
            story.append(Spacer(1, 20))
            story.append(Paragraph(
                _msg(m, "sample_track.pdf.no_data", "No samples to display."),
                styles["Normal"]))
        else:
            # Calculate column widths to fit within page. Header mapping keeps
            # widths correct when empty static columns are filtered out.
            if tab_key == "organ_samples":
                fixed_width_by_header = {
                    "IPID": ipid_col_width,
                    "Species": 10*mm,
                    "ID": id_col_width,
                    "Sex": 12*mm,
                    "Birth": 18*mm,
                    "Death": 18*mm,
                    "Unit": 22*mm,
                }
            else:
                fixed_width_by_header = {
                    "No.": 10*mm,
                    "IPID": ipid_col_width,
                    "Species": 10*mm,
                    "ID": id_col_width,
                    "Sex": 12*mm,
                    "Date": 18*mm,
                    "Unit": 22*mm,
                }

            fixed_widths = [
                fixed_width_by_header[header]
                for header in data_rows[0]
                if header in fixed_width_by_header
            ]
            num_tissue = sum(1 for header in data_rows[0] if header not in fixed_width_by_header)
            fixed_total = sum(fixed_widths)

            if num_tissue > 0:
                min_tissue_width = 10 * mm  # Minimum 10mm to fit abbreviations
                available_for_tissue = usable_width - fixed_total
                tissue_width = max(available_for_tissue / num_tissue, min_tissue_width)
                tissue_width = min(tissue_width, 15 * mm)  # Cap at 15mm to keep table compact
            else:
                tissue_width = 0

            col_widths = [
                fixed_width_by_header.get(header, max(tissue_width, 8*mm))
                for header in data_rows[0]
            ]
            col_widths = [max(w, 5*mm) for w in col_widths]

            tbl = Table(data_rows, colWidths=col_widths, repeatRows=1)
            style = TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.black),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7),  # Smaller font for headers
                ("LEFTPADDING", (0, 0), (-1, -1), 2),  # Minimal padding
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTSIZE", (0, 1), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 1), (-1, -1), "CENTER"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.lightyellow]),
                ("WORDWRAP", (0, 0), (-1, -1), True),
            ])
            tbl.setStyle(style)
            story.append(tbl)

        doc.build(story)
        from Plugins.core.institution_branding import brand_generated_pdf
        brand_generated_pdf(app, path)
        QMessageBox.information(
            parent_widget,
            "PDF",
            _msg(m, "sample_track.info.pdf_saved", "PDF saved to: {path}").replace("{path}", path))

        if mt and hasattr(mt, 'audit'):
            mt.audit("sample_track.export_pdf", tab_label, f"path={path}")

    except ImportError:
        QMessageBox.critical(
            parent_widget, "Error",
            "reportlab is not installed. Cannot export PDF.")
    except Exception as exc:
        QMessageBox.critical(
            parent_widget, "Error",
            _msg(m, "sample_track.error.pdf_failed", "PDF export failed: {error}").replace("{error}", str(exc)))
        logger.error("PDF export error: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# SampleTrackWindow
# ---------------------------------------------------------------------------

class SampleTrackWindow(QDialog):
    def __init__(self, app, messages: Dict, organ_store: JsonStore,
                 other_store: JsonStore, parent=None):
        super().__init__(parent)
        self._app = app
        m = messages
        self.setWindowTitle(_msg(m, "sample_track.window_title", "Sample Track"))
        self.setMinimumSize(1100, 500)
        self.setMaximumWidth(1100)
        self.resize(1100, 700)
        self.setModal(False)
        _icon_path = Path(__file__).parent.parent.parent / "icons" / "progtrack_icon.ico"
        if _icon_path.exists():
            self.setWindowIcon(QIcon(str(_icon_path)))

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        self.organ_tab = OrganSamplesTab(app, m, organ_store)
        self.other_tab = OtherSamplesTab(app, m, other_store)

        tabs.addTab(self.organ_tab,
                    _msg(m, "sample_track.tab.organ_samples", "Organ Samples"))
        tabs.addTab(self.other_tab,
                    _msg(m, "sample_track.tab.other_samples", "Other Samples"))

        settings = QSettings("ProgTrack", "Sample_Track")
        geom = settings.value("geometry")
        if geom:
            try:
                self.restoreGeometry(geom)
            except Exception:
                pass

    def closeEvent(self, event):
        settings = QSettings("ProgTrack", "Sample_Track")
        settings.setValue("geometry", self.saveGeometry())
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# SampleTrackPlugin
# ---------------------------------------------------------------------------

class SampleTrackPlugin:
    def __init__(self, app):
        self.app = app
        self.plugin_dir = PLUGIN_DIR
        self._window: Optional[SampleTrackWindow] = None
        self._organ_store = JsonStore(None, app.backend, "organs")
        self._other_store = JsonStore(None, app.backend, "other")

    @property
    def _messages(self) -> Dict:
        return getattr(self.app, 'messages', {}) or {}

    def show_window(self):
        _load_lookups(getattr(self.app, 'lang', 'en'))
        if self._window is None or not self._window.isVisible():
            self._window = SampleTrackWindow(
                self.app, self._messages,
                self._organ_store, self._other_store)
        self._window.show()
        self._window.raise_()
        self._window.activateWindow()

    def notify_blood_sample(self, animal_name: str, collection_date: str,
                            sample_number: str = "") -> None:
        """Called when a new blood (Progesteron) measurement is added."""
        self._upsert_other("plasma", animal_name, collection_date, sample_number)

    def notify_urine_sample(self, animal_name: str, collection_date: str,
                            sample_number: str = "") -> None:
        """Called when a new PdG (urine) measurement is added."""
        self._upsert_other("urin", animal_name, collection_date, sample_number)

    def _upsert_other(self, type_key: str, animal_name: str,
                      collection_date: str, sample_number: str = "") -> None:
        rows = self._other_store.read()
        found = None
        # Match by sample_number if provided, otherwise by (animal_name, collection_date)
        if sample_number:
            for r in rows:
                if r.get("sample_number") == sample_number:
                    found = r
                    break
        else:
            for r in rows:
                if (r.get("animal_name") == animal_name and
                    r.get("collection_date") == collection_date):
                    found = r
                    break
        if found is None:
            rec = (getattr(self.app, 'animals', {}).get(animal_name)
                   or getattr(self.app, 'archived', {}).get(animal_name, {}))
            # Progesterone (plasma) and urine samples always come from females
            sex_value = "Female" if type_key in ("plasma", "urin") else _record_sex_value(rec)
            new_row: Dict[str, Any] = {
                "sample_number": sample_number,
                "animal_name": animal_name,
                "species": _species_abbreviation(rec.get("species", "")),
                "id": rec.get("id", ""),
                "sex": sex_value,
                "collection_date": collection_date,
                "saved": True,
                f"{type_key}_checked": True,
                f"{type_key}_num_aliquots": 1,
                f"{type_key}_aliquot_locations": json.dumps([{"unit": "", "storage": ""}]),
            }
            rows.append(new_row)
        else:
            # Update found row - if sample_number was empty and now provided, populate it
            if sample_number and not found.get("sample_number"):
                found["sample_number"] = sample_number
            if not _str2bool(found.get(f"{type_key}_checked", False)):
                found[f"{type_key}_checked"] = True
                if not found.get(f"{type_key}_aliquot_locations"):
                    found[f"{type_key}_num_aliquots"] = 1
                    found[f"{type_key}_aliquot_locations"] = json.dumps(
                        [{"unit": "", "storage": ""}])
        self._other_store.write(rows)
        # Refresh open window
        if self._window and self._window.isVisible():
            self._window.other_tab.refresh()
