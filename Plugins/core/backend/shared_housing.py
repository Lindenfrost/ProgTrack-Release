# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Shared Cage Track edits and per-animal visibility."""

from collections.abc import Mapping
from copy import deepcopy

from .errors import ConflictError, PermissionDeniedError, SessionRevokedError, ValidationError

_ANIMAL_SECTIONS = ("occupants", "movement_history")
_STRUCTURE_SECTIONS = ("structures", "project_colors")
_KEYS = {"version", "ui_state", *_ANIMAL_SECTIONS, *_STRUCTURE_SECTIONS}


def affected_housing_animals(original, replacement):
    changed = set()
    for section in _ANIMAL_SECTIONS:
        before, after = original.get(section, {}), replacement.get(section, {})
        if not isinstance(before, Mapping) or not isinstance(after, Mapping):
            raise ValidationError("The housing records are invalid.")
        changed |= {ipid for ipid in before.keys() | after.keys() if before.get(ipid) != after.get(ipid)}
    return changed


def load_shared_housing(backend, session, defaults):
    with backend.protected_request(session, "cage.view"):
        core = backend.load_core_data(session=session)
        visible = core["animals"].keys() | core["archived_animals"].keys()
        housing, revision = backend.records.get_with_revision("housing", "cage", defaults)
        if not isinstance(housing, Mapping):
            raise ValidationError("The housing catalogue is invalid.")
        result = deepcopy(housing)
        for section in _ANIMAL_SECTIONS:
            if not isinstance(result.get(section), Mapping):
                raise ValidationError("The housing records are invalid.")
            result[section] = {ipid: row for ipid, row in result[section].items() if ipid in visible}
        return result, revision


def save_shared_housing(backend, session, original, replacement, revision, leases, defaults, structure_token):
    if replacement.keys() - _KEYS:
        raise ValidationError("Unknown shared housing field.")
    animals = affected_housing_animals(original, replacement)
    if animals != leases.keys():
        raise PermissionDeniedError("A lease is required for every affected animal.")
    fields = {key for key in _STRUCTURE_SECTIONS if original.get(key) != replacement.get(key)}
    required = {"cage.view"}
    if animals:
        required.add("cage.assign_locations")
    if "structures" in fields:
        required.add("cage.manage_rooms_buildings")
    if "project_colors" in fields:
        required.add("cage.edit")
    with backend.protected_request(
        session, *sorted(required), animal_leases=leases,
        entity_leases={("housing", "cage"): structure_token} if fields else {},
        write=True,
    ) as (
        connection, account, _,
    ):
        data, current_revision = backend.records.get_with_revision("housing", "cage", defaults)
        if current_revision != revision:
            raise ConflictError("The housing catalogue changed. Reload and try again.")
        for section in _ANIMAL_SECTIONS:
            for ipid in animals:
                previous = original.get(section, {}).get(ipid)
                if data.get(section, {}).get(ipid) != previous:
                    raise ConflictError("The animal's housing changed. Reload and try again.")
                updated = replacement.get(section, {}).get(ipid)
                if updated is None:
                    data[section].pop(ipid, None)
                else:
                    data[section][ipid] = updated
        for key in fields:
            data[key] = replacement[key]
        if "structures" in fields:
            cages = data["structures"].get("cages", {})
            if any(row.get("cage_id") and row["cage_id"] not in cages for row in data["occupants"].values()):
                raise ValidationError("Move all occupants before removing their cage.")
        data["ui_state"] = replacement.get("ui_state", data.get("ui_state", {}))
        result = backend.records.put("housing", "cage", data, expected_revision=revision)
        if animals or fields:
            backend.audit._append_in_transaction(
                connection, actor_login=account["username"], category="housing", action="update",
                entity_type="housing", entity_id="cage", payload={"animal_ipids": sorted(animals)},
            )
        return result


def apply_core_addresses(backend, connection, account, permissions, changes, addresses):
    """Persist Core dialog housing assignments in the caller's transaction."""
    addresses = addresses or {}
    departures = {
        ipid: change for ipid, change in changes.items() if change.original is not None and (
            change.original_section == "animals" and change.section == "archived_animals"
            or change.record.get("death_date") and change.record.get("death_date") != change.original.get("death_date")
        )
    }
    if not addresses and not departures:
        return
    if addresses and "*" not in permissions and "cage.assign_locations" not in permissions:
        raise PermissionDeniedError("This account cannot assign animal housing locations.")
    if addresses.keys() - changes.keys():
        raise ValidationError("A housing change must belong to a requested Core animal.")
    from Plugins.Cage__Track.cage_store import CageStore, UNASSIGNED_CAGE_ID

    data, revision = backend.records.get_with_revision("housing", "cage", CageStore._default_data())
    changed_housing = False
    for ipid, (old_cage, new_cage) in sorted(addresses.items()):
        occupant = data["occupants"].get(ipid)
        current = (occupant or {}).get("cage_id") or UNASSIGNED_CAGE_ID
        if current != (old_cage or UNASSIGNED_CAGE_ID):
            raise ConflictError("The animal's housing changed. Reopen the Core editor.")
        target = new_cage or UNASSIGNED_CAGE_ID
        if target not in data["structures"]["cages"]:
            raise ValidationError("The requested cage no longer exists.")
        change = changes[ipid]
        if target != UNASSIGNED_CAGE_ID and (
            change.section == "archived_animals" or change.record.get("death_date") or change.record.get("sterbedatum")
        ):
            raise ValidationError("An archived or deceased animal cannot be assigned to a cage.")
        if occupant is None:
            CageStore._create_occupant_in_data(data, ipid, "real", target)
        elif occupant.get("type", "real") != "real":
            raise ConflictError("A dummy occupant already uses this animal identity.")
        else:
            CageStore._move_occupant_in_data(data, ipid, target)
        changed_housing = True
        backend.audit._append_in_transaction(
            connection, actor_login=account["username"], category="housing", action="assign",
            entity_type="animal", entity_id=ipid, payload={"previous": current, "cage_id": target},
        )
    for ipid, change in sorted(departures.items()):
        occupant = data["occupants"].get(ipid)
        if occupant is None or occupant.get("type", "real") != "real":
            continue
        record = change.record
        when = record.get("death_date") or record.get("departure_date")
        label = "death" if record.get("death_date") else "departure"
        occupant.update(dead=bool(record.get("death_date")), archived=change.section == "archived_animals")
        CageStore._move_occupant_in_data(data, ipid, UNASSIGNED_CAGE_ID, moved_at=when, reason=label)
        changed_housing = True
        backend.audit._append_in_transaction(
            connection, actor_login=account["username"], category="housing", action="lifecycle",
            entity_type="animal", entity_id=ipid, payload={"reason": label},
        )
    if changed_housing:
        backend.records.put("housing", "cage", data, expected_revision=revision)


def load_shared_inspections(backend, session):
    with backend.protected_request(session, "cage.view"):
        return backend.records.get_with_revision("housing", "inspections", {"records": []})


def save_shared_inspections(backend, session, records, revision):
    token = backend.leases.acquire("housing", "inspections", session=session)["token"]
    try:
        with backend.protected_request(
            session, "cage.record_inspection", entity_leases={("housing", "inspections"): token}, write=True,
        ) as (connection, account, _):
            result = backend.records.put("housing", "inspections", {"records": records}, expected_revision=revision)
            backend.audit._append_in_transaction(
                connection, actor_login=account["username"], category="housing", action="inspection",
                entity_type="housing", entity_id="inspections", payload={},
            )
        return result
    finally:
        try:
            backend.leases.release("housing", "inspections", token, session=session)
        except SessionRevokedError:
            pass
