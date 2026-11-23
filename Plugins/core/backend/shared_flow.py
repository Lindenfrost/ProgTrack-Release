# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Scoped persistence for Flow Track records and embryo identities."""

from collections.abc import Mapping
from copy import deepcopy
from datetime import datetime, timezone

from .errors import ConflictError, PermissionDeniedError, ValidationError

_SECTIONS = {"egg_donors", "sperm_donors", "transfers_by_id"}
_FREEZER = "freezer_main"
_ANIMAL_FIELDS = {"surrogate_name", "egg_donor_name", "sperm_donor_name", "animal_name"}


def _flatten(data):
    manual = data.get("manual_data", {})
    if not isinstance(manual, Mapping):
        raise ValidationError("The Flow Track register is invalid.")
    rows = {}
    for section, body in manual.items():
        if section == "total_embryo_count":
            continue
        if not isinstance(body, Mapping):
            raise ValidationError("The Flow Track register contains an invalid section.")
        if section in _SECTIONS:
            rows.update({(section, key): value for key, value in body.items()})
        else:
            rows[("", section)] = body
    return rows, manual.get("total_embryo_count", 0)


def _references(value, *, freezer_name=""):
    if isinstance(value, Mapping):
        result = set()
        for key, item in value.items():
            if key in _ANIMAL_FIELDS and item and item != freezer_name:
                result.add(str(item))
            else:
                result |= _references(item, freezer_name=freezer_name)
        return result
    if isinstance(value, list):
        return set().union(*(_references(item, freezer_name=freezer_name) for item in value))
    return set()


def flow_record_animals(key, row):
    section, identity = key
    anchors = {identity} if section != "transfers_by_id" else set()
    freezer = "FREEZER" if key == ("transfers_by_id", _FREEZER) else ""
    return anchors | _references(row, freezer_name=freezer)


def _encode(rows, counter):
    manual = {key: {} for key in _SECTIONS}
    for (section, key), value in rows.items():
        if section:
            manual[section][key] = value
        else:
            manual[key] = value
    manual["total_embryo_count"] = counter
    return {"version": "3.0", "manual_data": manual, "last_updated": datetime.now(timezone.utc).isoformat()}


def changed_flow_animals(original, replacement):
    previous, _ = _flatten(original)
    after, _ = _flatten(replacement)
    changed = {key for key in previous.keys() | after.keys() if previous.get(key) != after.get(key)}
    animals = set()
    for key in changed:
        if key in previous:
            animals |= flow_record_animals(key, previous[key])
        if key in after:
            animals |= flow_record_animals(key, after[key])
    return changed, animals


def _embryo_ids(rows):
    result = set()
    for (section, _), row in rows.items():
        if section != "transfers_by_id":
            continue
        for embryo in row.get("embryos", ()):
            identity = str(embryo.get("embryo_id") or "").strip()
            if not identity or identity in result:
                raise ValidationError("Each embryo needs a unique, nonempty ID.")
            result.add(identity)
    return result


def load_shared_flow(backend, session):
    with backend.protected_request(session, "flow_track.use", "flow_track.open"):
        core = backend.load_core_data(session=session)
        visible = core["animals"].keys() | core["archived_animals"].keys()
        source, revision = backend.records.get_with_revision("reproduction", "flow", {})
        rows, counter = _flatten(source)
        allowed = {key: row for key, row in rows.items() if flow_record_animals(key, row) <= visible}
        return _encode(allowed, counter), revision


def save_shared_flow(backend, session, original, replacement, revision, leases):
    changed, animals = changed_flow_animals(original, replacement)
    if animals != leases.keys():
        raise PermissionDeniedError("Each animal affected by a Flow Track edit requires a lease.")
    before, old_count = _flatten(original)
    after, new_count = _flatten(replacement)
    old_ids, new_ids = _embryo_ids(before), _embryo_ids(after)
    required = {"flow_track.edit"}
    if new_ids - old_ids:
        required.add("flow_track.create")
    if old_ids - new_ids:
        required.add("flow_track.delete")
    if type(new_count) is not int or new_count < old_count:
        raise ValidationError("The Flow Track embryo counter cannot decrease.")
    with backend.protected_request(session, *sorted(required), animal_leases=leases, write=True) as (
        connection, account, _,
    ):
        stored, current_revision = backend.records.get_with_revision("reproduction", "flow", {})
        if revision != current_revision:
            raise ConflictError("Flow Track changed. Reload and try again.")
        rows, counter = _flatten(stored)
        if counter != old_count or any(rows.get(key) != before.get(key) for key in changed):
            raise ConflictError("The affected Flow Track records changed. Reload and try again.")
        for key in changed:
            if key in after:
                rows[key] = deepcopy(after[key])
            else:
                rows.pop(key, None)
        _embryo_ids(rows)
        next_revision = backend.records.put(
            "reproduction", "flow", _encode(rows, new_count), expected_revision=revision,
        )
        backend.audit._append_in_transaction(
            connection, actor_login=account["username"], category="flow", action="update",
            entity_type="flow", entity_id="register", payload={"animal_ipids": sorted(animals)},
        )
        return next_revision
