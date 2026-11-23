# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.2.3 or newer.
"""Shared Phase-1 identity and lifecycle convention helpers."""

from __future__ import annotations

import copy
import re
from datetime import date
from typing import Any, Dict, Iterable, Mapping, Optional

DEFAULT_ANIMAL_ID_PATTERN = "{species}_{year2}_{sequence:04d}_{sex}_{name}_{origin}"


ID_COMPONENT_PATTERNS = {
    "species": "{species}",
    "year": "{year}",
    "count": "{sequence:04d}",
    "year_short": "{year2}",
    "year_full": "{year4}",
    "count_year": "{sequence:04d}",
    "count_absolute": "{absolute_sequence:04d}",
    "sex": "{sex}",
    "name": "{name}",
    "origin": "{origin}",
}


def pattern_from_components(components: Iterable[str]) -> str:
    tokens = [
        ID_COMPONENT_PATTERNS[str(component)]
        for component in components
        if str(component) in ID_COMPONENT_PATTERNS
    ]
    return "_".join(tokens)


def preview_animal_id(
    components: Iterable[str],
    *,
    name: str = "",
    species: str = "",
    birth_date: str = "",
    sex: str = "",
    origin: str = "",
    sequence: Optional[int] = None,
    absolute_sequence: Optional[int] = None,
) -> str:
    """Render an unsaved ID with explicit placeholders for missing values."""
    component_values = {
        "species": species_token(species).lower() if str(species).strip() else "XX",
        "year_short": birth_year_token(birth_date) if re.search(r"\d{4}", str(birth_date)) else "XX",
        "year_full": birth_year_full_token(birth_date) if re.search(r"\d{4}", str(birth_date)) else "XXXX",
        "count_year": f"{int(sequence):04d}" if sequence is not None else "XXXX",
        "count_absolute": (
            f"{int(absolute_sequence):04d}"
            if absolute_sequence is not None else "XXXX"
        ),
        "year": birth_year_token(birth_date) if re.search(r"\d{4}", str(birth_date)) else "XX",
        "count": f"{int(sequence):04d}" if sequence is not None else "XXXX",
        "sex": sex_token(sex) if str(sex).strip() else "XX",
        "name": _token(name, "XXXX") if str(name).strip() else "XXXX",
        "origin": _token(origin, "XXXX") if str(origin).strip() else "XXXX",
    }
    return "_".join(
        component_values[str(component)]
        for component in components
        if str(component) in component_values
    )


def _token(value: Any, fallback: str = "unknown") -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip())
    return text.strip("_") or fallback


def species_token(species: str) -> str:
    words = re.findall(r"[A-Za-z]+", str(species or ""))
    if not words:
        return "xx"
    return "".join(word[0].lower() for word in words[:3])


def sex_token(sex: str) -> str:
    value = str(sex or "").strip().casefold()
    if value in {"male", "m", "männlich", "maennlich"}:
        return "M"
    if value in {"female", "f", "weiblich"}:
        return "F"
    return "U"


def birth_year_token(birth_date: str, today: Optional[date] = None) -> str:
    match = re.search(r"(\d{4})", str(birth_date or ""))
    year = int(match.group(1)) if match else (today or date.today()).year
    return f"{year % 100:02d}"


def birth_year_full_token(birth_date: str, today: Optional[date] = None) -> str:
    match = re.search(r"(\d{4})", str(birth_date or ""))
    year = int(match.group(1)) if match else (today or date.today()).year
    return f"{year:04d}"


def render_animal_id(
    pattern: str,
    *,
    name: str,
    species: str,
    birth_date: str,
    sex: str,
    origin: str,
    sequence: int,
    absolute_sequence: Optional[int] = None,
) -> str:
    values = {
        "name": _token(name),
        "species": species_token(species),
        "year": birth_year_token(birth_date),
        "year2": birth_year_token(birth_date),
        "year4": birth_year_full_token(birth_date),
        "sex": sex_token(sex),
        "origin": _token(origin),
        "sequence": int(sequence),
        "absolute_sequence": int(absolute_sequence if absolute_sequence is not None else sequence),
    }
    try:
        return str(pattern or DEFAULT_ANIMAL_ID_PATTERN).format(**values)
    except (KeyError, ValueError, IndexError):
        return DEFAULT_ANIMAL_ID_PATTERN.format(**values)


