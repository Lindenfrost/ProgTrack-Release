# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Versioned backend-neutral ProgTrack interchange packages."""

from __future__ import annotations

import hashlib
import copy
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ConflictError, PermissionDeniedError, ValidationError
from .json_codec import dumps, loads
from .managed_files import ManagedFileService, safe_relative_path, sha256_file
from .repositories import _execute, _fetchone, _json_placeholder, _placeholder, now_text
from .schema import SCHEMA_LOCK


INTERCHANGE_SCHEMA = "progtrack-interchange/1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    return safe_relative_path(name)


def _canonicalize_animal_payload(payload: Any) -> Any:
    """Validate and copy an export payload with canonical event keys.

    Interchange is a persistence boundary, not a legacy converter. Old event
    keys must be migrated before export and are rejected here with an
    actionable error.
    """
    if not isinstance(payload, dict):
        return payload
    result = copy.deepcopy(payload)
    events = result.get("events")
    if not isinstance(events, list):
        return result
    canonical_events: list[Any] = []
    for item in events:
        if not isinstance(item, dict):
            canonical_events.append(item)
            continue
        event = dict(item)
        legacy_keys = sorted(key for key in ("typ", "type") if key in event)
        if legacy_keys:
            raise ValidationError(
                "Animal event uses obsolete field(s) "
                + ", ".join(legacy_keys)
                + "; migrate the payload to event_type before export."
            )
        event_type = str(event.get("event_type") or "").strip()
        if not event_type:
            raise ValidationError("Animal event is missing canonical event_type.")
        event["event_type"] = event_type
        canonical_events.append(event)
    result["events"] = canonical_events
    return result


@dataclass
class ImportPreview:
    package_id: str
    schema: str
    counts: dict[str, int]
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors


