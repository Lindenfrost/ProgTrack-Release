# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Canonical account, organization Unit and job-policy mutations."""

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import uuid

from ..authorization import OrganizationUnit, normalize_unit_id
from .errors import AuthenticationError, ConflictError, PermissionDeniedError, ValidationError
from .json_codec import dumps, loads
from .repositories import _fetchone, _placeholder


USERS = ("security", "users")
UNITS = ("security", "organization-units")
JOBS = ("configuration", "job-bundles")
SETTINGS = ("configuration", "master-track")
PROTECTED_ACCOUNT_RECORDS = frozenset((USERS, UNITS, JOBS, SETTINGS))
PROFILE_FIELDS = frozenset(("display_name", "pronouns", "email", "phone", "mobile", "profession"))
STATUS_FIELDS = frozenset(("active", "disabled", "archived", "blocked"))
_PUBLIC_FIELDS = PROFILE_FIELDS | STATUS_FIELDS | {
    "username", "unit_id", "unit", "role", "jobs", "permissions", "last_login", "must_change_password",
}
_ROLES = {"lord", "master", "user"}


@dataclass(frozen=True)
class AccountMutation:
    changed: bool
    targets: tuple[str, ...] = ()
    actor_revoked: bool = False


def public_account(account):
    return {key: deepcopy(value) for key, value in account.items() if key in _PUBLIC_FIELDS}


def _edit_snapshot(account):
    return {key: value for key, value in public_account(account).items() if key != "last_login"}


def _active(account):
    return account.get("active", True) is not False and not any(account.get(key) for key in STATUS_FIELDS - {"active"})


def _require(allowed, permission):
    if "*" not in allowed and permission not in allowed:
        raise PermissionDeniedError("This account may not perform the requested account change.")


