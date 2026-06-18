# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Heritage Track persistence layer.

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple


PARENT_KEYS = ("egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father")


class HeritageStore:
    """Owns plugin-specific storage split across heritage_animals.json and heritage_settings.json.

    Animals and genotype colors → heritage_animals.json
    Node positions, collapsed families, and UI settings → heritage_settings.json

    Backward compat: if heritage_animals.json is missing but the old heritage.json exists,
    data is migrated from heritage.json on the first load.
    """

    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir
        self.file_path = os.path.join(plugin_dir, "heritage_animals.json")
        self.settings_path = os.path.join(plugin_dir, "heritage_settings.json")
        self._legacy_path = os.path.join(plugin_dir, "heritage.json")
        self._data: Optional[Dict[str, Any]] = None

    def _default_settings(self) -> Dict[str, bool]:
        return {
            "show_grid": False,
            "snap_to_grid": False,
            "show_heritage_only": True,
            "show_legend": True,
            "show_inbreeding_f": True,
            "exclude_archived": False,
        }

    def _default_data(self) -> Dict[str, Any]:
        return {
            "version": "1.0.0",
            "updated_at": datetime.utcnow().isoformat() + "Z",
            "settings": self._default_settings(),
            "node_positions": {},
            "collapsed_families": [],
            "genotype_colors": {},
            "animals": {},
        }

    def _normalize_text(self, value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        return text

    def _normalize_parents(self, values: Optional[Dict[str, Any]]) -> Dict[str, str]:
        src = values or {}
        return {key: self._normalize_text(src.get(key, "")) for key in PARENT_KEYS}

    def _normalize_sex(self, value: Any) -> str:
        text = self._normalize_text(value).lower()
        if not text:
            return ""

        male_markers = {
            "m",
            "male",
            "man",
            "maschio",
            "maschile",
            "männlich",
            "mannlich",
            "м",
            "муж",
            "мужской",
            "самец",
        }
        female_markers = {
            "f",
            "female",
            "woman",
            "femmina",
            "femminile",
            "weiblich",
            "w",
            "ж",
            "жен",
            "женский",
            "самка",
        }

        if text in male_markers:
            return "male"
        if text in female_markers:
            return "female"
        return ""

    def _set_parent_sex_from_core_role(self, parent_name: Any, forced_sex: str) -> bool:
        """Persist deterministic parent sex inferred from core mother/father fields."""
        key = self._normalize_text(parent_name)
        normalized = self._normalize_sex(forced_sex)
        if not key or not normalized:
            return False

        entry = self._entry(key)
        current = self._normalize_sex(entry.get("sex", ""))
        if current == normalized:
            return False

        entry["sex"] = normalized
        entry["updated_at"] = datetime.utcnow().isoformat() + "Z"
        return True

    def _normalize_genotype_key(self, value: Any) -> str:
        return self._normalize_text(value).lower()

    def _normalize_position(self, value: Any) -> Optional[Tuple[float, float]]:
        if isinstance(value, dict):
            raw_x = value.get("x")
            raw_y = value.get("y")
        elif isinstance(value, (list, tuple)) and len(value) >= 2:
            raw_x = value[0]
            raw_y = value[1]
        else:
            return None

        try:
            x = float(raw_x)
            y = float(raw_y)
        except (TypeError, ValueError):
            return None

        # Filter out NaN values (NaN != NaN).
        if not (x == x and y == y):
            return None
        return x, y

    def load(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data

        raw = self._load_raw()
        if raw is None:
            self._data = self._default_data()
            self.save()
            return self._data

        return self._normalize_and_cache(raw)

    def _load_raw(self) -> Optional[Dict[str, Any]]:
        """Load raw data from the split files or fall back to legacy heritage.json."""
        if os.path.exists(self.file_path):
            return self._load_split()
        if os.path.exists(self._legacy_path):
            return self._migrate_from_legacy()
        return None

    def _load_split(self) -> Dict[str, Any]:
        """Load animals from heritage_animals.json and settings from heritage_settings.json."""
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                animals_raw = json.load(f)
            if not isinstance(animals_raw, dict):
                animals_raw = {}
        except Exception:
            animals_raw = {}

        try:
            with open(self.settings_path, "r", encoding="utf-8") as f:
                settings_raw = json.load(f)
            if not isinstance(settings_raw, dict):
                settings_raw = {}
        except Exception:
            settings_raw = {}

        merged = self._default_data()
        merged["animals"] = animals_raw.get("animals", {})
        merged["genotype_colors"] = animals_raw.get("genotype_colors", {})
        merged["settings"] = settings_raw.get("settings", merged["settings"])
        merged["node_positions"] = settings_raw.get("node_positions", {})
        merged["collapsed_families"] = settings_raw.get("collapsed_families", [])
        if "version" in animals_raw:
            merged["version"] = animals_raw["version"]
        return merged

    def _migrate_from_legacy(self) -> Dict[str, Any]:
        """Read the old heritage.json and return its data (split saved on next save())."""
        try:
            with open(self._legacy_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                return self._default_data()
            return raw
        except Exception:
            return self._default_data()

    def _normalize_and_cache(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a raw combined dict, populate self._data, and trigger save."""
        if not isinstance(raw, dict):
            raw = self._default_data()

        raw.setdefault("version", "1.0.0")
        raw.setdefault("updated_at", datetime.utcnow().isoformat() + "Z")
        if not isinstance(raw.get("animals"), dict):
            raw["animals"] = {}
        if not isinstance(raw.get("node_positions"), dict):
            raw["node_positions"] = {}
        if not isinstance(raw.get("genotype_colors"), dict):
            raw["genotype_colors"] = {}
        if not isinstance(raw.get("collapsed_families"), list):
            raw["collapsed_families"] = []

        raw_settings = raw.get("settings", {})
        if not isinstance(raw_settings, dict):
            raw_settings = {}
        default_settings = self._default_settings()
        raw["settings"] = {
            "show_grid": bool(raw_settings.get("show_grid", default_settings["show_grid"])),
            "snap_to_grid": bool(raw_settings.get("snap_to_grid", default_settings["snap_to_grid"])),
            "show_heritage_only": bool(raw_settings.get("show_heritage_only", default_settings["show_heritage_only"])),
            "show_legend": bool(raw_settings.get("show_legend", default_settings["show_legend"])),
            "show_inbreeding_f": bool(raw_settings.get("show_inbreeding_f", default_settings["show_inbreeding_f"])),
        }

        normalized_positions: Dict[str, Dict[str, float]] = {}
        for name, raw_position in raw.get("node_positions", {}).items():
            key = self._normalize_text(name)
            if not key:
                continue
            normalized = self._normalize_position(raw_position)
            if normalized is None:
                continue
            x, y = normalized
            normalized_positions[key] = {"x": x, "y": y}
        raw["node_positions"] = normalized_positions

        normalized_collapsed_families: List[str] = []
        seen_families: Set[str] = set()
        for family_id in raw.get("collapsed_families", []):
            family_key = self._normalize_text(family_id)
            if not family_key or family_key in seen_families:
                continue
            normalized_collapsed_families.append(family_key)
            seen_families.add(family_key)
        raw["collapsed_families"] = normalized_collapsed_families

        normalized_genotype_colors: Dict[str, str] = {}
        for genotype, color_value in raw.get("genotype_colors", {}).items():
            genotype_key = self._normalize_genotype_key(genotype)
            if not genotype_key:
                continue
            normalized_genotype_colors[genotype_key] = self._normalize_text(color_value)
        raw["genotype_colors"] = normalized_genotype_colors

        # Normalize existing entries
        normalized_animals: Dict[str, Dict[str, Any]] = {}
        for name, entry in raw["animals"].items():
            if not isinstance(name, str):
                continue
            if not isinstance(entry, dict):
                entry = {}
            normalized_entry = self._normalize_parents(entry)
            normalized_entry["genotype"] = self._normalize_text(entry.get("genotype", ""))
            normalized_entry["node_fill_color"] = self._normalize_text(entry.get("node_fill_color", ""))
            normalized_entry["sex"] = self._normalize_sex(entry.get("sex", ""))
            normalized_entry["species"] = self._normalize_text(entry.get("species", ""))
            normalized_entry["heritage_only"] = bool(entry.get("heritage_only", False))
            normalized_entry["source"] = self._normalize_text(entry.get("source", "plugin")) or "plugin"
            normalized_entry["updated_at"] = self._normalize_text(entry.get("updated_at", ""))
            raw_f = entry.get("inbreeding_f")
            if raw_f is None:
                normalized_entry["inbreeding_f"] = None
            else:
                try:
                    normalized_entry["inbreeding_f"] = float(raw_f)
                except (TypeError, ValueError):
                    normalized_entry["inbreeding_f"] = None
            normalized_animals[name.strip()] = normalized_entry

        # Backfill genotype color map from existing entries (migration path).
        genotype_colors = dict(raw.get("genotype_colors", {}))
        for entry in normalized_animals.values():
            genotype_key = self._normalize_genotype_key(entry.get("genotype", ""))
            if not genotype_key or genotype_key in genotype_colors:
                continue
            color_value = self._normalize_text(entry.get("node_fill_color", ""))
            if color_value:
                genotype_colors[genotype_key] = color_value

        # Enforce single color per genotype in stored node visuals.
        for entry in normalized_animals.values():
            genotype_key = self._normalize_genotype_key(entry.get("genotype", ""))
            if not genotype_key:
                continue
            mapped_color = self._normalize_text(genotype_colors.get(genotype_key, ""))
            if entry.get("node_fill_color", "") == mapped_color:
                continue
            entry["node_fill_color"] = mapped_color
            entry["updated_at"] = datetime.utcnow().isoformat() + "Z"

        raw["genotype_colors"] = genotype_colors
        raw["animals"] = normalized_animals
        self._data = raw
        self.save()
        return self._data
    # end _normalize_and_cache

    @staticmethod
    def _atomic_write(path: str, payload: Dict[str, Any], plugin_dir: str) -> None:
        """Write *payload* to *path* atomically using a temp file.

        Falls back to a direct shutil.move on Windows paths where os.replace
        may raise PermissionError (e.g., network shares, antivirus locks).
        """
        os.makedirs(plugin_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="heritage_", suffix=".json", dir=plugin_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(payload, tmp, indent=2, ensure_ascii=False)
            # First attempt: atomic os.replace
            try:
                os.replace(temp_path, path)
                return  # success
            except PermissionError:
                pass
            # Retry once after a brief pause (antivirus / index service lock)
            time.sleep(0.05)
            try:
                os.replace(temp_path, path)
                return
            except PermissionError:
                pass
            # Final fallback: shutil.move (works across devices and Windows shares)
            shutil.move(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    def save(self) -> None:
        data = self.load()
        now = datetime.utcnow().isoformat() + "Z"
        data["updated_at"] = now

        animals_payload = {
            "version": data.get("version", "1.0.0"),
            "updated_at": now,
            "animals": data.get("animals", {}),
            "genotype_colors": data.get("genotype_colors", {}),
        }
        settings_payload = {
            "version": data.get("version", "1.0.0"),
            "updated_at": now,
            "settings": data.get("settings", self._default_settings()),
            "node_positions": data.get("node_positions", {}),
            "collapsed_families": data.get("collapsed_families", []),
        }

        self._atomic_write(self.file_path, animals_payload, self.plugin_dir)
        self._atomic_write(self.settings_path, settings_payload, self.plugin_dir)

    def _entry(self, animal_name: str) -> Dict[str, Any]:
        data = self.load()
        key = self._normalize_text(animal_name)
        if not key:
            return {}

        animals = data["animals"]
        if key not in animals:
            animals[key] = {
                **{k: "" for k in PARENT_KEYS},
                "genotype": "",
                "node_fill_color": "",
                "sex": "",
                "species": "",
                "heritage_only": False,
                "source": "plugin",
                "updated_at": datetime.utcnow().isoformat() + "Z",
                "inbreeding_f": None,
            }
        return animals[key]

    def get_settings(self) -> Dict[str, bool]:
        data = self.load()
        settings = data.get("settings", {}) if isinstance(data, dict) else {}
        default_settings = self._default_settings()
        if not isinstance(settings, dict):
            return dict(default_settings)
        return {
            "show_grid": bool(settings.get("show_grid", default_settings["show_grid"])),
            "snap_to_grid": bool(settings.get("snap_to_grid", default_settings["snap_to_grid"])),
            "show_heritage_only": bool(settings.get("show_heritage_only", default_settings["show_heritage_only"])),
            "show_legend": bool(settings.get("show_legend", default_settings["show_legend"])),
            "show_inbreeding_f": bool(settings.get("show_inbreeding_f", default_settings["show_inbreeding_f"])),
            "exclude_archived": bool(settings.get("exclude_archived", default_settings["exclude_archived"])),
        }

    def set_settings(self, settings: Dict[str, Any]) -> None:
        if not isinstance(settings, dict):
            return

        data = self.load()
        current = self.get_settings()
        for key in current.keys():
            if key in settings:
                current[key] = bool(settings.get(key))

        data["settings"] = current
        self.save()

    def get_all_entries(self) -> Dict[str, Dict[str, Any]]:
        data = self.load()
        return data.get("animals", {})

    def get_genotype_colors(self) -> Dict[str, str]:
        data = self.load()
        colors = data.get("genotype_colors", {}) if isinstance(data, dict) else {}
        if not isinstance(colors, dict):
            return {}
        normalized: Dict[str, str] = {}
        for genotype, color in colors.items():
            key = self._normalize_genotype_key(genotype)
            if not key:
                continue
            normalized[key] = self._normalize_text(color)
        return normalized

    def get_genotype_color(self, genotype: str) -> str:
        key = self._normalize_genotype_key(genotype)
        if not key:
            return ""
        return self.get_genotype_colors().get(key, "")

    def _apply_genotype_color_to_entries(self, genotype_key: str, fill_color: str) -> bool:
        data = self.load()
        animals = data.get("animals", {}) if isinstance(data, dict) else {}
        if not isinstance(animals, dict):
            return False

        changed = False
        now_iso = datetime.utcnow().isoformat() + "Z"
        for entry in animals.values():
            if not isinstance(entry, dict):
                continue
            entry_genotype_key = self._normalize_genotype_key(entry.get("genotype", ""))
            if entry_genotype_key != genotype_key:
                continue
            current_color = self._normalize_text(entry.get("node_fill_color", ""))
            if current_color == fill_color:
                continue
            entry["node_fill_color"] = fill_color
            entry["updated_at"] = now_iso
            changed = True
        return changed

    def set_genotype_color(self, genotype: str, fill_color: Optional[str], persist: bool = True) -> bool:
        genotype_key = self._normalize_genotype_key(genotype)
        if not genotype_key:
            return False

        color_value = self._normalize_text(fill_color)
        data = self.load()
        colors = data.get("genotype_colors", {})
        if not isinstance(colors, dict):
            colors = {}
            data["genotype_colors"] = colors

        changed = False
        if colors.get(genotype_key, "") != color_value:
            colors[genotype_key] = color_value
            changed = True

        if self._apply_genotype_color_to_entries(genotype_key, color_value):
            changed = True

        if changed and persist:
            self.save()
        return changed

    def get_node_positions(self) -> Dict[str, Tuple[float, float]]:
        data = self.load()
        raw_positions = data.get("node_positions", {}) if isinstance(data, dict) else {}
        if not isinstance(raw_positions, dict):
            return {}

        positions: Dict[str, Tuple[float, float]] = {}
        for name, raw_position in raw_positions.items():
            key = self._normalize_text(name)
            if not key:
                continue
            normalized = self._normalize_position(raw_position)
            if normalized is None:
                continue
            positions[key] = normalized
        return positions

    def set_node_position(self, animal_name: str, position: Tuple[float, float]) -> None:
        key = self._normalize_text(animal_name)
        normalized = self._normalize_position(position)
        if not key or normalized is None:
            return

        data = self.load()
        node_positions = data.get("node_positions", {})
        if not isinstance(node_positions, dict):
            node_positions = {}
            data["node_positions"] = node_positions

        node_positions[key] = {"x": normalized[0], "y": normalized[1]}
        self.save()

    def set_node_positions_batch(
        self, positions: Dict[str, Tuple[float, float]]
    ) -> None:
        """Update multiple node positions with a single save() call.

        Preferred over calling set_node_position() in a loop to avoid
        repeated disk writes (which cause PermissionErrors on Windows).
        """
        if not positions:
            return
        data = self.load()
        node_positions = data.get("node_positions", {})
        if not isinstance(node_positions, dict):
            node_positions = {}
            data["node_positions"] = node_positions
        for animal_name, position in positions.items():
            key = self._normalize_text(animal_name)
            normalized = self._normalize_position(position)
            if key and normalized is not None:
                node_positions[key] = {"x": normalized[0], "y": normalized[1]}
        self.save()

    def remove_node_position(self, animal_name: str) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        data = self.load()
        node_positions = data.get("node_positions", {})
        if not isinstance(node_positions, dict):
            return
        if key not in node_positions:
            return

        del node_positions[key]
        self.save()

    def get_collapsed_families(self) -> Set[str]:
        data = self.load()
        raw_families = data.get("collapsed_families", []) if isinstance(data, dict) else []
        if not isinstance(raw_families, list):
            return set()

        families: Set[str] = set()
        for family_id in raw_families:
            key = self._normalize_text(family_id)
            if key:
                families.add(key)
        return families

    def set_collapsed_families(self, family_ids: Iterable[str]) -> None:
        data = self.load()
        normalized: List[str] = []
        seen: Set[str] = set()
        for family_id in family_ids:
            key = self._normalize_text(family_id)
            if not key or key in seen:
                continue
            normalized.append(key)
            seen.add(key)

        data["collapsed_families"] = sorted(normalized, key=str.lower)
        self.save()

    def set_family_collapsed(self, family_id: str, collapsed: bool) -> None:
        key = self._normalize_text(family_id)
        if not key:
            return

        collapsed_families = self.get_collapsed_families()
        if collapsed:
            if key in collapsed_families:
                return
            collapsed_families.add(key)
        else:
            if key not in collapsed_families:
                return
            collapsed_families.remove(key)

        self.set_collapsed_families(collapsed_families)

    def get_parentage(self, animal_name: Optional[str], fallback_record: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        fallback = {
            "egg_donor": self._normalize_text((fallback_record or {}).get("eizellspenderin", "")),
            "sperm_donor": self._normalize_text((fallback_record or {}).get("samenspender", "")),
            "surrogate_mother": self._normalize_text((fallback_record or {}).get("ziehmutter", "")),
            "surrogate_father": self._normalize_text((fallback_record or {}).get("ziehvater", "")),
        }

        key = self._normalize_text(animal_name)
        if not key:
            return fallback

        entry = self.load().get("animals", {}).get(key, {})
        if not isinstance(entry, dict):
            return fallback

        # Heritage store is the source of truth for Heritage Track.
        # If an entry exists but its biological parent fields are empty,
        # fall back to the prog_track record's eizellspenderin/samenspender.
        stored = self._normalize_parents(entry)
        if not stored.get("egg_donor") and fallback.get("egg_donor"):
            stored["egg_donor"] = fallback["egg_donor"]
        if not stored.get("sperm_donor") and fallback.get("sperm_donor"):
            stored["sperm_donor"] = fallback["sperm_donor"]
        return stored

    def set_parentage(self, animal_name: str, parent_values: Dict[str, Any], source: str = "plugin") -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        entry = self._entry(key)
        normalized = self._normalize_parents(parent_values)
        for parent_key, value in normalized.items():
            entry[parent_key] = value
        entry["source"] = self._normalize_text(source) or "plugin"
        entry["updated_at"] = datetime.utcnow().isoformat() + "Z"
        entry["inbreeding_f"] = None
        self.save()

    def set_heritage_only(self, animal_name: str, heritage_only: bool) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        entry = self._entry(key)
        entry["heritage_only"] = bool(heritage_only)
        entry["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.save()

    def delete_animal(self, animal_name: str) -> bool:
        key = self._normalize_text(animal_name)
        if not key:
            return False

        data = self.load()
        animals = data.get("animals", {}) if isinstance(data, dict) else {}
        if not isinstance(animals, dict) or key not in animals:
            return False

        del animals[key]

        node_positions = data.get("node_positions", {})
        if isinstance(node_positions, dict) and key in node_positions:
            del node_positions[key]

        for entry in animals.values():
            if not isinstance(entry, dict):
                continue
            entry_changed = False
            for parent_key in PARENT_KEYS:
                if self._normalize_text(entry.get(parent_key, "")) == key:
                    entry[parent_key] = ""
                    entry_changed = True
            if entry_changed:
                entry["updated_at"] = datetime.utcnow().isoformat() + "Z"

        self.save()
        return True

    def is_heritage_only(self, animal_name: str) -> bool:
        key = self._normalize_text(animal_name)
        if not key:
            return False
        entry = self.load().get("animals", {}).get(key, {})
        if not isinstance(entry, dict):
            return False
        return bool(entry.get("heritage_only", False))

    def set_species(self, animal_name: str, species: Optional[str]) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return
        entry = self._entry(key)
        entry["species"] = self._normalize_text(species)
        entry["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.save()

    def get_species(self, animal_name: str) -> str:
        key = self._normalize_text(animal_name)
        if not key:
            return ""
        entry = self.load().get("animals", {}).get(key, {})
        if not isinstance(entry, dict):
            return ""
        return self._normalize_text(entry.get("species", ""))

    def set_manual_sex(self, animal_name: str, sex: Optional[str]) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        entry = self._entry(key)
        entry["sex"] = self._normalize_sex(sex)
        entry["updated_at"] = datetime.utcnow().isoformat() + "Z"
        self.save()

    def get_manual_sex(self, animal_name: str) -> str:
        key = self._normalize_text(animal_name)
        if not key:
            return ""

        entry = self.load().get("animals", {}).get(key, {})
        if not isinstance(entry, dict):
            return ""
        return self._normalize_sex(entry.get("sex", ""))

    def get_effective_sex(self, animal_name: Optional[str], fallback_record: Optional[Dict[str, Any]] = None) -> str:
        key = self._normalize_text(animal_name)
        if key:
            manual = self.get_manual_sex(key)
            if manual:
                return manual
        fallback_sex = self._normalize_sex((fallback_record or {}).get("sex", ""))
        return fallback_sex

    def set_node_visual(self, animal_name: str, genotype: Optional[str], fill_color: Optional[str]) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        entry = self._entry(key)
        changed = False
        old_genotype_text = self._normalize_text(entry.get("genotype", ""))
        old_genotype_key = self._normalize_genotype_key(old_genotype_text)
        old_fill_color = self._normalize_text(entry.get("node_fill_color", ""))

        if genotype is not None:
            normalized_genotype = self._normalize_text(genotype)
            if entry.get("genotype", "") != normalized_genotype:
                entry["genotype"] = normalized_genotype
                changed = True

        genotype_text = self._normalize_text(entry.get("genotype", ""))
        genotype_key = self._normalize_genotype_key(genotype_text)

        if fill_color is not None:
            normalized_color = self._normalize_text(fill_color)
            if genotype_key:
                mapped_color = self.get_genotype_color(genotype_text)
                # If genotype changed and color input stayed untouched, keep
                # the existing color already assigned to that genotype.
                if (
                    genotype_key != old_genotype_key
                    and normalized_color == old_fill_color
                    and mapped_color
                ):
                    normalized_color = mapped_color
                if self.set_genotype_color(genotype_text, normalized_color, persist=False):
                    changed = True
            elif entry.get("node_fill_color", "") != normalized_color:
                entry["node_fill_color"] = normalized_color
                changed = True
        elif genotype_key:
            mapped = self.get_genotype_color(genotype_text)
            if entry.get("node_fill_color", "") != mapped:
                entry["node_fill_color"] = mapped
                changed = True

        if changed:
            entry["updated_at"] = datetime.utcnow().isoformat() + "Z"
            self.save()

    def get_node_visual(self, animal_name: str, fallback_genotype: str = "") -> Dict[str, str]:
        key = self._normalize_text(animal_name)
        if not key:
            return {"genotype": self._normalize_text(fallback_genotype), "node_fill_color": ""}

        entry = self.load().get("animals", {}).get(key, {})
        genotype = self._normalize_text(entry.get("genotype", ""))
        if not genotype:
            genotype = self._normalize_text(fallback_genotype)

        genotype_key = self._normalize_genotype_key(genotype)
        genotype_colors = self.get_genotype_colors()
        if genotype_key and genotype_key in genotype_colors:
            fill_color = self._normalize_text(genotype_colors.get(genotype_key, ""))
        else:
            fill_color = self._normalize_text(entry.get("node_fill_color", ""))
        return {"genotype": genotype, "node_fill_color": fill_color}

    def sync_from_record(self, animal_name: str, record: Optional[Dict[str, Any]], persist: bool = True, in_main_animals: bool = True) -> bool:
        key = self._normalize_text(animal_name)
        if not key or not isinstance(record, dict):
            return False

        core_parent_map = {
            "egg_donor": "eizellspenderin",
            "sperm_donor": "samenspender",
            "surrogate_mother": "ziehmutter",
            "surrogate_father": "ziehvater",
        }

        has_parent_data = any(core_key in record for core_key in core_parent_map.values())
        has_genotype = "genotype" in record
        if not has_parent_data and not has_genotype:
            return False

        data = self.load()
        animals = data.get("animals", {}) if isinstance(data, dict) else {}
        entry_exists = isinstance(animals, dict) and key in animals
        entry = self._entry(key)
        changed = False

        if not entry_exists and self._normalize_text(entry.get("source", "")).lower() != "core":
            entry["source"] = "core"
            changed = True

        # Only clear heritage_only flag if the animal exists in main animals list
        # Heritage-only animals (not in main list) should keep their flag
        if entry.get("heritage_only", False) and in_main_animals:
            entry["heritage_only"] = False
            changed = True

        # Import from core only when creating a missing heritage entry.
        # Existing entries are authoritative in heritage.json.
        allow_core_backfill = not entry_exists

        for parent_key, core_key in core_parent_map.items():
            if core_key not in record:
                continue
            value = self._normalize_text(record.get(core_key, ""))
            current = self._normalize_text(entry.get(parent_key, ""))
            # Only import from core for missing values; keep heritage.json edits.
            if allow_core_backfill and not current and value:
                entry[parent_key] = value
                changed = True

        # Core mother/father semantics should map to explicit female/male sex,
        # not "auto", for referenced parent animals.
        if self._set_parent_sex_from_core_role(record.get("eizellspenderin", ""), "female"):
            changed = True
        if self._set_parent_sex_from_core_role(record.get("samenspender", ""), "male"):
            changed = True

        # Sync sex from core record (e.g., Male/Female/Unknown)
        # Also set sex deterministically based on role: female animals always female,
        # samenspender always male
        role = self._normalize_text(record.get("rolle", ""))
        role_determined_sex = ""
        if role in ("Spenderin", "Amme"):
            role_determined_sex = "female"
        elif role == "Samenspender":
            role_determined_sex = "male"

        if role_determined_sex:
            current_sex = self._normalize_sex(entry.get("sex", ""))
            # Role-determined sex always wins (overwrites existing)
            if current_sex != role_determined_sex:
                entry["sex"] = role_determined_sex
                changed = True
        elif "sex" in record:
            sex = self._normalize_sex(record.get("sex", ""))
            current_sex = self._normalize_sex(entry.get("sex", ""))
            # Only backfill missing sex from core.
            if allow_core_backfill and not current_sex and sex:
                entry["sex"] = sex
                changed = True

        if "genotype" in record:
            genotype = self._normalize_text(record.get("genotype", ""))
            current_genotype = self._normalize_text(entry.get("genotype", ""))
            # Only backfill missing genotype from core.
            if allow_core_backfill and not current_genotype and genotype:
                entry["genotype"] = genotype
                changed = True

        genotype_key = self._normalize_genotype_key(entry.get("genotype", ""))
        genotype_colors = self.get_genotype_colors()
        if genotype_key and genotype_key in genotype_colors:
            mapped_color = self._normalize_text(genotype_colors.get(genotype_key, ""))
            if entry.get("node_fill_color", "") != mapped_color:
                entry["node_fill_color"] = mapped_color
                changed = True

        # Sync additional core fields from ProgTrack record
        core_field_map = {
            "species": "species",
            "birth_date": "birth_date",
            "death_date": "death_date",
            "id": "id",
            "chip_nr": "chip_nr",
            "origin": "origin",
            "ref_weight": "ref_weight",
            "special_status": "special_status",
            "rolle": "rolle",
        }
        for core_key, entry_key in core_field_map.items():
            if core_key in record:
                value = self._normalize_text(record.get(core_key, ""))
                current = self._normalize_text(entry.get(entry_key, ""))
                # Backfill missing values from core
                if allow_core_backfill and not current and value:
                    entry[entry_key] = value
                    changed = True

        if changed:
            entry["updated_at"] = datetime.utcnow().isoformat() + "Z"
            if persist:
                self.save()

        return changed

    def get_inbreeding_f(self, animal_name: str) -> Optional[float]:
        key = self._normalize_text(animal_name)
        if not key:
            return None
        entry = self.load().get("animals", {}).get(key, {})
        if not isinstance(entry, dict):
            return None
        val = entry.get("inbreeding_f")
        if val is None:
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            return None

    def set_inbreeding_f_batch(self, updates: Dict[str, float]) -> None:
        """Set inbreeding_f for multiple animals in a single save() call."""
        if not updates:
            return
        now_iso = datetime.utcnow().isoformat() + "Z"
        for name, f_value in updates.items():
            key = self._normalize_text(name)
            if not key:
                continue
            entry = self._entry(key)
            entry["inbreeding_f"] = float(f_value)
            entry["updated_at"] = now_iso
        self.save()

    def sync_from_animals(self, animals: Any) -> None:
        if not isinstance(animals, dict):
            return

        changed = False
        data = self.load()
        store_animals = data.get("animals", {}) if isinstance(data, dict) else {}
        for name, record in animals.items():
            key = self._normalize_text(name)
            if not key or not isinstance(record, dict):
                continue

            existing = store_animals.get(key, {}) if isinstance(store_animals, dict) else {}
            if isinstance(existing, dict) and existing.get("heritage_only", False):
                existing["heritage_only"] = False
                existing["updated_at"] = datetime.utcnow().isoformat() + "Z"
                changed = True

            if self.sync_from_record(key, record, persist=False, in_main_animals=True):
                changed = True

        if changed:
            self.save()
