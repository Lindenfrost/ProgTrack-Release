"""Exact project-species assignment rules shared by core and Project Track."""

from __future__ import annotations

from typing import Any, Dict, Mapping, List


def species_match(project_species: Any, animal_species: str) -> bool:
    """Empty project species is unrestricted; otherwise matching is exact."""
    if isinstance(project_species, (list, tuple, set)):
        expected = {str(value).strip() for value in project_species if str(value).strip()}
        return not expected or str(animal_species or "") in expected
    expected = str(project_species or "")
    return not expected or expected == str(animal_species or "")


def project_species_values_for(
    projects: Mapping[str, Mapping[str, Any]],
    project_name: str,
) -> List[str]:
    record = projects.get(str(project_name or ""), {})
    summary = record.get("summary", {}) if isinstance(record, Mapping) else {}
    raw = summary.get("species") if isinstance(summary, Mapping) else None
    if raw in (None, ""):
        raw = record.get("species") if isinstance(record, Mapping) else None
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    value = str(raw or "").strip()
    return [value] if value else []


def project_species_for(
    projects: Mapping[str, Mapping[str, Any]],
    project_name: str,
) -> str:
    values = project_species_values_for(projects, project_name)
    return values[0] if len(values) == 1 else ""




def assignment_allowed(
    projects: Mapping[str, Mapping[str, Any]],
    project_name: str,
    animal_species: str,
) -> bool:
    if not str(project_name or ""):
        return True
    return species_match(project_species_values_for(projects, project_name), animal_species)


def remove_mismatched_assignments(
    animals: Dict[str, Dict[str, Any]],
    project_name: str,
    project_species: str,
) -> list[str]:
    removed: list[str] = []
    for animal_id, record in animals.items():
        if str(record.get("project") or "") != project_name:
            continue
        if species_match(project_species, str(record.get("species") or "")):
            continue
        record["project"] = ""
        record["project_severity"] = ""
        record["severity"] = ""
        removed.append(animal_id)
    return removed
