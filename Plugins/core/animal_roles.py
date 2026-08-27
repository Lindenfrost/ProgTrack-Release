"""Configurable animal role registry for ProgTrack."""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

from .experimental_limits import (
    normalize_experimental_limit_defaults,
)


SCHEMA_VERSION = 2

REPRESENTATION_ICON = "icon"
REPRESENTATION_TEXT = "text"
REPRESENTATION_MODES = (REPRESENTATION_ICON, REPRESENTATION_TEXT)

COLOR_MODE_SINGLE = "single"
COLOR_MODE_SEX = "sex"
COLOR_KEYS_BY_MODE = {
    COLOR_MODE_SINGLE: ("default",),
    COLOR_MODE_SEX: ("male", "female", "unknown"),
}

ROLE_VALUE_SPENDER = "egg_cell_donor"
ROLE_VALUE_AMME = "surrogate"
ROLE_VALUE_SAMENSP = "sperm_donor"
ROLE_VALUE_OFFSPRING = "offspring"
ROLE_VALUE_EXPERIMENTAL_OFFSPRING = "experimental_offspring"
ROLE_VALUE_PARTNER = "partner_animal"
ROLE_VALUE_ZUCHTTIER = "breeding_animal"
ROLE_VALUE_EXPERIMENTAL = "experimental_animal"
ROLE_VALUE_UNKNOWN = "unknown"

LEGACY_ROLE_VALUE_MAP: Dict[str, str] = {
    "Spenderin": ROLE_VALUE_SPENDER,
    "spenderin": ROLE_VALUE_SPENDER,
    "female_donor": ROLE_VALUE_SPENDER,
    "egg_donor": ROLE_VALUE_SPENDER,
    "egg_cell_donor": ROLE_VALUE_SPENDER,
    "Amme": ROLE_VALUE_AMME,
    "amme": ROLE_VALUE_AMME,
    "surrogate": ROLE_VALUE_AMME,
    "Samenspender": ROLE_VALUE_SAMENSP,
    "samenspender": ROLE_VALUE_SAMENSP,
    "sperm_donor": ROLE_VALUE_SAMENSP,
    "Nachkomme": ROLE_VALUE_OFFSPRING,
    "nachkomme": ROLE_VALUE_OFFSPRING,
    "offspring": ROLE_VALUE_OFFSPRING,
    "experimental_offspring": ROLE_VALUE_EXPERIMENTAL_OFFSPRING,
    "experimental offspring": ROLE_VALUE_EXPERIMENTAL_OFFSPRING,
    "Partnertier": ROLE_VALUE_PARTNER,
    "partnertier": ROLE_VALUE_PARTNER,
    "partner": ROLE_VALUE_PARTNER,
    "partner_animal": ROLE_VALUE_PARTNER,
    "Zuchttier": ROLE_VALUE_ZUCHTTIER,
    "zuchttier": ROLE_VALUE_ZUCHTTIER,
    "breeding": ROLE_VALUE_ZUCHTTIER,
    "breeding_animal": ROLE_VALUE_ZUCHTTIER,
    "Versuchstier": ROLE_VALUE_EXPERIMENTAL,
    "versuchstier": ROLE_VALUE_EXPERIMENTAL,
    "experimental": ROLE_VALUE_EXPERIMENTAL,
    "experimental_animal": ROLE_VALUE_EXPERIMENTAL,
    "Unbekannt": ROLE_VALUE_UNKNOWN,
    "unbekannt": ROLE_VALUE_UNKNOWN,
    "unknown": ROLE_VALUE_UNKNOWN,
}

ROLE_DISPLAY_LABELS: Dict[str, str] = {
    ROLE_VALUE_SPENDER: "Egg cell donor",
    ROLE_VALUE_AMME: "Surrogate",
    ROLE_VALUE_SAMENSP: "Sperm donor",
    ROLE_VALUE_OFFSPRING: "Offspring",
    ROLE_VALUE_EXPERIMENTAL_OFFSPRING: "Experimental offspring",
    ROLE_VALUE_PARTNER: "Partner animal",
    ROLE_VALUE_ZUCHTTIER: "Breeding animal",
    ROLE_VALUE_EXPERIMENTAL: "Experimental animal",
    ROLE_VALUE_UNKNOWN: "Unknown",
}

ROLE_LABEL_KEYS: Dict[str, str] = {
    ROLE_VALUE_SPENDER: "role.egg_cell_donor",
    ROLE_VALUE_AMME: "role.surrogate",
    ROLE_VALUE_SAMENSP: "role.sperm_donor",
    ROLE_VALUE_OFFSPRING: "role.offspring",
    ROLE_VALUE_EXPERIMENTAL_OFFSPRING: "role.experimental_offspring",
    ROLE_VALUE_PARTNER: "role.partner_animal",
    ROLE_VALUE_ZUCHTTIER: "role.breeding_animal",
    ROLE_VALUE_EXPERIMENTAL: "role.experimental_animal",
    ROLE_VALUE_UNKNOWN: "role.unknown",
}


