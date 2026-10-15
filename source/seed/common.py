from __future__ import annotations
import copy
import hashlib
import json
import re
import unicodedata
from datetime import date, datetime, timedelta
from functools import lru_cache
from itertools import permutations
from pathlib import Path
from typing import Any
from Plugins.core.animal_identity import animal_identity_key
from Plugins.core.animal_relationships import resolve_animal_reference
from Plugins.core.animal_roles import SCHEMA_VERSION as ROLE_SCHEMA_VERSION, canonical_role_value, normalize_role_definition
from Plugins.core.experimental_limits import KNOWN_LIMIT_FIELDS, default_experimental_limits, synchronize_experimental_limits
from Plugins.core.identity_conventions import render_animal_id
from .config import *


def normalize_example_animal_name(value: Any) -> str:
    """Return the ASCII-compatible display name used by fictional examples.

    The example dataset is deliberately portable across database clients and
    release environments. Unicode letters are transliterated by decomposing
    their accents (for example ``Eärendil`` -> ``Earendil``); punctuation,
    spaces, and the spelling of the name itself are retained.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(character)
    )


def normalized_example_public_id(
    old_id: Any,
    *,
    name: str,
    species: str,
    birth_date: str,
    sex: str,
    sequence: Any,
    absolute_sequence: Any,
) -> str:
    """Regenerate a changed example ID while preserving its seed counters.

    Existing fictional seed IDs use the five-component public form
    ``species_year_sequence_sex_name``. Keep that established shape and
    change only the name token; the immutable four-part IPID is rebuilt by
    :func:`animal_identity_key` separately.
    """
    old_text = str(old_id or "").strip()
    try:
        yearly = int(sequence or 0)
    except (TypeError, ValueError):
        yearly = 0
    if not old_text or not yearly:
        return old_text
    try:
        absolute = int(absolute_sequence or yearly)
    except (TypeError, ValueError):
        absolute = yearly
    return render_animal_id(
        "{species}_{year2}_{sequence:04d}_{sex}_{name}",
        name=name,
        species=species,
        birth_date=birth_date,
        sex=sex,
        origin="",
        sequence=yearly,
        absolute_sequence=absolute,
    )


def seeded_animal_role_configuration() -> dict[str, Any]:
    """Return the canonical Role Setup defaults stored in a fresh backend.

    Role Setup is mutable facility configuration, but a new example database
    must open with the reviewed SVG choices immediately.  The static catalog
    is normalized through the same schema code used at runtime; no legacy
    JSON fallback is introduced.
    """
    raw = load_input("animal-roles")
    roles = raw.get("roles", []) if isinstance(raw, dict) else []
    normalized = [
        normalize_role_definition(role, default_order=(index + 1) * 10)
        for index, role in enumerate(roles)
        if isinstance(role, dict)
    ]
    return {
        "schema_version": ROLE_SCHEMA_VERSION,
        "deleted_builtin_values": [],
        "roles": normalized,
    }


def canonical_project_name(value: Any) -> str:
    """Return a supported project name or an empty assignment.

    Project deletion must not leave dangling names in animal records or the
    project history. Unknown/removed projects become an unassigned (empty)
    project value.
    """
    name = str(value or "").strip()
    return name if name in SEED_PROJECTS else ""


def _is_removed_animal_reference(value: Any) -> bool:
    """Recognize an identity, public ID, or bare name for a removed animal."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    first = text.split(" | ", 1)[0].strip()
    if first in REMOVED_ANIMAL_NAMES or text in REMOVED_ANIMAL_NAMES:
        return True
    lowered = text.casefold()
    return any(lowered.endswith("_" + name.casefold()) for name in REMOVED_ANIMAL_NAMES)


def _mapping_identifies_removed_animal(value: dict[str, Any]) -> bool:
    """Return whether a list record is owned by a removed animal.

    Project histories and similar domain payloads store animal records as
    dictionaries inside lists.  Clearing only their ``ipid`` would leave a
    malformed, anonymous row behind, so the whole owned record is removed.
    Multi-animal snapshots (for example ``cage_mates_snapshot``) are not
    treated as owners and are pruned recursively instead.
    """
    for key, item in value.items():
        normalized = str(key).strip().casefold()
        if (
            normalized in _ANIMAL_REFERENCE_FIELDS
            or normalized.endswith("_ipid")
        ) and _is_removed_animal_reference(item):
            return True
    return False


