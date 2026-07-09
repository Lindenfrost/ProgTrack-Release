"""Configurable animal role registry for ProgTrack."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = 1

ROLE_VALUE_SPENDER = "egg_cell_donor"
ROLE_VALUE_AMME = "surrogate"
ROLE_VALUE_SAMENSP = "sperm_donor"
ROLE_VALUE_OFFSPRING = "offspring"
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
    ROLE_VALUE_PARTNER: "role.partner_animal",
    ROLE_VALUE_ZUCHTTIER: "role.breeding_animal",
    ROLE_VALUE_EXPERIMENTAL: "role.experimental_animal",
    ROLE_VALUE_UNKNOWN: "role.unknown",
}


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
    "surgery": {"label_key": "event.surgery", "aliases": ["op"], "planned": True, "limit_block": "max_op"},
    "embryo_transfer": {"label_key": "event.embryo_transfer", "aliases": ["embryo"], "planned": True, "limit_block": "max_embryo"},
    "pregnancy": {"label_key": "event.pregnancy", "aliases": ["traechtigkeit", "trächtigkeit"], "planned": False, "limit_block": "max_pregnancies"},
    "abortion": {"label_key": "event.abortion", "aliases": ["abort"], "planned": False, "limit_block": ""},
    "birth": {"label_key": "event.birth", "aliases": ["geburt"], "planned": False, "limit_block": "max_births"},
    "pgf": {"label_key": "event.pgf", "aliases": ["pgf"], "planned": True, "limit_block": "max_pgf"},
    "fsh": {"label_key": "event.fsh", "aliases": ["fsh"], "planned": True, "limit_block": "max_fsh"},
    "progesterone": {"label_key": "event.progesterone", "aliases": ["progesterone"], "planned": False, "limit_block": ""},
    "special_measurement": {"label_key": "event.special_measurement", "aliases": ["sondermessung"], "planned": True, "limit_block": "max_special"},
    "measurement": {"label_key": "event.measurement", "aliases": ["measurement"], "planned": True, "limit_block": "max_measurements"},
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
            "health_flags", "weight", "urine_pdg", "computed_values",
        ],
        "edit": [
            "identity", "id_chip_origin", "project_severity", "lifecycle",
            "cage_address", "parenting", "reference_weight", "partner_fields",
            "health_flags", "weight", "urine_pdg", "computed_values",
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
        "icon": "\u2640",
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
        "icon": "\u2640",
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
        "icon": "\u2642",
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
        "icon": "\U0001f476",
        "order": 40,
        "active": True,
        "built_in": True,
        "base_editor": "offspring",
        "field_preset": "offspring",
        "show_new_animal_button": True,
    },
    {
        "role_id": ROLE_VALUE_PARTNER,
        "value": ROLE_VALUE_PARTNER,
        "label": ROLE_DISPLAY_LABELS[ROLE_VALUE_PARTNER],
        "label_key": ROLE_LABEL_KEYS[ROLE_VALUE_PARTNER],
        "icon": "\U0001f43e",
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
        "icon": "\u26a4",
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
        "icon": "\U0001f4a1",
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
        "icon": "?",
        "order": 9990,
        "active": False,
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


def normalize_block_list(values: Any) -> List[str]:
    if isinstance(values, str):
        raw_values = [part.strip() for part in values.split(",")]
    elif isinstance(values, Iterable) and not isinstance(values, (bytes, bytearray, dict)):
        raw_values = [str(value or "").strip() for value in values]
    else:
        raw_values = []

    normalized: List[str] = []
    for block in [*REQUIRED_DIALOG_BLOCKS, *raw_values]:
        if block in ALL_DIALOG_BLOCKS and block not in normalized:
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

    return {
        "role_id": role_id,
        "value": value or f"custom.{role_id}",
        "label": label,
        "label_key": str(raw.get("label_key") or "").strip(),
        "icon": str(raw.get("icon") or "\u25cf").strip()[:8] or "\u25cf",
        "order": _coerce_int(raw.get("order"), default_order),
        "active": _coerce_bool(raw.get("active"), True),
        "built_in": built_in,
        "base_editor": str(raw.get("base_editor") or "basic").strip() or "basic",
        "field_preset": field_preset,
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
        "mapped_to": str(raw.get("mapped_to") or "").strip(),
    }


class AnimalRoleRegistry:
    """Load, merge, and save configurable animal role definitions."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._roles: List[Dict[str, Any]] = self._read_roles()

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
        return role.get("icon", "") if role else ""

    def display_for_value(self, value: str, messages: Optional[Dict[str, str]] = None) -> str:
        label = self.label_for_value(value, messages)
        icon = self.icon_for_value(value)
        return f"{icon} {label}".strip()

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
        icon: str = "\u25cf",
        *,
        existing_values: Optional[Iterable[str]] = None,
    ) -> Dict[str, Any]:
        label = str(label or "New role").strip() or "New role"
        icon = str(icon or "\u25cf").strip() or "\u25cf"
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

    def save_roles(self, roles: Iterable[Dict[str, Any]]) -> None:
        role_list = list(roles)
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
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "deleted_builtin_values": deleted_builtin_values,
            "roles": normalized,
        }
        with self.path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        self._roles = normalized

    def _read_roles(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return self._merge_with_defaults([])
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
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
                        "icon": normalized.get("icon") or default_normalized["icon"],
                        "order": normalized.get("order", default_normalized["order"]),
                        "active": normalized.get("active", default_normalized["active"]),
                        "dialog_blocks": normalized.get("dialog_blocks", default_normalized["dialog_blocks"]),
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