# Role colors live in the role definition itself. This is the sole fallback
# palette used by every consumer when an older backend record has no explicit
# ``colors`` field yet. Values preserve the effective Phase-1 list colors.
DEFAULT_ROLE_COLORS: Dict[str, Dict[str, str]] = {
    ROLE_VALUE_SPENDER: {"mode": COLOR_MODE_SINGLE, "default": "#FF1493"},
    ROLE_VALUE_AMME: {"mode": COLOR_MODE_SINGLE, "default": "#9370DB"},
    ROLE_VALUE_SAMENSP: {"mode": COLOR_MODE_SINGLE, "default": "#222222"},
    ROLE_VALUE_OFFSPRING: {
        "mode": COLOR_MODE_SEX,
        "male": "#1A1AFF",
        "female": "#FF69B4",
        "unknown": "#808080",
    },
    ROLE_VALUE_EXPERIMENTAL_OFFSPRING: {
        "mode": COLOR_MODE_SEX,
        "male": "#00CCAA",
        "female": "#FF7788",
        "unknown": "#00AAAA",
    },
    ROLE_VALUE_PARTNER: {
        "mode": COLOR_MODE_SEX,
        "male": "#FF8C00",
        "female": "#D2691E",
        "unknown": "#808080",
    },
    ROLE_VALUE_ZUCHTTIER: {
        "mode": COLOR_MODE_SEX,
        "male": "#00008B",
        "female": "#C71585",
        "unknown": "#808080",
    },
    ROLE_VALUE_EXPERIMENTAL: {
        "mode": COLOR_MODE_SEX,
        "male": "#00CCAA",
        "female": "#FF7788",
        "unknown": "#00AAAA",
    },
    ROLE_VALUE_UNKNOWN: {"mode": COLOR_MODE_SINGLE, "default": "#A0A0A0"},
}

DEFAULT_CUSTOM_ROLE_COLORS: Dict[str, str] = {
    "mode": COLOR_MODE_SEX,
    "male": "#3455A4",
    "female": "#A64D79",
    "unknown": "#6F6F6F",
}

_HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def canonical_role_value(value: Any, *, default: str = "") -> str:
    """Return the stable internal role ID for current or legacy role values."""
    text = str(value or "").strip()
    if not text:
        return default
    return LEGACY_ROLE_VALUE_MAP.get(text, text)

REQUIRED_DIALOG_BLOCKS = ("identity", "cage_address", "weight", "parenting")
OPTIONAL_DIALOG_BLOCKS = (
    "id_chip_origin",
    "lifecycle",
    "project_severity",
    "reference_weight",
    "health_flags",
    "limits_reproductive",
    "limits_measurements",
    "recovery_time",
    "blood_progesterone",
    "urine_pdg",
    "computed_values",
    "sperm_measurements",
    "reproductive_events",
    "procedure_events",
    "partner_fields",
    "mating_partner",
    "experimental_fields",
)
ALL_DIALOG_BLOCKS = REQUIRED_DIALOG_BLOCKS + OPTIONAL_DIALOG_BLOCKS

IMPORT_CAPABILITY_BLOCKS: Dict[str, str] = {
    "blood": "blood_progesterone",
    "urine": "urine_pdg",
    "weight": "weight",
    "sperm": "sperm_measurements",
}

GLOBAL_EVENT_CATALOG: Dict[str, Dict[str, Any]] = {
    "surgery": {"label_key": "event.surgery", "planned": True, "limit_block": "max_op"},
    "embryo_transfer": {"label_key": "event.embryo_transfer", "planned": True, "limit_block": "max_embryo"},
    "pregnancy": {"label_key": "event.pregnancy", "planned": False, "limit_block": "max_pregnancies"},
    "pregnancy_verification": {"label_key": "plot.event.pregnancy_verification", "planned": False, "limit_block": ""},
    "abortion": {"label_key": "event.abortion", "planned": False, "limit_block": ""},
    "birth": {"label_key": "event.birth", "planned": False, "limit_block": "max_births"},
    "pgf": {"label_key": "event.pgf", "planned": True, "limit_block": "max_pgf"},
    "fsh": {"label_key": "event.fsh", "planned": True, "limit_block": "max_fsh"},
    "progesterone": {"label_key": "event.progesterone", "planned": False, "limit_block": ""},
    "special_measurement": {"label_key": "event.special_measurement", "planned": True, "limit_block": "max_special"},
    "measurement": {"label_key": "event.measurement", "planned": True, "limit_block": "max_measurements"},
}

DEFAULT_DIALOG_RECIPES: Dict[str, Dict[str, Any]] = {
    "egg_cell_donor": {
        "new": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "limits_reproductive",
            "limits_measurements", "recovery_time", "health_flags", "weight",
            "blood_progesterone", "urine_pdg", "computed_values", "reproductive_events",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "limits_reproductive",
            "limits_measurements", "recovery_time", "health_flags", "weight",
            "blood_progesterone", "urine_pdg", "computed_values", "reproductive_events",
        ],
        "events": ["surgery", "pgf", "fsh", "progesterone"],
    },
    "surrogate": {
        "new": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "limits_reproductive",
            "limits_measurements", "recovery_time", "health_flags", "weight",
            "blood_progesterone", "urine_pdg", "computed_values", "reproductive_events",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "limits_reproductive",
            "limits_measurements", "recovery_time", "health_flags", "weight",
            "blood_progesterone", "urine_pdg", "computed_values", "reproductive_events",
        ],
        "events": ["embryo_transfer", "pregnancy", "abortion", "birth", "pgf", "progesterone"],
    },
    "sperm_donor": {
        "new": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "limits_measurements",
            "recovery_time", "health_flags", "weight", "sperm_measurements",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "limits_measurements",
            "recovery_time", "health_flags", "weight", "sperm_measurements",
        ],
        "events": [],
    },
    "offspring": {
        "new": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "limits_measurements", "health_flags",
            "weight", "procedure_events",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "limits_measurements", "health_flags",
            "weight", "procedure_events",
        ],
        "events": ["special_measurement", "surgery"],
    },
    "partner": {
        "new": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "partner_fields",
            "health_flags", "weight",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "partner_fields",
            "health_flags", "weight",
        ],
        "events": [],
    },
    "breeding": {
        "new": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "mating_partner",
            "limits_reproductive", "health_flags", "weight", "reproductive_events",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "mating_partner",
            "limits_reproductive", "health_flags", "weight", "reproductive_events",
        ],
        "events": ["pregnancy", "abortion", "birth"],
    },
    "experimental": {
        "new": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "limits_measurements",
            "health_flags", "weight", "procedure_events", "experimental_fields",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "limits_measurements",
            "health_flags", "weight", "procedure_events", "experimental_fields",
        ],
        "events": ["surgery", "measurement"],
    },
    "basic": {
        "new": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "health_flags", "weight",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "health_flags", "weight",
        ],
        "events": [],
    },
}

