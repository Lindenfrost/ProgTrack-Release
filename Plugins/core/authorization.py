"""Fail-closed authorization and canonical organizational-unit services.

This module is deliberately independent from Qt and plugin UI code.  It is the
single policy boundary for protected writes: a missing, disabled, or unavailable
Master Track never grants write access.  Housing units remain unrelated data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

UNIT_NAMESPACE = "security"
UNIT_RECORD_ID = "organization-units"
_UNIT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")

PROTECTED_WRITE_ACTIONS = frozenset({
    "core.create_animals", "core.edit_animal_core",
    "core.edit_animal_immutable", "core.archive_animals",
    "core.delete_animals", "core.import", "reports.write",
    "project.create", "project.edit", "project.delete",
    "project.manage", "cage.edit", "cage.assign_locations",
    "sample.create", "sample.edit", "sample.delete",
    "medi_track.add_docs", "medi_track.delete_document",
    "flow_track.edit", "flow_track.create", "flow_track.delete",
    "heritage.edit", "reports.export",
})

def normalize_unit_id(value: Any) -> str:
    """Return a canonical organizational unit ID or raise ValueError.

    Organizational IDs are intentionally distinct from housing IDs and are not
    inferred from display names at query time.
    """
    value = str(value or "").strip().casefold()
    if not value or not _UNIT_RE.fullmatch(value):
        raise ValueError("Invalid organizational unit ID.")
    return value

@dataclass(frozen=True)
class OrganizationUnit:
    unit_id: str
    display_name: str
    active: bool = True
    archived: bool = False
    revision: int = 1
    facility_ref: str = ""

    @classmethod
    def from_record(cls, value: Mapping[str, Any]) -> "OrganizationUnit":
        unit_id = normalize_unit_id(value.get("unit_id"))
        return cls(
            unit_id=unit_id,
            display_name=str(value.get("display_name") or unit_id),
            active=bool(value.get("active", True)),
            archived=bool(value.get("archived", False)),
            revision=max(1, int(value.get("revision", 1))),
            facility_ref=str(value.get("facility_ref") or ""),
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "display_name": self.display_name,
            "active": self.active,
            "archived": self.archived,
            "revision": self.revision,
            "facility_ref": self.facility_ref,
        }

class CanonicalUnitService:
    """Backend-owned organization/workgroup catalog.

    No housing structures are consulted.  Callers must pass unit_id explicitly
    when they need scope enforcement.
    """
    namespace = UNIT_NAMESPACE
    record_id = UNIT_RECORD_ID

    def __init__(self, backend: Any):
        self.backend = backend

    def load(self) -> dict[str, OrganizationUnit]:
        raw = self.backend.records.get(self.namespace, self.record_id, default={})
        if not isinstance(raw, Mapping):
            return {}
        result: dict[str, OrganizationUnit] = {}
        for item in raw.get("units", []) if isinstance(raw.get("units"), list) else []:
            if not isinstance(item, Mapping):
                continue
            try:
                unit = OrganizationUnit.from_record(item)
            except (TypeError, ValueError):
                continue
            result[unit.unit_id] = unit
        return result

    def save(self, units: Iterable[OrganizationUnit], *, expected_revision: int | None = None) -> int:
        normalized = list(units)
        ids = [normalize_unit_id(unit.unit_id) for unit in normalized]
        if len(ids) != len(set(ids)):
            raise ValueError("Organizational unit IDs must be unique.")
        payload = {
            "schema_version": 1,
            "units": [unit.as_record() for unit in sorted(normalized, key=lambda x: x.unit_id)],
        }
        return self.backend.records.put(
            self.namespace, self.record_id, payload, expected_revision=expected_revision
        )

    def ensure_seed(self, units: Iterable[OrganizationUnit]) -> dict[str, OrganizationUnit]:
        current = self.load()
        if not current:
            desired = {unit.unit_id: unit for unit in units}
            self.save(desired.values())
            return desired
        return current

    def get(self, unit_id: Any) -> OrganizationUnit | None:
        try:
            key = normalize_unit_id(unit_id)
        except ValueError:
            return None
        return self.load().get(key)

    def validate(self, unit_id: Any, *, allow_archived: bool = False) -> str:
        key = normalize_unit_id(unit_id)
        unit = self.get(key)
        if unit is None:
            raise ValueError("Unknown organizational unit.")
        if (not unit.active or unit.archived) and not allow_archived:
            raise ValueError("Organizational unit is inactive or archived.")
        return key

class AuthorizationService:
    """Single fail-closed policy boundary for protected operations."""
    def __init__(self, master_track: Any = None, *, disabled: bool = False, backend: Any = None):
        self.master_track = master_track
        self.disabled = bool(disabled)
        self.units = CanonicalUnitService(backend) if backend is not None else None

    @staticmethod
    def is_write_action(action: str) -> bool:
        action = str(action or "").strip()
        if action in PROTECTED_WRITE_ACTIONS:
            return True
        return any(token in action for token in (
            ".create", ".edit", ".delete", ".import", ".write", ".manage",
            ".assign", ".archive",
        ))

    @property
    def available(self) -> bool:
        return self.master_track is not None and not self.disabled

    @property
    def current_unit_id(self) -> str:
        mt = self.master_track
        if mt is None:
            return ""
        getter = getattr(mt, "current_unit_id", None)
        if getter is not None:
            try:
                return normalize_unit_id(getter)
            except ValueError:
                return ""
        record_getter = getattr(mt, "_current_user_record", None)
        try:
            record = record_getter() if callable(record_getter) else None
        except Exception:
            record = None
        if isinstance(record, Mapping):
            try:
                return normalize_unit_id(record.get("unit_id"))
            except ValueError:
                return ""
        return ""

    def same_unit(self, owner_unit_id: Any) -> bool:
        try:
            current = self.current_unit_id
            owner = normalize_unit_id(owner_unit_id)
        except ValueError:
            return False
        if not current or current != owner or self.units is None:
            return False
        current_unit = self.units.get(current)
        owner_unit = self.units.get(owner)
        return bool(
            current_unit
            and owner_unit
            and current_unit.active
            and not current_unit.archived
            and owner_unit.active
            and not owner_unit.archived
        )

    def can(self, action: str, *, owner_unit_id: Any = None, write: bool | None = None) -> bool:
        action = str(action or "").strip()
        protected = self.is_write_action(action) if write is None else bool(write)
        if protected and not self.available:
            return False
        mt = self.master_track
        if mt is None or self.disabled:
            return not protected
        if not bool(getattr(mt, "is_logged_in", False)) and protected:
            return False
        try:
            allowed = bool(mt.can(action))
        except Exception:
            return False
        if not allowed:
            return False
        if owner_unit_id is not None and protected and not self.same_unit(owner_unit_id):
            # Lord/Master permission still does not silently bypass a cross-unit
            # scope check; callers may use an explicit cross-unit action.
            return False
        return True
