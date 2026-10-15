"""Explicit seed input registry; missing or corrupt inputs are hard errors."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path(__file__).resolve().parent


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Required seed source is missing or invalid: {path}: {exc}") from exc


def input_path(name: str, source_root: Path = SOURCE_ROOT) -> Path:
    manifest = read_json(source_root / "manifest.json")
    if manifest.get("schema") != "progtrack-seed-source/1":
        raise ValueError("Unsupported seed authoring manifest schema")
    if name not in manifest["inputs"]:
        raise ValueError(f"Undeclared seed source: {name}")
    entry = manifest["inputs"][name]
    if entry.get("root", "seed") not in {"seed", "repository"}:
        raise ValueError(f"Invalid input root: {name}")
    base = ROOT if entry.get("root") == "repository" else source_root
    target = (base / entry["path"]).resolve()
    if not target.is_relative_to(base.resolve()):
        raise ValueError(f"Seed source escapes its declared root: {name}")
    return target


def load_input(name: str, source_root: Path = SOURCE_ROOT) -> Any:
    path = input_path(name, source_root)
    value = read_json(path)
    expected = list if name in {"samples-organs", "samples-other"} else dict
    if not isinstance(value, expected):
        raise ValueError(f"Invalid seed source shape: {path}: expected {expected.__name__}")
    return value


def source_hashes() -> dict[str, str]:
    manifest = read_json(SOURCE_ROOT / "manifest.json")
    paths = {name: input_path(name) for name in manifest["inputs"]}
    paths["manifest"] = SOURCE_ROOT / "manifest.json"
    paths.update({"code/" + p.name: p for p in SOURCE_ROOT.glob("*.py")})
    return {name: hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for name, p in sorted(paths.items())}


MANIFEST = read_json(SOURCE_ROOT / "manifest.json")
SEED_CREATED = MANIFEST["created_at"]
SEED_PACKAGE_ID = MANIFEST["package_id"]
ELDARION_BIRTH_DATE = date.fromisoformat(MANIFEST["reference_date"])
PROFILES = load_input("profiles")
CANONICAL_USERS = PROFILES["CANONICAL_USERS"]
CANONICAL_ORGANIZATION_UNITS = PROFILES["CANONICAL_ORGANIZATION_UNITS"]
PROJECT_DATA_PROFILES = PROFILES["PROJECT_DATA_PROFILES"]
DEFAULT_HOUSING_CAGES = PROFILES["DEFAULT_HOUSING_CAGES"]
SEED_PROJECTS = frozenset(PROFILES["SEED_PROJECTS"])
SHIRE_MOUSE_LIMIT = PROFILES["SHIRE_MOUSE_LIMIT"]
SHIRE_NAME_MONIKERS = tuple(PROFILES["SHIRE_NAME_MONIKERS"])
RINGBEARER_SURGERY_DATES = PROFILES["RINGBEARER_SURGERY_DATES"]
OTOF_EXPERIMENTAL_ANIMAL_NAMES = frozenset(PROFILES["OTOF_EXPERIMENTAL_ANIMAL_NAMES"])
REMOVED_ANIMAL_NAMES = frozenset(PROFILES["REMOVED_ANIMAL_NAMES"])
SEED_PROJECT_UNIT_ID = "org-tts"
REPRODUCTION = load_input("reproduction")
MOUSE_SPEC = load_input("mouse-colony")
SHIRE_SPEC = load_input("shire")
WEIGHT_SPEC = load_input("weights")
RETRIEVAL_DATES = tuple(date.fromisoformat(next(c["egg_date"] for c in REPRODUCTION["cycles"] if c["donor"] == i)) for i in range(4))
TRANSFER_SCENARIOS = tuple((c["surrogate"], date.fromisoformat(c["transfer_date"]), c["outcome"], c["offspring"]) for c in REPRODUCTION["cycles"] if c["transfer_date"])
HERITAGE_DUMMY_KINDS = frozenset({"direct", "former_core"})
HERITAGE_DUMMY_PERSISTENCE_KINDS = frozenset({"temporary_dummy", "direct_dummy", "former_core_dummy"})
_ANIMAL_REFERENCE_FIELDS = frozenset({"ipid", "animal", "animal_id", "animal_ipid", "occupant_id", "offspring", "sperm_donor", "egg_donor", "surrogate"})
_HERITAGE_REFERENCE_FIELDS = _ANIMAL_REFERENCE_FIELDS | frozenset({"egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father", "mother", "father", "parent", "parent_ipid", "mother_ipid", "father_ipid", "partner", "partner_von", "verpaart_mit", "ziehmutter", "ziehvater"})
__all__ = [name for name in globals() if not name.startswith("_")] + ["_ANIMAL_REFERENCE_FIELDS", "_HERITAGE_REFERENCE_FIELDS"]
