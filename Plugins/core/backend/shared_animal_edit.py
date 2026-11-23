# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Permission and concurrency checks for scoped Core animal mutations."""

from dataclasses import dataclass
from typing import Any

from ..authorization import organizational_owner_id
from .errors import ConflictError, PermissionDeniedError, ValidationError
from .json_codec import dumps

_CORE = "core.edit_animal_core"
_IDENTITY = "core.edit_animal_identity"
_IMMUTABLE = "core.edit_animal_immutable"
_HOUSING = "core.edit_animal_housing"
_MEASUREMENTS = "core.edit_animal_measurements"
_RESEARCH = "core.edit_animal_research_data"
_ROLE = "core.edit_animal_role"
_ASSIGN = "project.project_assign"
_CREATE = "core.create_animals"
_ARCHIVE = "core.archive_animals"
_FIELD_PERMISSIONS = {
    field: permission
    for permission, fields in {
        _CORE: (
            "genotype", "special_status", "sick", "sick_times", "sick_start_date",
            "sick_end_date", "abnormal_current", "abnormal_ever", "abnormal_start_date",
            "abnormal_end_date", "death_date", "sterbedatum", "death_cause", "lifecycle_events",
        ),
        _IDENTITY: ("id", "identity", "ipid", "name", "_base_name", "display_name", "species", "birth_date"),
        _IMMUTABLE: ("chip_nr", "origin"),
        _HOUSING: (
            "eizellspenderin", "samenspender", "ziehmutter", "ziehvater", "egg_donor",
            "sperm_donor", "surrogate_mother", "surrogate_father", "mother", "father",
            "parentage_mode", "sex", "cage", "cage_id", "housing_address", "cage_address",
            "building_id", "room_id", "unit_id", "partner_von", "verpaart_mit", "reproduktionsfeld",
        ),
        _MEASUREMENTS: ("gewicht", "events", "ref_weight"),
        _RESEARCH: (
            "pdg", "daten", "sperm", "recovery_time", "experimental_limits", "max_embryo",
            "max_fsh", "max_geburten", "max_measurements", "max_messungen", "max_op",
            "max_pgf", "max_pregnancies", "max_spermaproben",
        ),
        _ROLE: ("rolle", "role_id"),
        _ASSIGN: ("project", "project_id", "project_entry_date", "project_period_id", "project_history", "project_periods",
                  "project_severity", "severity"),
    }.items()
    for field in fields
}


@dataclass(frozen=True)
class AnimalChange:
    original_section: str | None
    original: dict[str, Any] | None
    section: str
    record: dict[str, Any]


def _changed_field_permissions(original, replacement, custom_limits=()):
    permissions = set()
    for key in original.keys() | replacement.keys():
        if dumps(original.get(key)) == dumps(replacement.get(key)):
            continue
        if key == "edits":
            permissions.update(_transition_note_permissions(original, replacement))
        elif key == "in_experiment":
            permissions.add("project.set_in_experiment" if replacement.get(key) else "project.unset_in_experiment")
        elif key in {"experiment_exit_date", "experiment_exit_reason"}:
            if not original.get("in_experiment") or replacement.get("in_experiment"):
                raise PermissionDeniedError("Exit details require a current experiment exit.")
            permissions.add("project.unset_in_experiment")
        elif key in _FIELD_PERMISSIONS:
            permissions.add(_FIELD_PERMISSIONS[key])
        elif key in custom_limits:
            permissions.add(_RESEARCH)
        else:
            raise PermissionDeniedError(f"The Core command does not allow modification of {key}.")
    return permissions


def _transition_note_permissions(original, replacement):
    transitions = {}
    if original.get("project") != replacement.get("project"):
        transitions["project_note"] = _ASSIGN
    if original.get("severity") != replacement.get("severity"):
        transitions["severity_note"] = _ASSIGN
    if bool(original.get("in_experiment")) != bool(replacement.get("in_experiment")):
        transitions["experiment_note"] = "project.set_in_experiment" if replacement.get("in_experiment") else "project.unset_in_experiment"
    if original.get("death_date") != replacement.get("death_date") and replacement.get("death_date"):
        transitions["death_note"] = _CORE
    old, new = original.get("edits") or {}, replacement.get("edits") or {}
    if not isinstance(old, dict) or not isinstance(new, dict):
        raise ValidationError("The animal's transition notes are invalid.")
    required = set()
    for day in old.keys() | new.keys():
        previous, updated = old.get(day, {}), new.get(day, {})
        if not isinstance(previous, dict) or not isinstance(updated, dict):
            raise ValidationError("The animal's transition notes are invalid.")
        for field in previous.keys() | updated.keys():
            if previous.get(field) != updated.get(field):
                if field not in transitions or not isinstance(updated.get(field), str):
                    raise PermissionDeniedError("Only current animal transitions may add report annotations.")
                required.add(transitions[field])
    return required


def _clinical_context(account, allowed):
    return {
        "actor_login": account["username"],
        "actor_role": account["role"],
        "signature": account.get("display_name") or account["username"],
        "shared_lifecycle_transitions": True,
        "permissions": {
            permission: "*" in allowed or permission in allowed
            for permission in (_CREATE, _CORE, _MEASUREMENTS, _ARCHIVE, "core.delete_animals")
        },
    }


