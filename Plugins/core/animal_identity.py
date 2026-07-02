# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.1
# Module: shared animal identity helpers.

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping


DATE_FORMAT = "%d.%m.%Y"
IDENTITY_SEPARATOR = " | "
_EMPTY_MARKERS = {"", "none", "null", "nan", "nat"}


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    return "" if text.casefold() in _EMPTY_MARKERS else text


def normalize_name(value: Any) -> str:
    return _clean(value)


def normalize_species(value: Any) -> str:
    return _clean(value)


def split_animal_identity_key(value: Any) -> tuple[str, str, str] | None:
    text = _clean(value)
    parts = [part.strip() for part in text.split(IDENTITY_SEPARATOR)]
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], parts[2]


def normalize_birth_date(value: Any, *, required: bool = False) -> str:
    if isinstance(value, datetime):
        return value.strftime(DATE_FORMAT)
    if isinstance(value, date):
        return value.strftime(DATE_FORMAT)

    text = _clean(value)
    if not text:
        if required:
            raise ValueError("Birth date is required for animal identity.")
        return ""

    for fmt in (DATE_FORMAT, "%d.%m.%y", "%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).strftime(DATE_FORMAT)
        except ValueError:
            pass
    raise ValueError(f"Invalid birth date '{text}'. Expected DD.MM.YYYY.")


def _reject_separator(value: str, field_name: str) -> None:
    if IDENTITY_SEPARATOR.strip() in value:
        raise ValueError(f"{field_name} must not contain '|'.")


def animal_identity_key(name: Any, species: Any, birth_date: Any) -> str:
    base_name = normalize_name(name)
    species_name = normalize_species(species)
    birth = normalize_birth_date(birth_date, required=True)
    if not base_name:
        raise ValueError("Animal name is required.")
    if not species_name:
        raise ValueError("Species is required for animal identity.")
    _reject_separator(base_name, "Animal name")
    _reject_separator(species_name, "Species")
    return IDENTITY_SEPARATOR.join((base_name, species_name, birth))


def animal_base_name(key: Any, record: Mapping[str, Any] | None = None) -> str:
    if isinstance(record, Mapping):
        for field in ("_base_name", "display_name", "name"):
            value = normalize_name(record.get(field))
            if value:
                return value
    parts = split_animal_identity_key(key)
    if parts is not None:
        return parts[0]
    return normalize_name(key)


def animal_identity_label(key: Any, record: Mapping[str, Any] | None = None) -> str:
    if isinstance(record, Mapping):
        base = animal_base_name(key, record)
        species = normalize_species(record.get("species"))
        try:
            birth = normalize_birth_date(record.get("birth_date"), required=False)
        except ValueError:
            birth = ""
        if base and species and birth:
            try:
                return animal_identity_key(base, species, birth)
            except ValueError:
                return normalize_name(key)
    return normalize_name(key)


def resolve_animal_reference_text(
    value: Any,
    *collections: Mapping[str, Mapping[str, Any]],
    target_species: Any = "",
) -> tuple[str, Mapping[str, Any], str]:
    """Resolve user-facing animal text to an animal key.

    Returns (key, record, status). status is:
    - "resolved" for exact IPID, exact identity label, or unique short name
    - "missing" when the value does not match known animals
    - "ambiguous" when a short name matches multiple known animals
    """
    text = normalize_name(value)
    if not text:
        return "", {}, "missing"

    records: dict[str, Mapping[str, Any]] = {}
    for collection in collections:
        if isinstance(collection, Mapping):
            records.update(collection)

    if text in records:
        return text, records.get(text, {}) or {}, "resolved"

    folded = text.casefold()
    label_matches = [
        (key, record)
        for key, record in records.items()
        if animal_identity_label(key, record).casefold() == folded
    ]
    if len(label_matches) == 1:
        return label_matches[0][0], label_matches[0][1] or {}, "resolved"
    if len(label_matches) > 1:
        return "", {}, "ambiguous"

    base_matches = [
        (key, record)
        for key, record in records.items()
        if animal_base_name(key, record).casefold() == folded
    ]
    if len(base_matches) == 1:
        return base_matches[0][0], base_matches[0][1] or {}, "resolved"
    if len(base_matches) > 1:
        species = normalize_species(target_species)
        if species:
            species_matches = [
                (key, record)
                for key, record in base_matches
                if normalize_species((record or {}).get("species")) == species
            ]
            if len(species_matches) == 1:
                return species_matches[0][0], species_matches[0][1] or {}, "resolved"
        return "", {}, "ambiguous"

    return text, {}, "missing"


def animal_identity_tuple(name: Any, species: Any, birth_date: Any) -> tuple[str, str, str]:
    return (
        normalize_name(name).casefold(),
        normalize_species(species).casefold(),
        normalize_birth_date(birth_date, required=False),
    )


def record_identity_tuple(key: Any, record: Mapping[str, Any] | None) -> tuple[str, str, str]:
    if not isinstance(record, Mapping):
        parts = split_animal_identity_key(key)
        if parts is None:
            return animal_identity_tuple(key, "", "")
        try:
            return animal_identity_tuple(*parts)
        except ValueError:
            return animal_identity_tuple(parts[0], parts[1], "")

    base = animal_base_name(key, record)
    species = normalize_species(record.get("species"))
    try:
        birth = normalize_birth_date(record.get("birth_date"), required=False)
    except ValueError:
        birth = ""
    if not species or not birth:
        parts = split_animal_identity_key(key)
        if parts is not None:
            base = base or parts[0]
            species = species or parts[1]
            try:
                birth = birth or normalize_birth_date(parts[2], required=False)
            except ValueError:
                pass
    return animal_identity_tuple(base, species, birth)


def identity_conflict(
    name: Any,
    species: Any,
    birth_date: Any,
    *collections: Mapping[str, Mapping[str, Any]],
    exclude_key: str | None = None,
) -> bool:
    wanted = animal_identity_tuple(name, species, birth_date)
    for collection in collections:
        for key, record in collection.items():
            if exclude_key is not None and key == exclude_key:
                continue
            if record_identity_tuple(key, record) == wanted:
                return True
    return False
