"""Configurable animal role registry for ProgTrack."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SCHEMA_VERSION = 1

ROLE_VALUE_SPENDER = "Spenderin"
ROLE_VALUE_AMME = "Amme"
ROLE_VALUE_SAMENSP = "Samenspender"
ROLE_VALUE_OFFSPRING = "Nachkomme"
ROLE_VALUE_PARTNER = "Partnertier"
ROLE_VALUE_ZUCHTTIER = "Zuchttier"
ROLE_VALUE_EXPERIMENTAL = "Versuchstier"
ROLE_VALUE_UNKNOWN = "Unbekannt"


DEFAULT_ROLE_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "role_id": "spenderin",
        "value": ROLE_VALUE_SPENDER,
        "label": "Spenderin",
        "label_key": "role.spenderin",
        "icon": "\u2640",
        "order": 10,
        "active": True,
        "built_in": True,
        "base_editor": "female",
        "field_preset": "female_donor",
    },
    {
        "role_id": "amme",
        "value": ROLE_VALUE_AMME,
        "label": "Amme",
        "label_key": "role.amme",
        "icon": "\u2640",
        "order": 20,
        "active": True,
        "built_in": True,
        "base_editor": "female",
        "field_preset": "surrogate",
    },
    {
        "role_id": "samenspender",
        "value": ROLE_VALUE_SAMENSP,
        "label": "Samenspender",
        "label_key": "role.samenspender",
        "icon": "\u2642",
        "order": 30,
        "active": True,
        "built_in": True,
        "base_editor": "samenspender",
        "field_preset": "sperm_donor",
    },
    {
        "role_id": "offspring",
        "value": ROLE_VALUE_OFFSPRING,
        "label": "Nachkomme",
        "label_key": "role.offspring",
        "icon": "\U0001f476",
        "order": 40,
        "active": True,
        "built_in": True,
        "base_editor": "offspring",
        "field_preset": "offspring",
    },
    {
        "role_id": "partnertier",
        "value": ROLE_VALUE_PARTNER,
        "label": "Partnertier",
        "label_key": "role.partnertier",
        "icon": "\U0001f43e",
        "order": 50,
        "active": True,
        "built_in": True,
        "base_editor": "partner",
        "field_preset": "partner",
    },
    {
        "role_id": "zuchttier",
        "value": ROLE_VALUE_ZUCHTTIER,
        "label": "Zuchttier",
        "label_key": "role.zuchttier",
        "icon": "\u26a4",
        "order": 60,
        "active": True,
        "built_in": True,
        "base_editor": "zuchttier",
        "field_preset": "breeding",
    },
    {
        "role_id": "versuchstier",
        "value": ROLE_VALUE_EXPERIMENTAL,
        "label": "Versuchstier",
        "label_key": "role.experimental",
        "icon": "\U0001f4a1",
        "order": 70,
        "active": True,
        "built_in": True,
        "base_editor": "versuchstier",
        "field_preset": "experimental",
    },
    {
        "role_id": "unknown",
        "value": ROLE_VALUE_UNKNOWN,
        "label": "Unbekannt",
        "label_key": "role.unknown",
        "icon": "?",
        "order": 9990,
        "active": False,
        "built_in": True,
        "base_editor": "basic",
        "field_preset": "basic",
    },
]


_DEFAULTS_BY_VALUE = {role["value"]: role for role in DEFAULT_ROLE_DEFINITIONS}


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


def normalize_role_definition(raw: Dict[str, Any], *, default_order: int = 1000) -> Dict[str, Any]:
    value = str(raw.get("value") or raw.get("role_id") or "").strip()
    label = str(raw.get("label") or value or "New role").strip()
    role_id = str(raw.get("role_id") or _slugify(label)).strip()
    built_in = _coerce_bool(raw.get("built_in"), False)

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
        "field_preset": str(raw.get("field_preset") or "basic").strip() or "basic",
        "imported": _coerce_bool(raw.get("imported"), False),
        "review_state": str(raw.get("review_state") or "").strip(),
        "original_label": str(raw.get("original_label") or "").strip(),
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
        value = str(value or "")
        for role in self._roles:
            if role.get("value") == value:
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
                "icon": icon,
                "order": self._next_order(),
                "active": True,
                "built_in": False,
                "base_editor": "basic",
                "field_preset": "basic",
            }
        )

    def save_roles(self, roles: Iterable[Dict[str, Any]]) -> None:
        normalized = self._merge_with_defaults(list(roles))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
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
        return self._merge_with_defaults(raw_roles)

    def _merge_with_defaults(self, raw_roles: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged_by_value: Dict[str, Dict[str, Any]] = {}
        for index, default in enumerate(DEFAULT_ROLE_DEFINITIONS):
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
                        "label": normalized.get("label") or default_normalized["label"],
                        "icon": normalized.get("icon") or default_normalized["icon"],
                        "order": normalized.get("order", default_normalized["order"]),
                        "active": normalized.get("active", default_normalized["active"]),
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
