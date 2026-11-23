# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Managed document/configuration-asset storage."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .document_operations import DocumentOperations, DocumentPublicationError
from .errors import BackendError, PermissionDeniedError, ValidationError
from .managed_storage import ManagedStorageUnavailable, require_storage_access
from .repositories import _fetchall, _fetchone, _placeholder


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_WINDOWS_DEVICE_NAME = re.compile(r"(?:con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³]|conin\$|conout\$)(?:\.|$)", re.I)


def sanitize_filename(value: str) -> str:
    name = Path(str(value or "")).name.strip()
    name = _SAFE_NAME.sub("_", name).strip(" .")
    if not name:
        raise ValidationError("Managed file requires a valid filename.")
    return name[:180]


def safe_relative_path(value: str) -> PurePosixPath:
    raw = str(value).replace("\\", "/")
    path = PurePosixPath(raw)
    if (path.is_absolute() or not path.parts or PureWindowsPath(raw).drive
            or any(part in {"", ".", ".."} or part.endswith((".", " ")) or _WINDOWS_DEVICE_NAME.match(part)
                   or any(ord(char) < 32 or char in ':*?"<>|' for char in part)
                   for part in raw.split("/"))):
        raise ValidationError("Unsafe managed relative path.")
    return path


def sha256_file(path: Path) -> str:
    return payload_fingerprint(path)[1]


