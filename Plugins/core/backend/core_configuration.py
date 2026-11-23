# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Revisioned commands for facility-wide Core configuration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from ..identity_conventions import next_generated_id
from ..resource_catalogs import CATALOG_FILES
from .errors import ConflictError, PermissionDeniedError, ValidationError

ANIMAL_ROLES = "animal-roles"
PRESETS = "role-block-presets"
IDENTITY_CONVENTIONS = "identity-lifecycle-conventions"
MESSAGE_LOCALES = ("de", "en", "it", "ru")
RECORD_IDS = (
    ANIMAL_ROLES, PRESETS, IDENTITY_CONVENTIONS,
    *(f"user-message-overrides:{locale}" for locale in MESSAGE_LOCALES),
)


class CoreConfigurationMissing(ValidationError):
    """The selected backend needs explicit Core configuration setup."""


def validate_payload(key, value):
    if not isinstance(value, dict):
        raise ValidationError(f"Invalid Core configuration record: {key}")
    if key == ANIMAL_ROLES:
        shapes = {"roles": list}
    elif key == PRESETS:
        shapes = {"presets": list}
    elif key == IDENTITY_CONVENTIONS:
        shapes = {
            **dict.fromkeys(CATALOG_FILES, list),
            "animal_id_pattern": str, "animal_id_components": list,
            "yearly_sequences": dict, "absolute_sequence": int, "genotypes": dict,
        }
    elif key in RECORD_IDS:
        shapes = {}
        if any(not isinstance(item, str) for item in value.values()):
            raise ValidationError(f"Invalid Core configuration message record: {key}")
    else:
        raise ValidationError(f"Unknown Core configuration record: {key}")
    if any(not isinstance(value.get(field), shape) for field, shape in shapes.items()):
        raise ValidationError(f"Incomplete Core configuration record: {key}")


def read_seed_configuration(application_root):
    package = Path(application_root) / "Resources" / "Seed" / "progtrack_seed.ptdb"
    try:
        with ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            member = "records/domain.jsonl"
            data = archive.read(member)
        if hashlib.sha256(data).hexdigest() != manifest["records"][member]["sha256"]:
            raise ValidationError("Core configuration seed checksum mismatch.")
        result = {}
        for line in data.splitlines():
            record = json.loads(line)
            key = record["record_id"]
            if record["namespace"] != "configuration" or key not in RECORD_IDS:
                continue
            if key in result:
                raise ValidationError(f"Duplicate Core configuration seed record: {key}")
            validate_payload(key, record["payload"])
            result[key] = record["payload"]
        if result.keys() != set(RECORD_IDS):
            raise ValidationError("The bundled seed lacks required Core configuration records.")
        return result
    except (OSError, BadZipFile, KeyError, ValueError, TypeError) as exc:
        raise ValidationError("Could not read the bundled Core configuration seed.") from exc


