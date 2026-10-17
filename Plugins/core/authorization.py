"""Authorization, trusted-local mode, and canonical organizational units.

This module is deliberately independent from Qt and plugin UI code. When the
Master Track service is available it is the policy boundary for protected
operations. If Master Track is absent or explicitly disabled, the application
intentionally enters trusted-local mode: the installation is treated as a
single trusted operator, all actions are allowed, and no actor-based audit
event is generated. Housing units remain unrelated data.
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
    "core.manage_institution_settings",
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


def organizational_owner_id(record: Mapping[str, Any]) -> str:
    """Explicit Core workgroup owner; never infer one from housing unit_id."""
    for field in ("organization_unit_id", "organizational_unit_id", "workgroup_id"):
        value = str(record.get(field, "") or "").strip()
        if value:
            return value
    return ""

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

    def __init__(self, backend: Any, authorization: Any = None):
        self.backend = backend
        self.authorization = authorization

    def _require_manage_permission(self, authorized: bool = False) -> None:
        policy = self.authorization
        if policy is not None:
            if not bool(policy.can("core.manage_institution_settings", write=True)):
                raise PermissionError(
                    "Managing organizational units requires institution-settings permission."
                )
            return
        # Seed/bootstrap callers do not have a live policy object.  They must
        # opt in explicitly; an arbitrary direct caller cannot mutate the
        # backend by omitting the authorization assertion.
        if not authorized:
            raise PermissionError(
                "Managing organizational units requires institution-settings permission."
            )

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

    def load_with_revision(self) -> tuple[dict[str, OrganizationUnit], int]:
        """Load the catalog together with its backend revision.

        The revision belongs to the backend record, not to a client-side
        cache.  This keeps concurrent administrators from silently overwriting
        one another and gives callers a stable value for optimistic writes.
        """
        getter = getattr(self.backend.records, "get_with_revision", None)
        if callable(getter):
            raw, revision = getter(self.namespace, self.record_id, default={})
        else:
            raw, revision = (
                self.backend.records.get(self.namespace, self.record_id, default={}),
                0,
            )
        if not isinstance(raw, Mapping):
            return {}, int(revision or 0)
        result: dict[str, OrganizationUnit] = {}
        items = raw.get("units", [])
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping):
                continue
            try:
                unit = OrganizationUnit.from_record(item)
            except (TypeError, ValueError):
                continue
            result[unit.unit_id] = unit
        return result, int(revision or 0)

    def _actor(self, actor: str) -> str:
        policy = self.authorization
        if bool(getattr(policy, "trusted_local", False)):
            return ""
        mt = getattr(policy, "master_track", None)
        if mt is not None:
            login = str(getattr(mt, "current_username", "") or "")
            if not login:
                raise PermissionError("An authenticated Unit administrator is required.")
            return login
        return str(actor or "")  # Explicit bootstrap/test context only.

    def _write(
        self, units, previous, revision, *, actor, action, unit_id, payload,
    ) -> int:
        """CAS catalogue + changed assignments + audit in ONE transaction."""
        catalogue = self._payload(units.values())
        records = [(self.namespace, self.record_id, catalogue)]
        expected = {(self.namespace, self.record_id): revision}
        changed_ids = {
            key for key, old in previous.items()
            if key not in units or units[key].display_name != old.display_name
        }
        affected = []
        if changed_ids:
            users, users_revision = self.backend.records.get_with_revision(
                "security", "users", default=[]
            )
            if not isinstance(users, list) or any(not isinstance(u, dict) for u in users):
                raise ValueError("Invalid user catalogue; Unit changes were not saved.")
            cleaned = []
            for user in users:
                copied = dict(user)
                key = str(copied.get("unit_id") or "").strip().casefold()
                if key in changed_ids:
                    affected.append(str(copied.get("username") or ""))
                    target = units.get(key)
                    copied["unit_id"] = key if target else ""
                    copied["unit"] = target.display_name if target else ""
                cleaned.append(copied)
            records.append(("security", "users", cleaned))
            expected[("security", "users")] = users_revision
        event_payload = dict(payload)
        event_payload.update(
            catalogue_revision_before=revision, catalogue_revision=revision + 1,
            users_affected=affected, users_affected_count=len(affected),
        )
        login = self._actor(actor)
        if login:
            writer = getattr(self.backend.records, "put_many_audited", None)
            if not callable(writer):
                raise RuntimeError("The backend does not support atomic audited Unit writes.")
            result = writer(records, expected_revisions=expected, audit_events=[dict(
                actor_login=login, category="security", action=action,
                entity_type="organization_unit", entity_id=unit_id, payload=event_payload,
            )])
        elif len(records) > 1:
            writer = getattr(self.backend.records, "put_many", None)
            if not callable(writer):
                raise RuntimeError("The backend does not support atomic Unit assignment writes.")
            result = writer(records, expected_revisions=expected)
        else:
            return self.backend.records.put(
                self.namespace, self.record_id, catalogue, expected_revision=revision
            )
        return result[(self.namespace, self.record_id)]

    def _snapshot(self, expected_revision):
        units, revision = self.load_with_revision()
        if expected_revision is not None and revision != expected_revision:
            from .backend.errors import ConflictError
            raise ConflictError("The Unit catalogue changed. Reopen Institution before retrying.")
        return units, revision

    @staticmethod
    def _payload(units: Iterable[OrganizationUnit]) -> dict[str, Any]:
        normalized = list(units)
        ids = [normalize_unit_id(unit.unit_id) for unit in normalized]
        if len(ids) != len(set(ids)):
            raise ValueError("Organizational unit IDs must be unique.")
        if any(unit.unit_id != key or not unit.display_name.strip()
               for unit, key in zip(normalized, ids)):
            raise ValueError("Unit IDs must be canonical and names must not be empty.")
        return {
            "schema_version": 1,
            "units": [
                unit.as_record()
                for unit in sorted(normalized, key=lambda value: value.unit_id)
            ],
        }

    def save(self, units: Iterable[OrganizationUnit], *, expected_revision: int | None = None,
             actor: str = "", authorized: bool = False) -> int:
        self._require_manage_permission(authorized)
        desired = list(units)
        self._payload(desired)
        previous, revision = self._snapshot(expected_revision)
        return self._write(
            {u.unit_id: u for u in desired}, previous, revision, actor=actor,
            action="organization_units_replace", unit_id="catalogue",
            payload={"before": self._payload(previous.values()), "after": self._payload(desired)},
        )

    def create(
        self, unit_id: Any, display_name: Any, *, actor: str = "", authorized: bool = False,
        expected_revision: int | None = None,
    ) -> OrganizationUnit:
        self._require_manage_permission(authorized)
        units, revision = self._snapshot(expected_revision)
        previous = dict(units)
        key = normalize_unit_id(unit_id)
        if key in units:
            raise ValueError("An organizational unit with this ID already exists.")
        label = str(display_name or "").strip()
        if not label:
            raise ValueError("The organizational unit name cannot be empty.")
        unit = OrganizationUnit(unit_id=key, display_name=label)
        units[key] = unit
        self._write(units, previous, revision, actor=actor, action="organization_unit_create",
                    unit_id=key, payload=unit.as_record())
        return unit

    def update(
        self, unit_id: Any, display_name: Any, *, actor: str = "", authorized: bool = False,
        expected_revision: int | None = None,
    ) -> OrganizationUnit:
        self._require_manage_permission(authorized)
        units, revision = self._snapshot(expected_revision)
        previous = dict(units)
        key = normalize_unit_id(unit_id)
        current = units.get(key)
        if current is None:
            raise ValueError("Unknown organizational unit.")
        label = str(display_name or "").strip()
        if not label:
            raise ValueError("The organizational unit name cannot be empty.")
        unit = OrganizationUnit(
            unit_id=key,
            display_name=label,
            active=current.active,
            archived=current.archived,
            revision=current.revision + 1,
            facility_ref=current.facility_ref,
        )
        units[key] = unit
        self._write(units, previous, revision, actor=actor, action="organization_unit_update",
                    unit_id=key, payload={"before": current.as_record(), "after": unit.as_record()})
        return unit

    def set_archived(
        self, unit_id: Any, archived: bool, *, actor: str = "", authorized: bool = False,
        expected_revision: int | None = None,
    ) -> OrganizationUnit:
        self._require_manage_permission(authorized)
        units, revision = self._snapshot(expected_revision)
        previous = dict(units)
        key = normalize_unit_id(unit_id)
        current = units.get(key)
        if current is None:
            raise ValueError("Unknown organizational unit.")
        unit = OrganizationUnit(
            unit_id=key,
            display_name=current.display_name,
            active=not bool(archived),
            archived=bool(archived),
            revision=current.revision + 1,
            facility_ref=current.facility_ref,
        )
        units[key] = unit
        self._write(units, previous, revision, actor=actor, action=(
            "organization_unit_archive" if archived else "organization_unit_restore"
        ), unit_id=key, payload=unit.as_record())
        return unit

    def delete(self, unit_id: Any, *, actor: str = "", authorized: bool = False,
               expected_revision: int | None = None) -> bool:
        """Delete a unit and clear all user assignments in one backend write."""
        self._require_manage_permission(authorized)
        units, catalog_revision = self._snapshot(expected_revision)
        previous = dict(units)
        key = normalize_unit_id(unit_id)
        current = units.pop(key, None)
        if current is None:
            return False

        self._write(units, previous, catalog_revision, actor=actor,
                    action="organization_unit_delete", unit_id=key,
                    payload={"unit": current.as_record(), "users_unassigned": True})
        return True

    def ensure_seed(self, units: Iterable[OrganizationUnit], *, authorized: bool = False) -> dict[str, OrganizationUnit]:
        current = self.load()
        if not current:
            desired = {unit.unit_id: unit for unit in units}
            self.save(desired.values(), authorized=authorized, expected_revision=0)
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
    """Central policy boundary for managed and trusted-local operation.

    trusted_local is deliberately explicit so callers cannot accidentally
    infer a real Lord identity from a missing plugin. It is enabled by the
    application only when Master Track is absent or globally disabled. The
    service then grants every action but exposes no authenticated actor and
    must not be used to write user/audit attribution.
    """
    def __init__(
        self,
        master_track: Any = None,
        *,
        disabled: bool = False,
        backend: Any = None,
        trusted_local: bool = False,
    ):
        self.master_track = master_track
        self.disabled = bool(disabled)
        self.trusted_local = bool(trusted_local)
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
        return self.trusted_local or (
            self.master_track is not None and not self.disabled
        )

    @property
    def managed(self) -> bool:
        """Whether a real Master Track actor currently governs actions."""
        return bool(
            self.master_track is not None
            and not self.disabled
            and not self.trusted_local
        )

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

    def can_read_unit(self, owner_unit_id: Any = None, *, allow_unassigned: bool = False) -> bool:
        """Return whether a managed actor may read a unit-owned record.

        Trusted-local mode deliberately bypasses unit scope. In managed mode an
        explicit owner is required unless the caller opts into the documented
        global/unassigned bucket; housing IDs are never inferred.
        """
        if self.trusted_local:
            return True
        if owner_unit_id in (None, ""):
            return bool(allow_unassigned)
        return self.same_unit(owner_unit_id)

    def filter_records(
        self,
        records: Iterable[Mapping[str, Any]],
        *,
        owner_key: str = "unit_id",
        allow_unassigned: bool = False,
    ) -> list[Mapping[str, Any]]:
        """Filter backend records through the canonical Unit boundary."""
        result = []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            if self.can_read_unit(record.get(owner_key), allow_unassigned=allow_unassigned):
                result.append(record)
        return result

    def can(self, action: str, *, owner_unit_id: Any = None, write: bool | None = None) -> bool:
        action = str(action or "").strip()
        if self.trusted_local:
            # Trusted-local operation is intentionally not an authenticated
            # Lord session. Do not emit audit records or invent a user here.
            return True
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
