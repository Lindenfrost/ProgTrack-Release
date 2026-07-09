"""Shared Phase-1 identity and lifecycle convention helpers."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


DEFAULT_CONVENTIONS: Dict[str, Any] = {
    "animal_id_pattern": "{species}_{year}_{sequence:04d}_{sex}_{name}",
    "default_offspring_origin": "",
    "yearly_sequences": {},
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
    return data


def save_conventions(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(DEFAULT_CONVENTIONS)
    merged.update(dict(data))
    path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
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


def render_animal_id(
    pattern: str,
    *,
    name: str,
    species: str,
    birth_date: str,
    sex: str,
    sequence: int,
) -> str:
    values = {
        "name": _token(name),
        "species": species_token(species),
        "year": birth_year_token(birth_date),
        "sex": sex_token(sex),
        "sequence": int(sequence),
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
    existing_ids: Iterable[str] = (),
) -> str:
    year = birth_year_token(birth_date)
    sequences = conventions.setdefault("yearly_sequences", {})
    sequence = max(0, int(sequences.get(year, 0)))
    existing = {str(item) for item in existing_ids if item}
    while True:
        sequence += 1
        candidate = render_animal_id(
            str(conventions.get("animal_id_pattern") or ""),
            name=name,
            species=species,
            birth_date=birth_date,
            sex=sex,
            sequence=sequence,
        )
        if candidate not in existing:
            sequences[year] = sequence
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
        sequence=old_sequence,
    )


def relationship_candidates(
    animals: Mapping[str, Mapping[str, Any]],
    *,
    required_sex: str = "",
) -> list[str]:
    expected = sex_token(required_sex) if required_sex else ""
    result = []
    for animal_id, record in animals.items():
        if record.get("archived") or record.get("death_date") or record.get("sterbedatum"):
            continue
        if expected and (expected == "U" or sex_token(str(record.get("sex") or "")) != expected):
            continue
        result.append(str(animal_id))
    return sorted(result, key=str.casefold)