class CoreConfiguration:
    def __init__(self, backend):
        self.backend = backend

    def snapshot(self):
        with self.backend.adapter.transaction(snapshot=True):
            return self.read_snapshot()

    def snapshot_for_session(self, session, *, known_revisions=None):
        with self.backend.protected_request(session, snapshot=True):
            if known_revisions is not None:
                revisions = self.backend.records.revisions("configuration", RECORD_IDS)
                if revisions == known_revisions:
                    return None
            return self.read_snapshot()

    def read_snapshot(self):
        return {
            key: self.backend.records.get_with_revision("configuration", key, default=None)
            for key in RECORD_IDS
        }

    def setup(self, session):
        seed = read_seed_configuration(self.backend.paths.application_root)
        with self.backend.protected_request(session, "core.manage_institution_settings", write=True) as (connection, account, permissions):
            self._check_scope(connection, account, permissions)
            missing = {key: seed[key] for key, (_value, revision) in self.read_snapshot().items() if not revision}
            return self._write(connection, account, missing, dict.fromkeys(missing, 0), "initialize")

    def _check_scope(self, connection, account, permissions):
        if "*" not in permissions and not self.backend.authenticated_sessions.active_unit(connection, account):
            raise PermissionDeniedError("An active organizational Unit is required.")

    def save_roles(self, session, payloads, revisions):
        if not payloads.keys() <= set(RECORD_IDS) - {IDENTITY_CONVENTIONS}:
            raise ValidationError("Invalid set of Role Setup records.")
        with self.backend.protected_request(session, "core.manage_animal_roles", write=True) as (connection, account, permissions):
            self._check_scope(connection, account, permissions)
            dependencies = tuple(key for key in revisions if key in payloads or key in {ANIMAL_ROLES, PRESETS})
            current = self.backend.records.revisions("configuration", dependencies, lock=True)
            for key, revision in current.items():
                if revision != revisions[key]:
                    raise ConflictError(f"Stale revision for configuration/{key}.")
            prepared = dict(payloads)
            for key, changes in payloads.items():
                if not key.startswith("user-message-overrides:"):
                    continue
                existing, revision = self.backend.records.get_with_revision("configuration", key, default=None)
                if revision != revisions.get(key):
                    raise ConflictError(f"Stale revision for configuration/{key}.")
                if not revision:
                    raise CoreConfigurationMissing(f"Initialize Core configuration before editing {key}.")
                validate_payload(key, existing)
                if not isinstance(changes, dict) or any(
                    not isinstance(name, str) or not name or (value is not None and not isinstance(value, str))
                    for name, value in changes.items()
                ):
                    raise ValidationError(f"Invalid message changes for {key}.")
                for name, value in changes.items():
                    if value is None:
                        existing.pop(name, None)
                    else:
                        existing[name] = value
                prepared[key] = existing
            return self._write(connection, account, prepared, revisions, "edit")

    def save_identity(self, session, payload, revision):
        with self.backend.protected_request(session, "core.manage_institution_settings", write=True) as (connection, account, permissions):
            self._check_scope(connection, account, permissions)
            existing, current = self.backend.records.get_with_revision(
                "configuration", IDENTITY_CONVENTIONS, default=None)
            if current != revision:
                raise ConflictError(f"Stale revision for configuration/{IDENTITY_CONVENTIONS}.")
            if not current:
                raise CoreConfigurationMissing("Initialize Core configuration before editing conventions.")
            validate_payload(IDENTITY_CONVENTIONS, existing)
            payload = dict(payload)
            for key in ("yearly_sequences", "absolute_sequence"):
                payload[key] = existing[key]
            return self._write(connection, account, {IDENTITY_CONVENTIONS: payload},
                               {IDENTITY_CONVENTIONS: revision}, "edit")

    def reserve_id(self, session, **inputs):
        inputs["existing_ids"] = tuple(inputs.get("existing_ids", ()))
        for _attempt in range(20):
            try:
                with self.backend.protected_request(session, "core.create_animals", write=True) as (connection, account, permissions):
                    self._check_scope(connection, account, permissions)
                    value, revision = self.backend.records.get_with_revision("configuration", IDENTITY_CONVENTIONS, default=None)
                    if not revision:
                        raise CoreConfigurationMissing("Initialize Core configuration before allocating animal IDs.")
                    validate_payload(IDENTITY_CONVENTIONS, value)
                    generated = next_generated_id(value, **inputs)
                    self._write(connection, account, {IDENTITY_CONVENTIONS: value},
                                {IDENTITY_CONVENTIONS: revision}, "reserve_id")
                    return generated, value
            except ConflictError:
                continue
        raise ConflictError("The animal ID counter changed repeatedly. Retry the operation.")

    def _write(self, connection, account, payloads, revisions, action):
        records = []
        for key, value in payloads.items():
            validate_payload(key, value)
            revision = revisions.get(key)
            if type(revision) is not int or revision < 0 or (revision == 0 and action != "initialize"):
                raise CoreConfigurationMissing(f"An initialized revision is required for {key}.")
            records.append(("configuration", key, value))
        with self.backend._allow_core_configuration_writes():
            saved = self.backend.records.put_many(
                records, expected_revisions={("configuration", key): revision for key, revision in revisions.items()},
            )
        correlation_id = str(uuid4())
        for (_namespace, key), revision in saved.items():
            self.backend.audit._append_in_transaction(
                connection, actor_login=account["username"], category="configuration", action=action,
                entity_type="configuration", entity_id=key, correlation_id=correlation_id,
                payload={"revision": revision, "result": "success"},
            )
        return {key: revision for (_namespace, key), revision in saved.items()}
