# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Shared Sample Track edits scoped to the affected Core animal IPIDs."""

from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import date, datetime

from .errors import ConflictError, PermissionDeniedError, ValidationError
from .json_codec import dumps


def _group_rows(rows):
    grouped = defaultdict(Counter)
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValidationError("The sample register contains an invalid row.")
        grouped[str(row.get("animal_name") or "")][dumps(row)] += 1
    return grouped


def changed_sample_animals(previous, next_rows):
    before, after = _group_rows(previous), _group_rows(next_rows)
    changed = {ipid for ipid in before.keys() | after.keys() if before.get(ipid) != after.get(ipid)}
    if "" in changed:
        raise ValidationError("A Shared sample must identify its Core animal.")
    return changed


def _record(record_id):
    if record_id not in {"organs", "other"}:
        raise ValidationError("Unknown sample register.")
    return str(record_id)


def load_shared_samples(backend, session, record_id):
    record_id = _record(record_id)
    with backend.protected_request(session, "sample_track.use"):
        snapshot = backend.load_core_data(session=session)
        keys = snapshot["animals"].keys() | snapshot["archived_animals"].keys()
        rows, revision = backend.records.get_with_revision("samples", record_id, default=[])
        if not isinstance(rows, list):
            raise ValidationError("The sample register is invalid.")
        return [row for row in rows if isinstance(row, Mapping) and row.get("animal_name") in keys], revision


def save_shared_samples(backend, session, record_id, original, rows, expected_revision, leases):
    record_id = _record(record_id)
    changed = changed_sample_animals(original, rows)
    if changed != leases.keys():
        raise PermissionDeniedError("Sample edits require a lease on each affected animal.")
    with backend.protected_request(
        session, "sample_track.use", animal_leases=leases, write=True,
    ) as (connection, account, _):
        stored, revision = backend.records.get_with_revision("samples", record_id, default=[])
        if expected_revision != revision:
            raise ConflictError("The sample register changed. Reload and try again.")
        if not isinstance(stored, list) or any(not isinstance(row, Mapping) for row in stored):
            raise ValidationError("The sample register is invalid.")
        current_by_ipid = _group_rows(stored)
        previous_by_ipid = _group_rows(original)
        if any(current_by_ipid.get(ipid) != previous_by_ipid.get(ipid) for ipid in changed):
            raise ConflictError("The affected samples changed. Reload and try again.")
        replacement = [row for row in stored if row.get("animal_name") not in changed] + [
            row for row in rows if row.get("animal_name") in changed
        ]
        if changed:
            backend.records.put("samples", record_id, replacement, expected_revision=revision)
            for ipid in sorted(changed):
                backend.audit._append_in_transaction(
                    connection, actor_login=account["username"], category="samples", action="update",
                    entity_type="animal", entity_id=ipid, payload={"register": record_id},
                )


def sample_species_abbreviation(species):
    words = str(species or "").strip().split()
    if len(words) >= 2:
        return "".join(word[0].lower() for word in words if word[0].isalnum())
    return words[0][:2].lower() if words else ""


def _sample_dates(record, field):
    dates = set()
    for entry in record.get(field, ()) or ():
        raw = entry.get("datum")
        if isinstance(raw, date):
            dates.add(raw.strftime("%d.%m.%Y"))
        elif raw:
            value = str(raw).strip().replace(". ", ".")
            try:
                parsed = date.fromisoformat(value[:10]) if "-" in value[:10] else datetime.strptime(value, "%d.%m.%Y")
            except ValueError as error:
                raise ValidationError("A sample measurement has an invalid collection date.") from error
            dates.add(parsed.strftime("%d.%m.%Y"))
    return dates


def apply_core_samples(backend, connection, account, changes):
    """Mirror newly collected hormone samples in the Core save transaction."""
    pending = []
    for ipid, change in sorted(changes.items()):
        for field, sample_type in (("daten", "plasma"), ("pdg", "urin")):
            before = change.original or {}
            if (before.get(field) or []) == (change.record.get(field) or []):
                continue
            old = _sample_dates(before, field)
            dates = _sample_dates(change.record, field) - old
            pending.extend((ipid, change.record, sample_type, collected) for collected in sorted(dates))
    if not pending:
        return
    rows, revision = backend.records.get_with_revision("samples", "other", default=[])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValidationError("The sample register is invalid.")
    for ipid, record, sample_type, collected in pending:
        row = next((item for item in rows if item.get("animal_name") == ipid and item.get("collection_date") == collected), None)
        if row is None:
            row = {
                "sample_number": "", "animal_name": ipid, "species": sample_species_abbreviation(record.get("species")),
                "id": record.get("id", ""), "sex": "Female", "collection_date": collected, "saved": True,
            }
            rows.append(row)
        marker = f"{sample_type}_checked"
        checked = str(row.get(marker) or "").strip().lower() in {"true", "1", "yes", "ja"}
        if not checked:
            row[marker] = True
            locations = f"{sample_type}_aliquot_locations"
            if not row.get(locations):
                row[f"{sample_type}_num_aliquots"] = 1
                row[locations] = dumps([{"unit": "", "storage": ""}])
        backend.audit._append_in_transaction(
            connection, actor_login=account["username"], category="samples", action="from_measurement",
            entity_type="animal", entity_id=ipid, payload={"sample_type": sample_type, "collection_date": collected},
        )
    backend.records.put("samples", "other", rows, expected_revision=revision)
