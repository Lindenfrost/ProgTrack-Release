# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Module: Heritage Track parent-field UI helpers.

from __future__ import annotations

from typing import Dict, Any, Tuple

from PyQt6.QtWidgets import QFormLayout, QGroupBox, QLineEdit


def build_parent_group(messages: Dict[str, Any], values: Dict[str, str]) -> Tuple[QGroupBox, Dict[str, QLineEdit]]:
    """Create parent input UI block and return both group and input widgets."""
    group = QGroupBox(messages.get("heritage_track.parents.group", "Parents"))
    layout = QFormLayout(group)

    fields = {
        "egg_donor": QLineEdit(values.get("egg_donor", "")),
        "sperm_donor": QLineEdit(values.get("sperm_donor", "")),
        "surrogate_mother": QLineEdit(values.get("surrogate_mother", "")),
        "surrogate_father": QLineEdit(values.get("surrogate_father", "")),
    }

    layout.addRow(messages.get("heritage_track.field.egg_donor", "Egg Donor:"), fields["egg_donor"])
    layout.addRow(messages.get("heritage_track.field.sperm_donor", "Sperm Donor:"), fields["sperm_donor"])
    layout.addRow(messages.get("heritage_track.field.surrogate_mother", "Surrogate Mother:"), fields["surrogate_mother"])
    layout.addRow(messages.get("heritage_track.field.surrogate_father", "Surrogate Father:"), fields["surrogate_father"])

    return group, fields


def extract_parent_values(fields: Dict[str, QLineEdit]) -> Dict[str, str]:
    """Extract and normalize free-text parent values from parent UI inputs."""
    return {
        "egg_donor": fields.get("egg_donor").text().strip() if fields.get("egg_donor") else "",
        "sperm_donor": fields.get("sperm_donor").text().strip() if fields.get("sperm_donor") else "",
        "surrogate_mother": fields.get("surrogate_mother").text().strip() if fields.get("surrogate_mother") else "",
        "surrogate_father": fields.get("surrogate_father").text().strip() if fields.get("surrogate_father") else "",
    }
