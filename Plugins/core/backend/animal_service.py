"""Animal and measurement services shared by both backend profiles."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from typing import Any, Mapping

from ..animal_identity import (
    animal_base_name,
    normalize_birth_date,
    split_animal_identity_key,
)
from .errors import ConflictError, ImmutableIdentityError, ValidationError
from .json_codec import dumps, loads
from .repositories import (
    _execute,
    _fetchall,
    _fetchone,
    _json_placeholder,
    _placeholder,
    deterministic_record_id,
    now_text,
)


IDENTITY_FIELDS = ("ipid", "name", "species", "birth_date", "origin")

MEASUREMENT_FIELDS = {
    "blood": "daten",
    "weight": "gewicht",
    "urine": "pdg",
    "sperm": "sperm",
}

PARENTAGE_REVISION_FIELDS = (
    # Keep this list identical to the UI-side parentage snapshot digest.  The
    # token protects not only the four parent links but also every identity,
    # lifecycle, project and organizational-visibility input used to build
    # the candidate catalogue.  A narrower backend token would reject every
    # guarded UI save (or miss a concurrent visibility change).
    "name",
    "_base_name",
    "id",
    "ipid",
    "display_name",
    "eizellspenderin",
    "samenspender",
    "ziehmutter",
    "ziehvater",
    "species",
    "sex",
    "birth_date",
    "death_date",
    "sterbedatum",
    "archived",
    "project",
    "project_id",
    "organization_unit_id",
    "organizational_unit_id",
    "workgroup_id",
)


def _identity(ipid: str, record: Mapping[str, Any]) -> dict[str, str]:
    parts = split_animal_identity_key(ipid)
    name = str(
        record.get("name")
        or record.get("_base_name")
        or record.get("display_name")
        or animal_base_name(ipid, dict(record))
    ).strip()
    species = str(record.get("species") or (parts[1] if parts else "")).strip()
    birth_date = normalize_birth_date(
        record.get("birth_date") or (parts[2] if parts else ""),
        required=True,
    )
    origin = str(record.get("origin") or (parts[3] if parts else "")).strip()
    if not name or not species or not origin:
        raise ValidationError(
            f"Complete immutable identity is required for {ipid!r}: "
            "name, species, full birth date, and origin."
        )
    return {
        "ipid": ipid,
        "name": name,
        "species": species,
        "birth_date": birth_date,
        "origin": origin,
    }


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value or "").strip()


class AnimalService:
    def __init__(self, adapter: Any):
        self.adapter = adapter

    def load_snapshot(self) -> dict[str, Any]:
        with self.adapter.transaction() as connection:
            rows = _fetchall(
                connection,
                "SELECT ipid,archived,record_json FROM animals ORDER BY ipid",
            )
            settings_row = _fetchone(
                connection,
                "SELECT value_json FROM installation WHERE key="
                + _placeholder(self.adapter),
                ("core.settings",),
            )
            measurement_rows = _fetchall(
                connection,
                "SELECT animal_ipid,kind,value_json FROM measurements "
                "ORDER BY animal_ipid,measured_at,measurement_id",
            )
            event_rows = _fetchall(
                connection,
                "SELECT animal_ipid,payload_json FROM animal_events "
                "ORDER BY animal_ipid,occurred_at,event_id",
            )
        animals: dict[str, Any] = {}
        archived: dict[str, Any] = {}
        for row in rows:
            data = self.adapter.row_to_dict(row)
            payload = data["record_json"]
            record = payload if isinstance(payload, dict) else loads(payload, {})
            for field in MEASUREMENT_FIELDS.values():
                record.setdefault(field, [])
            record.setdefault("events", [])
            (archived if bool(data["archived"]) else animals)[data["ipid"]] = record
        all_records = {**animals, **archived}
        for row in measurement_rows:
            data = self.adapter.row_to_dict(row)
            record = all_records.get(str(data["animal_ipid"]))
            field = MEASUREMENT_FIELDS.get(str(data["kind"]))
            if record is not None and field:
                payload = data["value_json"]
                record[field].append(
                    payload if isinstance(payload, dict) else loads(payload, {})
                )
        for row in event_rows:
            data = self.adapter.row_to_dict(row)
            record = all_records.get(str(data["animal_ipid"]))
            if record is not None:
                payload = data["payload_json"]
                record["events"].append(
                    payload if isinstance(payload, dict) else loads(payload, {})
                )
        settings: dict[str, Any] = {}
        if settings_row is not None:
            raw = (
                settings_row["value_json"]
                if isinstance(settings_row, dict)
                else settings_row[0]
            )
            settings = raw if isinstance(raw, dict) else loads(raw, {})
        return {
            "version": "5.0",
            "animals": animals,
            "archived_animals": archived,
            "settings": settings,
        }

    @staticmethod
    def parentage_revision_for_snapshot(snapshot: Mapping[str, Any]) -> str:
        """Return the Core parentage token shared with the UI validator.

        Only fields that affect parent candidate validation and cycle checks
        are included.  Keeping this canonical helper in the backend lets the
        commit transaction compare the exact live snapshot instead of relying
        on a session-local pre-check.
        """
        import hashlib

        records: dict[str, Any] = {}
        for section in ("animals", "archived_animals", "archived"):
            values = snapshot.get(section, {}) if isinstance(snapshot, Mapping) else {}
            if not isinstance(values, Mapping):
                continue
            for key, record in values.items():
                if isinstance(record, Mapping):
                    records[str(key)] = {
                        field: record.get(field, "")
                        for field in PARENTAGE_REVISION_FIELDS
                    }
        payload = json.dumps(records, sort_keys=True, default=str, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _parentage_revision_in_connection(self, connection: Any) -> str:
        rows = _fetchall(
            connection,
            "SELECT ipid,record_json FROM animals ORDER BY ipid",
        )
        records: dict[str, Any] = {}
        for row in rows:
            data = self.adapter.row_to_dict(row)
            raw = data.get("record_json")
            record = raw if isinstance(raw, Mapping) else loads(raw or "{}", {})
            if isinstance(record, Mapping):
                records[str(data.get("ipid", ""))] = {
                    field: record.get(field, "")
                    for field in PARENTAGE_REVISION_FIELDS
                }
        return self.parentage_revision_for_snapshot({"animals": records})

    def replace_snapshot(
        self,
        snapshot: Mapping[str, Any],
        *,
        expected_parentage_revision: str | None = None,
    ) -> None:
        active = snapshot.get("animals", {})
        archived = snapshot.get("archived_animals", snapshot.get("archived", {}))
        if not isinstance(active, Mapping) or not isinstance(archived, Mapping):
            raise ValidationError("Animal snapshot sections must be mappings.")
        combined: dict[str, tuple[Mapping[str, Any], bool]] = {}
        for ipid, record in active.items():
            combined[str(ipid)] = (record, False)
        for ipid, record in archived.items():
            if str(ipid) in combined:
                raise ValidationError(f"Animal exists in active and archived: {ipid}")
            combined[str(ipid)] = (record, True)

        mark = _placeholder(self.adapter)
        json_mark = _json_placeholder(self.adapter)
        timestamp = now_text()
        with self.adapter.transaction(write=True) as connection:
            if expected_parentage_revision:
                current_parentage_revision = self._parentage_revision_in_connection(connection)
                if str(expected_parentage_revision).strip() != current_parentage_revision:
                    raise ConflictError(
                        "Core parentage changed while the animal dialog was open. "
                        "Reload the animal and try again."
                    )
            existing_rows = _fetchall(
                connection,
                "SELECT ipid,name,species,birth_date,origin,revision FROM animals",
            )
            existing = {
                self.adapter.row_to_dict(row)["ipid"]: self.adapter.row_to_dict(row)
                for row in existing_rows
            }
            incoming_ids = set(combined)
            for ipid in set(existing) - incoming_ids:
                _execute(
                    connection,
                    f"DELETE FROM animals WHERE ipid={mark}",
                    (ipid,),
                )
            for ipid, (source_record, archived_flag) in combined.items():
                record = copy.deepcopy(dict(source_record))
                identity = _identity(ipid, record)
                record.update(identity)
                measurements = {
                    kind: list(record.pop(field, []) or [])
                    for kind, field in MEASUREMENT_FIELDS.items()
                }
                events = list(record.pop("events", []) or [])
                role_id = str(record.get("rolle") or record.get("role_id") or "unknown")
                previous = existing.get(ipid)
                if previous:
                    for field in IDENTITY_FIELDS:
                        previous_value = str(previous[field])
                        new_value = str(identity[field])
                        if previous_value != new_value:
                            raise ImmutableIdentityError(
                                f"{field} cannot change for established animal {ipid}."
                            )
                    _execute(
                        connection,
                        f"UPDATE animals SET archived={mark},role_id={mark},record_json={json_mark},"
                        f"revision=revision+1,updated_at={mark} WHERE ipid={mark}",
                        (
                            int(archived_flag) if self.adapter.dialect == "sqlite" else archived_flag,
                            role_id,
                            dumps(record),
                            timestamp,
                            ipid,
                        ),
                    )
                else:
                    _execute(
                        connection,
                        "INSERT INTO animals("
                        "ipid,name,species,birth_date,origin,archived,role_id,"
                        "record_json,revision,created_at,updated_at) VALUES("
                        + ",".join([mark] * 7 + [json_mark] + [mark] * 3)
                        + ")",
                        (
                            identity["ipid"],
                            identity["name"],
                            identity["species"],
                            identity["birth_date"],
                            identity["origin"],
                            int(archived_flag) if self.adapter.dialect == "sqlite" else archived_flag,
                            role_id,
                            dumps(record),
                            1,
                            timestamp,
                            timestamp,
                        ),
                    )
                _execute(
                    connection,
                    f"DELETE FROM measurements WHERE animal_ipid={mark}",
                    (ipid,),
                )
                for kind, entries in measurements.items():
                    for index, entry in enumerate(entries):
                        if not isinstance(entry, Mapping):
                            continue
                        measured_at = _date_text(
                            entry.get("datum") or entry.get("date")
                        )
                        if not measured_at:
                            continue
                        sample_id = str(
                            entry.get("probennummer")
                            or entry.get("sample_id")
                            or ""
                        )
                        measurement_id = deterministic_record_id(
                            "measurement", ipid, kind, measured_at, sample_id,
                            dumps(entry), index,
                        )
                        _execute(
                            connection,
                            "INSERT INTO measurements("
                            "measurement_id,animal_ipid,kind,measured_at,value_json,"
                            "sample_id,source_id,created_at) VALUES("
                            + ",".join([mark] * 4 + [json_mark] + [mark] * 3)
                            + ")",
                            (
                                measurement_id,
                                ipid,
                                kind,
                                measured_at,
                                dumps(dict(entry)),
                                sample_id,
                                "core",
                                timestamp,
                            ),
                        )
                _execute(
                    connection,
                    f"DELETE FROM animal_events WHERE animal_ipid={mark}",
                    (ipid,),
                )
                for index, event in enumerate(events):
                    if not isinstance(event, Mapping):
                        continue
                    occurred_at = _date_text(
                        event.get("datum") or event.get("date")
                    )
                    if not occurred_at:
                        continue
                    event_type = str(
                        event.get("typ") or event.get("type") or "event"
                    )
                    event_id = deterministic_record_id(
                        "event", ipid, event_type, occurred_at, dumps(event), index
                    )
                    _execute(
                        connection,
                        "INSERT INTO animal_events("
                        "event_id,animal_ipid,event_type,occurred_at,payload_json,"
                        "created_at) VALUES("
                        + ",".join([mark] * 4 + [json_mark, mark])
                        + ")",
                        (
                            event_id,
                            ipid,
                            event_type,
                            occurred_at,
                            dumps(dict(event)),
                            timestamp,
                        ),
                    )
            settings = dumps(snapshot.get("settings", {}))
            if self.adapter.dialect == "sqlite":
                _execute(
                    connection,
                    """
                    INSERT INTO installation(key,value_json,revision,updated_at)
                    VALUES(?,?,1,?)
                    ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,
                    revision=installation.revision+1,updated_at=excluded.updated_at
                    """,
                    ("core.settings", settings, timestamp),
                )
            else:
                _execute(
                    connection,
                    """
                    INSERT INTO installation(key,value_json,revision,updated_at)
                    VALUES(%s,%s::jsonb,1,%s)
                    ON CONFLICT(key) DO UPDATE SET value_json=EXCLUDED.value_json,
                    revision=installation.revision+1,updated_at=EXCLUDED.updated_at
                    """,
                    ("core.settings", settings, timestamp),
                )