FIELD_PRESET_ALIASES: Dict[str, str] = {
    "female_donor": "egg_cell_donor",
    "spenderin": "egg_cell_donor",
    "amme": "surrogate",
    "samenspender": "sperm_donor",
    "partner_animal": "partner",
    "partnertier": "partner",
    "breeding_animal": "breeding",
    "zuchttier": "breeding",
    "experimental_animal": "experimental",
    "versuchstier": "experimental",
}


def canonical_field_preset(value: Any, *, default: str = "basic") -> str:
    text = str(value or "").strip()
    if not text:
        return default
    return FIELD_PRESET_ALIASES.get(text, text)


DEFAULT_ROLE_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "role_id": ROLE_VALUE_SPENDER,
        "value": ROLE_VALUE_SPENDER,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_SPENDER],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_SPENDER],
        "icon": "role.female",
        "order": 10,
        "active": True,
        "built_in": True,
        "base_editor": "female",
        "field_preset": "egg_cell_donor",
    },
    {
        "role_id": ROLE_VALUE_AMME,
        "value": ROLE_VALUE_AMME,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_AMME],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_AMME],
        "icon": "role.female",
        "order": 20,
        "active": True,
        "built_in": True,
        "base_editor": "female",
        "field_preset": "surrogate",
    },
    {
        "role_id": ROLE_VALUE_SAMENSP,
        "value": ROLE_VALUE_SAMENSP,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_SAMENSP],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_SAMENSP],
        "icon": "role.male",
        "order": 30,
        "active": True,
        "built_in": True,
        "base_editor": "sperm_donor",
        "field_preset": "sperm_donor",
    },
    {
        "role_id": "offspring",
        "value": ROLE_VALUE_OFFSPRING,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_OFFSPRING],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_OFFSPRING],
        "icon": "role.offspring",
        "order": 40,
        "active": True,
        "built_in": True,
        "base_editor": "offspring",
        "field_preset": "offspring",
        "show_new_animal_button": True,
    },
    {
        "role_id": ROLE_VALUE_EXPERIMENTAL_OFFSPRING,
        "value": ROLE_VALUE_EXPERIMENTAL_OFFSPRING,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_EXPERIMENTAL_OFFSPRING],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_EXPERIMENTAL_OFFSPRING],
        "icon": "role.experimental_offspring",
        "order": 45,
        "active": True,
        "built_in": True,
        "base_editor": "experimental_animal",
        "field_preset": "experimental",
        "show_new_animal_button": True,
    },
    {
        "role_id": ROLE_VALUE_PARTNER,
        "value": ROLE_VALUE_PARTNER,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_PARTNER],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_PARTNER],
        "icon": "role.partner",
        "order": 50,
        "active": True,
        "built_in": True,
        "base_editor": "partner",
        "field_preset": "partner",
    },
    {
        "role_id": ROLE_VALUE_ZUCHTTIER,
        "value": ROLE_VALUE_ZUCHTTIER,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_ZUCHTTIER],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_ZUCHTTIER],
        "icon": "role.breeding",
        "order": 60,
        "active": True,
        "built_in": True,
        "base_editor": "breeding_animal",
        "field_preset": "breeding",
    },
    {
        "role_id": ROLE_VALUE_EXPERIMENTAL,
        "value": ROLE_VALUE_EXPERIMENTAL,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_EXPERIMENTAL],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_EXPERIMENTAL],
        "icon": "role.experimental",
        "order": 70,
        "active": True,
        "built_in": True,
        "base_editor": "experimental_animal",
        "field_preset": "experimental",
    },
    {
        "role_id": "unknown",
        "value": ROLE_VALUE_UNKNOWN,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_UNKNOWN],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_UNKNOWN],
        "icon": "",
        "representation_mode": REPRESENTATION_TEXT,
        "representation_text": "●",
        "order": 9990,
        "active": True,
        "built_in": True,
        "base_editor": "basic",
        "field_preset": "basic",
    },
]


_DEFAULTS_BY_VALUE = {role["value"]: role for role in DEFAULT_ROLE_DEFINITIONS}
_BUILTIN_VALUES = {role["value"] for role in DEFAULT_ROLE_DEFINITIONS}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", text.casefold()).strip("_")
    return slug or "role"


