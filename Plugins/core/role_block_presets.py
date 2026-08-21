"""Installation-wide custom role-dialog block presets.

The Role Builder stores custom block recipes independently from individual
animal roles.  A preset can therefore be selected for any role and for either
the create or edit dialog.  Role definitions only retain the selected preset
name; the preset body lives in this registry.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List
import re
import uuid

from Plugins.core.animal_roles import normalize_block_list


SCHEMA_VERSION = 2
DEFAULT_FILENAME = "role_block_presets.json"
RESERVED_PRESET_NAMES = {
    "custom",
    "new preset",
    "new role",
}


def role_block_presets_path(app_base_dir: Path | str) -> Path:
    return Path(app_base_dir) / "Plugins" / "core" / DEFAULT_FILENAME


def normalized_preset_name(name: Any) -> str:
    return " ".join(str(name or "").strip().split())


def valid_preset_name(name: Any, *, extra_reserved: Iterable[str] = ()) -> bool:
    value = normalized_preset_name(name)
    reserved = {
        normalized_preset_name(item).casefold()
        for item in (*RESERVED_PRESET_NAMES, *extra_reserved)
        if normalized_preset_name(item)
    }
    return bool(value) and value.casefold() not in reserved


def _stable_id(prefix: str, value: Any) -> str:
    """Create a deterministic first-write ID for schema-v1 migration."""
    token = normalized_preset_name(value).casefold()
    return f"{prefix}:{uuid.uuid5(uuid.NAMESPACE_URL, 'progtrack:role-block:' + token)}"


def _slug(value: Any) -> str:
    text = normalized_preset_name(value).casefold()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or "custom_block"


def normalize_experimental_block(raw: Dict[str, Any]) -> Dict[str, Any]:
    name = normalized_preset_name(raw.get("name") or raw.get("label") or "")
    block_id = str(raw.get("id") or "").strip()
    if not block_id:
        block_id = _stable_id("experimental-block", name or raw.get("event_type") or "custom-block")
    if not (block_id.startswith("experimental-block:") or block_id.startswith("custom_limit:")):
        block_id = _stable_id("experimental-block", block_id)
    kind = str(raw.get("kind") or "limiting").strip().casefold()
    if kind not in {"counting", "limiting"}:
        kind = "limiting"
    event_type = str(raw.get("event_type") or block_id.removeprefix("custom_limit:")).strip()
    event_type = re.sub(r"[^a-zA-Z0-9_]+", "_", event_type).strip("_") or "custom_event"
    max_field = str(raw.get("max_field") or raw.get("limit_block") or ("max_" + event_type)).strip()
    render_mode = str(raw.get("render_mode") or "symbol").strip().casefold()
    if render_mode not in {"line", "symbol"}:
        render_mode = "symbol"
    marker = str(raw.get("marker") or "o").strip() or "o"
    color = str(raw.get("color") or raw.get("default_color") or "#4C78A8").strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color):
        color = "#4C78A8"
    default_maximum = raw.get("default_maximum", raw.get("default_max", 0))
    try:
        default_maximum = max(0, int(default_maximum or 0))
    except (TypeError, ValueError):
        default_maximum = 0
    event_ids = (
        [str(v).strip() for v in raw.get("event_ids", []) if str(v).strip()]
        if isinstance(raw.get("event_ids"), list) else []
    )
    stable_id = str(raw.get("stable_id") or _stable_id("experimental-block", name or event_type))
    return {
        "id": block_id,
        "stable_id": stable_id,
        "name": name or event_type.replace("_", " ").title(),
        "description": str(raw.get("description") or "").strip(),
        "kind": kind,
        "default_maximum": default_maximum,
        "event_ids": list(dict.fromkeys(event_ids)),
        "event_type": event_type,
        "max_field": max_field if kind == "limiting" else "",
        "render_mode": render_mode,
        "marker": marker,
        "color": color.upper(),
        "active": bool(raw.get("active", True)),
        "retired_at": str(raw.get("retired_at") or ""),
        "revision": max(1, int(raw.get("revision", 1) or 1)),
    }


def normalize_event_definition(raw: Dict[str, Any]) -> Dict[str, Any]:
    event = normalize_experimental_block(raw)
    event_id = str(raw.get("id") or "").strip()
    if not event_id or not event_id.startswith("custom-event:"):
        event_id = _stable_id("custom-event", raw.get("event_type") or event["name"])
    block_id = str(raw.get("block_id") or event.get("id") or "").strip()
    stable_id = str(raw.get("stable_id") or _stable_id("custom-event", raw.get("event_type") or event["name"]))
    return {
        "id": event_id,
        "stable_id": stable_id,
        "block_id": block_id,
        "event_type": event["event_type"],
        "name": event["name"],
        "label": event["name"],
        "label_key": str(raw.get("label_key") or ""),
        "render_mode": event["render_mode"],
        "marker": event["marker"],
        "color": event["color"],
        "default_color": event["color"],
        "default_marker": event["marker"],
        "limit_block": event["max_field"] if event["kind"] == "limiting" else "",
        "active": bool(raw.get("active", True)),
        "revision": max(1, int(raw.get("revision", 1) or 1)),
    }


def normalize_preset(raw: Dict[str, Any]) -> Dict[str, Any]:
    name = normalized_preset_name(raw.get("name"))
    preset_id = str(raw.get("id") or "").strip()
    if not preset_id or not preset_id.startswith("preset:"):
        preset_id = _stable_id("preset", name or "unnamed")
    return {
        "id": preset_id,
        "name": name,
        "blocks": normalize_block_list(raw.get("blocks", [])),
        "active": bool(raw.get("active", True)),
        "retired_at": str(raw.get("retired_at") or ""),
        "revision": max(1, int(raw.get("revision", 1) or 1)),
    }


class RoleBlockPresetRegistry:
    """Backend-owned shared role-dialog presets and dynamic event definitions."""

    def __init__(self, backend: Any, *, initial_payload: Dict[str, Any] | None = None):
        if backend is None or not hasattr(backend, "records"):
            raise RuntimeError("RoleBlockPresetRegistry requires a configured backend.")
        self.backend = backend
        self._experimental_blocks: List[Dict[str, Any]] = []
        self._event_definitions: List[Dict[str, Any]] = []
        self._presets = self._read(initial_payload)

    def reload(self) -> None:
        self._presets = self._read()

    def presets(self) -> List[Dict[str, Any]]:
        return deepcopy(self._presets)

    def experimental_blocks(self) -> List[Dict[str, Any]]:
        return deepcopy(self._experimental_blocks)

    def event_definitions(self, *, include_retired: bool = True) -> List[Dict[str, Any]]:
        values = self._event_definitions if include_retired else [
            item for item in self._event_definitions if bool(item.get("active", True))
        ]
        return deepcopy(values)

    def active_experimental_blocks(self) -> List[Dict[str, Any]]:
        return deepcopy([item for item in self._experimental_blocks if bool(item.get("active", True))])

    def active_event_definitions(self) -> List[Dict[str, Any]]:
        return deepcopy([item for item in self._event_definitions if bool(item.get("active", True))])

    def prepare_payload(
        self,
        presets: Iterable[Dict[str, Any]],
        *,
        experimental_blocks: Iterable[Dict[str, Any]] | None = None,
        event_definitions: Iterable[Dict[str, Any]] | None = None,
    ) -> Dict[str, Any]:
        """Validate and normalize a complete registry payload without writing."""
        normalized: List[Dict[str, Any]] = []
        seen = set()
        for raw in presets:
            if not isinstance(raw, dict):
                continue
            preset = normalize_preset(raw)
            name_key = preset["name"].casefold()
            if not valid_preset_name(preset["name"]):
                raise ValueError(f"Invalid custom preset name: {preset['name']!r}")
            if name_key in seen:
                raise ValueError(f"Custom preset names must be unique: {preset['name']!r}")
            seen.add(name_key)
            normalized.append(preset)

        blocks_source = self._experimental_blocks if experimental_blocks is None else experimental_blocks
        events_source = self._event_definitions if event_definitions is None else event_definitions
        blocks: List[Dict[str, Any]] = []
        block_ids = set()
        for raw in blocks_source or []:
            if not isinstance(raw, dict):
                continue
            item = normalize_experimental_block(raw)
            if not item["name"] or item["id"] in block_ids:
                continue
            block_ids.add(item["id"])
            blocks.append(item)
        events: List[Dict[str, Any]] = []
        event_ids = set()
        for raw in events_source or []:
            if not isinstance(raw, dict):
                continue
            item = normalize_event_definition(raw)
            if item["event_type"] in event_ids:
                continue
            event_ids.add(item["event_type"])
            events.append(item)
        normalized.sort(key=lambda preset: preset["name"].casefold())
        blocks.sort(key=lambda item: item["name"].casefold())
        events.sort(key=lambda item: item["event_type"].casefold())
        return {
            "schema_version": SCHEMA_VERSION,
            "presets": normalized,
            "experimental_blocks": blocks,
            "event_definitions": events,
        }

    def apply_payload(self, payload: Dict[str, Any]) -> None:
        """Apply an already committed payload to this in-memory registry."""
        self._presets = self._read(payload if isinstance(payload, dict) else {})

    def save_presets(
        self,
        presets: Iterable[Dict[str, Any]],
        *,
        experimental_blocks: Iterable[Dict[str, Any]] | None = None,
        event_definitions: Iterable[Dict[str, Any]] | None = None,
        expected_revision: int | None = None,
    ) -> int:
        normalized: List[Dict[str, Any]] = []
        seen = set()
        for raw in presets:
            if not isinstance(raw, dict):
                continue
            preset = normalize_preset(raw)
            name_key = preset["name"].casefold()
            if not valid_preset_name(preset["name"]):
                raise ValueError(f"Invalid custom preset name: {preset['name']!r}")
            if name_key in seen:
                raise ValueError(f"Custom preset names must be unique: {preset['name']!r}")
            seen.add(name_key)
            normalized.append(preset)

        blocks_source = self._experimental_blocks if experimental_blocks is None else experimental_blocks
        events_source = self._event_definitions if event_definitions is None else event_definitions
        blocks: List[Dict[str, Any]] = []
        block_ids = set()
        for raw in blocks_source or []:
            if not isinstance(raw, dict):
                continue
            item = normalize_experimental_block(raw)
            if not item["name"] or item["id"] in block_ids:
                continue
            block_ids.add(item["id"])
            blocks.append(item)
        events: List[Dict[str, Any]] = []
        event_ids = set()
        for raw in events_source or []:
            if not isinstance(raw, dict):
                continue
            item = normalize_event_definition(raw)
            if item["event_type"] in event_ids:
                continue
            event_ids.add(item["event_type"])
            events.append(item)
        normalized.sort(key=lambda preset: preset["name"].casefold())
        blocks.sort(key=lambda item: item["name"].casefold())
        events.sort(key=lambda item: item["event_type"].casefold())
        payload = {
            "schema_version": SCHEMA_VERSION,
            "presets": normalized,
            "experimental_blocks": blocks,
            "event_definitions": events,
        }
        revision = self.backend.records.put(
            "configuration", "role-block-presets", payload,
            expected_revision=expected_revision,
        )
        self._presets = normalized
        self._experimental_blocks = blocks
        self._event_definitions = events
        return revision

    def payload(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "presets": self.presets(),
            "experimental_blocks": self.experimental_blocks(),
            "event_definitions": self.event_definitions(),
        }

    def _read(self, initial_payload: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        payload = initial_payload
        if not isinstance(payload, dict):
            payload = self.backend.records.get("configuration", "role-block-presets", default=None)
        if not isinstance(payload, dict):
            payload = {}
        raw_presets = payload.get("presets", [])
        if not isinstance(raw_presets, list):
            raw_presets = []
        presets: List[Dict[str, Any]] = []
        seen = set()
        for raw in raw_presets:
            if not isinstance(raw, dict):
                continue
            preset = normalize_preset(raw)
            name_key = preset["name"].casefold()
            if not valid_preset_name(preset["name"]) or name_key in seen:
                continue
            seen.add(name_key)
            presets.append(preset)
        self._experimental_blocks = []
        block_ids = set()
        for raw in payload.get("experimental_blocks", []) if isinstance(payload.get("experimental_blocks", []), list) else []:
            if not isinstance(raw, dict):
                continue
            item = normalize_experimental_block(raw)
            if item["id"] in block_ids:
                continue
            block_ids.add(item["id"])
            self._experimental_blocks.append(item)
        self._event_definitions = []
        event_ids = set()
        for raw in payload.get("event_definitions", []) if isinstance(payload.get("event_definitions", []), list) else []:
            if not isinstance(raw, dict):
                continue
            item = normalize_event_definition(raw)
            if item["event_type"] in event_ids:
                continue
            event_ids.add(item["event_type"])
            self._event_definitions.append(item)
        return sorted(presets, key=lambda preset: preset["name"].casefold())
