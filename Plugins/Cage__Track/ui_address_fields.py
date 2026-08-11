# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Cage Track address-field UI helpers.

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PyQt6.QtWidgets import QComboBox, QFormLayout, QGroupBox, QVBoxLayout, QWidget


def build_address_group(
    messages: Dict[str, Any],
    current_address: Dict[str, Optional[str]],
    buildings: List[Dict[str, Any]],
    units: List[Dict[str, Any]],
    rooms: List[Dict[str, Any]],
    cages: List[Dict[str, Any]],
) -> Tuple[QGroupBox, Dict[str, QComboBox]]:
    """Create address input UI block with cascading non-editable dropdowns.

    Parameters
    ----------
    messages : dict
        Localization dictionary.
    current_address : dict
        Keys ``building_id``, ``unit_id``, ``room_id``, ``cage_id``.
    buildings, units, rooms, cages : list of dict
        Structure records from CageStore.

    Returns
    -------
    (QGroupBox, dict of QComboBox)
        The group widget and a mapping for all four address levels.
    """
    group = QGroupBox(messages.get("address.label", "Address"))
    group.setObjectName("animalOptionalAddressSection")
    group.setCheckable(True)
    group.setStyleSheet(
        "QGroupBox#animalOptionalAddressSection {"
        " border: 1px solid palette(mid); border-radius: 3px; margin-top: 8px;"
        " padding-top: 4px; }"
        "QGroupBox#animalOptionalAddressSection::indicator {"
        " width: 18px; height: 18px; }"
    )
    has_address = any(v for v in current_address.values() if v)
    group.setChecked(has_address)

    group_layout = QVBoxLayout(group)
    group_layout.setContentsMargins(4, 2, 4, 4)

    content = QWidget()
    inner = QFormLayout(content)
    inner.setContentsMargins(0, 0, 0, 0)

    building_combo = QComboBox()
    building_combo.setEditable(False)
    unit_combo = QComboBox()
    unit_combo.setEditable(False)
    room_combo = QComboBox()
    room_combo.setEditable(False)
    cage_combo = QComboBox()
    cage_combo.setEditable(False)

    fields = {
        "building": building_combo, "unit": unit_combo,
        "room": room_combo, "cage": cage_combo,
    }

    # Placeholder items
    building_combo.addItem(messages.get("address.placeholder.building", "Select building"), None)
    unit_combo.addItem(messages.get("address.placeholder.unit", "Select unit"), None)
    room_combo.addItem(messages.get("address.placeholder.room", "Select room"), None)
    cage_combo.addItem(messages.get("address.placeholder.cage", "Select cage"), None)

    # Populate buildings
    for bld in buildings:
        building_combo.addItem(bld.get("display_name", bld["id"]), bld["id"])

    def _populate_units(building_id: Optional[str]) -> None:
        unit_combo.blockSignals(True)
        unit_combo.clear()
        unit_combo.addItem(messages.get("address.placeholder.unit", "Select unit"), None)
        if building_id:
            for unit in units:
                if unit.get("parent_building_id") == building_id:
                    unit_combo.addItem(unit.get("display_name", unit["id"]), unit["id"])
        unit_combo.blockSignals(False)
        _populate_rooms(None)

    def _populate_rooms(unit_id: Optional[str]) -> None:
        room_combo.blockSignals(True)
        room_combo.clear()
        room_combo.addItem(messages.get("address.placeholder.room", "Select room"), None)
        if unit_id:
            for r in rooms:
                if r.get("parent_unit_id") == unit_id:
                    room_combo.addItem(r.get("display_name", r["id"]), r["id"])
        room_combo.blockSignals(False)
        _populate_cages(None)

    def _populate_cages(room_id: Optional[str]) -> None:
        cage_combo.blockSignals(True)
        cage_combo.clear()
        cage_combo.addItem(messages.get("address.placeholder.cage", "Select cage"), None)
        if room_id:
            for c in cages:
                if c.get("parent_room_id") == room_id and not c.get("is_virtual"):
                    cage_combo.addItem(c.get("display_name", c["id"]), c["id"])
        cage_combo.blockSignals(False)

    def _on_building_changed(index: int) -> None:
        bid = building_combo.currentData()
        _populate_units(bid)

    def _on_unit_changed(index: int) -> None:
        unit_id = unit_combo.currentData()
        _populate_rooms(unit_id)

    def _on_room_changed(index: int) -> None:
        rid = room_combo.currentData()
        _populate_cages(rid)

    building_combo.currentIndexChanged.connect(_on_building_changed)
    unit_combo.currentIndexChanged.connect(_on_unit_changed)
    room_combo.currentIndexChanged.connect(_on_room_changed)

    # Set current values
    cur_bld = current_address.get("building_id")
    cur_unit = current_address.get("unit_id")
    cur_room = current_address.get("room_id")
    cur_cage = current_address.get("cage_id")

    if cur_bld:
        idx = building_combo.findData(cur_bld)
        if idx >= 0:
            building_combo.setCurrentIndex(idx)
    if cur_unit:
        idx = unit_combo.findData(cur_unit)
        if idx >= 0:
            unit_combo.setCurrentIndex(idx)
    if cur_room:
        idx = room_combo.findData(cur_room)
        if idx >= 0:
            room_combo.setCurrentIndex(idx)
    if cur_cage:
        idx = cage_combo.findData(cur_cage)
        if idx >= 0:
            cage_combo.setCurrentIndex(idx)

    inner.addRow(messages.get("address.building", "Building:"), building_combo)
    inner.addRow(messages.get("address.unit", "Unit:"), unit_combo)
    inner.addRow(messages.get("address.room", "Room:"), room_combo)
    inner.addRow(messages.get("address.cage", "Cage:"), cage_combo)

    group_layout.addWidget(content)

    # Toggle content visibility when checkbox is checked/unchecked
    group.toggled.connect(content.setVisible)
    content.setVisible(group.isChecked())

    return group, fields


def extract_address_values(fields: Dict[str, QComboBox]) -> Dict[str, Optional[str]]:
    """Extract selected IDs from address combo boxes."""
    return {
        "building_id": fields["building"].currentData() if fields.get("building") else None,
        "unit_id": fields["unit"].currentData() if fields.get("unit") else None,
        "room_id": fields["room"].currentData() if fields.get("room") else None,
        "cage_id": fields["cage"].currentData() if fields.get("cage") else None,
    }