def _coerce_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "y", "on"}
    if value is None:
        return default
    return bool(value)


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class RoleOrderValidationError(ValueError):
    """Raised before a malformed/duplicate role order can reach persistence."""

    def __init__(self, kind: str, *, value: Any = None, duplicates: Iterable[int] = ()):
        self.kind = str(kind)
        self.value = value
        self.duplicates = tuple(int(item) for item in duplicates)
        if self.kind == "duplicate":
            message = "Role order values must be unique: " + ", ".join(map(str, self.duplicates))
        else:
            message = f"Role order must be an integer: {value!r}"
        super().__init__(message)


def parse_role_order(value: Any) -> int:
    """Parse one explicit role order without lossy coercion.

    Arbitrarily large Python integers (including the built-in ``9990`` order)
    are valid. Floats, booleans, blanks, and mixed strings are rejected.
    """
    if isinstance(value, bool) or value is None:
        raise RoleOrderValidationError("invalid", value=value)
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not re.fullmatch(r"[+-]?\d+", text):
        raise RoleOrderValidationError("invalid", value=value)
    return int(text)


def validate_role_orders(roles: Iterable[Dict[str, Any]]) -> None:
    """Reject malformed or duplicate role orders before backend writes."""
    seen: Dict[int, int] = {}
    duplicates = set()
    for index, role in enumerate(roles):
        if not isinstance(role, dict):
            continue
        order = parse_role_order(role.get("order"))
        if order in seen:
            duplicates.add(order)
        else:
            seen[order] = index
    if duplicates:
        raise RoleOrderValidationError("duplicate", duplicates=sorted(duplicates))


def validate_role_visual_configuration(role: Dict[str, Any]) -> None:
    """Defensively reject ambiguous representation or invalid explicit colors."""
    if "representation_mode" in role:
        mode = str(role.get("representation_mode") or "").strip().casefold()
        icon = str(role.get("icon") or "").strip()
        text = str(role.get("representation_text") or "").strip()
        if mode == REPRESENTATION_ICON:
            if not icon or text:
                raise ValueError("Icon representation requires one icon and no text value.")
        elif mode == REPRESENTATION_TEXT:
            if not text or icon:
                raise ValueError("Text representation requires one text value and no icon.")
        else:
            raise ValueError(f"Invalid role representation mode: {mode!r}")
    if "colors" in role:
        expected_mode = role_color_mode(
            role.get("value"), base_editor=str(role.get("base_editor") or "basic")
        )
        validate_role_colors(role.get("colors"), mode=expected_mode)


def default_role_colors(value: Any, *, base_editor: str = "basic") -> Dict[str, str]:
    """Return a fresh copy of the canonical fallback colors for one role."""
    value = canonical_role_value(value)
    configured = DEFAULT_ROLE_COLORS.get(value)
    if configured is not None:
        return deepcopy(configured)
    if str(base_editor or "").strip() in {"female", "sperm_donor"}:
        return {"mode": COLOR_MODE_SINGLE, "default": "#555555"}
    return deepcopy(DEFAULT_CUSTOM_ROLE_COLORS)


def role_color_mode(value: Any, *, base_editor: str = "basic") -> str:
    return str(default_role_colors(value, base_editor=base_editor)["mode"])


def _normalized_hex_color(value: Any) -> str:
    text = str(value or "").strip()
    if not _HEX_COLOR_RE.fullmatch(text):
        raise ValueError(f"Invalid role color: {value!r}")
    return text.upper()


