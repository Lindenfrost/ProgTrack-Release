# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Shared surgery schedules store visible edits without overwriting other animals."""

from .errors import ConflictError, PermissionDeniedError, SessionRevokedError, ValidationError


_SCHEDULES = {"schedule", "draft-schedule"}
_RECORDS = _SCHEDULES | {"settings", "block-days"}


class SharedSurgeryStore:
    def __init__(self, backend, session, hold_animal, release_animal):
        self.backend = backend
        self.session = session
        self.hold_animal = hold_animal
        self.release_animal = release_animal
        self._read_revisions = {}
        self._read_entries = {}

    def load(self, record_id, default):
        self._check_id(record_id)
        with self.backend.protected_request(self.session(), "op_scheduler.view"):
            value, revision = self.backend.records.get_with_revision("surgery-planner", record_id, default=default)
            self._read_revisions[record_id] = revision
            if record_id in _SCHEDULES and value is not None:
                keys = self._visible_animals()
                entries = [row for row in self._entries(value) if self._ipid(row) in keys]
                self._read_entries[record_id] = entries
                return {"schedule": entries}
            return value

    def save(self, record_id, value):
        self._check_id(record_id)
        session = self.session()
        expected = self._read_revisions.get(record_id)
        if expected is None:
            with self.backend.protected_request(session, "op_scheduler.view"):
                current = self.backend.records.get_with_revision("surgery-planner", record_id, default=None)[1]
            if current:
                raise ConflictError("Reload the saved surgery plan before editing it.")
            expected = 0
        owner = object()
        animal_leases = {}
        lease = None
        try:
            incoming = self._entries(value) if record_id in _SCHEDULES else []
            before = self._group(self._read_entries.get(record_id, []))
            after = self._group(incoming)
            for ipid in sorted(before.keys() | after.keys()):
                if before.get(ipid) != after.get(ipid):
                    animal_leases[ipid] = self.hold_animal(ipid, owner)
            lease = self.backend.leases.acquire("surgery", record_id, session=session)["token"]
            with self.backend.protected_request(
                session, "op_scheduler.use", animal_leases=animal_leases,
                entity_leases={("surgery", record_id): lease}, write=True,
            ) as (connection, account, _):
                stored, revision = self.backend.records.get_with_revision("surgery-planner", record_id, default={})
                if expected != revision:
                    raise ConflictError("The surgery plan changed. Reload and try again.")
                if record_id in _SCHEDULES:
                    visible = self._visible_animals()
                    if any(self._ipid(row) not in visible for row in incoming):
                        raise PermissionDeniedError("A surgery plan animal is outside your current scope.")
                    persisted = self._entries(stored)
                    current = self._group(persisted)
                    if any(current.get(ipid) != before.get(ipid) for ipid in animal_leases):
                        raise ConflictError("An affected surgery plan changed. Reload and try again.")
                    value = {"schedule": [row for row in persisted if self._ipid(row) not in animal_leases] + [
                        row for row in incoming if self._ipid(row) in animal_leases
                    ]}
                next_revision = self.backend.records.put("surgery-planner", record_id, value, expected_revision=revision)
                self.backend.audit._append_in_transaction(
                    connection, actor_login=account["username"], category="surgery-planner", action="update",
                    entity_type="surgery", entity_id=record_id, payload={},
                )
            self._read_revisions[record_id] = next_revision
            if record_id in _SCHEDULES:
                self._read_entries[record_id] = incoming
        finally:
            for ipid in animal_leases:
                self.release_animal(ipid, owner)
            if lease is not None:
                try:
                    self.backend.leases.release("surgery", record_id, lease, session=session)
                except SessionRevokedError:
                    pass

    def _visible_animals(self):
        snapshot = self.backend.load_core_data(session=self.session())
        return snapshot["animals"].keys() | snapshot["archived_animals"].keys()

    @staticmethod
    def _check_id(record_id):
        if record_id not in _RECORDS:
            raise ValidationError("Unknown surgery plan record.")

    @staticmethod
    def _entries(value):
        entries = value.get("schedule", []) if isinstance(value, dict) else value
        if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
            raise ValidationError("The surgery plan is invalid.")
        return entries

    @staticmethod
    def _ipid(row):
        return str(row.get("ipid") or row.get("animal") or "")

    def _group(self, entries):
        grouped = {}
        for row in entries:
            grouped.setdefault(self._ipid(row), []).append(row)
        return grouped
