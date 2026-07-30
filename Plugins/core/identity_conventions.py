"""Shared Phase-1 identity and lifecycle convention helpers."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from Plugins.core.resource_catalogs import (
    CATALOG_FILES,
    read_catalog,
    read_genotypes,
    resources_path,
    write_catalog,
    write_genotypes,
)


DEFAULT_CONVENTIONS: Dict[str, Any] = {
    "animal_id_pattern": "{species}_{year2}_{sequence:04d}_{sex}_{name}_{origin}",
    "animal_id_components": ["species", "year_short", "count_year", "sex", "name", "origin"],
    "yearly_sequences": {},
    "absolute_sequence": 0,
    "experiment_exit_reasons": [
        "§4 Abs. 3 TierSchG – Organentnahme",
        "§7 Abs. 2 TierSchG – Abbruchkriterien erfüllt",
        "§7 Abs. 2 TierSchG – geplantes Versuchsende erreicht",
        "§7 Abs. 2 TierSchG – im Versuch verstorben",
        "§7 Abs. 2 TierSchG – tierärztliche Indikation",
        "Totgeburt",
    ],
    "death_causes": [],
    "departure_reasons": ["Abgabe", "Verlegung", "Sonstiges"],
    "handover_recipients": [],
}


def conventions_path(app_root: Path) -> Path:
    return app_root / "Plugins" / "core" / "identity_lifecycle_conventions.json"


def load_conventions(path: Path) -> Dict[str, Any]:
    data = dict(DEFAULT_CONVENTIONS)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data.update(raw)
        except (OSError, ValueError, TypeError):
            pass
    for key in (
        "experiment_exit_reasons",
        "death_causes",
        "departure_reasons",
        "handover_recipients",
    ):
        if not isinstance(data.get(key), list):
            data[key] = list(DEFAULT_CONVENTIONS[key])
    if not isinstance(data.get("yearly_sequences"), dict):
        data["yearly_sequences"] = {}
    if not isinstance(data.get("animal_id_components"), list):
        data["animal_id_components"] = list(DEFAULT_CONVENTIONS["animal_id_components"])
    aliases = {"year": "year_short", "count": "count_year"}
    data["animal_id_components"] = [
        aliases.get(str(value), str(value)) for value in data["animal_id_components"]
    ]
    if "origin" not in data["animal_id_components"]:
        data["animal_id_components"].append("origin")
    data["animal_id_pattern"] = pattern_from_components(
        data["animal_id_components"]
    )
    # Existing installations used one free-text default. Seed the new controlled
    # catalogue once, without retaining compatibility UI or migration logic.
    legacy_origin = str(data.get("default_offspring_origin") or "").strip()

    app_root = Path(path).parents[2]
    for key in CATALOG_FILES:
        catalog_path = resources_path(app_root) / CATALOG_FILES[key]
        if key != "species" and catalog_path.is_file():
            data[key] = read_catalog(app_root, key)
    data["species"] = read_catalog(app_root, "species")
    if not data.get("animal_origins") and legacy_origin:
        data["animal_origins"] = [legacy_origin]
    data["genotypes"] = read_genotypes(app_root)
    return data


def save_conventions(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_CONVENTIONS)
    merged.update(dict(data))
    for catalog_key in (*CATALOG_FILES, "genotypes"):
        merged.pop(catalog_key, None)
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    app_root = Path(path).parents[2]
    for key in CATALOG_FILES:
        if key in data:
            write_catalog(app_root, key, data.get(key) or [])
    if "genotypes" in data and isinstance(data.get("genotypes"), dict):
        write_genotypes(app_root, data.get("genotypes") or {})


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
) -> str:
    """Render an unsaved ID with explicit placeholders for missing values."""
    component_values = {
        "species": species_token(species).lower() if str(species).strip() else "XX",
        "year_short": birth_year_token(birth_date) if re.search(r"\d{4}", str(birth_date)) else "XX",
        "year_full": birth_year_full_token(birth_date) if re.search(r"\d{4}", str(birth_date)) else "XXXX",
        "count_year": "XXXX",
        "count_absolute": "XXXX",
        "year": birth_year_token(birth_date) if re.search(r"\d{4}", str(birth_date)) else "XX",
        "count": "XXXX",
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
        return str(pattern or DEFAULT_CONVENTIONS["animal_id_pattern"]).format(**values)
    except (KeyError, ValueError, IndexError):
        return DEFAULT_CONVENTIONS["animal_id_pattern"].format(**values)


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
