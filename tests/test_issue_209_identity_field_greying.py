"""Regression coverage for denied ID/chip field visual state (#209)."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QFormLayout, QWidget

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "progtrack_issue_209", ROOT / "ProgTrack.v.0.3.0.py"
)
MAIN = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MAIN)


def _app_with_permissions(granted: set[str]):
    app = MAIN.ProgTrackApp.__new__(MAIN.ProgTrackApp)
    app.messages = {}
    app._master_can = lambda permission: permission in granted
    app._load_identity_conventions = lambda: {"animal_origins": ["Lab"]}
    return app


def _build_row(granted: set[str]):
    app = _app_with_permissions(granted)
    host = QWidget()
    form = QFormLayout(host)
    fields = app._build_id_chip_origin_row(
        form,
        {
            "id": "mm_24_0001_M_Test",
            "chip_nr": "CHIP-0001",
            "origin": "Lab",
            "name": "Test",
            "species": "Callithrix jacchus",
            "birth_date": "01.01.2024",
            "ipid": "Test | Callithrix jacchus | 01.01.2024 | Lab",
        },
    )
    return host, fields


def test_denied_identity_and_chip_fields_are_read_only_and_greyed() -> None:
    qt = QApplication.instance() or QApplication([])
    host, (id_le, chip_le, _origin_le) = _build_row(set())

    assert id_le.isReadOnly()
    assert chip_le.isReadOnly()
    assert "background: #f0f0f0" in id_le.styleSheet()
    assert "color: #666" in id_le.styleSheet()
    assert "background: #f0f0f0" in chip_le.styleSheet()
    assert "color: #666" in chip_le.styleSheet()
    assert id_le.text() == "mm_24_0001_M_Test"
    assert chip_le.text() == "CHIP-0001"

    host.deleteLater()
    qt.processEvents()


def test_mixed_permissions_grey_only_the_denied_control() -> None:
    qt = QApplication.instance() or QApplication([])

    host_id, (id_le, chip_le, _origin_le) = _build_row(
        {"core.edit_animal_identity"}
    )
    assert not id_le.isReadOnly()
    assert "background: #f0f0f0" not in id_le.styleSheet()
    assert chip_le.isReadOnly()
    assert "background: #f0f0f0" in chip_le.styleSheet()

    host_chip, (id_le, chip_le, _origin_le) = _build_row(
        {"core.edit_animal_core"}
    )
    assert id_le.isReadOnly()
    assert "background: #f0f0f0" in id_le.styleSheet()
    assert chip_le.isReadOnly()
    assert "background: #f0f0f0" in chip_le.styleSheet()

    host_id.deleteLater()
    host_chip.deleteLater()
    qt.processEvents()


def test_allowed_identity_and_chip_fields_keep_normal_appearance() -> None:
    qt = QApplication.instance() or QApplication([])
    host, (id_le, chip_le, _origin_le) = _build_row(
        {"core.edit_animal_identity", "core.edit_animal_core"}
    )

    assert not id_le.isReadOnly()
    assert not chip_le.isReadOnly()
    assert "background: #f0f0f0" not in id_le.styleSheet()
    assert "background: #f0f0f0" not in chip_le.styleSheet()

    host.deleteLater()
    qt.processEvents()