def prune_removed_animal_references(value: Any) -> Any:
    """Remove deleted-animal keys/list values and clear scalar references."""
    if isinstance(value, dict):
        return {
            str(key): prune_removed_animal_references(item)
            for key, item in value.items()
            if not _is_removed_animal_reference(key)
        }
    if isinstance(value, list):
        result = []
        for item in value:
            if _is_removed_animal_reference(item):
                continue
            if isinstance(item, dict) and _mapping_identifies_removed_animal(item):
                continue
            result.append(prune_removed_animal_references(item))
        return result
    if _is_removed_animal_reference(value):
        return ""
    return value


def removed_animal_reference_paths(value: Any, path: str = "$") -> list[str]:
    """Return structured paths that still contain a removed-animal identity."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            if _is_removed_animal_reference(key):
                found.append(child + " (key)")
            found.extend(removed_animal_reference_paths(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(removed_animal_reference_paths(item, f"{path}[{index}]"))
    elif _is_removed_animal_reference(value):
        found.append(path)
    return found


def heritage_dummy_identity_values(value: Any) -> set[str]:
    """Return every identity token belonging to an archived Heritage dummy."""
    if not isinstance(value, dict) or not isinstance(value.get("animals"), dict):
        return set()
    identities: set[str] = set()
    for legacy_key, entry in value["animals"].items():
        if not isinstance(entry, dict) or not bool(entry.get("heritage_only")):
            continue
        for candidate in (
            legacy_key,
            entry.get("ipid"),
            entry.get("id"),
            entry.get("name"),
            entry.get("_base_name"),
            entry.get("display_name"),
        ):
            token = str(candidate or "").strip()
            if token:
                identities.add(token)
    return identities


def heritage_dummy_reference_paths(
    value: Any,
    identities: set[str],
    path: str = "$",
    reference_context: bool = False,
) -> list[str]:
    """Find references to Heritage dummies removed from the shipped seed."""
    if not identities:
        return []
    if isinstance(value, str):
        return [path] if reference_context and value.strip() in identities else []
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}"
            key_token = str(key or "").strip()
            if key_token in identities:
                found.append(child + " (key)")
            normalized_key = key_token.casefold()
            is_reference = (
                normalized_key in _HERITAGE_REFERENCE_FIELDS
                or normalized_key.endswith("_ipid")
            )
            if is_reference:
                found.extend(
                    heritage_dummy_reference_paths(
                        item, identities, child, reference_context=True
                    )
                )
            else:
                found.extend(heritage_dummy_reference_paths(item, identities, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.extend(
                heritage_dummy_reference_paths(
                    item,
                    identities,
                    f"{path}[{index}]",
                    reference_context=reference_context,
                )
            )
    return found


def dated_weights(birth: str, species: str, sex: str) -> list[dict[str, Any]]:
    born = datetime.strptime(birth, "%d.%m.%Y")
    key = species.split()[0]
    if key not in WEIGHT_SPEC:
        raise ValueError(f"No declared seed weight profile for {species}")
    profile = WEIGHT_SPEC[key]
    values = [v if v is not None else profile["adult_female" if sex == "Female" else "adult_male"] for v in profile["values"]]
    return [{"datum": (born + timedelta(days=offset)).isoformat(), "wert": value} for offset, value in zip(profile["offsets"], values)]


def complete_record(record: dict[str, Any], *, name: str, species: str,
                    birth: str, origin: str) -> dict[str, Any]:
    result = copy.deepcopy(record)
    result.update({
        "name": name,
        "_base_name": name,
        "display_name": name,
        "species": species,
        "birth_date": birth,
        "origin": origin,
    })
    result["rolle"] = canonical_role_value(
        result.get("rolle") or "breeding_animal", default="unknown"
    )
    result.setdefault("sex", "Unknown")
    # Authoring records already use the single canonical backend event stream.
    # Legacy event keys are rejected here rather than silently converted.
    canonical_events: list[dict[str, Any]] = []
    for item in result.get("events", []) or []:
        if not isinstance(item, dict):
            raise ValueError(f"Invalid event record in {name}: {item!r}")
        legacy_keys = sorted(key for key in ("typ", "type", "date") if key in item)
        if legacy_keys:
            raise ValueError(
                f"Non-canonical event keys in {name}: {', '.join(legacy_keys)}"
            )
        event_type = str(item.get("event_type") or "").strip()
        event_date = item.get("datum")
        if hasattr(event_date, "isoformat"):
            event_date = event_date.isoformat()
        if not event_type or parse_record_date(event_date) is None:
            raise ValueError(f"Missing or invalid event type/date in {name}: {item!r}")
        event = copy.deepcopy(item)
        event["event_type"] = event_type
        event["datum"] = str(event_date)
        canonical_events.append(event)
    legacy_sections = [key for key in ("op", "pgf", "embryo") if key in result]
    if legacy_sections:
        raise ValueError(
            f"Legacy event arrays in {name}: {', '.join(legacy_sections)}"
        )
    # Authoring inputs can contain the same lifecycle event more than once
    # (for example when an old domain export and the animal record are both
    # merged).  Retain distinct same-day events with different payloads, but
    # drop exact duplicates so the backend and plots cannot double-count them.
    unique_events: list[dict[str, Any]] = []
    seen_event_keys: set[tuple[str, str, str]] = set()
    for event in canonical_events:
        event_type = str(event.get("event_type") or "").strip()
        event_date = str(event.get("datum") or "").strip()
        sample_id = str(event.get("sample_id") or "").strip()
        payload_key = sample_id or json.dumps(
            event, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        key = (event_type, event_date, payload_key)
        if key in seen_event_keys:
            continue
        seen_event_keys.add(key)
        unique_events.append(event)
    # Mouse birth dates are immutable identity fields, not plot events in the
    # sample model.  Keep the mouse timeline focused on procedures and other
    # scientific events; this also removes stale birth rows from archived
    # authoring snapshots when the seed is rebuilt.
    if species == "Mus musculus":
        unique_events = [
            event for event in unique_events
            if str(event.get("event_type") or "").strip().casefold() != "birth"
        ]
    result["events"] = unique_events
    result.setdefault("daten", [])
    result.setdefault("pdg", [])
    result.setdefault("sperm", [])
    result.setdefault("sick", False)
    result.setdefault("abnormal_current", False)
    if not result.get("gewicht"):
        result["gewicht"] = dated_weights(birth, species, result["sex"])
    # Example records are canonicalized in one place.  Use the final measured
    # weight as a more useful species/sex-specific reference where no explicit
    # value was authored, then derive all exposed max_* projections from the
    # role defaults.  Unsupported legacy fields are removed from the seed.
    if not result.get("ref_weight") and result.get("gewicht"):
        last_weight = result["gewicht"][-1]
        if isinstance(last_weight, dict) and last_weight.get("wert") is not None:
            result["ref_weight"] = last_weight.get("wert")
    synchronize_experimental_limits(
        result,
        result.get("rolle", "unknown"),
        prefer_flat=True,
        prune_unsupported=True,
        replace_nonpositive=True,
    )
    return result


def rewrite_references(
    value: Any,
    key_map: dict[str, str],
    _compiled_pattern: re.Pattern[str] | None = None,
) -> Any:
    """Rewrite legacy references in one pass without replacement cascades.

    Sequentially applying every mapping can repeatedly extend an identity
    when a legacy IPID is a prefix of its new four-part IPID (for example an
    origin suffix).  A longest-first alternation replaces each token exactly
    once, keeping seed generation bounded and preserving unrelated text.
    """
    if _compiled_pattern is None:
        alternatives = [
            re.escape(old)
            for old, new in sorted(key_map.items(), key=lambda item: len(item[0]), reverse=True)
            if old and old != new
        ]
        _compiled_pattern = (
            re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(alternatives) + r")(?![A-Za-z0-9])")
            if alternatives
            else re.compile(r"(?!x)x")
        )

    if isinstance(value, str):
        return _compiled_pattern.sub(
            lambda match: key_map.get(match.group(0), match.group(0)), value
        )
    if isinstance(value, list):
        return [rewrite_references(item, key_map, _compiled_pattern) for item in value]
    if isinstance(value, dict):
        return {
            key_map.get(str(key), str(key)): rewrite_references(item, key_map, _compiled_pattern)
            for key, item in value.items()
        }
    return value


def parse_record_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            continue
    return None


def normalize_core():
    core = {"version": "5.0", "settings": {"language": "en", "seed_version": "0.2.1"}, "animals": {}, "archived_animals": {}}
    for section, label in (("animals", "active"), ("archived_animals", "archived")):
        for species in ("callitrix", "macaca", "papio", "mus"):
            for ipid, record in load_input(f"animals-{label}-{species}").items():
                if ipid in core["animals"] or ipid in core["archived_animals"]:
                    raise ValueError(f"Duplicate authored animal: {ipid}")
                if not isinstance(record, dict) or record.get("ipid") != ipid:
                    raise ValueError(f"Invalid authored animal identity: {ipid}")
                if animal_identity_key(record["name"], record["species"], record["birth_date"], record["origin"]) != ipid:
                    raise ValueError(f"Authored animal key does not match its immutable identity: {ipid}")
                core[section][ipid] = record
    return core, {}
