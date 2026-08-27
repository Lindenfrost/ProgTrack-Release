"""Canonical built-in experimental-limit defaults.

The application historically kept the values used by statistics and the
animal dialogs in a collection of flat ``max_*`` fields.  This module gives
those fields one shared, role-aware source while retaining the flat fields as
derived projections for the existing UI and report contracts.  Custom
experimental blocks are deliberately preserved in the same mapping but are
not interpreted here.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping


# These are fictional example-data defaults.  They are intentionally kept
# conservative and deterministic; they are not clinical recommendations.
# ``ref_weight`` is a generic fallback.  Seed records may replace it with the
# last plausible species/sex-specific weight while retaining the same key.
DEFAULT_EXPERIMENTAL_LIMITS: Dict[str, Dict[str, float | int]] = {
    "egg_cell_donor": {
        "ref_weight": 450,
        "max_messungen": 100,
        "max_pgf": 12,
        "max_op": 6,
        "max_fsh": 120,
        "recovery_time": 60,
    },
    "surrogate": {
        "ref_weight": 450,
        "max_messungen": 100,
        "max_pgf": 12,
        "max_embryo": 12,
        "max_pregnancies": 6,
        "max_geburten": 5,
        "recovery_time": 60,
    },
    "sperm_donor": {
        "ref_weight": 450,
        "max_spermaproben": 100,
        "recovery_time": 60,
    },
    # Normal offspring intentionally has no procedure/limit block.
    "offspring": {},
    "partner_animal": {"ref_weight": 450},
    "breeding_animal": {
        "ref_weight": 450,
        "max_pregnancies": 6,
        "max_geburten": 5,
    },
    "experimental_animal": {
        "ref_weight": 450,
        "max_op": 6,
        "max_measurements": 100,
    },
    "experimental_offspring": {
        "ref_weight": 450,
        "max_op": 6,
        "max_measurements": 100,
    },
    "unknown": {},
}

# Include the historical ``max_special`` projection as well.  It is no longer
# part of a built-in role recipe, but must be removed from canonical example
# records rather than mistaken for a custom block ID.
KNOWN_LIMIT_FIELDS = frozenset(
    {
        key
        for values in DEFAULT_EXPERIMENTAL_LIMITS.values()
        for key in values
    }
    | {"max_special"}
)


def _canonical_role(role: Any) -> str:
    """Normalize the stable role values without importing the role registry."""
    value = str(role or "").strip()
    aliases = {
        "Spenderin": "egg_cell_donor",
        "spenderin": "egg_cell_donor",
        "female_donor": "egg_cell_donor",
        "egg_donor": "egg_cell_donor",
        "Amme": "surrogate",
        "amme": "surrogate",
        "Samenspender": "sperm_donor",
        "samenspender": "sperm_donor",
        "Nachkomme": "offspring",
        "nachkomme": "offspring",
        "Partnertier": "partner_animal",
        "partnertier": "partner_animal",
        "partner": "partner_animal",
        "Zuchttier": "breeding_animal",
        "zuchttier": "breeding_animal",
        "breeding": "breeding_animal",
        "Versuchstier": "experimental_animal",
        "versuchstier": "experimental_animal",
        "experimental": "experimental_animal",
        "Unbekannt": "unknown",
        "unbekannt": "unknown",
    }
    return aliases.get(value, value)


def default_experimental_limits(role: Any) -> Dict[str, float | int]:
    """Return a fresh copy of defaults for one canonical or legacy role."""
    return deepcopy(DEFAULT_EXPERIMENTAL_LIMITS.get(_canonical_role(role), {}))


def _normalized_value(
    key: str,
    value: Any,
    fallback: float | int,
    *,
    replace_nonpositive: bool = False,
) -> float | int:
    """Coerce a stored default while refusing malformed/negative values."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if number < 0 or (replace_nonpositive and number <= 0):
        return fallback
    if key == "ref_weight":
        return int(number) if number.is_integer() else number
    return int(number)


def normalize_experimental_limit_defaults(
    raw: Any,
    role: Any,
) -> Dict[str, float | int]:
    """Normalize a Role Setup default map and fill omitted built-in values."""
    defaults = default_experimental_limits(role)
    source = raw if isinstance(raw, Mapping) else {}
    result: Dict[str, float | int] = {}
    for key, fallback in defaults.items():
        result[key] = _normalized_value(key, source.get(key, fallback), fallback)
    return result


def synchronize_experimental_limits(
    record: Dict[str, Any],
    role: Any,
    *,
    prefer_flat: bool = False,
    prune_unsupported: bool = False,
    replace_nonpositive: bool = False,
) -> Dict[str, Any]:
    """Synchronize canonical limits and legacy flat projections in one record.

    On load, an existing ``experimental_limits`` map is authoritative; old
    records without a map are promoted from their flat fields.  Dialog saves
    pass ``prefer_flat=True`` because the current controls edit those flat
    projections.  Unknown mapping keys (custom block IDs and retired blocks)
    are always retained so historical records remain readable.
    """
    if not isinstance(record, dict):
        return record
    defaults = default_experimental_limits(role)
    current = record.get("experimental_limits")
    current_map: Dict[str, Any] = dict(current) if isinstance(current, Mapping) else {}
    source: Dict[str, Any] = {}
    if prefer_flat:
        source.update(
            {
                key: record[key]
                for key in defaults
                if key in record and record.get(key) not in (None, "")
            }
        )
        source.update({key: current_map[key] for key in defaults if key not in source and key in current_map})
    elif current_map:
        source.update(current_map)
    else:
        source.update(
            {
                key: record[key]
                for key in defaults
                if key in record and record.get(key) not in (None, "")
            }
        )

    normalized: Dict[str, Any] = {
        key: value for key, value in current_map.items() if key not in KNOWN_LIMIT_FIELDS
    }
    for key, fallback in defaults.items():
        value = _normalized_value(
            key,
            source.get(key, fallback),
            fallback,
            replace_nonpositive=replace_nonpositive,
        )
        normalized[key] = value
        # Existing dialog/report code consumes these projections.  Keeping
        # them derived from the canonical map avoids 1/X drift.
        record[key] = value
    if prune_unsupported:
        for key in KNOWN_LIMIT_FIELDS - set(defaults):
            record.pop(key, None)
    record["experimental_limits"] = normalized
    return record