def save_shared_animal_changes(backend, session, changes, leases, *, expected_parentage_revision, addresses=None, derive_samples=False):
    """Lock only changed animals and apply their changes from a fresh snapshot."""
    expected_lease_keys = {ipid for ipid, change in changes.items() if change.original is not None}
    if expected_lease_keys != leases.keys():
        raise PermissionDeniedError("The change requires a lease for each existing animal.")
    with backend.protected_request(session, animal_leases=leases, write=True) as (connection, account, allowed):
        from .project_identity import read_catalog

        read_catalog(backend.adapter, connection)
        current = backend.animals.load_snapshot()
        pending_creates = {ipid: change.record for ipid, change in changes.items() if change.original is None}
        for ipid, change in sorted(changes.items()):
            _apply_animal_change(backend, connection, account, allowed, current, ipid, change, pending_creates)
        receipt = backend.animals.replace_snapshot(
            current,
            expected_parentage_revision=expected_parentage_revision,
            affected_ipids=set(changes),
            include_settings=False,
            clinical_context=_clinical_context(account, allowed),
        )
        from .shared_projects import project_history_changes
        from .shared_housing import apply_core_addresses

        apply_core_addresses(backend, connection, account, allowed, changes, addresses)
        if derive_samples:
            from .shared_samples import apply_core_samples

            apply_core_samples(backend, connection, account, changes)

        project_history_changes(
            backend, connection, {ipid: (change.original, change.record) for ipid, change in changes.items()}
        )
        for ipid, change in sorted(changes.items()):
            if change.original is None:
                action = "create"
            elif change.original_section == change.section:
                action = "edit"
            else:
                action = "archive" if change.section == "archived_animals" else "restore"
            if any(ipid in records for records in receipt.audit_after.values()):
                backend.audit._append_in_transaction(
                    connection, actor_login=account["username"], category="core", action=action,
                    entity_type="animal", entity_id=ipid, payload={},
                )
        return receipt


def _apply_animal_change(backend, connection, account, allowed, current, ipid, change, pending_creates):
    sections = ("animals", "archived_animals")
    if change.section not in sections or change.original_section not in (*sections, None):
        raise ValidationError("Invalid animal storage scope.")
    if change.record.get("ipid", ipid) != ipid:
        raise ValidationError("The supplied animal does not match its identity.")
    existing_section = next((section for section in sections if ipid in current[section]), None)
    original = current[existing_section].get(ipid) if existing_section else None
    if existing_section != change.original_section or dumps(original) != dumps(change.original):
        raise ConflictError("This animal changed while editing. Reload before saving.")
    owner = organizational_owner_id(change.record).casefold()
    old_owner = organizational_owner_id(original or {}).casefold()
    if owner and owner != backend.authenticated_sessions.active_unit(connection, account):
        raise PermissionDeniedError("The animal is outside the current organizational Unit.")
    if original is not None and owner != old_owner:
        raise PermissionDeniedError("An animal's organizational Unit cannot change in the Core editor.")
    if original is None:
        if change.section != "animals":
            raise ValidationError("An animal must be created in the active register.")
        required = {_CREATE}
        required.update(_transition_note_permissions({}, change.record))
        if change.record.get("project"):
            required.add(_ASSIGN)
        if change.record.get("in_experiment"):
            required.add("project.set_in_experiment")
    elif existing_section != change.section:
        changed = {
            key for key in original.keys() | change.record.keys()
            if dumps(original.get(key)) != dumps(change.record.get(key))
        }
        if changed - {"departure_reason", "handover_recipient", "departure_date", "lifecycle_events",
                      "project_history", "project_periods", "project", "project_id", "project_entry_date",
                      "project_severity", "severity", "in_experiment"}:
            raise PermissionDeniedError("An archive or restore may only change lifecycle fields.")
        required = {_ARCHIVE}
    else:
        presets = backend.records.get("configuration", "role-block-presets", default={})
        limits = presets.get("experimental_blocks", ()) if isinstance(presets, dict) else ()
        custom_limits = {
            item["max_field"] for item in limits
            if isinstance(item, dict) and item.get("kind") == "limiting" and item.get("max_field")
        }
        required = {_CORE} | _changed_field_permissions(original, change.record, custom_limits)
    if "*" not in allowed and not required <= allowed:
        raise PermissionDeniedError("This account may not save one or more modified animal fields.")
    if change.record.get("project") != (original or {}).get("project"):
        unrestricted, projects = backend.authenticated_sessions.animal_scope(connection, account, allowed)
        target = change.record.get("project")
        if target and not unrestricted and target not in projects:
            raise PermissionDeniedError("This account cannot assign the animal to the requested project.")
        if target:
            from ..project_species import assignment_allowed

            project_data = backend.records.get("projects", "catalog", {})
            catalogue = project_data.get("projects", {})
            if target not in catalogue or not assignment_allowed(catalogue, target, change.record.get("species", "")):
                raise ValidationError("The animal does not match the project's species scope.")
    for field in (
        "eizellspenderin", "samenspender", "ziehmutter", "ziehvater", "egg_donor",
        "sperm_donor", "surrogate_mother", "surrogate_father", "mother", "father",
        "partner_von", "verpaart_mit",
    ):
        target = change.record.get(field)
        if target and target != (original or {}).get(field):
            if target == ipid:
                raise ValidationError("An animal cannot reference itself.")
            if target in pending_creates:
                from ..project_visibility import animal_visible_by_project_scope

                scope = backend.authenticated_sessions.animal_scope(connection, account, allowed)
                if not animal_visible_by_project_scope(pending_creates[target], *scope):
                    raise PermissionDeniedError("The new related animal is outside the current project scope.")
            else:
                backend.authenticated_sessions.require_visible_animal(connection, account, allowed, target)
    if existing_section:
        del current[existing_section][ipid]
    current[change.section][ipid] = change.record
