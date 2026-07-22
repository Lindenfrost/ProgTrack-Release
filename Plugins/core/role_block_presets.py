"""Installation-wide custom role-dialog block presets.

The Role Builder stores custom block recipes independently from individual
animal roles.  A preset can therefore be selected for any role and for either
the create or edit dialog.  Role definitions only retain the selected preset
name; the preset body lives in this registry.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List

from Plugins.core.animal_roles import normalize_block_list


SCHEMA_VERSION = 1
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


def normalize_preset(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": normalized_preset_name(raw.get("name")),
        "blocks": normalize_block_list(raw.get("blocks", [])),
    }


class RoleBlockPresetRegistry:
    """Read and atomically write shared custom block presets."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._presets = self._read()

    def reload(self) -> None:
        self._presets = self._read()

    def presets(self) -> List[Dict[str, Any]]:
        return deepcopy(self._presets)

    def save_presets(self, presets: Iterable[Dict[str, Any]]) -> None:
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
                raise ValueError(
                    f"Custom preset names must be unique: {preset['name']!r}"
                )
            seen.add(name_key)
            normalized.append(preset)

        normalized.sort(key=lambda preset: preset["name"].casefold())
        payload = {"schema_version": SCHEMA_VERSION, "presets": normalized}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        temporary_path.replace(self.path)
        self._presets = normalized

    def _read(self) -> List[Dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return []
        raw_presets = payload.get("presets", []) if isinstance(payload, dict) else []
        if not isinstance(raw_presets, list):
            return []

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
        return sorted(presets, key=lambda preset: preset["name"].casefold())