def _relative_luminance(color: str) -> float:
    channels = [int(color[index:index + 2], 16) / 255.0 for index in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4 for channel in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def role_color_foreground(color: str) -> str:
    """Choose readable black/white text for a role-color swatch."""
    color = _normalized_hex_color(color)
    luminance = _relative_luminance(color)
    contrast_black = (luminance + 0.05) / 0.05
    contrast_white = 1.05 / (luminance + 0.05)
    return "#000000" if contrast_black >= contrast_white else "#FFFFFF"


def validate_role_colors(colors: Any, *, mode: Optional[str] = None) -> Dict[str, str]:
    """Validate and normalize one role's single source-of-truth palette."""
    if not isinstance(colors, dict):
        raise ValueError("Role colors must be a mapping.")
    actual_mode = str(mode or colors.get("mode") or "").strip()
    if actual_mode not in COLOR_KEYS_BY_MODE:
        raise ValueError(f"Invalid role color mode: {actual_mode!r}")
    normalized: Dict[str, str] = {"mode": actual_mode}
    rgb_values = []
    for key in COLOR_KEYS_BY_MODE[actual_mode]:
        color = _normalized_hex_color(colors.get(key))
        # Every stored color must support readable text through the adaptive
        # black/white foreground used by the Role Builder swatch.
        role_color_foreground(color)
        normalized[key] = color
        rgb_values.append(tuple(int(color[index:index + 2], 16) for index in (1, 3, 5)))
    if actual_mode == COLOR_MODE_SEX:
        for index, first in enumerate(rgb_values):
            for second in rgb_values[index + 1:]:
                distance = sum((left - right) ** 2 for left, right in zip(first, second)) ** 0.5
                if distance < 24.0:
                    raise ValueError("Male, female, and unknown role colors must be distinguishable.")
    return normalized


def normalize_role_colors(raw: Any, value: Any, *, base_editor: str = "basic") -> Dict[str, str]:
    """Load colors safely while retaining canonical defaults for old records."""
    defaults = default_role_colors(value, base_editor=base_editor)
    expected_mode = defaults["mode"]
    if not isinstance(raw, dict):
        return defaults
    candidate = {"mode": expected_mode}
    for key in COLOR_KEYS_BY_MODE[expected_mode]:
        candidate[key] = raw.get(key, defaults[key])
    try:
        return validate_role_colors(candidate, mode=expected_mode)
    except ValueError:
        return defaults


def _sex_color_key(sex: Any) -> str:
    text = str(sex or "").strip().casefold()
    if any(token in text for token in ("female", "weiblich", "femmina", "???")):
        return "female"
    if any(token in text for token in ("male", "m?nnlich", "maschio", "???")):
        return "male"
    return "unknown"


def role_color_for_definition(role: Dict[str, Any], sex: Any = "") -> str:
    """Project one normalized role definition to its effective animal color."""
    role = role if isinstance(role, dict) else {}
    colors = normalize_role_colors(
        role.get("colors"),
        role.get("value"),
        base_editor=str(role.get("base_editor") or "basic"),
    )
    if colors["mode"] == COLOR_MODE_SINGLE:
        return colors["default"]
    return colors[_sex_color_key(sex)]


def role_color_for_record(record: Dict[str, Any], registry: Any = None) -> str:
    """Return the shared configured color for one animal record."""
    record = record if isinstance(record, dict) else {}
    value = canonical_role_value(record.get("rolle"), default=ROLE_VALUE_UNKNOWN)
    if registry is not None and hasattr(registry, "color_for_value"):
        return str(registry.color_for_value(value, record.get("sex", "")))
    role = deepcopy(_DEFAULTS_BY_VALUE.get(value, {"value": value, "base_editor": "basic"}))
    return role_color_for_definition(role, record.get("sex", ""))


def normalize_block_list(values: Any) -> List[str]:
    if isinstance(values, str):
        raw_values = [part.strip() for part in values.split(",")]
    elif isinstance(values, Iterable) and not isinstance(values, (bytes, bytearray, dict)):
        raw_values = [str(value or "").strip() for value in values]
    else:
        raw_values = []

    normalized: List[str] = []
    for block in [*REQUIRED_DIALOG_BLOCKS, *raw_values]:
        if (
            block in ALL_DIALOG_BLOCKS
            or block.startswith("custom_limit:")
            or block.startswith("experimental-block:")
        ) and block not in normalized:
            normalized.append(block)
    return normalized


def import_capabilities_for_blocks(
    blocks: Iterable[str],
    *,
    steroid_active: bool = True,
    has_pdg_plugin: bool = True,
) -> Dict[str, bool]:
    """Return sidebar import capabilities implied by role dialog blocks."""
    block_set = set(normalize_block_list(blocks))
    return {
        "blood": steroid_active and IMPORT_CAPABILITY_BLOCKS["blood"] in block_set,
        "urine": steroid_active and has_pdg_plugin and IMPORT_CAPABILITY_BLOCKS["urine"] in block_set,
        "weight": IMPORT_CAPABILITY_BLOCKS["weight"] in block_set,
        "sperm": steroid_active and IMPORT_CAPABILITY_BLOCKS["sperm"] in block_set,
    }


def default_dialog_blocks(field_preset: str) -> Dict[str, Any]:
    field_preset = canonical_field_preset(field_preset)
    recipe = DEFAULT_DIALOG_RECIPES.get(field_preset, DEFAULT_DIALOG_RECIPES["basic"])
    return {
        "new": normalize_block_list(recipe.get("new", [])),
        "edit": normalize_block_list(recipe.get("edit", [])),
    }


def default_event_recipe(field_preset: str) -> Dict[str, Any]:
    field_preset = canonical_field_preset(field_preset)
    recipe = DEFAULT_DIALOG_RECIPES.get(field_preset, DEFAULT_DIALOG_RECIPES["basic"])
    events = [event for event in recipe.get("events", []) if event in GLOBAL_EVENT_CATALOG]
    return {
        "available_events": events,
        "default_event": events[0] if events else "",
    }


def normalize_role_definition(raw: Dict[str, Any], *, default_order: int = 1000) -> Dict[str, Any]:
    value = canonical_role_value(raw.get("value") or raw.get("role_id") or "")
    label = str(raw.get("label") or "").strip()
    role_id = canonical_role_value(raw.get("role_id") or value or _slugify(label))
    built_in = _coerce_bool(raw.get("built_in"), False)
    base_editor = str(raw.get("base_editor") or "basic").strip() or "basic"
    field_preset = canonical_field_preset(raw.get("field_preset") or "basic")
    dialog_blocks = raw.get("dialog_blocks")
    if isinstance(dialog_blocks, dict):
        normalized_blocks = {
            "new": normalize_block_list(dialog_blocks.get("new", [])),
            "edit": normalize_block_list(dialog_blocks.get("edit", [])),
        }
    else:
        normalized_blocks = default_dialog_blocks(field_preset)

    event_recipe = raw.get("event_recipe")
    if isinstance(event_recipe, dict):
        available_events = [
            str(event or "").strip()
            for event in event_recipe.get("available_events", [])
            if str(event or "").strip() in GLOBAL_EVENT_CATALOG
        ]
        normalized_event_recipe = {
            "available_events": available_events,
            "default_event": str(event_recipe.get("default_event") or "").strip(),
        }
        if normalized_event_recipe["default_event"] not in available_events:
            normalized_event_recipe["default_event"] = available_events[0] if available_events else ""
    else:
        normalized_event_recipe = default_event_recipe(field_preset)

    raw_icon = str(raw.get("icon") if raw.get("icon") is not None else "").strip()
    representation_mode = str(raw.get("representation_mode") or "").strip().casefold()
    representation_text = str(raw.get("representation_text") or "").strip()
    # A manually configured dot was historically stored in the icon field.
    # It is text artwork, not an SVG key; normalize it into the explicit text
    # representation so Role Setup does not present a broken icon preview.
    if representation_mode == REPRESENTATION_ICON and raw_icon == "●":
        representation_mode = REPRESENTATION_TEXT
        representation_text = representation_text or raw_icon
        raw_icon = ""
    if representation_mode not in REPRESENTATION_MODES:
        # Legacy role definitions rendered their non-empty icon first. Empty
        # icons therefore become a text representation without changing the
        # role's facility-managed label.
        representation_mode = REPRESENTATION_ICON if raw_icon else REPRESENTATION_TEXT
    if representation_mode == REPRESENTATION_ICON and not raw_icon:
        representation_mode = REPRESENTATION_TEXT
    if representation_mode == REPRESENTATION_TEXT:
        icon = ""
        representation_text = representation_text or label or "?"
    else:
        icon = raw_icon
        representation_text = ""

    normalized_value = value or f"custom.{role_id}"
    return {
        "role_id": role_id,
        "value": normalized_value,
        "label": label,
        "label_key": str(raw.get("label_key") or "").strip(),
        "icon": icon,
        "representation_mode": representation_mode,
        "representation_text": representation_text,
        "colors": normalize_role_colors(
            raw.get("colors"), normalized_value, base_editor=base_editor
        ),
        "order": _coerce_int(raw.get("order"), default_order),
        "active": _coerce_bool(raw.get("active"), True),
        "built_in": built_in,
        "base_editor": base_editor,
        "field_preset": field_preset,
        "experimental_limit_defaults": normalize_experimental_limit_defaults(
            raw.get("experimental_limit_defaults"), normalized_value
        ),
        "dialog_blocks": normalized_blocks,
        "custom_preset_names": raw.get("custom_preset_names") if isinstance(raw.get("custom_preset_names"), dict) else {},
        "event_recipe": normalized_event_recipe,
        "eligibility": raw.get("eligibility") if isinstance(raw.get("eligibility"), dict) else {},
        "status_display": raw.get("status_display") if isinstance(raw.get("status_display"), dict) else {},
        "show_new_animal_button": _coerce_bool(
            raw.get("show_new_animal_button"),
            value == ROLE_VALUE_OFFSPRING,
        ),
        "imported": _coerce_bool(raw.get("imported"), False),
        "review_state": str(raw.get("review_state") or "").strip(),
        "original_label": str(raw.get("original_label") or "").strip(),
        "import_source": str(raw.get("import_source") or "").strip(),
        "mapped_to": str(raw.get("mapped_to") or "").strip(),
    }


class AnimalRoleRegistry:
    """Load and save configurable roles in the backend configuration record.

    Role definitions are mutable facility configuration.  They deliberately
    have no JSON-file fallback: the bundled ``animal_roles.json`` is consumed
    once as a static bootstrap catalog by the application and the resulting
    configuration is thereafter owned by the backend.
    """

    def __init__(self, backend: Any, *, initial_payload: Optional[Dict[str, Any]] = None):
        if backend is None or not hasattr(backend, "records"):
            raise RuntimeError("AnimalRoleRegistry requires a configured backend.")
        self.backend = backend
        self._roles: List[Dict[str, Any]] = self._read_roles(initial_payload)

    def reload(self) -> None:
        self._roles = self._read_roles()

    def roles(self) -> List[Dict[str, Any]]:
        return deepcopy(self._roles)

    def active_roles(self) -> List[Dict[str, Any]]:
        return [role for role in self.roles() if role.get("active")]

    def get_by_value(self, value: str) -> Optional[Dict[str, Any]]:
        value = canonical_role_value(value)
        for role in self._roles:
            if role.get("value") == value:
                return deepcopy(role)
        return None

    def find_by_label_exact(self, label: str) -> Optional[Dict[str, Any]]:
        """Return a role whose label exactly matches *label*.

        Role import matching is intentionally case- and whitespace-sensitive.
        """
        label = str(label or "")
        for role in self._roles:
            if str(role.get("label") or "") == label:
                return deepcopy(role)
        return None

    def label_for_value(self, value: str, messages: Optional[Dict[str, str]] = None) -> str:
        role = self.get_by_value(value)
        if not role:
            return str(value or "")
        label_key = role.get("label_key")
        if label_key and messages:
            return messages.get(label_key, role.get("label", value))
        return role.get("label", value)

    def icon_for_value(self, value: str) -> str:
        role = self.get_by_value(value)
        if not role or role.get("representation_mode") != REPRESENTATION_ICON:
            return ""
        return str(role.get("icon") or "")

    def representation_for_value(
        self, value: str, messages: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Return the single effective icon-or-text representation."""
        role = self.get_by_value(value)
        if not role:
            return {"mode": REPRESENTATION_TEXT, "text": str(value or ""), "icon": ""}
        if role.get("representation_mode") == REPRESENTATION_ICON and role.get("icon"):
            return {"mode": REPRESENTATION_ICON, "icon": str(role["icon"]), "text": ""}
        text = str(role.get("representation_text") or "").strip()
        return {
            "mode": REPRESENTATION_TEXT,
            "text": text or self.label_for_value(value, messages),
            "icon": "",
        }

    def display_for_value(self, value: str, messages: Optional[Dict[str, str]] = None) -> str:
        """Return displayable text without leaking or concatenating icon keys."""
        representation = self.representation_for_value(value, messages)
        if representation["mode"] == REPRESENTATION_TEXT:
            return representation["text"]
        return self.label_for_value(value, messages)

    def color_for_value(self, value: str, sex: Any = "") -> str:
        role = self.get_by_value(value)
        if role is None:
            role = {"value": canonical_role_value(value), "base_editor": "basic"}
        return role_color_for_definition(role, sex)

    def dialog_blocks_for_value(self, value: str, mode: str = "edit") -> List[str]:
        role = self.get_by_value(value) or {}
        blocks = role.get("dialog_blocks", {}) if isinstance(role, dict) else {}
        if not isinstance(blocks, dict):
            return default_dialog_blocks("basic").get(mode, list(REQUIRED_DIALOG_BLOCKS))
        return normalize_block_list(blocks.get(mode, blocks.get("edit", [])))

    def event_recipe_for_value(self, value: str) -> Dict[str, Any]:
        role = self.get_by_value(value) or {}
        recipe = role.get("event_recipe", {}) if isinstance(role, dict) else {}
        if not isinstance(recipe, dict):
            return default_event_recipe("basic")
        return deepcopy(recipe)

    def make_custom_role(
        self,
        label: str,
        icon: str = "",
        *,
        existing_values: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        label = str(label or "New role").strip() or "New role"
        icon = str(icon or "").strip()
        representation_mode = REPRESENTATION_ICON if icon else REPRESENTATION_TEXT
        existing = {role.get("value") for role in self._roles}
        existing.update(str(value) for value in (existing_values or []))

        slug = _slugify(label)
        value = f"custom.{slug}"
        suffix = 2
        while value in existing:
            value = f"custom.{slug}_{suffix}"
            suffix += 1

        return normalize_role_definition(
            {
                "role_id": value.removeprefix("custom."),
                "value": value,
                "label": label,
                "label_key": f"role.{value}",
                "icon": icon,
                "representation_mode": representation_mode,
                "representation_text": label if representation_mode == REPRESENTATION_TEXT else "",
                "order": self._next_order(),
                "active": True,
                "built_in": False,
                "base_editor": "basic",
                "field_preset": "basic",
                "dialog_blocks": default_dialog_blocks("basic"),
                "event_recipe": default_event_recipe("basic"),
            }
        )

    def make_imported_role(
        self,
        original_label: str,
        *,
        source: str = "",
        existing_values: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        original_label = str(original_label or "Imported role").strip() or "Imported role"
        existing_match = self.find_by_label_exact(original_label)
        if existing_match:
            existing_match["original_label"] = original_label
            existing_match["import_source"] = str(source or "").strip()
            return normalize_role_definition(existing_match)

        existing = {role.get("value") for role in self._roles}
        existing.update(str(value) for value in (existing_values or []))
        slug = _slugify(original_label)
        value = f"imported.{slug}"
        suffix = 2
        while value in existing:
            value = f"imported.{slug}_{suffix}"
            suffix += 1

        return normalize_role_definition(
            {
                "role_id": value.removeprefix("imported."),
                "value": value,
                "label": original_label,
                "label_key": f"role.{value}",
                "icon": "!",
                "order": self._next_order(),
                "active": True,
                "built_in": False,
                "base_editor": "basic",
                "field_preset": "basic",
                "dialog_blocks": default_dialog_blocks("basic"),
                "event_recipe": default_event_recipe("basic"),
                "imported": True,
                "review_state": "confirmed",
                "original_label": original_label,
                "import_source": str(source or "").strip(),
            }
        )

    def prepare_payload(self, roles: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
        """Validate and normalize role definitions without writing them."""
        role_list = list(roles)
        validate_role_orders(role_list)
        for role in role_list:
            if isinstance(role, dict):
                validate_role_visual_configuration(role)
        provided_values = {
            canonical_role_value(role.get("value") or "")
            for role in role_list
            if isinstance(role, dict) and str(role.get("value") or "")
        }
        deleted_builtin_values = sorted(_BUILTIN_VALUES - provided_values)
        normalized = self._merge_with_defaults(
            role_list,
            deleted_builtin_values=deleted_builtin_values,
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "deleted_builtin_values": deleted_builtin_values,
            "roles": normalized,
        }

    def apply_payload(self, payload: Dict[str, Any]) -> None:
        """Apply an already committed role payload to this in-memory registry."""
        self._roles = self._read_roles(payload if isinstance(payload, dict) else {})

    def save_roles(self, roles: Iterable[Dict[str, Any]]) -> None:
        role_list = list(roles)
        # Validate before normalization/merging so malformed values are never
        # silently replaced and duplicate orders never overwrite user intent.
        validate_role_orders(role_list)
        for role in role_list:
            if isinstance(role, dict):
                validate_role_visual_configuration(role)
        provided_values = {
            canonical_role_value(role.get("value") or "")
            for role in role_list
            if isinstance(role, dict) and str(role.get("value") or "")
        }
        deleted_builtin_values = sorted(_BUILTIN_VALUES - provided_values)
        normalized = self._merge_with_defaults(
            role_list,
            deleted_builtin_values=deleted_builtin_values,
        )
        payload = {
            "schema_version": SCHEMA_VERSION,
            "deleted_builtin_values": deleted_builtin_values,
            "roles": normalized,
        }
        self.backend.records.put("configuration", "animal-roles", payload)
        self._roles = normalized

    def payload(self) -> Dict[str, Any]:
        """Return the normalized backend payload for callers that need it."""
        return {"schema_version": SCHEMA_VERSION, "roles": self.roles()}

    def _read_roles(
        self, initial_payload: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        payload = initial_payload
        if not isinstance(payload, dict):
            payload = self.backend.records.get(
                "configuration", "animal-roles", default=None
            )
        if not isinstance(payload, dict):
            return self._merge_with_defaults([])
        raw_roles = payload.get("roles", []) if isinstance(payload, dict) else []
        if not isinstance(raw_roles, list):
            raw_roles = []
        deleted_builtin_values = payload.get("deleted_builtin_values", []) if isinstance(payload, dict) else []
        return self._merge_with_defaults(raw_roles, deleted_builtin_values=deleted_builtin_values)

    def _merge_with_defaults(
        self,
        raw_roles: Iterable[Dict[str, Any]],
        *,
        deleted_builtin_values: Iterable[str] = (),
    ) -> List[Dict[str, Any]]:
        merged_by_value: Dict[str, Dict[str, Any]] = {}
        deleted_builtins = {
            canonical_role_value(value)
            for value in deleted_builtin_values
            if canonical_role_value(value)
        }
        for index, default in enumerate(DEFAULT_ROLE_DEFINITIONS):
            if default["value"] in deleted_builtins:
                continue
            merged_by_value[default["value"]] = normalize_role_definition(
                default, default_order=(index + 1) * 10
            )

        for index, raw in enumerate(raw_roles):
            if not isinstance(raw, dict):
                continue
            normalized = normalize_role_definition(raw, default_order=1000 + index * 10)
            default = _DEFAULTS_BY_VALUE.get(normalized["value"])
            if default:
                default_normalized = normalize_role_definition(default)
                default_normalized.update(
                    {
                        "label": normalized.get("label"),
                        "icon": normalized.get("icon", default_normalized["icon"]),
                        "representation_mode": normalized.get(
                            "representation_mode", default_normalized["representation_mode"]
                        ),
                        "representation_text": normalized.get(
                            "representation_text", default_normalized["representation_text"]
                        ),
                        "colors": normalized.get("colors", default_normalized["colors"]),
                        "order": normalized.get("order", default_normalized["order"]),
                        "active": normalized.get("active", default_normalized["active"]),
                        "dialog_blocks": normalized.get("dialog_blocks", default_normalized["dialog_blocks"]),
                        "experimental_limit_defaults": normalized.get(
                            "experimental_limit_defaults",
                            default_normalized.get("experimental_limit_defaults", {}),
                        ),
                        "custom_preset_names": normalized.get("custom_preset_names", default_normalized.get("custom_preset_names", {})),
                        "event_recipe": normalized.get("event_recipe", default_normalized["event_recipe"]),
                        "eligibility": normalized.get("eligibility", default_normalized.get("eligibility", {})),
                        "status_display": normalized.get("status_display", default_normalized.get("status_display", {})),
                    }
                )
                normalized = default_normalized
            merged_by_value[normalized["value"]] = normalized

        return sorted(
            merged_by_value.values(),
            key=lambda role: (role.get("order", 1000), role.get("label", "").casefold()),
        )

    def _next_order(self) -> int:
        if not self._roles:
            return 100
        return max(_coerce_int(role.get("order"), 0) for role in self._roles) + 10


def clear_deleted_role_assignments(
    animal_records: Dict[str, Dict[str, Any]],
    deleted_role_values: Iterable[str],
) -> List[str]:
    """Clear role assignments for animals that still reference deleted roles."""
    deleted = {
        canonical_role_value(value)
        for value in deleted_role_values
        if canonical_role_value(value)
    }
    changed: List[str] = []
    if not deleted or not isinstance(animal_records, dict):
        return changed
    for animal_name, record in animal_records.items():
        if isinstance(record, dict) and canonical_role_value(record.get("rolle")) in deleted:
            record["rolle"] = ""
            changed.append(str(animal_name))
    return changed


def normalize_animal_record_role(record: Dict[str, Any]) -> bool:
    """Normalize one animal record's role field to the internal role ID."""
    if not isinstance(record, dict):
        return False
    old_role = record.get("rolle")
    new_role = canonical_role_value(old_role, default=ROLE_VALUE_UNKNOWN)
    if old_role != new_role:
        record["rolle"] = new_role
        return True
    return False


def normalize_animal_record_roles(
    animal_records: Dict[str, Dict[str, Any]],
) -> List[str]:
    """Normalize role values for a mapping of animal records."""
    changed: List[str] = []
    if not isinstance(animal_records, dict):
        return changed
    for animal_name, record in animal_records.items():
        if normalize_animal_record_role(record):
            changed.append(str(animal_name))
    return changed
