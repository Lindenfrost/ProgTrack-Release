# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Shared project catalog operations scoped to one stable project identity."""

from copy import deepcopy

from .errors import ConflictError, PermissionDeniedError, ValidationError
from .project_identity import read_catalog, server_timestamp, write_catalog
from .repositories import _fetchone
from ..project_lifecycle import set_project_status

_IDENTITY = ("project_id", "identity_creator", "identity_created_at", "identity_sequence")
_EDITABLE = {"status", "summary", "iacuc", "assoc_users", "animals_config", "arrive"}


def load_shared_project_catalog(backend, session):
    with backend.protected_request(session, "project.view") as (connection, account, permissions):
        catalog, revision = backend.records.get_with_revision(
            "projects", "catalog", {"version": 1, "projects": {}}
        )
        if not isinstance(catalog, dict) or not isinstance(catalog.get("projects"), dict):
            raise ValidationError("The project catalogue is invalid.")
        unrestricted, visible = backend.authenticated_sessions.animal_scope(connection, account, permissions)
        return {
            "version": catalog.get("version", 1),
            "projects": {name: record for name, record in catalog["projects"].items()
                         if unrestricted or name in visible},
        }, revision


def load_shared_project_history(backend, session):
    with backend.protected_request(session, "project.view") as (connection, account, permissions):
        unrestricted, visible = backend.authenticated_sessions.animal_scope(connection, account, permissions)
        history = backend.records.get("projects", "history", {"version": 1, "projects": {}})
        if not isinstance(history, dict) or not isinstance(history.get("projects"), dict):
            raise ValidationError("The project history catalogue is invalid.")
        return {"version": 1, "projects": {
            name: project for name, project in history["projects"].items()
            if unrestricted or name in visible
        }}


def project_history_changes(backend, connection, changes):
    from ..animal_identity import animal_base_name

    updates = [
        (ipid, original or {}, replacement)
        for ipid, (original, replacement) in changes.items()
        if ((original or {}).get("project") != replacement.get("project")
            or (original or {}).get("severity") != replacement.get("severity")
            or bool((original or {}).get("in_experiment")) != bool(replacement.get("in_experiment")))
    ]
    if not updates:
        return
    history, revision = backend.records.get_with_revision(
        "projects", "history", {"version": 1, "projects": {}},
    )
    if not isinstance(history, dict) or not isinstance(history.get("projects"), dict):
        raise ValidationError("The project history catalogue is invalid.")
    projects = history["projects"]
    when = backend.authenticated_sessions._now(connection).strftime("%d.%m.%Y")

    def active_rows(project, animal_ipid):
        records = projects.setdefault(project, {"archived": False, "animals": []})["animals"]
        return records, [
            row for row in records
            if (row.get("ipid") or row.get("name")) == animal_ipid and row.get("status") == "active"
        ]

    for ipid, before, after in updates:
        old, new = before.get("project"), after.get("project")
        if old and old != new:
            _, active = active_rows(old, ipid)
            for row in active:
                row["status"] = "former"
                row["date_left"] = when
                row["last_severity"] = before.get("severity") or row.get("last_severity")
                row["previous_in_experiment"] = bool(
                    before.get("in_experiment") or row.get("previous_in_experiment") or row.get("had_in_experiment")
                )
        if not new:
            continue
        records, active = active_rows(new, ipid)
        if old != new and not active:
            previous_projects = [
                {"project": name, **row}
                for name, project in projects.items() if name != new
                for row in project["animals"]
                if (row.get("ipid") or row.get("name")) == ipid and row.get("status") == "former"
            ]
            active = [{
                "ipid": ipid, "name": animal_base_name(ipid), "status": "active",
                "date_entered": after.get("project_entry_date") or when,
                "date_left": None, "last_severity": None,
                "previous_project_snapshot": previous_projects,
                "previous_experimental_snapshot": [
                    dict(row) for row in previous_projects if row.get("previous_in_experiment", row.get("last_severity"))
                ],
            }]
            records.extend(active)
        if before.get("in_experiment") or after.get("in_experiment"):
            for row in active:
                row["previous_in_experiment"] = row["had_in_experiment"] = True
                if after.get("severity"):
                    row["last_severity"] = after["severity"]
    backend.records.put("projects", "history", history, expected_revision=revision)


