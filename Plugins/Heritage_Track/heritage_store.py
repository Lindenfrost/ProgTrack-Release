# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track persistence layer.

from __future__ import annotations

import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from Plugins.core.animal_identity import animal_base_name
from Plugins.core.animal_roles import ROLE_VALUE_AMME, ROLE_VALUE_SAMENSP, ROLE_VALUE_SPENDER, canonical_role_value
from Plugins.core.backend_store import BackendJsonStore
from Plugins.core.backend.errors import ConflictError

PARENT_KEYS = ("egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father")


class HeritageStore:
    # Runtime persistence is backend-only; the legacy JSON description below
    # is retained solely as historical context and is not an active contract.
    """Owns the Heritage graph in the shared backend.

    The legacy split-file layout is archived.  Runtime graph data, positions,
    collapse state, and settings are read from the configured backend record.
    """

    def __init__(self, plugin_dir: str, backend: Any):
        self.backend_store = BackendJsonStore(backend, "heritage", "graph")
        self._data: Optional[Dict[str, Any]] = None
        self._pending_animal_save = False
        self._pending_settings_save = False
        self._genotype_colors_cache: Optional[Dict[str, str]] = None
        self._backend_revision: int = 0

    def _default_settings(self) -> Dict[str, Any]:
        return {
            "show_grid": False,
            "snap_to_grid": False,
            "show_heritage_only": True,
            "show_legend": True,
            "exclude_archived": False,
            "vertical_layout_mode": "partner_normalized",
            "animal_label_detail": "inbreeding_f",
            "legend_pos": None,
        }

    @staticmethod
    def _normalize_vertical_layout_mode(value: Any) -> str:
        normalized = str(value or "").strip().casefold()
        if normalized == "chronological":
            return "chronological"
        return "partner_normalized"

    @staticmethod
    def _normalize_animal_label_detail(value: Any) -> str:
        normalized = str(value or "").strip().casefold()
        if normalized in {"nothing", "inbreeding_f", "birth_date", "animal_id"}:
            return normalized
        return "inbreeding_f"

    @staticmethod
    def _normalize_legend_pos(value: Any) -> Optional[List[float]]:
        """Normalize a persisted legend anchor in axes coordinates."""
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return None
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        return [max(0.0, min(1.0, x)), max(0.0, min(1.0, y))]

    @staticmethod
    def _utc_now_iso() -> str:
        # Keep the historical naive-UTC-plus-Z wire format stable.
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"

    def _default_data(self) -> Dict[str, Any]:
        return {
            "version": "1.0.0",
            "updated_at": self._utc_now_iso(),
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
        if text in {"u", "unknown", "unknown sex", "unbekannt", "sconosciuto", "sconosciuta"}:
            return "unknown"
        return ""

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
            # Do not create a backend record merely because a read/render occurred.
            self._data = self._default_data()
            self._genotype_colors_cache = None
            return self._data

        return self._normalize_and_cache(raw)

    def _load_raw(self) -> Optional[Dict[str, Any]]:
        """Load the combined Heritage record from the shared backend."""
        return self.backend_store.load(None)

    def get_backend_revision(self) -> int:
        """Return the current backend revision for the combined graph record."""
        _raw, revision = self.backend_store.load_with_revision(None)
        self._backend_revision = int(revision or 0)
        return self._backend_revision

    def atomic_update(self, mutator, *, expected_revision: int | None = None) -> Any:
        """Apply one graph mutation and persist it in one backend write.

        The callback receives a deep copy of the normalized graph.  If it
        raises, the cached graph and backend remain unchanged.  Real backend
        repositories perform the optimistic revision check in their write
        transaction; tiny legacy test stores continue to work via the
        BackendJsonStore fallback.
        """
        current = deepcopy(self.load())
        current_revision = self.get_backend_revision()
        if expected_revision is not None and int(expected_revision) != current_revision:
            raise ConflictError(
                f"Stale Heritage graph revision {expected_revision}; "
                f"current revision is {current_revision}."
            )
        working = deepcopy(current)
        result = mutator(working)
        working["updated_at"] = self._utc_now_iso()
        previous = self._data
        try:
            self.backend_store.save(
                working,
                expected_revision=current_revision if current_revision else None,
            )
        except Exception:
            self._data = previous
            raise
        self._data = working
        self._backend_revision = current_revision + 1
        self._genotype_colors_cache = None
        self._pending_animal_save = False
        self._pending_settings_save = False
        return result

    def _normalize_and_cache(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize a raw combined dict, populate self._data, and trigger save."""
        original = deepcopy(raw) if isinstance(raw, dict) else None
        if not isinstance(raw, dict):
            raw = self._default_data()

        raw.setdefault("version", "1.0.0")
        raw.setdefault("updated_at", self._utc_now_iso())
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
            "exclude_archived": bool(raw_settings.get("exclude_archived", default_settings["exclude_archived"])),
            "vertical_layout_mode": self._normalize_vertical_layout_mode(
                raw_settings.get("vertical_layout_mode", default_settings["vertical_layout_mode"])
            ),
            "animal_label_detail": self._normalize_animal_label_detail(
                raw_settings.get("animal_label_detail", default_settings["animal_label_detail"])
            ),
            "legend_pos": self._normalize_legend_pos(
                raw_settings.get("legend_pos", default_settings["legend_pos"])
            ),
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
            visible_name = (
                self._normalize_text(entry.get("name", ""))
                or self._normalize_text(entry.get("display_name", ""))
                or self._normalize_text(entry.get("_base_name", ""))
                or animal_base_name(name)
            )
            normalized_entry["ipid"] = self._normalize_text(entry.get("ipid", "")) or name.strip()
            normalized_entry["name"] = visible_name
            normalized_entry["_base_name"] = visible_name
            normalized_entry["display_name"] = visible_name
            normalized_entry["genotype"] = self._normalize_text(entry.get("genotype", ""))
            normalized_entry["node_fill_color"] = self._normalize_text(entry.get("node_fill_color", ""))
            normalized_entry["sex"] = self._normalize_sex(entry.get("sex", ""))
            normalized_entry["species"] = self._normalize_text(entry.get("species", ""))
            normalized_entry["birth_date"] = self._normalize_text(entry.get("birth_date", ""))
            normalized_entry["heritage_only"] = bool(entry.get("heritage_only", False))
            normalized_entry["source"] = self._normalize_text(entry.get("source", "plugin")) or "plugin"
            normalized_entry["updated_at"] = self._normalize_text(entry.get("updated_at", ""))
            normalized_entry["parentage_revision"] = self._normalize_text(
                entry.get("parentage_revision", entry.get("revision", ""))
            )
            normalized_entry["parentage_revision_display"] = self._normalize_text(
                entry.get("parentage_revision_display", "")
            )
            if entry.get("identity_review_required"):
                normalized_entry["identity_review_required"] = True
                normalized_entry["identity_review_reason"] = self._normalize_text(
                    entry.get("identity_review_reason", ""))
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
            entry["updated_at"] = self._utc_now_iso()

        raw["genotype_colors"] = genotype_colors
        raw["animals"] = normalized_animals
        self._genotype_colors_cache = None
        self._data = raw
        if original != raw:
            self._save_sections(animals=True, settings=True)
        return self._data
    # end _normalize_and_cache

    def _mark_pending_sections(self, *, animals: bool = False, settings: bool = False) -> None:
        self._pending_animal_save = self._pending_animal_save or animals
        self._pending_settings_save = self._pending_settings_save or settings

    def has_pending_changes(self) -> bool:
        return self._pending_animal_save or self._pending_settings_save

    def flush_pending(self) -> bool:
        """Persist queued derived changes once, outside paint/resize handlers."""
        if not self.has_pending_changes():
            return False
        self._save_sections(
            animals=self._pending_animal_save,
            settings=self._pending_settings_save,
        )
        return True

    def _save_sections(
        self,
        *,
        animals: bool,
        settings: bool,
        defer: bool = False,
    ) -> None:
        """Persist requested sections or queue them for an explicit flush."""
        if not animals and not settings:
            return
        if defer:
            self._mark_pending_sections(animals=animals, settings=settings)
            return

        data = self.load()
        data["updated_at"] = self._utc_now_iso()
        self.backend_store.save(data)
        if animals:
            self._pending_animal_save = False
        if settings:
            self._pending_settings_save = False

    def _save_animals(self) -> None:
        """Persist only animal records and genotype colours."""
        self._save_sections(animals=True, settings=False)

    def _save_settings(self) -> None:
        """Persist only UI settings, node positions, and collapsed families."""
        self._save_sections(animals=False, settings=True)

    def save(self) -> None:
        """Persist the complete combined store."""
        self._save_sections(animals=True, settings=True)

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
                "updated_at": self._utc_now_iso(),
                "inbreeding_f": None,
                "parentage_revision": "",
                "parentage_revision_display": "",
            }
        return animals[key]

    def get_settings(self) -> Dict[str, Any]:
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
            "exclude_archived": bool(settings.get("exclude_archived", default_settings["exclude_archived"])),
            "vertical_layout_mode": self._normalize_vertical_layout_mode(
                settings.get("vertical_layout_mode", default_settings["vertical_layout_mode"])
            ),
            "animal_label_detail": self._normalize_animal_label_detail(
                settings.get("animal_label_detail", default_settings["animal_label_detail"])
            ),
            "legend_pos": self._normalize_legend_pos(
                settings.get("legend_pos", default_settings["legend_pos"])
            ),
        }

    def set_settings(self, settings: Dict[str, Any]) -> None:
        if not isinstance(settings, dict):
            return

        data = self.load()
        current = self.get_settings()
        for key in ("show_grid", "snap_to_grid", "show_heritage_only", "show_legend", "exclude_archived"):
            if key in settings:
                current[key] = bool(settings.get(key))
        if "vertical_layout_mode" in settings:
            current["vertical_layout_mode"] = self._normalize_vertical_layout_mode(
                settings.get("vertical_layout_mode")
            )
        if "animal_label_detail" in settings:
            current["animal_label_detail"] = self._normalize_animal_label_detail(
                settings.get("animal_label_detail")
            )
        if "legend_pos" in settings:
            current["legend_pos"] = self._normalize_legend_pos(settings.get("legend_pos"))

        data["settings"] = current
        self._save_settings()

    def get_all_entries(self) -> Dict[str, Dict[str, Any]]:
        data = self.load()
        return data.get("animals", {})

    def get_genotype_colors(self) -> Dict[str, str]:
        if self._genotype_colors_cache is not None:
            return dict(self._genotype_colors_cache)
        data = self.load()
        colors = data.get("genotype_colors", {}) if isinstance(data, dict) else {}
        if not isinstance(colors, dict):
            self._genotype_colors_cache = {}
            return {}
        normalized: Dict[str, str] = {}
        for genotype, color in colors.items():
            key = self._normalize_genotype_key(genotype)
            if not key:
                continue
            normalized[key] = self._normalize_text(color)
        self._genotype_colors_cache = normalized
        return dict(normalized)

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
        now_iso = self._utc_now_iso()
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

        if changed:
            self._genotype_colors_cache = None
            if persist:
                self._save_animals()
            else:
                self._mark_pending_sections(animals=True)
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
        self._save_settings()

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
        self._save_settings()

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
        self._save_settings()

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
        self._save_settings()

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
        entry["updated_at"] = self._utc_now_iso()
        entry["inbreeding_f"] = None
        self._save_animals()

    def set_heritage_only(self, animal_name: str, heritage_only: bool) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        entry = self._entry(key)
        entry["heritage_only"] = bool(heritage_only)
        entry["updated_at"] = self._utc_now_iso()
        self._save_animals()

    def set_heritage_only_batch(self, animal_names: Iterable[str], heritage_only: bool) -> None:
        changed = False
        timestamp = self._utc_now_iso()
        target = bool(heritage_only)
        for animal_name in animal_names:
            key = self._normalize_text(animal_name)
            if not key:
                continue
            entry = self._entry(key)
            if bool(entry.get("heritage_only", False)) == target:
                continue
            entry["heritage_only"] = target
            entry["updated_at"] = timestamp
            changed = True
        if changed:
            self._save_animals()

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
                entry["updated_at"] = self._utc_now_iso()

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
        entry["updated_at"] = self._utc_now_iso()
        self._save_animals()

    def set_identity_fields(
        self,
        animal_name: str,
        *,
        display_name: Optional[str] = None,
        species: Optional[str] = None,
        birth_date: Optional[str] = None,
        review_required: bool = False,
        review_reason: str = "",
    ) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return
        visible = self._normalize_text(display_name) or animal_base_name(key)
        entry = self._entry(key)
        entry["ipid"] = key
        entry["name"] = visible
        entry["_base_name"] = visible
        entry["display_name"] = visible
        if species is not None:
            entry["species"] = self._normalize_text(species)
        if birth_date is not None:
            entry["birth_date"] = self._normalize_text(birth_date)
        if review_required:
            entry["identity_review_required"] = True
            entry["identity_review_reason"] = self._normalize_text(review_reason)
        else:
            entry.pop("identity_review_required", None)
            entry.pop("identity_review_reason", None)
        entry["updated_at"] = self._utc_now_iso()
        self._save_animals()

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
        entry["updated_at"] = self._utc_now_iso()
        self._save_animals()

    def set_manual_sex_batch(self, updates: Dict[str, str]) -> None:
        changed = False
        timestamp = self._utc_now_iso()
        for animal_name, sex in updates.items():
            key = self._normalize_text(animal_name)
            if not key:
                continue
            normalized = self._normalize_sex(sex)
            entry = self._entry(key)
            if self._normalize_sex(entry.get("sex", "")) == normalized:
                continue
            entry["sex"] = normalized
            entry["updated_at"] = timestamp
            changed = True
        if changed:
            self._save_animals()

    def get_manual_sex(self, animal_name: str) -> str:
        key = self._normalize_text(animal_name)
        if not key:
            return ""

        entry = self.load().get("animals", {}).get(key, {})
        if not isinstance(entry, dict):
            return ""
        return self._normalize_sex(entry.get("sex", ""))

    def get_effective_sex(self, animal_name: Optional[str], fallback_record: Optional[Dict[str, Any]] = None) -> str:
        fallback_sex = self._normalize_sex((fallback_record or {}).get("sex", ""))
        if fallback_sex:
            return fallback_sex
        key = self._normalize_text(animal_name)
        if key:
            manual = self.get_manual_sex(key)
            if manual:
                return manual
        return ""

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
            entry["updated_at"] = self._utc_now_iso()
            self._save_animals()

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

        data = self.load()
        animals = data.get("animals", {}) if isinstance(data, dict) else {}
        entry_exists = isinstance(animals, dict) and key in animals
        entry = self._entry(key)
        changed = False

        visible_name = animal_base_name(key, record)
        for field, value in (
            ("ipid", key),
            ("name", visible_name),
            ("_base_name", visible_name),
            ("display_name", visible_name),
        ):
            if self._normalize_text(entry.get(field, "")) != value:
                entry[field] = value
                changed = True

        if in_main_animals and self._normalize_text(entry.get("source", "")).lower() != "core":
            entry["source"] = "core"
            changed = True

        # Main/archived application membership is authoritative.  A stale
        # Heritage flag must never turn a real animal into an editable
        # Heritage-only placeholder.
        if in_main_animals and bool(entry.get("heritage_only", False)):
            entry["heritage_only"] = False
            changed = True

        # Import from core only when creating a missing heritage entry.
        # Existing entries in the backend graph are authoritative.
        allow_core_backfill = not entry_exists

        for parent_key, core_key in core_parent_map.items():
            if core_key not in record:
                continue
            value = self._normalize_text(record.get(core_key, ""))
            current = self._normalize_text(entry.get(parent_key, ""))
            # Only import from core for missing values; keep graph edits.
            if allow_core_backfill and not current and value:
                entry[parent_key] = value
                changed = True

        # Sex on a real application animal is owned by its core record.  An
        # explicit Unknown is a real value and wins over role inference.  A
        # role is used only for legacy records that have no explicit value.
        role = canonical_role_value(record.get("rolle", ""))
        role_determined_sex = ""
        if role in (ROLE_VALUE_SPENDER, ROLE_VALUE_AMME):
            role_determined_sex = "female"
        elif role == ROLE_VALUE_SAMENSP:
            role_determined_sex = "male"
        explicit_sex = self._normalize_sex(record.get("sex", ""))
        authoritative_sex = explicit_sex or role_determined_sex
        current_sex = self._normalize_sex(entry.get("sex", ""))
        if in_main_animals and current_sex != authoritative_sex:
            entry["sex"] = authoritative_sex
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
            entry["updated_at"] = self._utc_now_iso()
            if persist:
                self._save_animals()

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

    def set_inbreeding_f_batch(
        self, updates: Dict[str, float], *, persist: bool = True
    ) -> bool:
        """Set derived F values, optionally queueing the write outside rendering."""
        if not updates:
            return False
        now_iso = self._utc_now_iso()
        changed = False
        for name, f_value in updates.items():
            key = self._normalize_text(name)
            if not key:
                continue
            entry = self._entry(key)
            value = float(f_value)
            try:
                current = float(entry.get("inbreeding_f"))
            except (TypeError, ValueError):
                current = None
            if current == value:
                continue
            entry["inbreeding_f"] = value
            entry["updated_at"] = now_iso
            changed = True
        if not changed:
            return False
        if persist:
            self._save_animals()
        else:
            self._mark_pending_sections(animals=True)
        return True

    def sync_from_animals(self, animals: Any, *, persist: bool = True) -> bool:
        if not isinstance(animals, dict):
            return

        changed = False
        data = self.load()
        store_animals = data.get("animals", {}) if isinstance(data, dict) else {}
        core_keys = {
            self._normalize_text(name)
            for name in animals
            if self._normalize_text(name)
        }
        for name, record in animals.items():
            key = self._normalize_text(name)
            if not key or not isinstance(record, dict):
                continue

            if self.sync_from_record(key, record, persist=False, in_main_animals=True):
                changed = True

        # Reconcile both directions in one pass.  Records present in the app
        # (active or archived, as supplied by the plugin) are real; records
        # present only in this store are Heritage-only regardless of a stale
        # serialized flag.
        if isinstance(store_animals, dict):
            timestamp = self._utc_now_iso()
            for key, entry in store_animals.items():
                if not isinstance(entry, dict):
                    continue
                expected = key not in core_keys
                if bool(entry.get("heritage_only", False)) == expected:
                    continue
                entry["heritage_only"] = expected
                entry["updated_at"] = timestamp
                changed = True

        if changed:
            if persist:
                self._save_animals()
            else:
                self._mark_pending_sections(animals=True)
        return changed
