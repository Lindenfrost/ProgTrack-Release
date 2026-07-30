"""Versioned backend-neutral ProgTrack interchange packages."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from .errors import ConflictError, ValidationError
from .json_codec import dumps, loads
from .managed_files import ManagedFileService, safe_relative_path, sha256_file
from .repositories import _fetchall


INTERCHANGE_SCHEMA = "progtrack-interchange/1"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_member(name: str) -> PurePosixPath:
    return safe_relative_path(name)


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

    def export_package(
        self,
        target: str | Path,
        *,
        package_id: str | None = None,
        created_at: str | None = None,
    ) -> Path:
        target_path = Path(target)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        package_id = package_id or str(uuid.uuid4())
        timestamp = created_at or datetime.now(timezone.utc).isoformat()
        snapshot = self.backend.load_core_data()
        domain_rows = self.backend.records.list_all() if hasattr(self.backend.records, "list_all") else {}
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
                    "payload": payload,
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
        payload_entries: list[tuple[str, Path, dict[str, Any]]] = []
        for record in self.managed_files.list_active():
            payload_path = self.managed_files.payload_path(record)
            package_name = (
                "payload/config-assets/" if record["category"] == "config-asset"
                else "payload/documents/"
            ) + f"{record['document_id']}/{record['original_name']}"
            payload_entries.append((package_name, payload_path, record))

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
                        key: record[key]
                        for key in (
                            "document_id", "owner_type", "owner_id", "category",
                            "original_name", "media_type", "byte_size", "sha256",
                            "created_at", "created_by",
                        )
                    },
                    "package_path": name,
                }
                for name, _path, record in payload_entries
            ],
        }
        temp_target = target_path.with_suffix(target_path.suffix + ".tmp")
        with zipfile.ZipFile(temp_target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
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
            for name, source, _record in payload_entries:
                archive.write(source, name)
        os.replace(temp_target, target_path)
        return target_path

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
                for name in names:
                    _safe_member(name)
                manifest = json.loads(archive.read("manifest.json"))
                schema = str(manifest.get("schema") or "")
                package_id = str(manifest.get("package_id") or "")
                if schema != INTERCHANGE_SCHEMA:
                    errors.append(f"Unsupported interchange schema: {schema}")
                for record_name, expected in manifest.get("records", {}).items():
                    data = archive.read(record_name)
                    if _sha256_bytes(data) != expected.get("sha256"):
                        errors.append(f"Checksum mismatch: {record_name}")
                for entry in manifest.get("managed_files", []):
                    payload = archive.read(entry["package_path"])
                    if len(payload) != int(entry["byte_size"]):
                        errors.append(f"Size mismatch: {entry['package_path']}")
                    if _sha256_bytes(payload) != entry["sha256"]:
                        errors.append(f"Checksum mismatch: {entry['package_path']}")
                counts.update({
                    key: int(value)
                    for key, value in manifest.get("counts", {}).items()
                    if key in counts
                })
        except (OSError, zipfile.BadZipFile, KeyError, ValueError, json.JSONDecodeError) as exc:
            errors.append(str(exc))
        return ImportPreview(package_id, schema, counts, errors, warnings)

    def import_package(self, package: str | Path, *, require_empty: bool = True) -> ImportPreview:
        preview = self.preview(package)
        if not preview.valid:
            return preview
        existing = self.backend.load_core_data()
        if require_empty and (
            existing.get("animals")
            or existing.get("archived_animals")
            or self.backend.records.namespace_names()
        ):
            raise ConflictError("Interchange import requires an empty backend.")
        with zipfile.ZipFile(package) as archive:
            animals: dict[str, Any] = {}
            archived: dict[str, Any] = {}
            for line in archive.read("records/animals.jsonl").decode("utf-8").splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                (archived if record["archived"] else animals)[record["ipid"]] = record["payload"]
            settings_line = next(
                line for line in archive.read("records/settings.jsonl").decode("utf-8").splitlines()
                if line.strip()
            )
            settings = json.loads(settings_line)["payload"]
            self.backend.save_core_data({
                "animals": animals,
                "archived_animals": archived,
                "settings": settings,
            })
            domain_content = archive.read("records/domain.jsonl").decode("utf-8")
            for line in domain_content.splitlines():
                if not line.strip():
                    continue
                record = json.loads(line)
                self.backend.records.put(
                    record["namespace"], record["record_id"], record["payload"]
                )
            # Payload import intentionally uses the same managed roots and safe
            # paths, but preserves document IDs through metadata registration.
            manifest = json.loads(archive.read("manifest.json"))
            for entry in manifest.get("managed_files", []):
                suffix = Path(entry["original_name"]).suffix
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp:
                    temp.write(archive.read(entry["package_path"]))
                    temp_path = Path(temp.name)
                try:
                    self.managed_files.import_preserving_identity(temp_path, entry)
                finally:
                    temp_path.unlink(missing_ok=True)
        return preview
