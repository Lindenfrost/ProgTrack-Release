"""Installation-wide, human-editable identity/lifecycle resource catalogs."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List


CATALOG_FILES = {
    "species": "Species_List.txt",
    "animal_origins": "Animal_Origins.txt",
    "experiment_exit_reasons": "Experiment_Exit_Reasons.txt",
    "death_causes": "Death_Causes.txt",
    "departure_reasons": "Departure_Reasons.txt",
    "handover_recipients": "Handover_Recipients.txt",
}
GENOTYPE_FILE = "Genotype_List.txt"


def resources_path(app_root: Path) -> Path:
    return Path(app_root) / "Plugins" / "Resources"


def _unique_lines(values: Iterable[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for value in values:
        text = str(value or "").strip()
        folded = text.casefold()
        if not text or folded in seen:
            continue
        seen.add(folded)
        result.append(text)
    return result


def read_catalog(app_root: Path, catalog: str) -> List[str]:
    filename = CATALOG_FILES[catalog]
    path = resources_path(app_root) / filename
    try:
        return _unique_lines(
            line for line in path.read_text(encoding="utf-8-sig").splitlines()
            if not line.lstrip().startswith("#")
        )
    except OSError:
        return []


def write_catalog(app_root: Path, catalog: str, values: Iterable[str]) -> None:
    filename = CATALOG_FILES[catalog]
    _atomic_write_lines(resources_path(app_root) / filename, _unique_lines(values))


def read_genotypes(app_root: Path) -> Dict[str, List[str]]:
    """Read tab-separated ``species<TAB>genotype`` records."""
    path = resources_path(app_root) / GENOTYPE_FILE
    result: Dict[str, List[str]] = {}
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return result
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        species, separator, genotype = raw.partition("\t")
        if not separator:
            continue
        species = species.strip()
        genotype = genotype.strip()
        if species and genotype:
            result.setdefault(species, []).append(genotype)
    return {species: _unique_lines(values) for species, values in result.items()}


def write_genotypes(app_root: Path, mapping: Dict[str, Iterable[str]]) -> None:
    lines = ["# Species\tGenotype"]
    for species in sorted(mapping, key=str.casefold):
        for genotype in _unique_lines(mapping[species]):
            lines.append(f"{species}\t{genotype}")
    _atomic_write_lines(resources_path(app_root) / GENOTYPE_FILE, lines)


def _atomic_write_lines(path: Path, values: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.stem + "_", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            for value in values:
                handle.write(str(value).rstrip("\r\n") + "\n")
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
