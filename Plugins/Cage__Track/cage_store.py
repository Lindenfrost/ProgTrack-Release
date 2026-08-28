# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Cage Track persistence layer for backend housing records.

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from Plugins.core.animal_identity import animal_base_name
from Plugins.core.backend_store import BackendJsonStore


UNASSIGNED_CAGE_ID = "cage_unassigned"


class CageStore:
    """Owns Cage Track storage in backend housing records."""

    def __init__(self, plugin_dir: str, backend: Any):
        self.plugin_dir = plugin_dir
        self.backend_store = BackendJsonStore(backend, "housing", "cage")
        self.inspection_store = BackendJsonStore(
            backend, "housing", "inspections"
        )
        self._data: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------
    # Defaults
    # ------------------------------------------------------------------

    def _default_data(self) -> Dict[str, Any]:
        return {
            "version": "1.0",
            "structures": {
                "buildings": {},
                "units": {},
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
                "expanded_units": [],
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

    @staticmethod
    def _event_date_iso(value: Any = None) -> str:
        """Normalize lifecycle dates to the movement-history ISO date format."""
        text = str(value or "").strip()
        if not text:
            return CageStore._now_iso()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text[:10], fmt).date().isoformat()
            except ValueError:
                continue
        return CageStore._now_iso()

    # ------------------------------------------------------------------
    # Load / Save
    # ------------------------------------------------------------------

    def load_data(self) -> Dict[str, Any]:
        if self._data is not None:
            return self._data

        raw = self.backend_store.load(self._default_data())
        if not isinstance(raw, dict):
            raw = self._default_data()

        # Ensure all top-level keys exist
        raw.setdefault("version", "1.0")
        structures = raw.setdefault("structures", {})
        if not isinstance(structures, dict):
            structures = {}
            raw["structures"] = structures
        structures.setdefault("buildings", {})
        structures.setdefault("units", {})
        structures.setdefault("rooms", {})
        cages = structures.setdefault("cages", {})
        for container_name in ("buildings", "units", "rooms", "cages"):
            for entry in structures[container_name].values():
                entry.setdefault("virtual", False)

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
        for key, occupant in list(raw["occupants"].items()):
            if not isinstance(occupant, dict):
                continue
            occupant.setdefault("ipid", key)
            occupant.setdefault("name", animal_base_name(key))
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
        ui.setdefault("expanded_units", [])
        ui.setdefault("expanded_rooms", [])
        ui.setdefault("view_position", {"x": 0, "y": 0})
        ui.setdefault("zoom_level", 1.0)
        ui.setdefault("show_legend", True)

        self._data = raw
        return self._data

    def save_data(self) -> None:
        data = self.load_data()
        self.backend_store.save(data)

    # ------------------------------------------------------------------
    # Structure CRUD
    # ------------------------------------------------------------------

    def create_building(self, name: str, virtual: bool = False) -> Dict[str, Any]:
        data = self.load_data()
        buildings = data["structures"]["buildings"]
        max_order = max((b.get("order", 0) for b in buildings.values()), default=-1)
        bid = self.generate_unique_id("bld")
        entry = {
            "id": bid, "display_name": name.strip(),
            "order": max_order + 1, "virtual": bool(virtual),
        }
        buildings[bid] = entry
        self.save_data()
        return entry

    def create_unit(
        self, parent_building_id: str, name: str, virtual: bool = False
    ) -> Dict[str, Any]:
        data = self.load_data()
        structures = data["structures"]
        if parent_building_id not in structures["buildings"]:
            raise ValueError(f"Unknown parent building: {parent_building_id}")
        units = structures["units"]
        siblings = [
            unit for unit in units.values()
            if unit.get("parent_building_id") == parent_building_id
        ]
        max_order = max((unit.get("order", 0) for unit in siblings), default=-1)
        unit_id = self.generate_unique_id("unit")
        entry = {
            "id": unit_id,
            "parent_building_id": parent_building_id,
            "display_name": name.strip(),
            "order": max_order + 1,
            "virtual": bool(virtual),
        }
        units[unit_id] = entry
        self.save_data()
        return entry

    def create_room(
        self, parent_id: str, name: str, virtual: bool = False
    ) -> Dict[str, Any]:
        data = self.load_data()
        structures = data["structures"]
        rooms = structures["rooms"]
        if parent_id in structures["units"]:
            parent_unit_id: Optional[str] = parent_id
            parent_building_id = structures["units"][parent_id].get("parent_building_id")
        elif parent_id in structures["buildings"]:
            # Legacy three-level installations remain readable and editable.
            parent_unit_id = None
            parent_building_id = parent_id
        else:
            raise ValueError(f"Unknown parent unit/building: {parent_id}")
        siblings = [
            room for room in rooms.values()
            if room.get("parent_unit_id") == parent_unit_id
            and room.get("parent_building_id") == parent_building_id
        ]
        max_order = max((r.get("order", 0) for r in siblings), default=-1)
        rid = self.generate_unique_id("room")
        entry = {
            "id": rid,
            "parent_building_id": parent_building_id,
            "parent_unit_id": parent_unit_id,
            "display_name": name.strip(),
            "order": max_order + 1,
            "virtual": bool(virtual),
        }
        rooms[rid] = entry
        self.save_data()
        return entry

    def create_cage(
        self, parent_room_id: str, name: str, virtual: bool = False
    ) -> Dict[str, Any]:
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
            "virtual": bool(virtual),
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
            child_units = [
                unit_id for unit_id, unit in structures["units"].items()
                if unit.get("parent_building_id") == struct_id
            ]
            for unit_id in child_units:
                self._cascade_delete_unit(unit_id, structures, data["occupants"])
            # Legacy rooms may still be attached directly to a building.
            child_rooms = [rid for rid, r in structures["rooms"].items()
                           if r.get("parent_building_id") == struct_id
                           and not r.get("parent_unit_id")]
            for rid in child_rooms:
                self._cascade_delete_room(rid, structures, data["occupants"])
            del structures["buildings"][struct_id]

        elif struct_type == "unit":
            if struct_id not in structures["units"]:
                return False
            self._cascade_delete_unit(struct_id, structures, data["occupants"])

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
        type_map = {
            "building": "buildings", "unit": "units",
            "room": "rooms", "cage": "cages",
        }
        container = data["structures"].get(type_map.get(struct_type, ""), {})
        if struct_id not in container:
            return False
        container[struct_id]["display_name"] = new_name.strip()
        self.save_data()
        return True

    def _cascade_delete_unit(self, unit_id: str, structures: dict, occupants: dict) -> None:
        """Delete a unit and all child rooms/cages, orphaning occupants."""
        child_rooms = [
            room_id for room_id, room in structures["rooms"].items()
            if room.get("parent_unit_id") == unit_id
        ]
        for room_id in child_rooms:
            self._cascade_delete_room(room_id, structures, occupants)
        structures["units"].pop(unit_id, None)

    def set_structure_virtual(
        self, struct_id: str, struct_type: str, virtual: bool
    ) -> bool:
        data = self.load_data()
        type_map = {
            "building": "buildings", "unit": "units",
            "room": "rooms", "cage": "cages",
        }
        container = data["structures"].get(type_map.get(struct_type, ""), {})
        if struct_id not in container:
            return False
        container[struct_id]["virtual"] = bool(virtual)
        self.save_data()
        return True

    def is_effectively_virtual(self, struct_id: str) -> bool:
        data = self.load_data()
        structures = data["structures"]
        if struct_id in structures["buildings"]:
            return bool(structures["buildings"][struct_id].get("virtual"))
        if struct_id in structures["units"]:
            unit = structures["units"][struct_id]
            building = structures["buildings"].get(
                unit.get("parent_building_id"), {})
            return bool(unit.get("virtual") or building.get("virtual"))
        if struct_id in structures["rooms"]:
            room = structures["rooms"][struct_id]
            unit = structures["units"].get(room.get("parent_unit_id"), {})
            building = structures["buildings"].get(
                unit.get("parent_building_id") or room.get("parent_building_id"), {})
            return bool(
                room.get("virtual") or unit.get("virtual") or building.get("virtual")
            )
        if struct_id in structures["cages"]:
            cage = structures["cages"][struct_id]
            room = structures["rooms"].get(cage.get("parent_room_id"), {})
            unit = structures["units"].get(room.get("parent_unit_id"), {})
            building = structures["buildings"].get(
                unit.get("parent_building_id") or room.get("parent_building_id"), {})
            return bool(
                cage.get("virtual") or room.get("virtual")
                or unit.get("virtual") or building.get("virtual")
            )
        return False

    def eligible_inspection_cages(self) -> List[str]:
        data = self.load_data()
        occupied = {
            str(item.get("cage_id") or "")
            for item in data.get("occupants", {}).values()
            if item.get("cage_id")
            and not item.get("archived")
            and not item.get("dead")
            and not item.get("death_date")
        }
        return sorted(
            cage_id
            for cage_id in occupied
            if cage_id != UNASSIGNED_CAGE_ID
            and cage_id in data["structures"]["cages"]
            and not self.is_effectively_virtual(cage_id)
        )

    def move_structure(self, struct_id: str, new_parent_id: str, new_order: int) -> bool:
        data = self.load_data()
        structures = data["structures"]

        # Determine type from ID presence
        if struct_id in structures["units"]:
            entry = structures["units"][struct_id]
            if new_parent_id not in structures["buildings"]:
                return False
            entry["parent_building_id"] = new_parent_id
            entry["order"] = new_order
        elif struct_id in structures["rooms"]:
            entry = structures["rooms"][struct_id]
            if new_parent_id in structures["units"]:
                entry["parent_unit_id"] = new_parent_id
                entry["parent_building_id"] = structures["units"][new_parent_id].get(
                    "parent_building_id")
            elif new_parent_id in structures["buildings"]:
                entry["parent_unit_id"] = None
                entry["parent_building_id"] = new_parent_id
            else:
                return False
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
        for kind in ("buildings", "units", "rooms", "cages"):
            if struct_id in structures[kind]:
                structures[kind][struct_id]["order"] = new_order
                self.save_data()
                return True
        return False

    def get_structure_by_id(self, struct_id: str) -> Optional[Dict[str, Any]]:
        data = self.load_data()
        structures = data["structures"]
        for kind in ("buildings", "units", "rooms", "cages"):
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
            "ipid": name.strip(),
            "name": animal_base_name(name),
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

    def _move_occupant_in_data(
        self,
        data: Dict[str, Any],
        occupant_id: str,
        cage_id: str,
        *,
        moved_at: Any = None,
        reason: Optional[str] = None,
    ) -> bool:
        """Move one occupant while preserving exactly one open history row."""
        occupants = data["occupants"]
        key = occupant_id.strip()
        if key not in occupants:
            return False

        target_cage = cage_id or UNASSIGNED_CAGE_ID
        event_date = self._event_date_iso(moved_at)
        history = data["movement_history"].setdefault(key, [])
        old_cage = occupants[key].get("cage_id") or UNASSIGNED_CAGE_ID
        open_entries = [
            entry for entry in history if entry.get("moved_out") is None
        ]
        if (
            old_cage == target_cage
            and len(open_entries) == 1
            and open_entries[0].get("cage_id") == target_cage
        ):
            return False

        # Daily precision: replace the final movement made on the same day,
        # then close every open predecessor before recording the new address.
        last_moved_in = str(history[-1].get("moved_in") or "") if history else ""
        if history and last_moved_in[:10] == event_date[:10]:
            history.pop()
            if history:
                previous_out = str(history[-1].get("moved_out") or "")
                if previous_out[:10] == event_date[:10]:
                    history[-1]["moved_out"] = None

        for entry in history:
            if entry.get("moved_out") is None:
                entry["moved_out"] = event_date

        mates = [] if target_cage == UNASSIGNED_CAGE_ID else [
            occupant["occupant_id"]
            for occupant in occupants.values()
            if occupant.get("cage_id") == target_cage
            and occupant.get("occupant_id") != key
            and not occupant.get("archived")
            and not occupant.get("dead")
        ]
        movement = {
            "cage_id": target_cage,
            "moved_in": event_date,
            "moved_out": None,
            "cage_mates_snapshot": mates,
        }
        if reason:
            movement["reason"] = str(reason)
        history.append(movement)
        occupants[key]["cage_id"] = target_cage
        occupants[key]["moved_at"] = event_date
        return True

    def unhouse_occupant(
        self,
        occupant_id: str,
        *,
        moved_at: Any = None,
        reason: str = "lifecycle",
    ) -> bool:
        """Move an occupant to Unassigned and close prior housing history."""
        data = self.load_data()
        changed = self._move_occupant_in_data(
            data,
            occupant_id,
            UNASSIGNED_CAGE_ID,
            moved_at=moved_at,
            reason=reason,
        )
        if changed:
            self.save_data()
        return changed

    def set_occupant_cage(self, occupant_id: str, cage_id: str) -> None:
        """Move occupant to a new cage and record movement history.

        Daily precision: if the animal was already moved today the
        previous same-day entry is overwritten instead of appended.
        """
        data = self.load_data()
        occupant = data["occupants"].get(occupant_id.strip())
        if not occupant:
            return
        target = cage_id
        if (occupant.get("archived") or occupant.get("dead")) and cage_id != UNASSIGNED_CAGE_ID:
            target = UNASSIGNED_CAGE_ID
        if self._move_occupant_in_data(data, occupant_id, target):
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
        """Return current building/unit/room/cage selection for ProgTrack dialog."""
        data = self.load_data()
        occ = data["occupants"].get(occupant_id.strip())
        if not occ:
            return {
                "building_id": None, "unit_id": None,
                "room_id": None, "cage_id": None,
            }

        cage_id = occ.get("cage_id")
        if not cage_id or cage_id == UNASSIGNED_CAGE_ID:
            return {
                "building_id": None, "unit_id": None,
                "room_id": None, "cage_id": None,
            }

        structures = data["structures"]
        cage = structures["cages"].get(cage_id)
        if not cage:
            return {
                "building_id": None, "unit_id": None,
                "room_id": None, "cage_id": None,
            }

        room_id = cage.get("parent_room_id")
        room = structures["rooms"].get(room_id) if room_id else None
        unit_id = room.get("parent_unit_id") if room else None
        unit = structures["units"].get(unit_id) if unit_id else None
        building_id = (
            unit.get("parent_building_id") if unit
            else room.get("parent_building_id") if room else None
        )

        return {
            "building_id": building_id,
            "unit_id": unit_id,
            "room_id": room_id,
            "cage_id": cage_id,
        }

    def set_address_from_dialog(
        self,
        occupant_id: str,
        building_id: Optional[str],
        unit_id: Optional[str],
        room_id: Optional[str],
        cage_id: Optional[str],
    ) -> None:
        """Validate and persist the dialog selection in the backend only."""
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

    def clear_project_colors(self) -> None:
        data = self.load_data()
        data["project_colors"] = {}
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
        """Synchronize identities and lifecycle-safe current housing.

        Dead and archived animals are always un-housed.  Restoring an archived
        animal clears its archived marker but intentionally leaves it in
        ``Unassigned`` until a keeper chooses a new physical address.
        """
        if not isinstance(animals_dict, dict):
            return
        archived_dict = archived_dict or {}

        data = self.load_data()
        occupants = data["occupants"]
        changed = False

        source_records = dict(archived_dict)
        source_records.update(animals_dict)

        # 1. Create occupant entries for every active or archived animal.
        for animal_name, record in source_records.items():
            if animal_name not in occupants:
                now = self._now_iso()
                occupants[animal_name] = {
                    "occupant_id": animal_name,
                    "ipid": animal_name,
                    "name": animal_base_name(animal_name, record),
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

        # 2. Remove occupants that no longer exist in ProgTrack (deleted).
        for occ_id, occ in list(occupants.items()):
            if occ.get("type") != "real":
                continue
            if occ_id not in animals_dict and occ_id not in archived_dict:
                del occupants[occ_id]
                # Movement history is keyed by the same canonical identity.
                # Keeping it after a hard deletion creates an orphan row that
                # cannot be displayed or attributed to an animal any longer.
                data["movement_history"].pop(occ_id, None)
                changed = True

        # Also repair histories orphaned by older versions that removed only
        # the occupant record.
        for occ_id in list(data["movement_history"]):
            if occ_id not in occupants:
                del data["movement_history"][occ_id]
                changed = True

        # 3. Refresh identity/lifecycle markers and reconcile current housing.
        valid_cages = set(data["structures"]["cages"].keys())
        for occ_id, occ in occupants.items():
            if occ.get("type") == "real":
                rec = source_records.get(occ_id, {}) or {}
                expected_name = animal_base_name(occ_id, rec)
                if occ.get("ipid") != occ_id:
                    occ["ipid"] = occ_id
                    changed = True
                if occ.get("name") != expected_name:
                    occ["name"] = expected_name
                    changed = True

                stored_id = str(rec.get("id") or "").strip()
                if stored_id and occ.get("animal_id") != stored_id:
                    occ["animal_id"] = stored_id
                    changed = True
                elif not stored_id and "animal_id" in occ:
                    del occ["animal_id"]
                    changed = True

                is_archived = occ_id in archived_dict
                if bool(occ.get("archived")) != is_archived:
                    if is_archived:
                        occ["archived"] = True
                    else:
                        occ.pop("archived", None)
                    changed = True

                death_date = str(rec.get("death_date") or "").strip()
                is_dead = bool(death_date)
                if bool(occ.get("dead")) != is_dead:
                    if is_dead:
                        occ["dead"] = True
                    else:
                        occ.pop("dead", None)
                    changed = True
                if death_date:
                    if occ.get("death_date") != death_date:
                        occ["death_date"] = death_date
                        changed = True
                elif "death_date" in occ:
                    del occ["death_date"]
                    changed = True

                if is_dead or is_archived:
                    event_date = death_date or rec.get("departure_date")
                    reason = "death" if is_dead else "archived"
                    changed = self._move_occupant_in_data(
                        data,
                        occ_id,
                        UNASSIGNED_CAGE_ID,
                        moved_at=event_date,
                        reason=reason,
                    ) or changed
                    continue

            if occ.get("cage_id") not in valid_cages:
                changed = self._move_occupant_in_data(
                    data,
                    occ_id,
                    UNASSIGNED_CAGE_ID,
                    reason="invalid_cage",
                ) or changed

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

    def get_all_units(self) -> List[Dict[str, Any]]:
        data = self.load_data()
        units = list(data["structures"]["units"].values())
        units.sort(key=lambda unit: unit.get("order", 0))
        return units

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