class InterchangeService:
    def __init__(self, backend: Any, managed_files: ManagedFileService):
        self.backend = backend
        self.managed_files = managed_files

    def _is_shared(self):
        return getattr(getattr(self.backend, "adapter", None), "dialect", None) == "postgresql"

    @contextmanager
    def _maintenance(self, *, write=False, snapshot=False):
        options = {"snapshot": True} if snapshot else {}
        with self.backend.adapter.transaction(write=write, **options) as connection:
            if not self._is_shared():
                yield connection
                return
            proof = _fetchone(connection, """
                SELECT pg_has_role(current_user, datdba, 'USAGE') AS authorized
                FROM pg_database WHERE datname=current_database()
            """)
            if proof is None or not proof["authorized"]:
                raise PermissionDeniedError("Shared interchange requires the PostgreSQL database owner role.")
            if write:
                lock = _fetchone(connection, "SELECT pg_try_advisory_xact_lock(%s,%s) AS acquired", SCHEMA_LOCK)
                if not lock["acquired"]:
                    raise ConflictError("Another operation already holds the maintenance lock.")
            with self.backend._allow_authorized_records(), self.backend._allow_account_maintenance():
                yield connection

    def _load_core(self):
        if self._is_shared():
            return self.backend.animals.load_snapshot()
        return self.backend.load_core_data()

    def _save_core(self, snapshot):
        if self._is_shared():
            return self.backend.animals.replace_snapshot(snapshot)
        return self.backend.save_core_data(snapshot)

    def export_package(
        self,
        target: str | Path,
        *,
        package_id: str | None = None,
        created_at: str | None = None,
    ) -> Path:
        with self._maintenance(snapshot=self._is_shared()):
            snapshot = self._load_core()
            domain_rows = self.backend.records.list_all() if hasattr(self.backend.records, "list_all") else {}
            documents = self.managed_files.export_records()
        return self._export_package(target, package_id, created_at, snapshot, domain_rows, documents)

    def _export_package(self, target, package_id, created_at, snapshot, domain_rows, documents):
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        package_id = package_id or str(uuid.uuid4())
        timestamp = created_at or datetime.now(timezone.utc).isoformat()
        animals_lines = []
        for archived, section in (
            (False, snapshot.get("animals", {})),
            (True, snapshot.get("archived_animals", {})),
        ):
            for ipid, payload in sorted(section.items()):
                animals_lines.append(dumps({
                    "type": "animal",
                    "ipid": ipid,
                    "archived": archived,
                    "payload": _canonicalize_animal_payload(payload),
                }))
        records_lines = [
            dumps({
                "type": "domain_record",
                "namespace": namespace,
                "record_id": record_id,
                "payload": payload,
            })
            for namespace, records in sorted(domain_rows.items())
            for record_id, payload in sorted(records.items())
        ]
        settings_lines = [
            dumps({"type": "settings", "payload": snapshot.get("settings", {})})
        ]
        record_files = {
            "records/animals.jsonl": ("\n".join(animals_lines) + "\n").encode("utf-8"),
            "records/domain.jsonl": ("\n".join(records_lines) + ("\n" if records_lines else "")).encode("utf-8"),
            "records/settings.jsonl": ("\n".join(settings_lines) + "\n").encode("utf-8"),
        }
        payload_entries: list[tuple[str, dict[str, Any]]] = []
        for record in documents:
            package_name = (
                "payload/config-assets/" if record["category"] == "config-asset"
                else "payload/documents/"
            ) + f"{record['document_id']}/{record['original_name']}"
            member = _safe_member(package_name)
            if len(member.parts) != 4 or member.as_posix() != package_name:
                raise ValidationError("Managed document metadata contains an unsafe interchange filename.")
            payload_entries.append((package_name, record))

        manifest = {
            "schema": INTERCHANGE_SCHEMA,
            "package_id": package_id,
            "created_at": timestamp,
            "source_profile": self.backend.paths.profile.value,
            "counts": {
                "animals": len(animals_lines),
                "domain_records": len(records_lines),
                "managed_files": len(payload_entries),
            },
            "records": {
                name: {"sha256": _sha256_bytes(content), "bytes": len(content)}
                for name, content in record_files.items()
            },
            "managed_files": [
                {
                    **{
                        key: str(record[key]) if key == "created_at" else record[key]
                        for key in (
                            "document_id", "owner_type", "owner_id", "category",
                            "original_name", "media_type", "byte_size", "sha256",
                            "created_at", "created_by",
                        )
                    },
                    "package_path": name,
                }
                for name, record in payload_entries
            ],
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".progtrack-export-", dir=target_path.parent)
        os.close(descriptor)
        temp_target = Path(temporary)
        try:
            self._write_package(temp_target, created_at, manifest, record_files, payload_entries)
            os.replace(temp_target, target_path)
        finally:
            temp_target.unlink(missing_ok=True)
        return target_path

    def _write_package(self, target, created_at, manifest, record_files, payload_entries):
        with (tempfile.TemporaryDirectory(prefix="progtrack-export-") as staging,
              zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive):
            def write_bytes(name: str, content: bytes | str) -> None:
                if created_at:
                    info = zipfile.ZipInfo(name, date_time=(2026, 7, 30, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o600 << 16
                    archive.writestr(info, content)
                else:
                    archive.writestr(name, content)

            write_bytes(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2),
            )
            for name, content in record_files.items():
                write_bytes(name, content)
            for index, (name, record) in enumerate(payload_entries):
                staged = Path(staging) / str(index)
                self.managed_files.copy_verified_payload(record, staged)
                archive.write(staged, name)

    def preview(self, package: str | Path) -> ImportPreview:
        path = Path(package)
        errors: list[str] = []
        warnings: list[str] = []
        counts = {"animals": 0, "domain_records": 0, "managed_files": 0}
        package_id = ""
        schema = ""
        try:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                if len(names) != len(set(names)):
                    raise ValidationError("Interchange contains duplicate archive members.")
                for name in names:
                    _safe_member(name.rstrip("/"))
                manifest = json.loads(archive.read("manifest.json"))
                schema = str(manifest.get("schema") or "")
                package_id = str(manifest.get("package_id") or "")
                if schema != INTERCHANGE_SCHEMA:
                    errors.append(f"Unsupported interchange schema: {schema}")
                if not package_id:
                    errors.append("Interchange package ID is missing.")
                for record_name, expected in manifest.get("records", {}).items():
                    data = archive.read(record_name)
                    if _sha256_bytes(data) != expected.get("sha256"):
                        errors.append(f"Checksum mismatch: {record_name}")
                managed_ids = set()
                managed_paths = set()
                for entry in manifest.get("managed_files", []):
                    document_id = str(uuid.UUID(entry["document_id"]))
                    package_path = entry["package_path"]
                    if document_id in managed_ids or package_path in managed_paths:
                        raise ValidationError("Interchange contains duplicate managed document identity.")
                    managed_ids.add(document_id)
                    managed_paths.add(package_path)
                    payload = archive.read(package_path)
                    if len(payload) != int(entry["byte_size"]):
                        errors.append(f"Size mismatch: {entry['package_path']}")
                    if _sha256_bytes(payload) != entry["sha256"]:
                        errors.append(f"Checksum mismatch: {entry['package_path']}")
                expected = {"manifest.json", *manifest.get("records", {}).keys()}
                expected.update(
                    str(entry.get("package_path") or "")
                    for entry in manifest.get("managed_files", [])
                )
                unexpected = [
                    name for name in names
                    if not name.endswith("/") and name not in expected
                ]
                if unexpected:
                    errors.append(
                        "Unexpected interchange members: " + ", ".join(sorted(unexpected))
                    )
                counts.update({
                    key: int(value)
                    for key, value in manifest.get("counts", {}).items()
                    if key in counts
                })
        except (OSError, zipfile.BadZipFile, KeyError, ValueError, TypeError, ValidationError) as exc:
            errors.append(str(exc))
        return ImportPreview(package_id, schema, counts, errors, warnings)

    def import_package(self, package: str | Path, *, require_empty: bool = True, admin_operation=None) -> ImportPreview:
        if self._is_shared():
            from .postgresql_admin import _AdminOperation

            if not require_empty:
                raise PermissionDeniedError("Shared interchange can only initialize an empty target.")
            if not isinstance(admin_operation, _AdminOperation):
                raise PermissionDeniedError("Fresh PostgreSQL transfer authorization is required.")
            admin_operation.require_transfer(self.backend)

        @contextmanager
        def request():
            if admin_operation is not None:
                admin_operation.require_transfer(self.backend)
            with self._maintenance(write=True) as connection:
                yield connection, admin_operation.initiator if admin_operation else "interchange"
                if admin_operation is not None:
                    admin_operation.require_transfer(self.backend)

        with tempfile.TemporaryDirectory(prefix="progtrack-import-") as directory:
            folder = Path(directory)
            staged = folder / "package"
            shutil.copyfile(package, staged)
            preview = self.preview(staged)
            if not preview.valid:
                return preview
            package_hash = sha256_file(staged)
            receipt = f"interchange-package:{preview.package_id}"
            with request() as (connection, _actor):
                if self._check_import_target(connection, receipt, package_hash, require_empty):
                    return preview
            with zipfile.ZipFile(staged) as archive:
                manifest = json.loads(archive.read("manifest.json"))
                prepared = []
                for index, entry in enumerate(manifest.get("managed_files", [])):
                    payload = folder / str(index)
                    with archive.open(entry["package_path"]) as reader, payload.open("xb") as writer:
                        shutil.copyfileobj(reader, writer)
                    operation_id = uuid.uuid5(uuid.NAMESPACE_URL,
                                              f"progtrack/import/{preview.package_id}/{entry['document_id']}")
                    intent = self.managed_files.import_intent(payload, entry, operation_id=operation_id, request=request)
                    attempt = self.managed_files.operations.publish(intent, payload, request)
                    prepared.append((intent, attempt))
                operations = self.managed_files.operations
                with request() as (connection, actor):
                    if self._check_import_target(connection, receipt, package_hash, require_empty):
                        results = [None] * len(prepared)
                    else:
                        with self.backend._allow_core_configuration_writes():
                            self._import_records(archive)
                        results = [operations.complete(connection, actor, intent, attempt) for intent, attempt in prepared]
                        mark = _placeholder(self.backend.adapter)
                        _execute(connection, f"INSERT INTO installation(key,value_json,updated_at) "
                                 f"VALUES({mark},{_json_placeholder(self.backend.adapter)},{mark})",
                                 (receipt, dumps(package_hash), now_text()))
                for (intent, attempt), result in zip(prepared, results, strict=True):
                    operations.discard_redundant_attempt(intent, attempt, request, result=result)
            return preview

    def _check_import_target(self, connection, receipt, package_hash, require_empty):
        mark = _placeholder(self.backend.adapter)
        previous = _fetchone(connection, f"SELECT value_json FROM installation WHERE key={mark}", (receipt,))
        if previous is not None:
            value = previous["value_json"]
            if not self._is_shared():
                value = loads(value)
            if value == package_hash:
                return True
            raise ConflictError("The interchange package ID was already used for different content.")
        if require_empty:
            existing = self._load_core()
            if (existing.get("animals") or existing.get("archived_animals")
                    or self.backend.records.namespace_names() or self.managed_files.list_active()):
                raise ConflictError("Interchange import requires an empty backend.")
        return False

    def _import_records(self, archive):
        animals = {}
        archived = {}
        for line in archive.read("records/animals.jsonl").decode("utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                (archived if record["archived"] else animals)[record["ipid"]] = record["payload"]
        settings_line = next(
            line for line in archive.read("records/settings.jsonl").decode("utf-8").splitlines()
            if line.strip()
        )
        domain_content = archive.read("records/domain.jsonl").decode("utf-8")
        for line in domain_content.splitlines():
            if line.strip():
                record = json.loads(line)
                self.backend.records.put(record["namespace"], record["record_id"], record["payload"])
        self._save_core({"animals": animals, "archived_animals": archived, "settings": json.loads(settings_line)["payload"]})

    def transfer_to_backend(
        self,
        target_backend: Any,
        *,
        package_path: str | Path,
        package_id: str | None = None,
    ) -> ImportPreview:
        """Export this backend, preview it, and import it atomically into target."""
        package = self.export_package(package_path, package_id=package_id)
        preview = target_backend.interchange.preview(package)
        if not preview.valid:
            return preview
        return target_backend.interchange.import_package(package, require_empty=True)