def payload_fingerprint(path: Path, report=None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
            if report is not None:
                report({"bytes": size})
    return size, digest.hexdigest()


def stage_managed_payload(source: Path, target: Path, report=None) -> None:
    size = 0
    with source.open("rb") as reader, target.open("xb") as writer:
        for chunk in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(chunk)
            size += len(chunk)
            if report is not None:
                report({"bytes": size})
        writer.flush()
        try:
            os.fsync(writer.fileno())
        except OSError as error:
            if error.errno not in {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP}:
                raise


def publish_managed_payload(source: Path, target: Path, report=None) -> None:
    """Publish on one filesystem with a non-overwriting fallback for remote mounts."""
    if os.name == "nt":
        os.rename(source, target)
        return
    try:
        os.link(source, target, follow_symlinks=False)
    except OSError as error:
        if error.errno not in {errno.EACCES, errno.EPERM, errno.ENOTSUP, errno.ENOSYS, errno.EXDEV}:
            raise
        stage_managed_payload(source, target, report)
    source.unlink()


def remove_verified_payload(path: Path, size: int, checksum: str, report=None) -> bool:
    try:
        actual = payload_fingerprint(path, report)
    except FileNotFoundError:
        return True
    if actual != (size, checksum):
        return False
    path.unlink(missing_ok=True)
    return True


def confined_payload_path(root: Path, path: Path) -> Path:
    """Reject path components that could redirect reads or writes below the root."""
    if not path.is_relative_to(root) or path == root:
        raise ManagedStorageUnavailable("invalid_path")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            attributes = current.lstat()
        except FileNotFoundError:
            break
        if (stat.S_ISLNK(attributes.st_mode)
                or getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
            raise ManagedStorageUnavailable("invalid_path")
    return path


def storage_entries(root: Path):
    """Walk a category without traversing symlinks or junctions."""
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                relative = path.relative_to(root).as_posix()
                try:
                    attributes = entry.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if (entry.is_symlink()
                        or getattr(attributes, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    yield relative, "unsafe"
                elif stat.S_ISDIR(attributes.st_mode):
                    pending.append(path)
                    yield relative, "directory"
                elif stat.S_ISREG(attributes.st_mode):
                    yield relative, "file"
                else:
                    yield relative, "unsafe"


class ManagedFileService:
    def __init__(self, adapter: Any, documents_root: Path | None, config_assets_root: Path | None,
                 authorize=None, *, create_roots=True, storage_access=None, storage_client=None,
                 authorize_repair=None):
        self.adapter = adapter
        self.authorize = authorize
        self.authorize_repair = authorize_repair
        self.storage_access = storage_access
        self.storage_client = storage_client
        self.documents_root = documents_root
        self.config_assets_root = config_assets_root
        self._viewer_files = None
        self.operations = DocumentOperations(self) if adapter is not None else None
        if create_roots:
            self.documents_root.mkdir(parents=True, exist_ok=True)
            self.config_assets_root.mkdir(parents=True, exist_ok=True)

    def _require_access(self, owner_type):
        if self.authorize is not None and (owner_type is None or owner_type in {
            "animal-medical", "project-document", "project-sop",
        }):
            self.authorize("managed_files")

    def require_repair_scope(self):
        if self.authorize_repair is None:
            raise PermissionDeniedError("Managed storage repair requires a backend maintenance request.")
        self.authorize_repair()

    def _root_for(self, category: str) -> Path:
        root = self.config_assets_root if category == "config-asset" else self.documents_root
        if root is None:
            raise ManagedStorageUnavailable("invalid_path")
        return root

    def require_storage(self, category=None, *, writable=False):
        if self.storage_access is not None:
            self.storage_access(writable=writable)
        if self.storage_client is not None:
            if category is None:
                children = ("documents", "config-assets")
            else:
                children = ("config-assets" if category == "config-asset" else "documents",)
            self.storage_client.check(writable=writable, children=children)
            return
        for kind in (category,) if category is not None else ("document", "config-asset"):
            require_storage_access(self._root_for(kind).absolute(), writable=writable)

    def payload_size(self, path: Path) -> int | None:
        if self.storage_client is not None:
            return self.storage_client.file("stat", path)
        return path.stat().st_size if path.is_file() else None

    def source_fingerprint(self, source: Path) -> tuple[int, str]:
        if self.storage_client is not None:
            return self.storage_client.source_fingerprint(source)
        return payload_fingerprint(source)

    def operation_path(self, category, value) -> Path:
        root = self._root_for(category)
        relative = safe_relative_path(value)
        path = root.joinpath(*relative.parts)
        if self.storage_client is None:
            confined_payload_path(root.parent, path)
        return path

    def stage_payload(self, source: Path, target: Path) -> None:
        if self.storage_client is not None:
            self.storage_client.file("stage", target, source=source)
        else:
            stage_managed_payload(source, target)

    def publish_payload(self, source: Path, target: Path) -> None:
        if self.storage_client is not None:
            self.storage_client.file("publish", target, source=source)
        else:
            publish_managed_payload(source, target)

    def payload_checksum(self, path: Path) -> str:
        if self.storage_client is not None:
            return self.storage_client.file("digest", path)
        return sha256_file(path)

    def _make_destination_directory(self, directory: Path) -> None:
        if self.storage_client is not None:
            self.storage_client.file("mkdir", directory)
        else:
            directory.mkdir(parents=self.storage_access is None, exist_ok=True)

    def _unlink_payload(self, path: Path, *, missing_ok=False) -> None:
        if self.storage_client is not None:
            self.storage_client.file("unlink", path, missing_ok=missing_ok)
        else:
            path.unlink(missing_ok=missing_ok)

    def remove_verified_retired_payload(self, path, size, checksum):
        if self.storage_client is not None:
            return self.storage_client.remove_retired(path, size, checksum)
        return remove_verified_payload(path, size, checksum)

    def operation_request(self, actor, request=None):
        return request if request is not None else lambda: self.operations.standalone_request(actor)

    def prepare_document(self, kind, source, request, **intent_arguments):
        # Confirm the connected backend and effective actor before touching storage.
        with request() as (_connection, actor):
            self.operations._require_scope(actor)
        return self.operations.intent(kind, source=source, **intent_arguments)

    def add(self, source, *, owner_type, owner_id, actor="", category="document", media_type="",
            operation_id=None, request=None, update_owner=None, owner_intent=None):
        request = self.operation_request(actor, request)
        intent = self.prepare_document(
            "create", source, request, owner_type=owner_type, owner_id=owner_id, category=category,
            media_type=media_type, operation_id=operation_id, owner_intent=owner_intent,
        )
        return self.operations.run(intent, source, request, update_owner=update_owner)

    def replace(self, source, *, document_id, owner_type, owner_id, expected_revision, expected_checksum,
                actor="", category="document", operation_id=None, request=None, update_owner=None, owner_intent=None):
        request = self.operation_request(actor, request)
        intent = self.prepare_document(
            "replace", source, request, owner_type=owner_type, owner_id=owner_id, category=category,
            document_id=document_id, expected_revision=expected_revision, expected_checksum=expected_checksum,
            operation_id=operation_id, owner_intent=owner_intent,
        )
        result = self.operations.run(intent, source, request, update_owner=update_owner)
        return self.finish_retirement(result, request)

    def import_intent(self, source, metadata, *, operation_id, request):
        intent = self.prepare_document(
            "create", source, request, owner_type=metadata["owner_type"], owner_id=metadata["owner_id"],
            category=metadata["category"], document_id=metadata["document_id"], operation_id=operation_id,
            original_name=metadata["original_name"], media_type=metadata["media_type"],
            created_at=metadata["created_at"], created_by=metadata["created_by"],
        )
        if intent["sha256"] != metadata["sha256"] or intent["byte_size"] != int(metadata["byte_size"]):
            raise DocumentPublicationError("checksum_mismatch", intent["operation_id"])
        return intent

    def get(self, document_id: str) -> dict[str, Any]:
        mark = _placeholder(self.adapter)
        with self.adapter.transaction() as connection:
            row = _fetchone(
                connection,
                f"SELECT * FROM managed_files WHERE document_id={mark}",
                (document_id,),
            )
        if row is None:
            raise KeyError(document_id)
        self._require_access(row["owner_type"])
        return self.adapter.row_to_dict(row)

    def payload_path(self, record: dict[str, Any]) -> Path:
        self.require_storage(str(record["category"]))
        return self.operation_path(record["category"], record["relative_path"])

    def available_payload_path(self, record: dict[str, Any]) -> Path:
        if record["state"] != "active":
            raise ManagedStorageUnavailable("missing_payload")
        path = self.payload_path(record)
        size = self.payload_size(path)
        if size is None:
            raise ManagedStorageUnavailable("missing_payload")
        if size != record["byte_size"] or self.payload_checksum(path) != record["sha256"]:
            raise DocumentPublicationError("checksum_mismatch", record.get("operation_id"))
        return path

    def viewer_payload_path(self, record):
        """Give the OS viewer a verified local copy when a storage worker owns access."""
        if self.storage_client is None:
            return self.available_payload_path(record)
        name = safe_relative_path(sanitize_filename(record["original_name"]))
        if self._viewer_files is None:
            self._viewer_files = tempfile.TemporaryDirectory(prefix="progtrack-open-", ignore_cleanup_errors=True)
        folder = Path(tempfile.mkdtemp(dir=self._viewer_files.name))
        destination = folder / name.name
        try:
            self.copy_verified_payload(record, destination)
        except BaseException:
            try:
                folder.rmdir()
            except OSError:
                pass
            raise
        return destination

    def close(self):
        if self._viewer_files is not None:
            self._viewer_files.cleanup()
            self._viewer_files = None

    def copy_payload_to(self, source: Path, destination: Path) -> None:
        """Read a managed payload into an explicitly chosen output or local staging file."""
        if self.storage_client is not None:
            with tempfile.TemporaryDirectory(prefix=".progtrack-payload-", dir=destination.parent) as staging:
                temporary = Path(staging).absolute() / "payload"
                self.storage_client.download(source, temporary)
                os.replace(temporary, destination)
        else:
            shutil.copy2(source, destination)

    def copy_verified_payload(self, record, destination):
        if record["state"] != "active":
            raise ManagedStorageUnavailable("missing_payload")
        source = self.payload_path(record)
        with tempfile.TemporaryDirectory(prefix=".progtrack-verified-", dir=destination.parent) as folder:
            staged = Path(folder) / "verified"
            self.copy_payload_to(source, staged)
            if payload_fingerprint(staged) != (record["byte_size"], record["sha256"]):
                raise DocumentPublicationError("checksum_mismatch", record.get("operation_id"))
            os.replace(staged, destination)

    def list_active(self) -> list[dict[str, Any]]:
        self._require_access(None)
        mark = _placeholder(self.adapter)
        with self.adapter.transaction() as connection:
            rows = _fetchall(
                connection,
                f"SELECT * FROM managed_files WHERE state={mark} ORDER BY document_id",
                ("active",),
            )
        return [self.adapter.row_to_dict(row) for row in rows]

    def export_records(self):
        """Select complete records and reject interrupted or unresolved journal state."""
        self._require_access(None)
        with self.adapter.transaction() as connection:
            unresolved = _fetchone(connection, """
                SELECT (SELECT COUNT(*) FROM managed_files WHERE state IN ('staged','pending','quarantined'))
                     + (SELECT COUNT(*) FROM document_operations WHERE state='prepared' OR cleanup_pending=1)
                     AS unresolved
            """)["unresolved"]
            if unresolved:
                raise DocumentPublicationError("unresolved_operations", None)
            rows = _fetchall(connection, "SELECT * FROM managed_files WHERE state='active' ORDER BY document_id")
        return [self.adapter.row_to_dict(row) for row in rows]

    def list_for_owner(
        self,
        owner_type: str,
        owner_id: str,
        *,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        self._require_access(owner_type)
        mark = _placeholder(self.adapter)
        sql = (
            f"SELECT * FROM managed_files WHERE state={mark} "
            f"AND owner_type={mark} AND owner_id={mark}"
        )
        params: tuple[Any, ...] = ("active", owner_type, owner_id)
        if category is not None:
            sql += f" AND category={mark}"
            params += (category,)
        sql += " ORDER BY original_name,document_id"
        with self.adapter.transaction() as connection:
            rows = _fetchall(connection, sql, params)
        return [self.adapter.row_to_dict(row) for row in rows]

    def delete_active(self, document_id, *, owner_type, owner_id, expected_revision, expected_checksum,
                      actor="", operation_id=None, reason="", request=None, update_owner=None, owner_intent=None):
        request = self.operation_request(actor, request)
        intent = self.operations.intent(
            "retire", owner_type=owner_type, owner_id=owner_id, document_id=document_id,
            expected_revision=expected_revision, expected_checksum=expected_checksum, operation_id=operation_id,
            reason=reason, owner_intent=owner_intent,
        )
        result = self.operations.run(intent, None, request, update_owner=update_owner)
        return self.finish_retirement(result, request)

    def finish_retirement(self, result, request):
        if not result["cleanup_pending"] or getattr(self.adapter, "in_transaction", False):
            return result
        try:
            return self.operations.cleanup_retired(
                result["operation_id"], request, expected_revision=result["revision"],
            )
        except BackendError:
            # Logical retirement was committed. A subsequent request can retry cleanup.
            return result | {"cleanup_reason": "publication_interrupted"}

    def reconcile(self, *, request=None):
        """Compare a metadata snapshot with the client root without changing either."""
        try:
            self.require_storage()
            if request is None:
                options = {"snapshot": True} if self.adapter.dialect == "postgresql" else {}
                with self.adapter.transaction(**options) as connection:
                    inventory = self._inventory(connection)
            else:
                with request() as (connection, _actor):
                    inventory = self._inventory(connection)
            findings = self._findings(*inventory)
            self.require_storage()
            return {"status": "available", "findings": findings}
        except ManagedStorageUnavailable as error:
            return {"status": "storage_unavailable", "reason": error.reason, "findings": []}
        except OSError:
            return {"status": "storage_unavailable", "reason": "not_accessible", "findings": []}

    def _inventory(self, connection):
        self._require_access(None)
        return tuple([self.adapter.row_to_dict(row) for row in _fetchall(connection, statement)] for statement in (
            "SELECT * FROM managed_files ORDER BY document_id",
            "SELECT * FROM document_operations ORDER BY operation_id",
            "SELECT * FROM document_publish_attempts ORDER BY attempt_id",
        ))

    def _findings(self, records, operations, attempts):
        findings = []
        physical = {}
        referenced = set()
        active_by_folder = {}
        stale_before = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        for category in ("document", "config-asset"):
            if self.storage_client is None:
                entries = storage_entries(self._root_for(category))
            else:
                entries = self.storage_client.list_entries("config-assets" if category == "config-asset" else "documents")
            physical.update({(category, path): kind for path, kind in entries if kind != "directory"})

        def add(reason, key, owner, **details):
            category, path = key
            findings.append({"reason": reason, "category": category, "relative_path": path,
                             "document_id": owner.get("document_id"), "operation_id": owner.get("operation_id"),
                             **details})

        for record in records:
            category = "config-asset" if record["category"] == "config-asset" else "document"
            key = category, record["relative_path"]
            try:
                safe_relative_path(key[1])
            except ValidationError:
                add("unsafe_path", (category, ""), record)
                continue
            if record["state"] != "deleted":
                referenced.add(key)
            if record["state"] != "active":
                if record["state"] != "deleted":
                    add("legacy_incomplete", key, record)
                continue
            active_by_folder[(category, key[1].split("/", 1)[0])] = record
            parents = safe_relative_path(key[1]).parents
            if physical.get(key) == "unsafe" or any(physical.get((category, parent.as_posix())) == "unsafe"
                                                    for parent in parents):
                add("unsafe_path", key, record)
            elif key not in physical:
                add("missing_payload", key, record)
            else:
                path = self.operation_path(*key)
                try:
                    if not self.operations._verified(path, record):
                        add("checksum_mismatch", key, record)
                except ManagedStorageUnavailable as error:
                    if error.reason != "invalid_path":
                        raise
                    add("unsafe_path", key, record)

        by_id = {operation["operation_id"]: operation for operation in operations}
        for operation in operations:
            if operation["state"] == "prepared":
                intent = json.loads(operation["intent_json"])
                add("pending_operation", (intent["category"], ""), operation, updated_at=operation["updated_at"])
            elif operation["cleanup_pending"]:
                result = json.loads(operation["result_json"])
                key = result["category"], operation["retired_path"]
                referenced.add(key)
                add("cleanup_pending", key, operation, revision=result["revision"])
        for attempt in attempts:
            operation = by_id[attempt["operation_id"]]
            for column in ("staging_path", "final_path"):
                key = attempt["category"], attempt[column]
                if key in referenced or key not in physical:
                    continue
                referenced.add(key)
                if physical[key] == "unsafe":
                    add("unsafe_path", key, operation)
                elif column == "staging_path":
                    reason = "stale_staging" if attempt["created_at"] < stale_before else "staging"
                    add(reason, key, operation, attempt_id=attempt["attempt_id"])
                else:
                    reason = "pending_payload" if operation["state"] == "prepared" else "unreferenced_attempt"
                    add(reason, key, operation)
        for key, kind in sorted(physical.items()):
            if key in referenced:
                continue
            try:
                safe_relative_path(key[1])
            except ValidationError:
                kind = "unsafe"
            owner = active_by_folder.get((key[0], key[1].split("/", 1)[0]), {})
            if kind == "unsafe":
                add("unsafe_path", key, owner)
            elif key[1].endswith(".pending"):
                add("unreferenced_staging", key, owner)
            else:
                add("duplicate_payload" if owner else "unreferenced_payload", key, owner)
        return findings
