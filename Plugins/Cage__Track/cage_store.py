# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.0 RC
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Cage Track persistence layer for cage.json.

from __future__ import annotations

import json
import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional


UNASSIGNED_CAGE_ID = "cage_unassigned"


class CageStore:
    """Owns plugin-specific storage in cage.json."""

    def __init__(self, plugin_dir: str):
        self.plugin_dir = plugin_dir
        self.file_path = os.path.join(plugin_dir, "cage.json")
        self._data: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def _default_data(self) -> Dict[str, Any]:
        return {
            "version": "1.0",
            "structures": {
                "buildings": {},
                "rooms": {},
                "cages": {
                    UNASSIGNED_CAGE_ID: {
                        "id": UNASSIGNED_CAGE_ID,
                        "parent_room_id": None,
                        "display_name": "Unassigned",
                        "order": 9999,
                        "is_virtual": True,
                    }
                },
            },
            "occupants": {},
            "movement_history": {},
            "project_colors": {},
            "ui_state": {
                "expanded_buildings": [],
                "expanded_rooms": [],
                "view_position": {"x": 0, "y": 0},
                "zoom_level": 1.0,
                "show_legend": True,
            },
        }

    # ------------------------------------------------------------------
    # ID generation
    # ------------------------------------------------------------------

    @staticmethod
    def generate_unique_id(prefix: str = "id") -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    # ------------------------------------------------------------------
    # Timestamp helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now_iso() -> str:
        """Return today's date as YYYY-MM-DD (daily precision)."""
        return date.today().isoformat()

    @staticmethod
    def calculate_duration(moved_in: Optional[str], moved_out: Optional[str]) -> Optional[int]:
        """Return duration in days between two date/ISO strings, or None."""
        if not moved_in:
            return None
        try:
            d_in = datetime.fromisoformat(moved_in).date()
            d_out = datetime.fromisoformat(moved_out).date() if moved_out else date.today()
            return max(0, (d_out - d_in).days)
        except (ValueError, TypeError):
            return None

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load_data(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data

        if not os.path.exists(self.file_path):
            self._data = self._default_data()
            self.save_data()
            return self._data

        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict):
                raw = self._default_data()
        except Exception:
            raw = self._default_data()

        # Ensure all top-level keys exist
        raw.setdefault("version", "1.0")
        structures = raw.setdefault("structures", {})
        if not isinstance(structures, dict):
            structures = {}
            raw["structures"] = structures
        structures.setdefault("buildings", {})
        structures.setdefault("rooms", {})
        cages = structures.setdefault("cages", {})

        # Ensure virtual unassigned cage always exists
        if UNASSIGNED_CAGE_ID not in cages:
            cages[UNASSIGNED_CAGE_ID] = {
                "id": UNASSIGNED_CAGE_ID,
                "parent_room_id": None,
                "display_name": "Unassigned",
                "order": 9999,
                "is_virtual": True,
            }

        raw.setdefault("occupants", {})
        if not isinstance(raw["occupants"], dict):
            raw["occupants"] = {}
        raw.setdefault("movement_history", {})
        if not isinstance(raw["movement_history"], dict):
            raw["movement_history"] = {}
        raw.setdefault("project_colors", {})
        if not isinstance(raw["project_colors"], dict):
            raw["project_colors"] = {}

        ui = raw.setdefault("ui_state", {})
        if not isinstance(ui, dict):
            ui = {}
            raw["ui_state"] = ui
        ui.setdefault("expanded_buildings", [])
        ui.setdefault("expanded_rooms", [])
        ui.setdefault("view_position", {"x": 0, "y": 0})
        ui.setdefault("zoom_level", 1.0)
        ui.setdefault("show_legend", True)

        self._data = raw
        return self._data

    def save_data(self) -> None:
        data = self.load_data()
        os.makedirs(self.plugin_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="cage_", suffix=".json", dir=self.plugin_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as tmp:
                json.dump(data, tmp, indent=2, ensure_ascii=False)
            os.replace(temp_path, self.file_path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Structure CRUD
    # ------------------------------------------------------------------

    def create_building(self, name: str) -> Dict[str, Any]:
        data = self.load_data()
        buildings = data["structures"]["buildings"]
        max_order = max((b.get("order", 0) for b in buildings.values()), default=-1)
        bid = self.generate_unique_id("bld")
        entry = {"id": bid, "display_name": name.strip(), "order": max_order + 1}
        buildings[bid] = entry
        self.save_data()
        return entry

    def create_room(self, parent_building_id: str, name: str) -> Dict[str, Any]:
        data = self.load_data()
        rooms = data["structures"]["rooms"]
        siblings = [r for r in rooms.values() if r.get("parent_building_id") == parent_building_id]
        max_order = max((r.get("order", 0) for r in siblings), default=-1)
        rid = self.generate_unique_id("room")
        entry = {
            "id": rid,
            "parent_building_id": parent_building_id,
            "display_name": name.strip(),
            "order": max_order + 1,
        }
        rooms[rid] = entry
        self.save_data()
        return entry

    def create_cage(self, parent_room_id: str, name: str) -> Dict[str, Any]:
        data = self.load_data()
        cages = data["structures"]["cages"]
        siblings = [c for c in cages.values() if c.get("parent_room_id") == parent_room_id]
        max_order = max((c.get("order", 0) for c in siblings), default=-1)
        cid = self.generate_unique_id("cage")
        entry = {
            "id": cid,
            "parent_room_id": parent_room_id,
            "display_name": name.strip(),
            "order": max_order + 1,
        }
        cages[cid] = entry
        self.save_data()
        return entry

    def delete_structure(self, struct_id: str, struct_type: str) -> bool:
        """Cascade-delete a structure and all its children.

        Occupants from deleted cages are moved to the Unassigned cage.
        Returns True on success.
        """
        data = self.load_data()
        structures = data["structures"]

        if struct_type == "building":
            if struct_id not in structures["buildings"]:
                return False
            # Collect child rooms
            child_rooms = [rid for rid, r in structures["rooms"].items()
                           if r.get("parent_building_id") == struct_id]
            # Cascade: delete each child room (which cascades cages+occupants)
            for rid in child_rooms:
                self._cascade_delete_room(rid, structures, data["occupants"])
            del structures["buildings"][struct_id]

        elif struct_type == "room":
            if struct_id not in structures["rooms"]:
                return False
            self._cascade_delete_room(struct_id, structures, data["occupants"])

        elif struct_type == "cage":
            if struct_id not in structures["cages"]:
                return False
            if structures["cages"][struct_id].get("is_virtual"):
                return False
            self._orphan_occupants(struct_id, data["occupants"])
            del structures["cages"][struct_id]

        else:
            return False

        self.save_data()
        return True

    def _cascade_delete_room(self, room_id: str, structures: dict, occupants: dict) -> None:
        """Delete a room and all its child cages, orphaning occupants."""
        child_cages = [cid for cid, c in structures["cages"].items()
                       if c.get("parent_room_id") == room_id and not c.get("is_virtual")]
        for cid in child_cages:
            self._orphan_occupants(cid, occupants)
            del structures["cages"][cid]
        structures["rooms"].pop(room_id, None)

    def _orphan_occupants(self, cage_id: str, occupants: dict) -> None:
        """Move all occupants of a cage to the Unassigned cage."""
        for occ in occupants.values():
            if occ.get("cage_id") == cage_id:
                occ["cage_id"] = UNASSIGNED_CAGE_ID

    def rename_structure(self, struct_id: str, struct_type: str, new_name: str) -> bool:
        data = self.load_data()
        type_map = {"building": "buildings", "room": "rooms", "cage": "cages"}
        container = data["structures"].get(type_map.get(struct_type, ""), {})
        if struct_id not in container:
            return False
        container[struct_id]["display_name"] = new_name.strip()
        self.save_data()
        return True

    def move_structure(self, struct_id: str, new_parent_id: str, new_order: int) -> bool:
        data = self.load_data()
        structures = data["structures"]

        # Determine type from ID presence
        if struct_id in structures["rooms"]:
            entry = structures["rooms"][struct_id]
            entry["parent_building_id"] = new_parent_id
            entry["order"] = new_order
        elif struct_id in structures["cages"]:
            entry = structures["cages"][struct_id]
            if entry.get("is_virtual"):
                return False
            entry["parent_room_id"] = new_parent_id
            entry["order"] = new_order
        else:
            return False

        self.save_data()
        return True

    def reorder_structure(self, struct_id: str, new_order: int) -> bool:
        data = self.load_data()
        structures = data["structures"]
        for kind in ("buildings", "rooms", "cages"):
            if struct_id in structures[kind]:
                structures[kind][struct_id]["order"] = new_order
                self.save_data()
                return True
        return False

    def get_structure_by_id(self, struct_id: str) -> Optional[Dict[str, Any]]:
        data = self.load_data()
        structures = data["structures"]
        for kind in ("buildings", "rooms", "cages"):
            if struct_id in structures[kind]:
                return structures[kind][struct_id]
        return None

    # ------------------------------------------------------------------
    # Occupants
    # ------------------------------------------------------------------

    def create_occupant(
        self,
        name: str,
        occ_type: str = "real",
        cage_id: Optional[str] = None,
        note: Optional[str] = None,
    ) -> Dict[str, Any]:
        data = self.load_data()
        occupants = data["occupants"]
        now = self._now_iso()
        target_cage = cage_id or UNASSIGNED_CAGE_ID
        entry = {
            "occupant_id": name.strip(),
            "type": occ_type,
            "cage_id": target_cage,
            "moved_at": now,
        }
        if note:
            entry["note"] = note
        occupants[name.strip()] = entry

        # Record initial movement history
        history = data["movement_history"]
        history.setdefault(name.strip(), [])
        mates = [
            o["occupant_id"]
            for o in occupants.values()
            if o.get("cage_id") == target_cage and o["occupant_id"] != name.strip()
        ]
        history[name.strip()].append({
            "cage_id": target_cage,
            "moved_in": now,
            "moved_out": None,
            "cage_mates_snapshot": mates,
        })

        self.save_data()
        return entry

    def get_occupant(self, occupant_id: str) -> Optional[Dict[str, Any]]:
        data = self.load_data()
        return data["occupants"].get(occupant_id.strip())

    def get_occupant_cage(self, occupant_id: str) -> Optional[str]:
        occ = self.get_occupant(occupant_id)
        if occ:
            return occ.get("cage_id")
        return None

    def set_occupant_cage(self, occupant_id: str, cage_id: str) -> None:
        """Move occupant to a new cage and record movement history.

        Daily precision: if the animal was already moved today the
        previous same-day entry is overwritten instead of appended.
        """
        data = self.load_data()
        occupants = data["occupants"]
        key = occupant_id.strip()
        if key not in occupants:
            return

        old_cage = occupants[key].get("cage_id")
        if old_cage == cage_id:
            return

        today = self._now_iso()

        history = data["movement_history"].setdefault(key, [])

        # Same-day overwrite: if the last entry started today, remove it
        # and re-open the entry before it so the chain stays consistent.
        if history and history[-1].get("moved_in", "")[:10] == today[:10]:
            history.pop()
            if history and history[-1].get("moved_out", "")[:10] == today[:10]:
                history[-1]["moved_out"] = None

        # Close the current open entry
        if history:
            last = history[-1]
            if last.get("moved_out") is None:
                last["moved_out"] = today

        # Snapshot cage mates at new cage
        mates = [
            o["occupant_id"]
            for o in occupants.values()
            if o.get("cage_id") == cage_id and o["occupant_id"] != key
        ]
        history.append({
            "cage_id": cage_id,
            "moved_in": today,
            "moved_out": None,
            "cage_mates_snapshot": mates,
        })

        occupants[key]["cage_id"] = cage_id
        occupants[key]["moved_at"] = today

        self.save_data()

    def delete_occupant(self, occupant_id: str) -> bool:
        """Remove a dummy occupant. Real animals cannot be deleted here."""
        data = self.load_data()
        key = occupant_id.strip()
        occ = data["occupants"].get(key)
        if not occ or occ.get("type") != "dummy":
            return False
        del data["occupants"][key]
        # Keep movement history for audit trail
        self.save_data()
        return True

    def get_cage_occupants(self, cage_id: str) -> List[Dict[str, Any]]:
        """Dynamically build occupant list for a cage."""
        data = self.load_data()
        return [
            o for o in data["occupants"].values()
            if o.get("cage_id") == cage_id
        ]

    def get_cage_mates(self, occupant_id: str) -> List[str]:
        occ = self.get_occupant(occupant_id)
        if not occ:
            return []
        cage_id = occ.get("cage_id")
        return [
            o["occupant_id"]
            for o in self.get_cage_occupants(cage_id)
            if o["occupant_id"] != occupant_id.strip()
        ]

    def get_movement_history(self, occupant_id: str) -> List[Dict[str, Any]]:
        data = self.load_data()
        return data["movement_history"].get(occupant_id.strip(), [])

    def update_history_date(self, occupant_id: str, entry_index: int,
                            field: str, new_date: Optional[str]) -> bool:
        """Update moved_in or moved_out for a specific history entry."""
        data = self.load_data()
        history = data["movement_history"].get(occupant_id.strip(), [])
        if entry_index < 0 or entry_index >= len(history):
            return False
        if field not in ("moved_in", "moved_out"):
            return False
        history[entry_index][field] = new_date
        self.save_data()
        return True

    def get_unassigned_occupants(self) -> List[Dict[str, Any]]:
        """Return unassigned occupants (archived ones included, shown grey)."""
        return self.get_cage_occupants(UNASSIGNED_CAGE_ID)

    # ------------------------------------------------------------------
    # Address helpers for ProgTrack dialog integration
    # ------------------------------------------------------------------

    def get_address_for_dialog(self, occupant_id: str) -> Dict[str, Optional[str]]:
        """Return current building/room/cage selection for ProgTrack dialog."""
        data = self.load_data()
        occ = data["occupants"].get(occupant_id.strip())
        if not occ:
            return {"building_id": None, "room_id": None, "cage_id": None}

        cage_id = occ.get("cage_id")
        if not cage_id or cage_id == UNASSIGNED_CAGE_ID:
            return {"building_id": None, "room_id": None, "cage_id": None}

        structures = data["structures"]
        cage = structures["cages"].get(cage_id)
        if not cage:
            return {"building_id": None, "room_id": None, "cage_id": None}

        room_id = cage.get("parent_room_id")
        room = structures["rooms"].get(room_id) if room_id else None
        building_id = room.get("parent_building_id") if room else None

        return {
            "building_id": building_id,
            "room_id": room_id,
            "cage_id": cage_id,
        }

    def set_address_from_dialog(
        self,
        occupant_id: str,
        building_id: Optional[str],
        room_id: Optional[str],
        cage_id: Optional[str],
    ) -> None:
        """Validate and persist dialog selection to cage.json only."""
        key = occupant_id.strip()
        data = self.load_data()

        target_cage = cage_id if cage_id else UNASSIGNED_CAGE_ID

        # Validate cage exists
        if target_cage != UNASSIGNED_CAGE_ID:
            if target_cage not in data["structures"]["cages"]:
                target_cage = UNASSIGNED_CAGE_ID

        # Create occupant if it doesn't exist yet (real animal first assignment)
        if key not in data["occupants"]:
            self.create_occupant(key, occ_type="real", cage_id=target_cage)
        else:
            self.set_occupant_cage(key, target_cage)

    # ------------------------------------------------------------------
    # Project colors
    # ------------------------------------------------------------------

    def get_project_color(self, project_name: str) -> str:
        data = self.load_data()
        return data["project_colors"].get(project_name, "")

    def set_project_color(self, project_name: str, color: str) -> None:
        data = self.load_data()
        data["project_colors"][project_name] = color
        self.save_data()

    def get_all_project_colors(self) -> Dict[str, str]:
        data = self.load_data()
        return dict(data.get("project_colors", {}))

    # ------------------------------------------------------------------
    # UI state
    # ------------------------------------------------------------------

    def get_ui_state(self) -> Dict[str, Any]:
        data = self.load_data()
        return dict(data.get("ui_state", {}))

    def set_ui_state(self, state: Dict[str, Any]) -> None:
        data = self.load_data()
        ui = data.setdefault("ui_state", {})
        ui.update(state)
        self.save_data()

    # ------------------------------------------------------------------
    # Sync from ProgTrack (read-only from ProgTrack perspective)
    # ------------------------------------------------------------------

    def sync_from_progtrack(self, animals_dict: Dict[str, Any],
                            archived_dict: Optional[Dict[str, Any]] = None) -> None:
        """Ensure every ProgTrack animal has an occupant record; orphan stale ones.

        Archived animals are removed from the unassigned section.
        """
        if not isinstance(animals_dict, dict):
            return
        archived_dict = archived_dict or {}

        data = self.load_data()
        occupants = data["occupants"]
        changed = False

        # 1. Create occupant entries for new ProgTrack animals
        for animal_name in animals_dict:
            if animal_name not in occupants:
                now = self._now_iso()
                occupants[animal_name] = {
                    "occupant_id": animal_name,
                    "type": "real",
                    "cage_id": UNASSIGNED_CAGE_ID,
                    "moved_at": now,
                }
                history = data["movement_history"].setdefault(animal_name, [])
                history.append({
                    "cage_id": UNASSIGNED_CAGE_ID,
                    "moved_in": now,
                    "moved_out": None,
                    "cage_mates_snapshot": [],
                })
                changed = True

        # 2. Mark archived animals (grey in unassigned, but still visible)
        for arch_name in archived_dict:
            occ = occupants.get(arch_name)
            if occ and occ.get("type") == "real":
                if not occ.get("archived"):
                    occ["archived"] = True
                    changed = True
        # Un-archive animals that are back in the active dict
        for animal_name in animals_dict:
            occ = occupants.get(animal_name)
            if occ and occ.get("archived"):
                del occ["archived"]
                changed = True

        # 3. Remove occupants that no longer exist in ProgTrack (deleted)
        for occ_id, occ in list(occupants.items()):
            if occ.get("type") != "real":
                continue
            if occ_id not in animals_dict and occ_id not in archived_dict:
                del occupants[occ_id]
                changed = True

        # 3. Check for invalid cage references
        valid_cages = set(data["structures"]["cages"].keys())
        for occ_id, occ in occupants.items():
            if occ.get("cage_id") and occ["cage_id"] not in valid_cages:
                occ["cage_id"] = UNASSIGNED_CAGE_ID
                occ["moved_at"] = self._now_iso()
                changed = True

        if changed:
            self.save_data()

    # ------------------------------------------------------------------
    # Bulk accessors
    # ------------------------------------------------------------------

    def get_all_buildings(self) -> List[Dict[str, Any]]:
        data = self.load_data()
        buildings = list(data["structures"]["buildings"].values())
        buildings.sort(key=lambda b: b.get("order", 0))
        return buildings

    def get_all_rooms(self) -> List[Dict[str, Any]]:
        data = self.load_data()
        rooms = list(data["structures"]["rooms"].values())
        rooms.sort(key=lambda r: r.get("order", 0))
        return rooms

    def get_all_cages(self) -> List[Dict[str, Any]]:
        data = self.load_data()
        cages = [c for c in data["structures"]["cages"].values() if not c.get("is_virtual")]
        cages.sort(key=lambda c: c.get("order", 0))
        return cages

    def get_all_occupants(self) -> Dict[str, Dict[str, Any]]:
        data = self.load_data()
        return dict(data.get("occupants", {}))
