# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Required ProgTrack version: see plugin manifest.
# Required Launcher version: 0.1.0 RC or newer.
# Module: Cage Track hierarchy business logic.

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from .cage_store import CageStore, UNASSIGNED_CAGE_ID


class CageEngine:
    """Hierarchy queries and business logic operating on CageStore data."""

    def __init__(self, store: CageStore):
        self.store = store

    # ------------------------------------------------------------------
    # Hierarchy building
    # ------------------------------------------------------------------

    def build_hierarchy(self) -> List[Dict[str, Any]]:
        """Build Building → Unit → Room → Cage tree, retaining legacy rooms."""
        data = self.store.load_data()
        structures = data["structures"]

        buildings = sorted(structures["buildings"].values(), key=lambda b: b.get("order", 0))
        units = structures["units"]
        rooms = structures["rooms"]
        cages = structures["cages"]
        occupants_by_cage: Dict[str, List[Dict[str, Any]]] = {}
        for occupant in data.get("occupants", {}).values():
            cage_id = occupant.get("cage_id") or UNASSIGNED_CAGE_ID
            occupants_by_cage.setdefault(cage_id, []).append(occupant)

        tree = []

        def room_node(room: Dict[str, Any]) -> Dict[str, Any]:
            room_id = room["id"]
            room_cages = sorted(
                [
                    cage for cage in cages.values()
                    if cage.get("parent_room_id") == room_id
                    and not cage.get("is_virtual")
                ],
                key=lambda cage: cage.get("order", 0),
            )
            cage_nodes = [
                {**cage, "occupants": occupants_by_cage.get(cage["id"], [])}
                for cage in room_cages
            ]
            return {**room, "cages": cage_nodes}

        for bld in buildings:
            bld_id = bld["id"]
            bld_units = sorted(
                [
                    unit for unit in units.values()
                    if unit.get("parent_building_id") == bld_id
                ],
                key=lambda unit: unit.get("order", 0),
            )
            unit_nodes = []
            for unit in bld_units:
                unit_rooms = sorted(
                    [
                        room for room in rooms.values()
                        if room.get("parent_unit_id") == unit["id"]
                    ],
                    key=lambda room: room.get("order", 0),
                )
                unit_nodes.append({**unit, "rooms": [room_node(room) for room in unit_rooms]})
            legacy_rooms = sorted(
                [
                    room for room in rooms.values()
                    if room.get("parent_building_id") == bld_id
                    and not room.get("parent_unit_id")
                ],
                key=lambda room: room.get("order", 0),
            )
            tree.append({
                **bld,
                "units": unit_nodes,
                "rooms": [room_node(room) for room in legacy_rooms],
            })
        return tree

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_all_buildings(self) -> List[Dict[str, Any]]:
        return self.store.get_all_buildings()

    def get_units_in_building(self, building_id: str) -> List[Dict[str, Any]]:
        data = self.store.load_data()
        units = [
            unit for unit in data["structures"]["units"].values()
            if unit.get("parent_building_id") == building_id
        ]
        units.sort(key=lambda unit: unit.get("order", 0))
        return units

    def get_rooms_in_unit(self, unit_id: str) -> List[Dict[str, Any]]:
        data = self.store.load_data()
        rooms = [
            room for room in data["structures"]["rooms"].values()
            if room.get("parent_unit_id") == unit_id
        ]
        rooms.sort(key=lambda room: room.get("order", 0))
        return rooms

    def get_rooms_in_building(self, building_id: str) -> List[Dict[str, Any]]:
        data = self.store.load_data()
        rooms = [
            r for r in data["structures"]["rooms"].values()
            if r.get("parent_building_id") == building_id
        ]
        rooms.sort(key=lambda r: r.get("order", 0))
        return rooms

    def get_cages_in_room(self, room_id: str) -> List[Dict[str, Any]]:
        data = self.store.load_data()
        cages = [
            c for c in data["structures"]["cages"].values()
            if c.get("parent_room_id") == room_id and not c.get("is_virtual")
        ]
        cages.sort(key=lambda c: c.get("order", 0))
        return cages

    def get_occupants_in_cage(self, cage_id: str) -> List[Dict[str, Any]]:
        return self.store.get_cage_occupants(cage_id)

    def resolve_cage_path(self, cage_id: str,
                          unassigned_label: str = "Unassigned") -> str:
        """Get full display path: Building > Unit > Room > Cage."""
        data = self.store.load_data()
        structures = data["structures"]

        if cage_id == UNASSIGNED_CAGE_ID:
            return unassigned_label

        cage = structures["cages"].get(cage_id)
        if not cage:
            return f"Unknown Cage ({cage_id})"

        cage_name = cage.get("display_name", cage_id)
        room_id = cage.get("parent_room_id")
        room = structures["rooms"].get(room_id) if room_id else None
        if not room:
            return cage_name

        room_name = room.get("display_name", room_id)
        unit_id = room.get("parent_unit_id")
        unit = structures["units"].get(unit_id) if unit_id else None
        bld_id = unit.get("parent_building_id") if unit else room.get("parent_building_id")
        bld = structures["buildings"].get(bld_id) if bld_id else None
        if not bld:
            return f"{room_name} > {cage_name}"

        bld_name = bld.get("display_name", bld_id)
        if unit:
            unit_name = unit.get("display_name", unit_id)
            return f"{bld_name} > {unit_name} > {room_name} > {cage_name}"
        return f"{bld_name} > {room_name} > {cage_name}"

    def validate_structure_exists(self, struct_id: str, struct_type: str) -> bool:
        data = self.store.load_data()
        type_map = {
            "building": "buildings", "unit": "units",
            "room": "rooms", "cage": "cages",
        }
        container = data["structures"].get(type_map.get(struct_type, ""), {})
        return struct_id in container

    def get_empty_structures(self) -> Dict[str, List[Dict[str, Any]]]:
        """Find structures with no child elements or occupants."""
        data = self.store.load_data()
        structures = data["structures"]
        occupants = data["occupants"]

        empty_buildings = []
        for bld in structures["buildings"].values():
            has_units = any(
                unit.get("parent_building_id") == bld["id"]
                for unit in structures["units"].values()
            )
            has_legacy_rooms = any(
                room.get("parent_building_id") == bld["id"]
                and not room.get("parent_unit_id")
                for room in structures["rooms"].values()
            )
            if not has_units and not has_legacy_rooms:
                empty_buildings.append(bld)

        empty_units = []
        for unit in structures["units"].values():
            has_rooms = any(
                room.get("parent_unit_id") == unit["id"]
                for room in structures["rooms"].values()
            )
            if not has_rooms:
                empty_units.append(unit)

        empty_rooms = []
        for room in structures["rooms"].values():
            has_cages = any(
                c.get("parent_room_id") == room["id"]
                for c in structures["cages"].values()
                if not c.get("is_virtual")
            )
            if not has_cages:
                empty_rooms.append(room)

        empty_cages = []
        for cage in structures["cages"].values():
            if cage.get("is_virtual"):
                continue
            has_occupants = any(o.get("cage_id") == cage["id"] for o in occupants.values())
            if not has_occupants:
                empty_cages.append(cage)

        return {
            "buildings": empty_buildings, "units": empty_units,
            "rooms": empty_rooms, "cages": empty_cages,
        }

    def get_structure_by_id(self, struct_id: str, struct_type: str) -> Optional[Dict[str, Any]]:
        data = self.store.load_data()
        type_map = {
            "building": "buildings", "unit": "units",
            "room": "rooms", "cage": "cages",
        }
        container = data["structures"].get(type_map.get(struct_type, ""), {})
        return container.get(struct_id)

    # ------------------------------------------------------------------
    # Project distribution
    # ------------------------------------------------------------------

    def calculate_cage_project_distribution(
        self, cage_id: str, animals_dict: Optional[Dict[str, Any]] = None
    ) -> Dict[str, float]:
        """Get percentage of occupants per project in a cage."""
        occupants = self.get_occupants_in_cage(cage_id)
        if not occupants or not animals_dict:
            return {}

        project_counts: Dict[str, int] = {}
        total = 0
        for occ in occupants:
            if occ.get("type") != "real":
                continue
            animal = animals_dict.get(occ["occupant_id"], {})
            project = animal.get("project", "") or ""
            if project:
                project_counts[project] = project_counts.get(project, 0) + 1
                total += 1

        if total == 0:
            return {}
        return {proj: count / total for proj, count in project_counts.items()}

    # ------------------------------------------------------------------
    # Role color lookup
    # ------------------------------------------------------------------

    def get_occupant_role_color(
        self,
        occupant_id: str,
        role_color_lookup: Optional[Callable[[str], str]] = None,
    ) -> str:
        """Get role color for real occupants; neutral gray for dummies."""
        occ = self.store.get_occupant(occupant_id)
        if not occ:
            return "#757575"
        if occ.get("type") == "dummy":
            return "#757575"
        if role_color_lookup:
            color = role_color_lookup(occupant_id)
            if color:
                return color
        return "#000000"

    # ------------------------------------------------------------------
    # Drag-and-drop validation
    # ------------------------------------------------------------------

    def validate_drag_drop(self, source_id: str, target_id: str, drag_type: str) -> bool:
        """Check if a drag-and-drop move is valid."""
        if source_id == target_id:
            return False

        if drag_type == "occupant":
            # Target must be a valid cage
            data = self.store.load_data()
            return target_id in data["structures"]["cages"]

        if drag_type == "cage":
            # Target must be a valid room
            data = self.store.load_data()
            return target_id in data["structures"]["rooms"]

        if drag_type == "room":
            # New rooms target units; legacy installations may use buildings.
            data = self.store.load_data()
            return (
                target_id in data["structures"]["units"]
                or target_id in data["structures"]["buildings"]
            )

        if drag_type == "unit":
            data = self.store.load_data()
            return target_id in data["structures"]["buildings"]

        if drag_type == "building":
            # Buildings can only be reordered (target is another building)
            data = self.store.load_data()
            return target_id in data["structures"]["buildings"]

        return False

    # ------------------------------------------------------------------
    # Duration computation
    # ------------------------------------------------------------------

    @staticmethod
    def compute_duration_days(moved_in: Optional[str], moved_out: Optional[str]) -> Optional[int]:
        return CageStore.calculate_duration(moved_in, moved_out)