def mutate_shared_project(backend, session, project_name, project_id, lease_token, *, operation,
                          replacement=None, expected_revision=None):
    if operation not in {"update", "archive", "restore", "delete"}:
        raise ValidationError("Unknown project command.")
    permission = "project.archive_project" if operation in {"archive", "restore"} else "project.manage"
    with backend.protected_request(
        session, permission, entity_leases={("project", project_id): lease_token}, write=True,
    ) as (connection, account, allowed):
        animal_leases, temporary = _hold_species_change_leases(
            backend, connection, session, account, allowed, project_name, replacement if operation == "update" else None
        )
        catalog, revision = read_catalog(backend.adapter, connection)
        if expected_revision is not None and revision != expected_revision:
            raise ConflictError("The project catalogue changed. Reload and try again.")
        current = catalog["projects"].get(project_name)
        if not isinstance(current, dict) or current.get("project_id") != project_id:
            raise ConflictError("The selected project changed.")
        history, history_revision = backend.records.get_with_revision("projects", "history", {"version": 1, "projects": {}})
        if not isinstance(history, dict) or not isinstance(history.get("projects"), dict):
            raise ValidationError("The project history catalogue is invalid.")
        history_record = history["projects"].get(project_name, {})
        archived = bool(history_record.get("archived"))
        if operation == "update":
            if not isinstance(replacement, dict) or any(replacement.get(key) != current.get(key) for key in _IDENTITY):
                raise ValidationError("A project edit must preserve its identity.")
            if archived:
                raise PermissionDeniedError("Restore the archived project before editing it.")
            if (replacement.get("summary") or {}).get("species") != (current.get("summary") or {}).get("species"):
                if "project.manage_species_scope" not in allowed and "*" not in allowed:
                    raise PermissionDeniedError("This account cannot change the species scope.")
                species = (replacement.get("summary") or {}).get("species", "")
                _reconcile_species_scope(backend, connection, account, allowed, session, project_name, species, animal_leases)
            from ..authorization import CanonicalUnitService

            prior_unit = (current.get("iacuc") or {}).get("unit") or ""
            unit_id = (replacement.get("iacuc") or {}).get("unit") or ""
            if unit_id and unit_id != prior_unit:
                unit = CanonicalUnitService(backend).get(unit_id)
                if unit is None or not unit.active or unit.archived:
                    raise ValidationError("The selected Organizational Unit is unavailable.")
            if "status" in replacement:
                set_project_status(current, replacement["status"], can_manage=True)
            current.update({key: replacement[key] for key in _EDITABLE - {"status"} if key in replacement})
            current["modified_by"] = account.get("display_name") or account["username"]
            current["modified_at"] = server_timestamp(backend.adapter, connection)
            write_catalog(backend.adapter, connection, catalog, revision)
        else:
            if operation == "delete":
                if not archived:
                    raise PermissionDeniedError("Archive the project before deleting it.")
                catalog.setdefault("project_tombstones", {})[project_id] = {
                    **{key: current[key] for key in _IDENTITY}, "name": project_name,
                }
                del catalog["projects"][project_name]
                history["projects"].pop(project_name, None)
                write_catalog(backend.adapter, connection, catalog, revision)
            else:
                row = history["projects"].setdefault(project_name, {"animals": []})
                row["archived"] = operation == "archive"
            backend.records.put("projects", "history", history, expected_revision=history_revision)
        backend.audit._append_in_transaction(
            connection, actor_login=account["username"], category="projects", action=operation,
            entity_type="project", entity_id=project_id, payload={"name": project_name},
        )
        for ipid, token in animal_leases.items():
            backend.leases.require_shared_lease(connection, session, "animal", ipid, token)
        for ipid in temporary:
            backend.leases.release("animal", ipid, animal_leases[ipid], session=session)


def _hold_species_change_leases(backend, connection, session, account, allowed, project, replacement):
    if replacement is None:
        return {}, []
    prior = backend.records.get("projects", "catalog", {}).get("projects", {}).get(project, {})
    species = (replacement.get("summary") or {}).get("species")
    if species == (prior.get("summary") or {}).get("species"):
        return {}, []
    if "project.manage_species_scope" not in allowed and "*" not in allowed:
        raise PermissionDeniedError("This account cannot change the species scope.")
    from Plugins.core.project_species import remove_mismatched_assignments

    current = backend.animals.load_snapshot()
    affected = remove_mismatched_assignments(current["animals"], project, species)
    leases, temporary = {}, []
    for ipid in sorted(affected):
        backend.authenticated_sessions.require_visible_animal(connection, account, allowed, ipid)
        owned = _fetchone(connection, """
            SELECT token FROM entity_leases
            WHERE entity_type='animal' AND entity_id=%s AND owner_session_id=%s AND expires_at>clock_timestamp()
        """, (ipid, session.session_id))
        leases[ipid] = backend.leases.acquire("animal", ipid, session=session)["token"]
        if owned is None:
            temporary.append(ipid)
    return leases, temporary


def _reconcile_species_scope(backend, connection, account, allowed, session, project, species, leases):
    from Plugins.core.project_periods import ensure_project_periods, make_project_period_id
    from Plugins.core.project_species import remove_mismatched_assignments
    from .shared_animal_edit import _clinical_context

    current = backend.animals.load_snapshot()
    original = deepcopy(current["animals"])
    affected = set(remove_mismatched_assignments(current["animals"], project, species))
    if affected != leases.keys():
        raise ConflictError("Animal assignments changed. Reload the project and try again.")
    if not affected:
        return
    when = _fetchone(connection, "SELECT clock_timestamp()::date AS today")["today"].strftime("%d.%m.%Y")
    for ipid in sorted(affected):
        backend.leases.require_shared_lease(connection, session, "animal", ipid, leases[ipid])
        prior, animal = original[ipid], current["animals"][ipid]
        ensure_project_periods(prior)
        history = animal.setdefault("project_history", [])
        entry_date = prior.get("project_entry_date", "")
        history.append({
            "project": project, "project_id": prior.get("project_id", ""),
            "entry_date": entry_date, "leave_date": when,
            "period_id": prior.get("project_period_id") or make_project_period_id(project, entry_date, when, len(history)),
            "reason": "project_species_changed", "actor": account["username"],
        })
        animal.pop("project_id", None)
        animal.pop("project_period_id", None)
    backend.animals.replace_snapshot(
        current, affected_ipids=affected, include_settings=False,
        clinical_context=_clinical_context(account, allowed),
    )
    project_history_changes(
        backend, connection, {ipid: (original[ipid], current["animals"][ipid]) for ipid in affected}
    )