def next_generated_id(
    conventions: Dict[str, Any],
    *,
    name: str,
    species: str,
    birth_date: str,
    sex: str,
    origin: str,
    existing_ids: Iterable[str] = (),
) -> str:
    year = birth_year_token(birth_date)
    sequences = conventions.setdefault("yearly_sequences", {})
    sequence = max(0, int(sequences.get(year, 0)))
    absolute = max(0, int(conventions.get("absolute_sequence", 0)))
    existing = {str(item) for item in existing_ids if item}
    while True:
        sequence += 1
        absolute += 1
        candidate = render_animal_id(
            str(conventions.get("animal_id_pattern") or ""),
            name=name,
            species=species,
            birth_date=birth_date,
            sex=sex,
            origin=origin,
            sequence=sequence,
            absolute_sequence=absolute,
        )
        if candidate not in existing:
            sequences[year] = sequence
            conventions["absolute_sequence"] = absolute
            return candidate


def preview_next_generated_id(
    conventions: Mapping[str, Any],
    *,
    name: str = "",
    species: str = "",
    birth_date: str = "",
    sex: str = "",
    origin: str = "",
    existing_ids: Iterable[str] = (),
) -> str:
    """Render the next generated ID without mutating or reserving counters.

    The same collision logic used by the final allocator runs against a deep
    copy.  Missing descriptive fields retain the preview placeholders while
    the yearly and absolute count segments show their real next values.
    """
    simulated = copy.deepcopy(dict(conventions or {}))
    next_generated_id(
        simulated,
        name=name,
        species=species,
        birth_date=birth_date,
        sex=sex,
        origin=origin,
        existing_ids=existing_ids,
    )
    year = birth_year_token(birth_date)
    return preview_animal_id(
        simulated.get("animal_id_components", []),
        name=name,
        species=species,
        birth_date=birth_date,
        sex=sex,
        origin=origin,
        sequence=int(simulated.get("yearly_sequences", {}).get(year, 0)),
        absolute_sequence=int(simulated.get("absolute_sequence", 0)),
    )


def regenerated_id_for_edit(
    conventions: Mapping[str, Any],
    old_record: Mapping[str, Any],
    new_record: Mapping[str, Any],
) -> str:
    """Regenerate a convention-managed ID while preserving manual overrides."""
    old_id = str(old_record.get("id") or "")
    old_sequence = int(old_record.get("generated_id_sequence") or 0)
    if not old_sequence:
        return old_id
    return render_animal_id(
        str(conventions.get("animal_id_pattern") or ""),
        name=str(new_record.get("_base_name") or new_record.get("name") or ""),
        species=str(new_record.get("species") or ""),
        birth_date=str(new_record.get("birth_date") or ""),
        sex=str(new_record.get("sex") or ""),
        origin=str(new_record.get("origin") or ""),
        sequence=old_sequence,
        absolute_sequence=int(old_record.get("generated_id_absolute_sequence") or old_sequence),
    )


def relationship_candidates(
    animals: Mapping[str, Mapping[str, Any]],
    *,
    required_sex: str = "",
    species: str = "",
    exclude_id: str = "",
) -> list[str]:
    expected = sex_token(required_sex) if required_sex else ""
    expected_species = str(species or "").strip().casefold()
    excluded = str(exclude_id or "").strip()
    result = []
    for animal_id, record in animals.items():
        if str(animal_id) == excluded:
            continue
        if record.get("archived") or record.get("death_date") or record.get("sterbedatum"):
            continue
        if expected_species and str(record.get("species") or "").strip().casefold() != expected_species:
            continue
        if expected and (expected == "U" or sex_token(str(record.get("sex") or "")) != expected):
            continue
        result.append(str(animal_id))
    return sorted(result, key=str.casefold)


def relationship_display_label(animal_id: str, record: Mapping[str, Any]) -> str:
    """Build the human label while keeping the stable IPID out of display text."""
    name = str(record.get("_base_name") or record.get("name") or animal_id).strip()
    public_id = str(record.get("id") or "").strip()
    if public_id and public_id != name:
        return f"{name} ({public_id})"
    return name
