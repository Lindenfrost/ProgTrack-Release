# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.3
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: see release metadata.
# Module: Heritage Track persistence layer.

from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from Plugins.core.animal_identity import animal_base_name
from Plugins.core.animal_roles import ROLE_VALUE_AMME, ROLE_VALUE_SAMENSP, ROLE_VALUE_SPENDER, canonical_role_value
from Plugins.core.backend_store import BackendJsonStore
from Plugins.core.backend.errors import ConflictError

PARENT_KEYS = ("egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father")
_PATCH_DELETE = object()


class HeritageStore:
    # Runtime persistence is backend-only; the legacy JSON description below
    # is retained solely as historical context and is not an active contract.
    """Owns the Heritage graph in the shared backend.

    The legacy split-file layout is archived.  Runtime graph data, positions,
    collapse state, and settings are read from the configured backend record.
    """

    POSITION_CACHE_LIMIT = 1000

    def __init__(self, plugin_dir: str, backend: Any):
        self.backend_store = BackendJsonStore(backend, "heritage", "graph")
        self._data: Optional[Dict[str, Any]] = None
        self._pending_animal_save = False
        self._pending_settings_save = False
        # The editable in-memory view is never used as a complete write
        # payload.  This is the last backend snapshot from which the view was
        # derived; writes below diff against it and apply field-level patches.
        self._committed_snapshot: Optional[Dict[str, Any]] = None
        self._genotype_colors_cache: Optional[Dict[str, str]] = None
        self._backend_revision: int = 0
        # Invalid legacy coordinates are kept out of the normalized in-memory
        # view, but remembered until an explicit refresh can clean them up.
        # This prevents opening/resizing a graph (or an unrelated settings
        # save) from silently rewriting backend data.
        self._invalid_node_positions: Dict[str, Any] = {}

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
            # User/selection-scoped logical positions.  The legacy
            # ``node_positions`` map is intentionally not migrated into this
            # cache: it has no reliable selection or user ownership metadata.
            "position_cache": {},
            "collapsed_families": [],
            "genotype_colors": {},
            # Derived values for real Core animals live in their own
            # Heritage-owned namespace.  Core animal records are never copied
            # into ``animals`` merely to persist a rebuildable F value.
            "derived_inbreeding_cache": {},
            "pedigree_revision": "",
            "pedigree_sequence": 0,
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

    def _normalize_inbreeding_cache(self, value: Any) -> Optional[Dict[str, Any]]:
        """Validate one structured, lineage-bound F cache entry.

        A legacy scalar ``inbreeding_f`` value deliberately does not satisfy
        this contract; callers must recalculate it when revision metadata is
        absent or malformed.
        """
        if not isinstance(value, dict):
            return None
        status = self._normalize_text(value.get("status", "")).casefold()
        if status not in {"valid", "unavailable"}:
            return None
        pedigree_revision = self._normalize_text(value.get("pedigree_revision", ""))
        lineage_fingerprint = self._normalize_text(value.get("lineage_fingerprint", ""))
        if not pedigree_revision or not lineage_fingerprint:
            return None
        numeric: Optional[float] = None
        if status == "valid":
            try:
                numeric = float(value.get("value"))
            except (TypeError, ValueError):
                return None
            if not math.isfinite(numeric) or numeric < 0.0 or numeric > 1.0:
                return None
        return {
            "value": numeric,
            "pedigree_revision": pedigree_revision,
            "lineage_fingerprint": lineage_fingerprint,
            "status": status,
        }

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

        # Persisted geometry must be finite.  ``float`` accepts textual
        # Infinity values, so the explicit check is required in addition to
        # conversion.  Comma-decimal input intentionally remains invalid.
        if not math.isfinite(x) or not math.isfinite(y):
            return None
        return x, y

    def _normalize_position_cache_entry(self, value: Any) -> Optional[Dict[str, Any]]:
        """Normalize one persisted, user-owned selection position map.

        Cache records are deliberately independent of the historical global
        ``node_positions`` map.  Invalid coordinates are discarded at the
        cache boundary so a malformed saved entry can never reach routing.
        """
        if not isinstance(value, dict):
            return None
        raw_positions = value.get("positions", {})
        if not isinstance(raw_positions, dict):
            return None
        positions: Dict[str, Dict[str, float]] = {}
        for raw_name, raw_position in raw_positions.items():
            name = self._normalize_text(raw_name)
            normalized = self._normalize_position(raw_position)
            if not name or normalized is None:
                return None
            x, y = normalized
            positions[name] = {"x": x, "y": y}
        # Position validity is scoped to the records which can affect this
        # layout.  Older entries only contain the global pedigree token and
        # are intentionally discarded: they cannot prove that an unrelated
        # lineage edit left this selection's geometry unchanged.
        dependency_revision = self._normalize_text(value.get("dependency_revision", ""))
        if not dependency_revision:
            return None
        revision = self._normalize_text(value.get("pedigree_revision", "")) or dependency_revision
        raw_dependencies = value.get("dependency_ids", ())
        if isinstance(raw_dependencies, (str, bytes)):
            raw_dependencies = (raw_dependencies,)
        if not isinstance(raw_dependencies, (list, tuple, set, frozenset)):
            return None
        dependencies = sorted(
            {
                self._normalize_text(item)
                for item in raw_dependencies
                if self._normalize_text(item)
            },
            key=lambda item: (item.casefold(), item),
        )
        updated_at = self._normalize_text(value.get("updated_at", ""))
        if not updated_at:
            return None
        selection_type = self._normalize_text(value.get("selection_type", "selected")) or "selected"
        return {
            "pedigree_revision": revision,
            "dependency_revision": dependency_revision,
            "dependency_ids": dependencies,
            "positions": positions,
            "selection_type": selection_type,
            "updated_at": updated_at,
        }

    @classmethod
    def build_position_dependency_revision(
        cls,
        dependency_ids: Iterable[str],
        parent_map: Dict[str, Any],
        records: Dict[str, Any],
    ) -> str:
        """Hash only the immutable inputs that can affect one layout.

        The aggregate backend pedigree revision is deliberately not part of
        this token.  A change in a disjoint pedigree component must not turn a
        valid user/selection position map into a cache miss.  Callers provide
        the exact display/dependency scope and snapshots captured for one
        render transaction.
        """
        dependencies = sorted(
            {str(item or "").strip() for item in dependency_ids if str(item or "").strip()},
            key=lambda item: (item.casefold(), item),
        )
        normalized_parent_map: Dict[str, Dict[str, str]] = {}
        for node in dependencies:
            raw_values = parent_map.get(node, {}) if isinstance(parent_map, dict) else {}
            if not isinstance(raw_values, dict):
                raw_values = {}
            normalized_parent_map[node] = {
                str(key): str(raw_values.get(key, "") or "").strip()
                for key in sorted(raw_values, key=str)
            }
        normalized_records: Dict[str, Any] = {}
        for node in dependencies:
            value = records.get(node, {}) if isinstance(records, dict) else {}
            normalized_records[node] = deepcopy(value) if isinstance(value, dict) else {}
        payload = {
            "schema": "heritage-position-dependencies.v1",
            "dependencies": dependencies,
            "parent_map": normalized_parent_map,
            "records": normalized_records,
        }
        encoded = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def load(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data

        raw, revision = self.backend_store.load_with_revision(None)
        if raw is None:
            # Do not create a backend record merely because a read/render occurred.
            self._data = self._default_data()
            self._committed_snapshot = deepcopy(self._data)
            self._backend_revision = int(revision or 0)
            self._genotype_colors_cache = None
            self._invalid_node_positions = {}
            return self._data

        # Reads (including render-time projection) must not rewrite the shared
        # backend merely because an older payload needs normalization.  The
        # normalized in-memory view is repaired by an explicit write path such
        # as sync_from_animals(), an edit command, or flush_pending().
        normalized = self._normalize_and_cache(raw, persist=False)
        self._committed_snapshot = deepcopy(normalized)
        self._backend_revision = int(revision or 0)
        return normalized

    def _load_raw(self) -> Optional[Dict[str, Any]]:
        """Load the combined Heritage record from the shared backend."""
        return self.backend_store.load(None)

    def load_latest_with_revision(self) -> Tuple[Dict[str, Any], int]:
        """Read and normalize the current backend graph without persisting it.

        Command handlers must not build a write payload from a long-lived
        in-memory graph: another session may have committed a change since
        the plugin was opened.  Normalization is therefore applied to a copy
        of the latest backend payload and the command caller decides when the
        resulting snapshot is published.
        """
        raw, revision = self.backend_store.load_with_revision(None)
        previous = self._data
        previous_invalid = self._invalid_node_positions
        try:
            if raw is None:
                snapshot = deepcopy(self._default_data())
            else:
                snapshot = deepcopy(self._normalize_and_cache(deepcopy(raw), persist=False))
        finally:
            self._data = previous
            self._invalid_node_positions = previous_invalid
            self._genotype_colors_cache = None
        return snapshot, int(revision or 0)

    def get_backend_revision(self) -> int:
        """Return the current backend revision for the combined graph record."""
        _raw, revision = self.backend_store.load_with_revision(None)
        self._backend_revision = int(revision or 0)
        return self._backend_revision

    def adopt_read_snapshot(self, snapshot: Dict[str, Any], revision: int) -> None:
        """Adopt a freshly read backend snapshot without writing it.

        This keeps non-render callers from continuing to observe an older
        ``_data`` cache after another session commits.  Pending derived writes
        are deliberately left untouched; their flush path still performs its
        own optimistic read/merge.
        """
        if self.has_pending_changes() or not isinstance(snapshot, dict):
            return
        self._data = deepcopy(snapshot)
        self._committed_snapshot = deepcopy(snapshot)
        self._backend_revision = int(revision or 0)
        self._genotype_colors_cache = None

    def atomic_update(
        self,
        mutator,
        *,
        expected_revision: int | None = None,
        preserve_pending: bool = True,
    ) -> Any:
        """Apply one graph mutation and persist it in one backend write.

        The callback receives a deep copy of the normalized graph.  If it
        raises, the cached graph and backend remain unchanged.  Real backend
        repositories perform the optimistic revision check in their write
        transaction; tiny legacy test stores continue to work via the
        BackendJsonStore fallback.
        """
        # A render or another command may have queued a derived patch in the
        # same client while this atomic operation was prepared.  Capture that
        # patch before reading the latest backend snapshot so an unrelated
        # atomic mutation cannot clear or discard it.
        pending_patch: Optional[Dict[str, Any]] = None
        pending_baseline: Optional[Dict[str, Any]] = None
        if preserve_pending and self.has_pending_changes():
            local = self.load()
            baseline = self._committed_snapshot
            if isinstance(local, dict) and isinstance(baseline, dict):
                pending_patch = self._compute_persistence_patch(
                    local,
                    baseline,
                    animals=self._pending_animal_save,
                    settings=self._pending_settings_save,
                )
                pending_baseline = deepcopy(baseline)

        current, current_revision = self.load_latest_with_revision()
        if expected_revision is not None and int(expected_revision) != current_revision:
            raise ConflictError(
                f"Stale Heritage graph revision {expected_revision}; "
                f"current revision is {current_revision}."
            )
        working = deepcopy(current)
        if pending_patch and pending_patch.get("has_changes"):
            self._check_patch_conflicts(
                pending_patch,
                pending_baseline or {},
                working,
            )
            self._apply_persistence_patch(working, pending_patch)
        result = mutator(working)
        working["updated_at"] = self._utc_now_iso()
        previous = self._data
        try:
            next_revision = self.backend_store.save(
                working, expected_revision=current_revision
            )
        except Exception:
            self._data = previous
            raise
        self._data = working
        self._backend_revision = int(next_revision or (current_revision + 1))
        self._committed_snapshot = deepcopy(working)
        self._genotype_colors_cache = None
        # ``preserve_pending=False`` is used only by the position-cache
        # mutator, which already merges its pending patch explicitly.  All
        # other callers commit the captured pending patch in this same
        # transaction, so clearing the flags is now safe and lossless.
        self._pending_animal_save = False
        self._pending_settings_save = False
        return result

    def _normalize_and_cache(
        self, raw: Dict[str, Any], *, persist: bool = True
    ) -> Dict[str, Any]:
        """Normalize a raw combined dict, populate self._data, and trigger save."""
        original = deepcopy(raw) if isinstance(raw, dict) else None
        if not isinstance(raw, dict):
            raw = self._default_data()

        raw.setdefault("version", "1.0.0")
        raw.setdefault("updated_at", self._utc_now_iso())
        if not isinstance(raw.get("animals"), dict):
            raw["animals"] = {}
        if not isinstance(raw.get("node_positions"), dict):
            self._invalid_node_positions = {
                "__node_positions__": deepcopy(raw.get("node_positions"))
            }
            raw["node_positions"] = {}
        else:
            self._invalid_node_positions = {}
        raw_position_cache = raw.get("position_cache", {})
        if not isinstance(raw_position_cache, dict):
            raw_position_cache = {}
        normalized_position_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for raw_user, raw_entries in raw_position_cache.items():
            user = self._normalize_text(raw_user) or "guest"
            if not isinstance(raw_entries, dict):
                continue
            user_entries: Dict[str, Dict[str, Any]] = {}
            for raw_key, raw_entry in raw_entries.items():
                key = self._normalize_text(raw_key)
                normalized_entry = self._normalize_position_cache_entry(raw_entry)
                if key and normalized_entry is not None:
                    user_entries[key] = normalized_entry
            if user_entries:
                # Existing payloads may have been written before the bound was
                # introduced.  Keep the newest deterministic entries only.
                ordered = sorted(
                    user_entries.items(),
                    key=lambda item: (
                        str(item[1].get("updated_at", "")),
                        item[0],
                    ),
                    reverse=True,
                )[: self.POSITION_CACHE_LIMIT]
                normalized_position_cache[user] = dict(ordered)
        raw["position_cache"] = normalized_position_cache
        if not isinstance(raw.get("genotype_colors"), dict):
            raw["genotype_colors"] = {}
        if not isinstance(raw.get("collapsed_families"), list):
            raw["collapsed_families"] = []
        raw["pedigree_revision"] = self._normalize_text(raw.get("pedigree_revision", ""))
        try:
            raw["pedigree_sequence"] = max(0, int(raw.get("pedigree_sequence", 0) or 0))
        except (TypeError, ValueError):
            raw["pedigree_sequence"] = 0

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
                self._invalid_node_positions[key] = deepcopy(raw_position)
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
            normalized_entry["unit_id"] = self._normalize_text(entry.get("unit_id", ""))
            normalized_entry["dummy_kind"] = self._normalize_text(entry.get("dummy_kind", ""))
            normalized_entry["persistence_kind"] = self._normalize_text(entry.get("persistence_kind", ""))
            normalized_entry["source"] = self._normalize_text(entry.get("source", "plugin")) or "plugin"
            normalized_entry["updated_at"] = self._normalize_text(entry.get("updated_at", ""))
            normalized_entry["parentage_revision"] = self._normalize_text(
                entry.get("parentage_revision", entry.get("revision", ""))
            )
            normalized_entry["parentage_revision_display"] = self._normalize_text(
                entry.get("parentage_revision_display", "")
            )
            normalized_entry["genetic_parentage_revision"] = self._normalize_text(
                entry.get("genetic_parentage_revision", "")
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
            f_cache = self._normalize_inbreeding_cache(entry.get("inbreeding_f_cache"))
            if f_cache is not None:
                normalized_entry["inbreeding_f_cache"] = f_cache
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

        derived_cache = raw.get("derived_inbreeding_cache", {})
        if not isinstance(derived_cache, dict):
            derived_cache = {}
        normalized_derived_cache: Dict[str, Dict[str, Any]] = {}
        for raw_key, raw_meta in derived_cache.items():
            cache_key = self._normalize_text(raw_key)
            metadata = self._normalize_inbreeding_cache(raw_meta)
            if cache_key and metadata is not None:
                normalized_derived_cache[cache_key] = metadata

        raw["genotype_colors"] = genotype_colors
        raw["derived_inbreeding_cache"] = normalized_derived_cache
        raw["animals"] = normalized_animals
        self._genotype_colors_cache = None
        self._data = raw
        if persist and original != raw:
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

        local = self.load()
        baseline = self._committed_snapshot
        if not isinstance(baseline, dict):
            # ``load`` establishes this in normal operation.  Keep a safe
            # fallback for custom stores that construct a store by hand.
            baseline = deepcopy(local)
        patch = self._compute_persistence_patch(
            local, baseline, animals=animals, settings=settings
        )
        if not patch["has_changes"]:
            if animals:
                self._pending_animal_save = False
            if settings:
                self._pending_settings_save = False
            return

        # Always read immediately before modifying the backend.  The payload
        # is a fresh snapshot plus a field-level patch, never stale ``_data``.
        latest, revision = self.load_latest_with_revision()
        self._check_patch_conflicts(patch, baseline, latest)
        merged = deepcopy(latest)
        self._apply_persistence_patch(merged, patch)

        # Preserve malformed legacy coordinates until the explicit cleanup
        # command.  An unrelated save must not silently repair or discard
        # them, but it also must not replace any valid coordinate from the
        # newer backend snapshot.
        if self._invalid_node_positions:
            has_invalid_container = "__node_positions__" in self._invalid_node_positions
            invalid_container = self._invalid_node_positions.get("__node_positions__")
            if has_invalid_container:
                merged["node_positions"] = deepcopy(invalid_container)
            node_positions = merged.setdefault("node_positions", {})
            if isinstance(node_positions, dict) and not has_invalid_container:
                for key, raw_position in self._invalid_node_positions.items():
                    if key != "__node_positions__":
                        node_positions.setdefault(key, deepcopy(raw_position))
        merged["updated_at"] = self._utc_now_iso()
        try:
            next_revision = self.backend_store.save(
                merged, expected_revision=revision
            )
        except Exception:
            # Retain the pending flags and editable local view so the caller
            # can retry after a revision conflict or transient backend error.
            raise

        # If another section is still dirty, preserve that local draft while
        # marking only the requested patch as committed.
        remaining = self._compute_persistence_patch(
            local, baseline, animals=not animals, settings=not settings
        )
        visible = deepcopy(merged)
        self._apply_persistence_patch(visible, remaining)
        self._data = visible
        self._committed_snapshot = deepcopy(merged)
        self._backend_revision = int(next_revision or (int(revision or 0) + 1))
        self._genotype_colors_cache = None
        if animals:
            self._pending_animal_save = False
        if settings:
            self._pending_settings_save = False

    @staticmethod
    def _mapping_value(mapping: Any, key: Any) -> Any:
        if isinstance(mapping, dict) and key in mapping:
            return mapping[key]
        return _PATCH_DELETE

    def _compute_persistence_patch(
        self,
        current: Dict[str, Any],
        baseline: Dict[str, Any],
        *,
        animals: bool,
        settings: bool,
    ) -> Dict[str, Any]:
        """Build explicit field-level changes from the last backend snapshot."""
        patch: Dict[str, Any] = {
            "animal_fields": {},
            "animal_deletes": set(),
            "genotype_colors": {},
            "derived_inbreeding_cache": {},
            "settings": {},
            "node_positions": {},
            "collapsed_added": set(),
            "collapsed_removed": set(),
            "has_changes": False,
        }
        if animals:
            current_animals = current.get("animals", {}) if isinstance(current, dict) else {}
            base_animals = baseline.get("animals", {}) if isinstance(baseline, dict) else {}
            if not isinstance(current_animals, dict):
                current_animals = {}
            if not isinstance(base_animals, dict):
                base_animals = {}
            for key in sorted(set(current_animals) | set(base_animals), key=str):
                current_entry = self._mapping_value(current_animals, key)
                base_entry = self._mapping_value(base_animals, key)
                if current_entry is _PATCH_DELETE:
                    if base_entry is not _PATCH_DELETE:
                        patch["animal_deletes"].add(key)
                    continue
                if base_entry is _PATCH_DELETE:
                    patch["animal_fields"][key] = deepcopy(current_entry)
                    continue
                if not isinstance(current_entry, dict) or not isinstance(base_entry, dict):
                    if current_entry != base_entry:
                        patch["animal_fields"][key] = deepcopy(current_entry)
                    continue
                fields: Dict[str, Any] = {}
                for field in sorted(set(current_entry) | set(base_entry), key=str):
                    # ``updated_at`` is local provenance metadata.  It is
                    # regenerated for the committed aggregate and must not
                    # turn an unrelated concurrent edit into a field
                    # conflict or overwrite its newer timestamp.
                    if field == "updated_at":
                        continue
                    value = self._mapping_value(current_entry, field)
                    old_value = self._mapping_value(base_entry, field)
                    if value != old_value:
                        fields[field] = value if value is _PATCH_DELETE else deepcopy(value)
                if fields:
                    patch["animal_fields"][key] = fields

            current_colors = current.get("genotype_colors", {})
            base_colors = baseline.get("genotype_colors", {})
            if not isinstance(current_colors, dict):
                current_colors = {}
            if not isinstance(base_colors, dict):
                base_colors = {}
            for key in sorted(set(current_colors) | set(base_colors), key=str):
                value = self._mapping_value(current_colors, key)
                old_value = self._mapping_value(base_colors, key)
                if value != old_value:
                    patch["genotype_colors"][key] = (
                        value if value is _PATCH_DELETE else deepcopy(value)
                    )

            current_derived = current.get("derived_inbreeding_cache", {})
            base_derived = baseline.get("derived_inbreeding_cache", {})
            if not isinstance(current_derived, dict):
                current_derived = {}
            if not isinstance(base_derived, dict):
                base_derived = {}
            for key in sorted(set(current_derived) | set(base_derived), key=str):
                value = self._mapping_value(current_derived, key)
                old_value = self._mapping_value(base_derived, key)
                if value != old_value:
                    patch["derived_inbreeding_cache"][key] = (
                        value if value is _PATCH_DELETE else deepcopy(value)
                    )

        if settings:
            current_settings = current.get("settings", {})
            base_settings = baseline.get("settings", {})
            if not isinstance(current_settings, dict):
                current_settings = {}
            if not isinstance(base_settings, dict):
                base_settings = {}
            for key in sorted(set(current_settings) | set(base_settings), key=str):
                value = self._mapping_value(current_settings, key)
                old_value = self._mapping_value(base_settings, key)
                if value != old_value:
                    patch["settings"][key] = value if value is _PATCH_DELETE else deepcopy(value)

            current_positions = current.get("node_positions", {})
            base_positions = baseline.get("node_positions", {})
            if not isinstance(current_positions, dict):
                current_positions = {}
            if not isinstance(base_positions, dict):
                base_positions = {}
            for key in sorted(set(current_positions) | set(base_positions), key=str):
                value = self._mapping_value(current_positions, key)
                old_value = self._mapping_value(base_positions, key)
                if value != old_value:
                    patch["node_positions"][key] = value if value is _PATCH_DELETE else deepcopy(value)

            current_collapsed = {
                str(item) for item in current.get("collapsed_families", [])
                if str(item).strip()
            }
            base_collapsed = {
                str(item) for item in baseline.get("collapsed_families", [])
                if str(item).strip()
            }
            patch["collapsed_added"] = current_collapsed - base_collapsed
            patch["collapsed_removed"] = base_collapsed - current_collapsed

        patch["has_changes"] = bool(
            patch["animal_fields"]
            or patch["animal_deletes"]
            or patch["genotype_colors"]
            or patch["derived_inbreeding_cache"]
            or patch["settings"]
            or patch["node_positions"]
            or patch["collapsed_added"]
            or patch["collapsed_removed"]
        )
        return patch

    def _check_patch_conflicts(
        self,
        patch: Dict[str, Any],
        baseline: Dict[str, Any],
        latest: Dict[str, Any],
    ) -> None:
        """Reject an overlapping edit instead of silently losing either one."""
        base_animals = baseline.get("animals", {}) if isinstance(baseline, dict) else {}
        latest_animals = latest.get("animals", {}) if isinstance(latest, dict) else {}
        if not isinstance(base_animals, dict):
            base_animals = {}
        if not isinstance(latest_animals, dict):
            latest_animals = {}
        for key in patch["animal_deletes"]:
            if key in latest_animals and key not in base_animals:
                raise ConflictError(f"Heritage animal {key!r} was created concurrently.")
            if key in latest_animals and latest_animals.get(key) != base_animals.get(key):
                raise ConflictError(f"Heritage animal {key!r} changed concurrently.")
        for key, fields in patch["animal_fields"].items():
            base_entry = base_animals.get(key, _PATCH_DELETE)
            latest_entry = latest_animals.get(key, _PATCH_DELETE)
            if not isinstance(fields, dict):
                if latest_entry is not _PATCH_DELETE and latest_entry != base_entry and latest_entry != fields:
                    raise ConflictError(f"Heritage animal {key!r} changed concurrently.")
                continue
            for field, intended in fields.items():
                old = self._mapping_value(base_entry, field)
                now = self._mapping_value(latest_entry, field)
                if now != old and now != intended:
                    raise ConflictError(
                        f"Heritage field {key!r}/{field!r} changed concurrently."
                    )

        base_colors = baseline.get("genotype_colors", {}) if isinstance(baseline, dict) else {}
        latest_colors = latest.get("genotype_colors", {}) if isinstance(latest, dict) else {}
        for key, intended in patch["genotype_colors"].items():
            old = self._mapping_value(base_colors, key)
            now = self._mapping_value(latest_colors, key)
            if now != old and now != intended:
                raise ConflictError(f"Genotype colour {key!r} changed concurrently.")

        base_derived = baseline.get("derived_inbreeding_cache", {}) if isinstance(baseline, dict) else {}
        latest_derived = latest.get("derived_inbreeding_cache", {}) if isinstance(latest, dict) else {}
        for key, intended in patch["derived_inbreeding_cache"].items():
            old = self._mapping_value(base_derived, key)
            now = self._mapping_value(latest_derived, key)
            if now != old and now != intended:
                raise ConflictError(f"Derived inbreeding cache {key!r} changed concurrently.")

        base_settings = baseline.get("settings", {}) if isinstance(baseline, dict) else {}
        latest_settings = latest.get("settings", {}) if isinstance(latest, dict) else {}
        for key, intended in patch["settings"].items():
            old = self._mapping_value(base_settings, key)
            now = self._mapping_value(latest_settings, key)
            if now != old and now != intended:
                raise ConflictError(f"Heritage setting {key!r} changed concurrently.")

        base_positions = baseline.get("node_positions", {}) if isinstance(baseline, dict) else {}
        latest_positions = latest.get("node_positions", {}) if isinstance(latest, dict) else {}
        for key, intended in patch["node_positions"].items():
            old = self._mapping_value(base_positions, key)
            now = self._mapping_value(latest_positions, key)
            if now != old and now != intended:
                raise ConflictError(f"Heritage position {key!r} changed concurrently.")

        if patch["collapsed_added"] or patch["collapsed_removed"]:
            base = {str(item) for item in baseline.get("collapsed_families", [])}
            now = {str(item) for item in latest.get("collapsed_families", [])}
            concurrent_added = now - base
            concurrent_removed = base - now
            if (
                concurrent_added & patch["collapsed_removed"]
                or concurrent_removed & patch["collapsed_added"]
            ):
                raise ConflictError("Collapsed Heritage families changed concurrently.")

    @staticmethod
    def _patch_mapping(target: Dict[str, Any], changes: Dict[str, Any]) -> None:
        for key, value in changes.items():
            if value is _PATCH_DELETE:
                target.pop(key, None)
            else:
                target[key] = deepcopy(value)

    def _apply_persistence_patch(self, target: Dict[str, Any], patch: Dict[str, Any]) -> None:
        animals = target.setdefault("animals", {})
        if not isinstance(animals, dict):
            animals = {}
            target["animals"] = animals
        for key in patch["animal_deletes"]:
            animals.pop(key, None)
        for key, fields in patch["animal_fields"].items():
            if isinstance(fields, dict):
                entry = animals.setdefault(key, {})
                if not isinstance(entry, dict):
                    entry = {}
                    animals[key] = entry
                self._patch_mapping(entry, fields)
            elif fields is _PATCH_DELETE:
                animals.pop(key, None)
            else:
                animals[key] = deepcopy(fields)
        colors = target.setdefault("genotype_colors", {})
        if not isinstance(colors, dict):
            colors = {}
            target["genotype_colors"] = colors
        self._patch_mapping(colors, patch["genotype_colors"])
        derived = target.setdefault("derived_inbreeding_cache", {})
        if not isinstance(derived, dict):
            derived = {}
            target["derived_inbreeding_cache"] = derived
        self._patch_mapping(derived, patch["derived_inbreeding_cache"])

        settings = target.setdefault("settings", {})
        if not isinstance(settings, dict):
            settings = {}
            target["settings"] = settings
        self._patch_mapping(settings, patch["settings"])
        positions = target.setdefault("node_positions", {})
        if not isinstance(positions, dict):
            positions = {}
            target["node_positions"] = positions
        self._patch_mapping(positions, patch["node_positions"])

        collapsed = {str(item) for item in target.get("collapsed_families", [])}
        collapsed.update(patch["collapsed_added"])
        collapsed.difference_update(patch["collapsed_removed"])
        target["collapsed_families"] = sorted(collapsed, key=str.casefold)

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
                "unit_id": "",
                "dummy_kind": "",
                "persistence_kind": "",
                "source": "plugin",
                "updated_at": self._utc_now_iso(),
                "inbreeding_f": None,
                "parentage_revision": "",
                "parentage_revision_display": "",
                "genetic_parentage_revision": "",
                "inbreeding_f_cache": None,
            }
        return animals[key]

    @staticmethod
    def _is_owned_dummy_entry(entry: Any) -> bool:
        """Return whether a stored animal is an explicit Heritage dummy.

        The store must never create a record merely because a compatibility
        setter was called with a Core IPID.  Explicit lifecycle markers are
        required; a bare ``heritage_only`` flag is not enough to establish
        ownership after the one-way Core projection change.
        """
        if not isinstance(entry, dict) or not bool(entry.get("heritage_only", False)):
            return False
        persistence = str(entry.get("persistence_kind", "") or "").strip().casefold()
        if persistence in {"temporary_dummy", "direct_dummy", "former_core_dummy"}:
            return True
        return str(entry.get("dummy_kind", "") or "").strip().casefold() in {
            "direct", "former_core",
        }

    def _existing_owned_dummy_entry(self, animal_name: str) -> Optional[Dict[str, Any]]:
        key = self._normalize_text(animal_name)
        if not key:
            return None
        data = self.load()
        animals = data.get("animals", {}) if isinstance(data, dict) else {}
        entry = animals.get(key) if isinstance(animals, dict) else None
        return entry if self._is_owned_dummy_entry(entry) else None

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

    def get_all_entries(self, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Dict[str, Any]]:
        data = snapshot if isinstance(snapshot, dict) else self.load()
        return data.get("animals", {})

    def get_genotype_colors(self, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        if snapshot is None and self._genotype_colors_cache is not None:
            return dict(self._genotype_colors_cache)
        data = snapshot if isinstance(snapshot, dict) else self.load()
        colors = data.get("genotype_colors", {}) if isinstance(data, dict) else {}
        if not isinstance(colors, dict):
            if snapshot is None:
                self._genotype_colors_cache = {}
            return {}
        normalized: Dict[str, str] = {}
        for genotype, color in colors.items():
            key = self._normalize_genotype_key(genotype)
            if not key:
                continue
            normalized[key] = self._normalize_text(color)
        if snapshot is None:
            self._genotype_colors_cache = normalized
        return dict(normalized)

    def get_genotype_color(self, genotype: str, snapshot: Optional[Dict[str, Any]] = None) -> str:
        key = self._normalize_genotype_key(genotype)
        if not key:
            return ""
        return self.get_genotype_colors(snapshot=snapshot).get(key, "")

    def _apply_genotype_color_to_entries(self, genotype_key: str, fill_color: str) -> bool:
        data = self.load()
        animals = data.get("animals", {}) if isinstance(data, dict) else {}
        if not isinstance(animals, dict):
            return False

        changed = False
        now_iso = self._utc_now_iso()
        for entry in animals.values():
            if not self._is_owned_dummy_entry(entry):
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

    def set_genotype_color(
        self,
        genotype: str,
        fill_color: Optional[str],
        persist: bool = True,
        *,
        update_entries: bool = True,
    ) -> bool:
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

        if update_entries and self._apply_genotype_color_to_entries(genotype_key, color_value):
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

    @staticmethod
    def _normalize_position_cache_user(value: Any) -> str:
        return str(value or "guest").strip() or "guest"

    def get_position_cache_entry(
        self,
        user_id: Any,
        cache_key: Any,
        *,
        pedigree_revision: Optional[str] = None,
        dependency_ids: Optional[Iterable[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return one complete logical position cache record, read-only.

        Optional revision/dependency arguments are checked without mutating
        storage.  This keeps redraws, language changes and viewport changes
        strictly read-only; stale records are replaced by the next successful
        automatic layout or explicitly removed by invalidation.
        """
        user = self._normalize_position_cache_user(user_id)
        key = self._normalize_text(cache_key)
        if not key:
            return None
        # Refresh the read from the backend so a second session's committed
        # cache replacement is visible immediately.  The helper restores this
        # object's in-memory/pending state and does not write normalization.
        data, _revision = self.load_latest_with_revision()
        cache = data.get("position_cache", {}) if isinstance(data, dict) else {}
        entries = cache.get(user, {}) if isinstance(cache, dict) else {}
        if not isinstance(entries, dict):
            return None
        entry = self._normalize_position_cache_entry(entries.get(key))
        if entry is None:
            return None
        expected_revision = self._normalize_text(pedigree_revision)
        if expected_revision and entry["dependency_revision"] != expected_revision:
            return None
        if dependency_ids is not None:
            expected_dependencies = sorted(
                {
                    self._normalize_text(item)
                    for item in dependency_ids
                    if self._normalize_text(item)
                },
                key=lambda item: (item.casefold(), item),
            )
            if entry["dependency_ids"] != expected_dependencies:
                return None
        return deepcopy(entry)

    def set_position_cache_entry(
        self,
        user_id: Any,
        cache_key: Any,
        positions: Dict[str, Any],
        pedigree_revision: str,
        dependency_ids: Iterable[str],
        *,
        selection_type: str = "selected",
    ) -> Dict[str, Any]:
        """Atomically replace one user's complete selection position map.

        ``pedigree_revision`` is the dependency-scoped token produced by
        :meth:`build_position_dependency_revision`; the historical parameter
        name is retained for the widget/store call boundary only.

        Returns deterministic eviction metadata for a localized UI notice.
        Validation happens before the backend mutation, and the mutation is
        performed through ``atomic_update`` so a failed write leaves the
        previous cache entry untouched.
        """
        user = self._normalize_position_cache_user(user_id)
        key = self._normalize_text(cache_key)
        revision = self._normalize_text(pedigree_revision)
        if not key or not revision or not isinstance(positions, dict):
            raise ValueError("A cache key, pedigree revision and position map are required")
        normalized_positions: Dict[str, Dict[str, float]] = {}
        for raw_name, raw_position in positions.items():
            name = self._normalize_text(raw_name)
            normalized = self._normalize_position(raw_position)
            if not name:
                continue
            if normalized is None:
                raise ValueError(f"Invalid non-finite cached node position for {name}")
            x, y = normalized
            normalized_positions[name] = {"x": x, "y": y}
        dependencies = sorted(
            {
                self._normalize_text(item)
                for item in dependency_ids
                if self._normalize_text(item)
            },
            key=lambda item: (item.casefold(), item),
        )
        normalized_type = self._normalize_text(selection_type) or "selected"

        # A render may have queued derived metadata immediately before the
        # position write.  Do not pre-flush it: that would create two writes
        # and leave a race between the derived update and this replacement.
        # Instead, carry the field-level pending patch into the same atomic
        # transaction below and validate it against that transaction's fresh
        # backend snapshot.
        pending_patch: Optional[Dict[str, Any]] = None
        pending_baseline: Optional[Dict[str, Any]] = None
        if self.has_pending_changes():
            local = self.load()
            baseline = self._committed_snapshot
            if isinstance(local, dict) and isinstance(baseline, dict):
                pending_patch = self._compute_persistence_patch(
                    local, baseline,
                    animals=self._pending_animal_save,
                    settings=self._pending_settings_save,
                )
                pending_baseline = deepcopy(baseline)

        def mutate(data: Dict[str, Any]) -> Dict[str, Any]:
            if pending_patch and pending_patch.get("has_changes"):
                self._check_patch_conflicts(
                    pending_patch,
                    pending_baseline or {},
                    data,
                )
                self._apply_persistence_patch(data, pending_patch)
            cache = data.setdefault("position_cache", {})
            if not isinstance(cache, dict):
                cache = {}
                data["position_cache"] = cache
            entries = cache.setdefault(user, {})
            if not isinstance(entries, dict):
                entries = {}
                cache[user] = entries
            replaced = key in entries
            entries[key] = {
                "pedigree_revision": revision,
                "dependency_revision": revision,
                "dependency_ids": list(dependencies),
                "positions": deepcopy(normalized_positions),
                "selection_type": normalized_type,
                "updated_at": self._utc_now_iso(),
            }
            evicted_key = None
            if len(entries) > self.POSITION_CACHE_LIMIT:
                # Oldest timestamp wins; key is a deterministic tie-breaker.
                oldest = min(
                    entries.items(),
                    key=lambda item: (
                        str(item[1].get("updated_at", "")),
                        item[0],
                    ),
                )[0]
                if oldest != key:
                    evicted_key = oldest
                    entries.pop(oldest, None)
                else:
                    # A clock collision must never evict the just-written
                    # entry; remove the next deterministic oldest record.
                    candidates = sorted(
                        (item for item in entries if item != key),
                        key=lambda item: (
                            str(entries[item].get("updated_at", "")),
                            item,
                        ),
                    )
                    if candidates:
                        evicted_key = candidates[0]
                        entries.pop(evicted_key, None)
            return {
                "replaced": replaced,
                "evicted_key": evicted_key,
                "count": len(entries),
            }

        # This mutator merges the pending field-level patch itself so it can
        # validate it before replacing the position map.  Disable the generic
        # merge to avoid applying that patch twice.
        result = self.atomic_update(mutate, preserve_pending=False)
        return dict(result or {})

    def remove_position_cache_entry(self, user_id: Any, cache_key: Any) -> bool:
        """Atomically remove exactly one user/selection cache entry."""
        user = self._normalize_position_cache_user(user_id)
        key = self._normalize_text(cache_key)
        current = self.load().get("position_cache", {})
        current_entries = current.get(user, {}) if isinstance(current, dict) else {}
        if not isinstance(current_entries, dict) or key not in current_entries:
            return False

        def mutate(data: Dict[str, Any]) -> bool:
            cache = data.get("position_cache", {})
            entries = cache.get(user, {}) if isinstance(cache, dict) else {}
            if not isinstance(entries, dict) or key not in entries:
                return False
            entries.pop(key, None)
            if not entries:
                cache.pop(user, None)
            return True

        return bool(self.atomic_update(mutate))

    def invalidate_position_cache_dependencies(self, dependency_ids: Iterable[str]) -> int:
        """Drop cached maps that depend on changed animals, atomically."""
        dependencies = {
            self._normalize_text(item)
            for item in dependency_ids
            if self._normalize_text(item)
        }
        if not dependencies:
            return 0
        current = self.load().get("position_cache", {})
        if not isinstance(current, dict):
            return 0
        has_match = False
        for raw_entries in current.values():
            if not isinstance(raw_entries, dict):
                continue
            for raw_entry in raw_entries.values():
                entry = self._normalize_position_cache_entry(raw_entry)
                if entry is not None and set(entry["dependency_ids"]) & dependencies:
                    has_match = True
                    break
            if has_match:
                break
        if not has_match:
            return 0

        def mutate(data: Dict[str, Any]) -> int:
            cache = data.get("position_cache", {})
            if not isinstance(cache, dict):
                return 0
            removed = 0
            for user in list(cache):
                entries = cache.get(user)
                if not isinstance(entries, dict):
                    cache.pop(user, None)
                    continue
                for key in list(entries):
                    entry = self._normalize_position_cache_entry(entries.get(key))
                    entry_dependencies = set(entry.get("dependency_ids", [])) if entry else set()
                    if entry is None or entry_dependencies & dependencies:
                        entries.pop(key, None)
                        removed += 1
                if not entries:
                    cache.pop(user, None)
            return removed

        return int(self.atomic_update(mutate) or 0)

    def get_invalid_node_positions(self) -> Dict[str, Any]:
        """Return invalid legacy coordinates awaiting explicit cleanup."""
        return deepcopy(self._invalid_node_positions)

    def cleanup_invalid_node_positions(self) -> int:
        """Remove invalid persisted coordinates in one explicit backend write.

        Reads and normalizes the latest record without persistence first, then
        replaces it only when malformed coordinates were found.  A failed
        write leaves both the backend and the in-memory view untouched so the
        caller can retain or discard the affected render-cache entry.
        """
        raw, revision = self.backend_store.load_with_revision(None)
        if raw is None or not isinstance(raw, dict):
            self._invalid_node_positions = {}
            return 0

        previous_data = self._data
        previous_invalid = self._invalid_node_positions
        try:
            normalized = deepcopy(self._normalize_and_cache(deepcopy(raw), persist=False))
            invalid = dict(self._invalid_node_positions)
            if not invalid:
                return 0
            next_revision = self.backend_store.save(
                normalized,
                expected_revision=revision,
            )
        except Exception:
            self._data = previous_data
            self._invalid_node_positions = previous_invalid
            self._genotype_colors_cache = None
            raise

        self._data = normalized
        self._invalid_node_positions = {}
        self._backend_revision = int(next_revision or (int(revision or 0) + 1))
        self._committed_snapshot = deepcopy(normalized)
        self._genotype_colors_cache = None
        self._pending_settings_save = False
        return len(invalid)

    def set_node_position(self, animal_name: str, position: Tuple[float, float]) -> None:
        key = self._normalize_text(animal_name)
        normalized = self._normalize_position(position)
        if not key:
            return
        if normalized is None:
            raise ValueError(f"Invalid non-finite node position for {key}")

        data = self.load()
        node_positions = data.get("node_positions", {})
        if not isinstance(node_positions, dict):
            node_positions = {}
            data["node_positions"] = node_positions

        node_positions[key] = {"x": normalized[0], "y": normalized[1]}
        self._invalid_node_positions.pop("__node_positions__", None)
        self._invalid_node_positions.pop(key, None)
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
        normalized_batch: Dict[str, Tuple[float, float]] = {}
        for animal_name, position in positions.items():
            key = self._normalize_text(animal_name)
            if not key:
                continue
            normalized = self._normalize_position(position)
            if normalized is None:
                raise ValueError(f"Invalid non-finite node position for {key}")
            normalized_batch[key] = normalized

        data = self.load()
        node_positions = data.get("node_positions", {})
        if not isinstance(node_positions, dict):
            node_positions = {}
            data["node_positions"] = node_positions
        for key, normalized in normalized_batch.items():
            node_positions[key] = {"x": normalized[0], "y": normalized[1]}
            self._invalid_node_positions.pop("__node_positions__", None)
            self._invalid_node_positions.pop(key, None)
        self._save_settings()

    def remove_node_position(self, animal_name: str) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        data = self.load()
        node_positions = data.get("node_positions", {})
        if not isinstance(node_positions, dict):
            node_positions = {}
            data["node_positions"] = node_positions
        if key not in node_positions and key not in self._invalid_node_positions:
            return

        node_positions.pop(key, None)
        self._invalid_node_positions.pop("__node_positions__", None)
        self._invalid_node_positions.pop(key, None)
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

    def get_parentage(
        self,
        animal_name: Optional[str],
        fallback_record: Optional[Dict[str, Any]] = None,
        *,
        core_authoritative: bool = False,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        fallback = {
            "egg_donor": self._normalize_text((fallback_record or {}).get("eizellspenderin", "")),
            "sperm_donor": self._normalize_text((fallback_record or {}).get("samenspender", "")),
            "surrogate_mother": self._normalize_text((fallback_record or {}).get("ziehmutter", "")),
            "surrogate_father": self._normalize_text((fallback_record or {}).get("ziehvater", "")),
        }

        # Core owns all four relationship fields for real application
        # animals.  An explicit empty value is intentional and must clear a
        # stale denormalized Heritage projection rather than falling back to
        # it.  Heritage-only and former-Core dummy records continue to use
        # the persisted canonical graph below.
        if core_authoritative and isinstance(fallback_record, dict):
            return fallback

        key = self._normalize_text(animal_name)
        if not key:
            return fallback

        source = snapshot if isinstance(snapshot, dict) else self.load()
        entries = source.get("animals", {}) if isinstance(source, dict) else {}
        entry = entries.get(key, {}) if isinstance(entries, dict) else {}
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

        entry = self._existing_owned_dummy_entry(key)
        if entry is None:
            # Core records are a read-only projection; do not materialize a
            # shadow when an obsolete setter is called directly.
            return
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
        entry = self._existing_owned_dummy_entry(key)
        if entry is None:
            return
        target = bool(heritage_only)
        if bool(entry.get("heritage_only", False)) == target:
            return
        entry["heritage_only"] = target
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
            entry = self._existing_owned_dummy_entry(key)
            if entry is None or bool(entry.get("heritage_only", False)) == target:
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
        # Identity/species changes belong to the canonical dummy command in
        # the UI; this compatibility setter is restricted to explicit
        # Heritage-owned dummies and never creates a Core shadow.
        entry = self._existing_owned_dummy_entry(key)
        if entry is None:
            return
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
        # Immutable identity is established by the canonical dummy command;
        # only an explicit Heritage-owned dummy may be updated here.
        entry = self._existing_owned_dummy_entry(key)
        if entry is None:
            return
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

    def get_species(
        self,
        animal_name: str,
        fallback_record: Optional[Dict[str, Any]] = None,
        *,
        core_authoritative: bool = False,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> str:
        if core_authoritative and isinstance(fallback_record, dict):
            return self._normalize_text(fallback_record.get("species", ""))
        key = self._normalize_text(animal_name)
        if not key:
            return ""
        source = snapshot if isinstance(snapshot, dict) else self.load()
        entries = source.get("animals", {}) if isinstance(source, dict) else {}
        entry = entries.get(key, {}) if isinstance(entries, dict) else {}
        if not isinstance(entry, dict):
            return ""
        return self._normalize_text(entry.get("species", ""))

    def set_manual_sex(self, animal_name: str, sex: Optional[str]) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        entry = self._existing_owned_dummy_entry(key)
        if entry is None:
            return
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
            entry = self._existing_owned_dummy_entry(key)
            if entry is None:
                continue
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

    def get_effective_sex(
        self,
        animal_name: Optional[str],
        fallback_record: Optional[Dict[str, Any]] = None,
        *,
        core_authoritative: bool = False,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> str:
        fallback_sex = self._normalize_sex((fallback_record or {}).get("sex", ""))
        if fallback_sex:
            return fallback_sex
        if core_authoritative and isinstance(fallback_record, dict):
            # Empty/absent Core sex is a real lack of a value.  Role-derived
            # sex remains the documented legacy inference for donor roles,
            # but no stale manual Heritage value may win over Core.
            role = canonical_role_value(fallback_record.get("rolle", ""))
            if role in (ROLE_VALUE_SPENDER, ROLE_VALUE_AMME):
                return "female"
            if role == ROLE_VALUE_SAMENSP:
                return "male"
            return ""
        key = self._normalize_text(animal_name)
        if key:
            source = snapshot if isinstance(snapshot, dict) else self.load()
            entries = source.get("animals", {}) if isinstance(source, dict) else {}
            entry = entries.get(key, {}) if isinstance(entries, dict) else {}
            manual = self._normalize_sex(entry.get("sex", "")) if isinstance(entry, dict) else ""
            if manual:
                return manual
        return ""

    def set_node_visual(self, animal_name: str, genotype: Optional[str], fill_color: Optional[str]) -> None:
        key = self._normalize_text(animal_name)
        if not key:
            return

        entry = self._existing_owned_dummy_entry(key)
        if entry is None:
            # Unknown keys are never materialized through this compatibility
            # surface; the plugin command separately rejects real Core IPIDs.
            return
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

    def get_node_visual(
        self,
        animal_name: str,
        fallback_genotype: str = "",
        fallback_record: Optional[Dict[str, Any]] = None,
        *,
        core_authoritative: bool = False,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, str]:
        key = self._normalize_text(animal_name)
        if not key:
            return {"genotype": self._normalize_text(fallback_genotype), "node_fill_color": ""}

        source = snapshot if isinstance(snapshot, dict) else self.load()
        entries = source.get("animals", {}) if isinstance(source, dict) else {}
        entry = entries.get(key, {}) if isinstance(entries, dict) else {}
        if core_authoritative and isinstance(fallback_record, dict):
            # Core genotype is authoritative, including an intentional clear.
            genotype = self._normalize_text(fallback_record.get("genotype", ""))
            fallback_color = self._normalize_text(fallback_record.get("node_fill_color", ""))
        else:
            genotype = self._normalize_text(entry.get("genotype", ""))
            if not genotype:
                genotype = self._normalize_text(fallback_genotype)
            fallback_color = self._normalize_text(entry.get("node_fill_color", ""))

        genotype_key = self._normalize_genotype_key(genotype)
        genotype_colors = self.get_genotype_colors(snapshot=snapshot)
        if genotype_key and genotype_key in genotype_colors:
            fill_color = self._normalize_text(genotype_colors.get(genotype_key, ""))
        else:
            fill_color = fallback_color
        return {"genotype": genotype, "node_fill_color": fill_color}

    def sync_from_record(self, animal_name: str, record: Optional[Dict[str, Any]], persist: bool = True, in_main_animals: bool = True) -> bool:
        """Compatibility hook that never mirrors a Core record.

        Core identity and relationships are projected directly at read time.
        Durable dummies, including former-Core snapshots, are created by their
        explicit commands so this broad synchronizer cannot create a second
        persisted copy of a real animal.
        """
        _ = (animal_name, record, persist, in_main_animals)
        return False

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

    def get_pedigree_revision(self) -> str:
        """Return the latest committed genetic-pedigree revision token."""
        return self._normalize_text(self.load().get("pedigree_revision", ""))

    def get_inbreeding_cache(
        self,
        animal_name: str,
        snapshot: Optional[Dict[str, Any]] = None,
        *,
        cache_key: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return validated lineage-bound F metadata, never a legacy scalar."""
        key = self._normalize_text(animal_name)
        if not key:
            return None
        source = snapshot if isinstance(snapshot, dict) else self.load()
        derived = source.get("derived_inbreeding_cache", {}) if isinstance(source, dict) else {}
        if not isinstance(derived, dict):
            derived = {}
        derived_key = self._normalize_text(cache_key)
        if derived_key:
            cached = self._normalize_inbreeding_cache(derived.get(derived_key))
            if cached is not None:
                return deepcopy(cached)
        entries = source.get("animals", {}) if isinstance(source, dict) else {}
        entry = entries.get(key, {}) if isinstance(entries, dict) else {}
        if not isinstance(entry, dict):
            return None
        cached = self._normalize_inbreeding_cache(entry.get("inbreeding_f_cache"))
        return deepcopy(cached) if cached is not None else None

    def set_inbreeding_cache_batch(
        self,
        updates: Dict[str, Dict[str, Any]],
        *,
        persist: bool = True,
        cache_keys: Optional[Dict[str, str]] = None,
    ) -> bool:
        """Store validated derived F metadata in one optional deferred write.

        Only already-materialized Core/Heritage records are updated; an
        unresolved parent must not become a new animal merely because a render
        calculated an unavailable value for it.
        """
        if not isinstance(updates, dict) or not updates:
            return False
        data = self.load()
        animals = data.get("animals", {}) if isinstance(data, dict) else {}
        if not isinstance(animals, dict):
            animals = {}
            data["animals"] = animals
        derived = data.setdefault("derived_inbreeding_cache", {})
        if not isinstance(derived, dict):
            derived = {}
            data["derived_inbreeding_cache"] = derived
        cache_keys = cache_keys if isinstance(cache_keys, dict) else {}
        changed = False
        now_iso = self._utc_now_iso()
        for animal_name, raw_meta in updates.items():
            key = self._normalize_text(animal_name)
            derived_key = self._normalize_text(cache_keys.get(key, ""))
            if not key or not isinstance(raw_meta, dict):
                continue
            metadata = self._normalize_inbreeding_cache(raw_meta)
            if metadata is None:
                continue
            # Real Core animals are addressed by their stable IPID in the
            # dedicated derived namespace.  Only Heritage-owned dummies use
            # the per-animal cache field; never create a Core shadow record.
            if derived_key:
                if self._normalize_inbreeding_cache(derived.get(derived_key)) != metadata:
                    derived[derived_key] = metadata
                    changed = True
                continue
            if key not in animals or not self._is_owned_dummy_entry(animals.get(key)):
                # An unresolved reference or an unowned Core projection must
                # not receive a per-animal cache field.  Real Core caches use
                # the dedicated stable-IPID ``cache_keys`` namespace above.
                continue
            entry = animals[key]
            if self._normalize_inbreeding_cache(entry.get("inbreeding_f_cache")) != metadata:
                entry["inbreeding_f_cache"] = metadata
                entry["updated_at"] = now_iso
                changed = True
            scalar = metadata["value"] if metadata["status"] == "valid" else None
            if entry.get("inbreeding_f") != scalar:
                # Keep the historical scalar as a read-only mirror for old
                # exports/tests; render code accepts it only through the
                # structured metadata above.
                entry["inbreeding_f"] = scalar
                entry["updated_at"] = now_iso
                changed = True
        if not changed:
            return False
        if persist:
            self._save_animals()
        else:
            self._mark_pending_sections(animals=True)
        return True

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
            entry = self._existing_owned_dummy_entry(key)
            if entry is None:
                continue
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
        """Compatibility hook; Core records are never mirrored into storage."""
        _ = (animals, persist)
        return False