class UserAccountCommands:
    def __init__(self, backend):
        self.backend = backend

    @property
    def sessions(self):
        return self.backend.authenticated_sessions

    def directory(self, context):
        with self.backend.adapter.transaction() as connection:
            self.sessions.require(connection, context)
            accounts, revision = self.sessions._catalogue(connection)
            return [public_account(account) for account in accounts], revision

    def job_policy_snapshot(self, context):
        """Return the displayed job policy and its save precondition together."""
        with self.backend.adapter.transaction() as connection:
            account = self.sessions.require(connection, context)
            _require(self.sessions.effective_permissions(connection, account), "master.manage_job_bundles")
            mark = _placeholder(self.backend.adapter)
            policy = []
            for record, defaults in ((JOBS, {}), (SETTINGS, {"timeout_minutes": 30})):
                row = _fetchone(
                    connection, f"SELECT payload_json,revision FROM domain_records WHERE namespace={mark} AND record_id={mark}",
                    record,
                )
                raw = row["payload_json"] if row else defaults
                policy.append((loads(raw) if isinstance(raw, str) else raw, int(row["revision"]) if row else 0))
            (jobs, jobs_revision), (settings, settings_revision) = policy
            return jobs, settings, (jobs_revision, settings_revision)

    @contextmanager
    def _mutation(self, context, *, allow_password_change=False):
        with self.backend.adapter.transaction(write=True) as connection:
            actor = self.sessions.require(connection, context, mutate_accounts=True,
                                          allow_password_change=allow_password_change)
            allowed = self.sessions.effective_permissions(connection, actor)
            accounts, revision = self.sessions._catalogue(connection)
            yield connection, actor, allowed, accounts, revision

    @staticmethod
    def _target(accounts, username):
        key = str(username or "").strip().casefold()
        target = next((account for account in accounts if account["username"].casefold() == key), None)
        if target is None:
            raise ConflictError("The account no longer exists. Reopen the editor.")
        return target

    def _read(self, record, default):
        return self.backend.records.get_with_revision(*record, default=default)

    def _write(self, connection, record, value, revision):
        # The command already owns the outer transaction. Keeping persistence
        # on that connection also gives standalone SQLite the same semantics.
        stamp = self.sessions._now(connection).isoformat()
        with self.backend._allow_account_writes():
            return self.backend.records._put(connection, *record, dumps(value), revision, stamp)

    def _commit(self, connection, context, actor, accounts, desired, revision, *,
                operation, changes=(), affected=(), extra=(), allow_password_change=False, policy_event=None):
        old = {account["username"]: account for account in accounts}
        new = {account["username"]: account for account in desired}
        targets = {name for name, account in old.items() if new.get(name) != account} | set(affected)
        if accounts == desired and not extra and not targets:
            return AccountMutation(False)
        if not any(account.get("role") == "lord" and _active(account) for account in desired):
            raise PermissionDeniedError("The last active Lord account must be retained.")
        # Retest the actor before their own update can change their authority.
        # The exclusive users row stays locked through the commit.
        self.sessions.require(connection, context, allow_password_change=allow_password_change)
        if accounts != desired:
            self._write(connection, USERS, desired, revision)
        for record, data, expected in extra:
            self._write(connection, record, data, expected)
        self.sessions.require_current_token(connection, context)
        correlation = str(uuid.uuid4())
        targets = tuple(sorted(targets, key=str.casefold))
        if policy_event is not None:
            kind, entity_id, payload = policy_event
            self.backend.audit._append_in_transaction(
                connection, actor_login=actor["username"], category="security", action=operation,
                entity_type=kind, entity_id=entity_id, correlation_id=correlation,
                payload={**payload, "users_affected": list(targets), "users_affected_count": len(targets)},
            )
        for name in targets:
            session_id = self.sessions.revoke_account_session(connection, name)
            self.backend.audit._append_in_transaction(
                connection, actor_login=actor["username"], category="security",
                action="user_account_changed" if policy_event else operation,
                entity_type="account", entity_id=name, correlation_id=correlation,
                payload={"reason": "user_account_changed", "operation": operation, "changed_fields": sorted(changes),
                         "revoked_session_id": session_id},
            )
        if not targets and policy_event is None:
            created = new.keys() - old.keys()
            self.backend.audit._append_in_transaction(
                connection, actor_login=actor["username"], category="security", action=operation,
                entity_type="account" if created else "account_policy",
                entity_id=next(iter(created)) if created else operation, correlation_id=correlation,
                payload={"changed_fields": sorted(changes), "revoked_session_id": None},
            )
        return AccountMutation(True, targets, actor["username"] in targets)

    def _unit_settings(self, target, changed, *, new=False):
        requested = str(target.get("unit_id") or "").strip().casefold()
        if not requested:
            if target["role"] == "user" and (new or changed):
                raise ValidationError("A regular user must have an organizational Unit.")
            if changed:
                target.update(unit_id="", unit="")
            return
        if not (new or changed):
            return
        unit_id = normalize_unit_id(requested)
        raw, _ = self._read(UNITS, {"units": []})
        unit = next((OrganizationUnit.from_record(item) for item in raw["units"]
                     if item.get("unit_id") == unit_id), None)
        if unit is None or not unit.active or unit.archived:
            raise ValidationError("The selected organizational Unit is unavailable.")
        target.update(unit_id=unit_id, unit=unit.display_name)

    def create(self, context, username, password, *, role="user", unit_id="", **profile):
        from Plugins.Master_Track.auth import build_user_record

        username = str(username or "").strip()
        if (not username or not isinstance(password, str) or len(password) < 6 or role not in _ROLES
                or profile.keys() - PROFILE_FIELDS or any(not isinstance(value, str) for value in profile.values())):
            raise ValidationError("Enter a valid username, role and password.")
        account = build_user_record(username, password, role=role, unit_id=unit_id, **profile)
        with self._mutation(context) as (connection, actor, allowed, accounts, revision):
            _require(allowed, "master.create_users")
            if role != "user":
                _require(allowed, "master.assign_primary_role")
            if any(row["username"].casefold() == username.casefold() for row in accounts):
                raise ConflictError("The account username already exists.")
            self._unit_settings(account, True, new=True)
            self._commit(connection, context, actor, accounts, [*accounts, account], revision, operation="user_created")
        return public_account(account)

    def edit(self, context, username, patch, *, expected=None):
        permitted = PROFILE_FIELDS | STATUS_FIELDS | {"role", "jobs", "permissions", "unit_id"}
        if not isinstance(patch, dict) or patch.keys() - permitted or any(
            not isinstance(patch[key], str) for key in patch.keys() & (PROFILE_FIELDS | {"unit_id"})
        ):
            raise ValidationError("This account edit contains unrecognized fields.")
        with self._mutation(context) as (connection, actor, allowed, accounts, revision):
            target = self._target(accounts, username)
            if expected is not None and _edit_snapshot(expected) != _edit_snapshot(target):
                raise ConflictError("The account changed. Reopen the editor before saving.")
            desired = deepcopy(target)
            desired.update(patch)
            fields = {key for key in patch if desired.get(key) != target.get(key)}
            self._validate_edit(allowed, target, desired, fields)
            rows = [desired if row is target else row for row in accounts]
            return self._commit(connection, context, actor, accounts, rows, revision, operation="user_edited", changes=fields)

    def _validate_edit(self, allowed, before, after, fields):
        from Plugins.Master_Track.permissions import ALL_PERMISSIONS, DEFAULT_JOB_BUNDLES, JOB_ANIMAL_WELFARE, JOB_VET

        if fields & (PROFILE_FIELDS | STATUS_FIELDS | {"unit_id"}):
            _require(allowed, "master.edit_users")
        if "role" in fields:
            _require(allowed, "master.assign_primary_role")
            if after.get("role") not in _ROLES:
                raise ValidationError("Unknown account role.")
        promoted = "role" in fields and after["role"] in {"lord", "master"}
        if any(not isinstance(after[key], bool) for key in fields & STATUS_FIELDS):
            raise ValidationError("Invalid account activation status.")
        if "jobs" in fields:
            if not (promoted and after["jobs"] == []):
                _require(allowed, "master.assign_jobs")
            jobs, _ = self._read(JOBS, {})
            assigned = after["jobs"]
            if not isinstance(assigned, list) or any(not isinstance(job, str) for job in assigned):
                raise ValidationError("Invalid account jobs.")
            assigned = list(dict.fromkeys(assigned))
            if set(assigned) - (DEFAULT_JOB_BUNDLES.keys() | jobs.keys()):
                raise ValidationError("An assigned job no longer exists.")
            if JOB_ANIMAL_WELFARE in assigned and JOB_VET not in assigned:
                raise ValidationError("The AWO job requires the Vet job.")
            after["jobs"] = assigned
        if "permissions" in fields:
            direct = after["permissions"]
            if not isinstance(direct, dict) or direct.keys() != {"granted", "revoked"} or any(
                not isinstance(items, list) or any(not isinstance(item, str) for item in items) for items in direct.values()
            ):
                raise ValidationError("Invalid direct permissions.")
            granted, revoked = set(direct["granted"]), set(direct["revoked"])
            if granted & revoked or (granted | revoked) - set(ALL_PERMISSIONS):
                raise ValidationError("Invalid direct permissions.")
            prior = before.get("permissions") or {}
            old_granted, old_revoked = set(prior.get("granted", [])), set(prior.get("revoked", []))
            grants = (granted - old_granted) | (old_revoked - revoked)
            if grants and not (promoted and not granted and not revoked):
                _require(allowed, "master.grant_direct_permissions")
            if ((revoked - old_revoked) | (old_granted - granted)) and not (promoted and not granted and not revoked):
                _require(allowed, "master.revoke_direct_permissions")
            after["permissions"] = {"granted": sorted(granted), "revoked": sorted(revoked)}
        self._unit_settings(after, bool(fields & {"unit_id", "role"}))

    def delete(self, context, username):
        with self._mutation(context) as (connection, actor, allowed, accounts, revision):
            _require(allowed, "master.edit_users")
            target = self._target(accounts, username)
            if target["username"] == actor["username"]:
                raise PermissionDeniedError("You cannot delete your own account.")
            return self._commit(connection, context, actor, accounts, [row for row in accounts if row is not target],
                                revision, operation="user_deleted")

    def reset_password(self, context, username, password, *, must_change=True):
        from Plugins.Master_Track.auth import hash_password

        if not isinstance(password, str) or len(password) < 6:
            raise ValidationError("A password of at least six characters is required.")
        password_hash, salt = hash_password(password)
        with self._mutation(context) as (connection, actor, allowed, accounts, revision):
            _require(allowed, "master.edit_users")
            target = self._target(accounts, username)
            candidate = dict(target, password_hash=password_hash, salt=salt, must_change_password=bool(must_change))
            rows = [candidate if row is target else row for row in accounts]
            return self._commit(connection, context, actor, accounts, rows, revision,
                                operation="user_password_reset", changes=("credentials",))

    def change_own_password(self, context, previous, replacement):
        from Plugins.Master_Track.auth import hash_password, verify_password

        if not isinstance(replacement, str) or len(replacement) < 6:
            raise ValidationError("A password of at least six characters is required.")
        password_hash, salt = hash_password(replacement)
        with self._mutation(context, allow_password_change=True) as (connection, actor, _, accounts, revision):
            target = self._target(accounts, actor["username"])
            try:
                verified = verify_password(previous, target["password_hash"], target["salt"])
            except (KeyError, TypeError, ValueError):
                verified = False
            if not verified:
                raise AuthenticationError("The current password is not correct.")
            candidate = dict(target, password_hash=password_hash, salt=salt, must_change_password=False)
            rows = [candidate if row is target else row for row in accounts]
            return self._commit(connection, context, actor, accounts, rows, revision, operation="user_password_changed",
                                changes=("credentials",), allow_password_change=True)

    def replace_units(self, context, units, expected_revision, *, action):
        from ..authorization import CanonicalUnitService

        if action not in {"organization_units_replace", "organization_unit_create", "organization_unit_update",
                          "organization_unit_archive", "organization_unit_restore", "organization_unit_delete"}:
            raise ValidationError("Unknown organizational Unit operation.")
        payload = CanonicalUnitService._payload(units)
        with self._mutation(context) as (connection, actor, allowed, accounts, revision):
            _require(allowed, "core.manage_institution_settings")
            prior, catalog_revision = self._read(UNITS, {"schema_version": 1, "units": []})
            if expected_revision != catalog_revision:
                raise ConflictError("The Unit catalogue changed. Reopen Institution before retrying.")
            old = {item["unit_id"]: item for item in prior["units"]}
            new = {item["unit_id"]: item for item in payload["units"]}
            updated = deepcopy(accounts)
            affected = set()
            for account in updated:
                unit = str(account.get("unit_id") or "").casefold()
                if unit not in (old.keys() | new.keys()):
                    continue
                previous, current = old.get(unit), new.get(unit)
                if current is None:
                    account.update(unit_id="", unit="")
                elif previous and current["display_name"] != previous["display_name"]:
                    account["unit"] = current["display_name"]
                active = lambda row: bool(row and row.get("active", True) and not row.get("archived", False))
                if active(previous) != active(current) or (
                    previous and current and previous.get("facility_ref") != current.get("facility_ref")
                ):
                    affected.add(account["username"])
            if prior == payload:
                return catalog_revision, AccountMutation(False)
            differences = sorted(key for key in old.keys() | new.keys() if old.get(key) != new.get(key))
            entity_id = differences[0] if len(differences) == 1 else "catalogue"
            audit = {"catalogue_revision_before": catalog_revision, "catalogue_revision": catalog_revision + 1}
            if entity_id == "catalogue":
                audit.update(before=prior, after=payload)
            else:
                audit.update(before=old.get(entity_id), after=new.get(entity_id))
                if entity_id not in new:
                    audit.update(unit=old[entity_id], users_unassigned=True)
            result = self._commit(connection, context, actor, accounts, updated, revision, operation=action,
                                  affected=affected, extra=((UNITS, payload, catalog_revision),), changes=("unit",),
                                  policy_event=("organization_unit", entity_id, audit))
            return catalog_revision + 1, result

    def change_job(self, context, name, *, action, permissions=(), timeout=None, expected=None):
        from Plugins.Master_Track.permissions import ALL_PERMISSIONS, DEFAULT_JOB_BUNDLES

        name = str(name or "").strip().casefold()
        if not isinstance(permissions, (list, tuple, set, frozenset)) or any(not isinstance(item, str) for item in permissions):
            raise ValidationError("Unknown job definition or permissions.")
        requested = set(permissions)
        if (not name or action not in {"create", "update", "delete"} or requested - set(ALL_PERMISSIONS)
                or timeout is not None and (not isinstance(timeout, int) or isinstance(timeout, bool) or timeout < 1)):
            raise ValidationError("Unknown job definition or permissions.")
        with self._mutation(context) as (connection, actor, allowed, accounts, revision):
            _require(allowed, "master.manage_job_bundles")
            raw, job_revision = self._read(JOBS, {})
            settings, settings_revision = self._read(SETTINGS, {"timeout_minutes": 30})
            if expected is not None and expected != (job_revision, settings_revision):
                raise ConflictError("The job policy changed. Reopen the editor before saving.")
            configured = set(raw) | set(DEFAULT_JOB_BUNDLES)
            if (action == "create" and name in configured) or (action != "create" and name not in configured):
                raise ConflictError("The job catalogue changed. Reopen the editor before saving.")
            if action == "delete" and name in DEFAULT_JOB_BUNDLES:
                raise ValidationError("Built-in jobs cannot be deleted.")
            jobs, policy = deepcopy(raw), deepcopy(settings)
            timeouts = policy.setdefault("job_timeouts", {})
            assigned = deepcopy(accounts)
            members = [account for account in assigned if name in account.get("jobs", [])]
            if action == "delete":
                jobs.pop(name, None)
                timeouts.pop(name, None)
                for account in members:
                    account["jobs"] = [job for job in account["jobs"] if job != name]
            else:
                old_perms = set(raw.get(name, DEFAULT_JOB_BUNDLES.get(name, ())))
                if action == "create" or old_perms != requested:
                    jobs[name] = sorted(requested)
                if timeout is not None and int(timeout) != timeouts.get(name, policy.get("timeout_minutes", 30)):
                    timeouts[name] = max(1, int(timeout))
                if action == "update":
                    for account in members:
                        direct = account.get("permissions") or {}
                        if direct.get("granted") or direct.get("revoked"):
                            account["permissions"] = {"granted": [], "revoked": []}
            if not timeouts and "job_timeouts" not in settings:
                policy.pop("job_timeouts", None)
            extra = tuple((key, value, expected_revision) for key, value, old, expected_revision in (
                (JOBS, jobs, raw, job_revision), (SETTINGS, policy, settings, settings_revision),
            ) if value != old)
            affected = [account["username"] for account in members] if extra else []
            return self._commit(connection, context, actor, accounts, assigned, revision, operation=f"job_{action}",
                                affected=affected, extra=extra, changes=("job_policy",),
                                policy_event=("job", name, {"changed_policy_records": [key[1] for key, _, _ in extra]}))
