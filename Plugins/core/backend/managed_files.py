"""Managed document/configuration-asset storage."""

from __future__ import annotations

import hashlib
import mimetypes
import os
import re
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import Any

from .errors import ValidationError
from .repositories import _execute, _fetchall, _fetchone, _placeholder, now_text


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")
_ALLOWED_STATES = {"staged", "pending", "active", "quarantined", "deleted"}


def sanitize_filename(value: str) -> str:
    name = Path(str(value or "")).name.strip()
    name = _SAFE_NAME.sub("_", name).strip(" .")
    if not name:
        raise ValidationError("Managed file requires a valid filename.")
    return name[:180]


def safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(str(value).replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValidationError(f"Unsafe managed relative path: {value!r}")
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ManagedFileService:
    def __init__(self, adapter: Any, documents_root: Path, config_assets_root: Path):
        self.adapter = adapter
        self.documents_root = documents_root
        self.config_assets_root = config_assets_root
        self.documents_root.mkdir(parents=True, exist_ok=True)
        self.config_assets_root.mkdir(parents=True, exist_ok=True)

    def _root_for(self, category: str) -> Path:
        return self.config_assets_root if category == "config-asset" else self.documents_root

    def add(
        self,
        source: str | Path,
        *,
        owner_type: str,
        owner_id: str,
        category: str = "document",
        actor: str,
        media_type: str = "",
    ) -> dict[str, Any]:
        source_path = Path(source)
        if not source_path.is_file():
            raise ValidationError(f"Managed source file does not exist: {source_path}")
        original_name = sanitize_filename(source_path.name)
        document_id = str(uuid.uuid4())
        relative = PurePosixPath(document_id) / original_name
        destination = self._root_for(category).joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        timestamp = now_text()
        size = source_path.stat().st_size
        checksum = sha256_file(source_path)
        detected_type = media_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        mark = _placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            _execute(
                connection,
                "INSERT INTO managed_files("
                "document_id,owner_type,owner_id,category,original_name,"
                "relative_path,media_type,byte_size,sha256,state,created_at,"
                f"created_by,updated_at) VALUES({','.join([mark] * 13)})",
                (
                    document_id,
                    owner_type,
                    owner_id,
                    category,
                    original_name,
                    relative.as_posix(),
                    detected_type,
                    size,
                    checksum,
                    "staged",
                    timestamp,
                    actor,
                    timestamp,
                ),
            )
        temporary = destination.with_suffix(destination.suffix + ".pending")
        shutil.copy2(source_path, temporary)
        if sha256_file(temporary) != checksum:
            temporary.unlink(missing_ok=True)
            self._set_state(document_id, "quarantined")
            raise ValidationError("Managed file checksum changed during copy.")
        self._set_state(document_id, "pending")
        os.replace(temporary, destination)
        self._set_state(document_id, "active")
        return self.get(document_id)

    def import_preserving_identity(
        self,
        source: str | Path,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        """Import a verified interchange payload without changing its document ID."""
        source_path = Path(source)
        if not source_path.is_file():
            raise ValidationError(f"Managed source file does not exist: {source_path}")
        document_id = str(metadata.get("document_id") or "").strip()
        try:
            uuid.UUID(document_id)
        except (ValueError, AttributeError) as exc:
            raise ValidationError("Managed import requires a valid document ID.") from exc
        original_name = sanitize_filename(str(metadata.get("original_name") or source_path.name))
        category = str(metadata.get("category") or "document")
        relative = PurePosixPath(document_id) / original_name
        destination = self._root_for(category).joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        checksum = sha256_file(source_path)
        expected_checksum = str(metadata.get("sha256") or "")
        size = source_path.stat().st_size
        if checksum != expected_checksum or size != int(metadata.get("byte_size") or -1):
            raise ValidationError("Managed import payload does not match its manifest.")
        timestamp = str(metadata.get("created_at") or now_text())
        updated_at = now_text()
        mark = _placeholder(self.adapter)
        values = (
            document_id,
            str(metadata.get("owner_type") or ""),
            str(metadata.get("owner_id") or ""),
            category,
            original_name,
            relative.as_posix(),
            str(metadata.get("media_type") or "application/octet-stream"),
            size,
            checksum,
            "staged",
            timestamp,
            str(metadata.get("created_by") or "interchange"),
            updated_at,
        )
        with self.adapter.transaction(write=True) as connection:
            existing = _fetchone(
                connection,
                f"SELECT document_id FROM managed_files WHERE document_id={mark}",
                (document_id,),
            )
            if existing is not None:
                raise ValidationError(f"Managed document ID already exists: {document_id}")
            _execute(
                connection,
                "INSERT INTO managed_files("
                "document_id,owner_type,owner_id,category,original_name,"
                "relative_path,media_type,byte_size,sha256,state,created_at,"
                f"created_by,updated_at) VALUES({','.join([mark] * 13)})",
                values,
            )
        temporary = destination.with_suffix(destination.suffix + ".pending")
        shutil.copy2(source_path, temporary)
        if sha256_file(temporary) != checksum:
            temporary.unlink(missing_ok=True)
            self._set_state(document_id, "quarantined")
            raise ValidationError("Managed file checksum changed during import.")
        self._set_state(document_id, "pending")
        os.replace(temporary, destination)
        self._set_state(document_id, "active")
        return self.get(document_id)

    def _set_state(self, document_id: str, state: str) -> None:
        if state not in _ALLOWED_STATES:
            raise ValidationError(f"Invalid managed-file state: {state}")
        mark = _placeholder(self.adapter)
        with self.adapter.transaction(write=True) as connection:
            _execute(
                connection,
                f"UPDATE managed_files SET state={mark},updated_at={mark} "
                f"WHERE document_id={mark}",
                (state, now_text(), document_id),
            )

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
        return self.adapter.row_to_dict(row)

    def payload_path(self, record: dict[str, Any]) -> Path:
        relative = safe_relative_path(str(record["relative_path"]))
        return self._root_for(str(record["category"])).joinpath(*relative.parts)

    def list_active(self) -> list[dict[str, Any]]:
        mark = _placeholder(self.adapter)
        with self.adapter.transaction() as connection:
            rows = _fetchall(
                connection,
                f"SELECT * FROM managed_files WHERE state={mark} ORDER BY document_id",
                ("active",),
            )
        return [self.adapter.row_to_dict(row) for row in rows]

    def list_for_owner(
        self,
        owner_type: str,
        owner_id: str,
        *,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
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

    def delete_active(self, document_id: str) -> dict[str, Any]:
        """Delete one active payload without leaving an active DB reference.

        The payload is first moved to a same-directory tombstone.  Only after
        that move succeeds is the database state changed to ``deleted``; a
        failed state update restores the original path.  Cleanup failures
        quarantine the record so it can never be presented as active.
        Callers should write a success audit entry only after this returns.
        """
        record = self.get(document_id)
        if str(record.get("state", "")) != "active":
            raise ValidationError("Only active managed documents can be deleted.")
        path = self.payload_path(record)
        if not path.is_file():
            self._set_state(document_id, "quarantined")
            raise ValidationError("The managed document payload is missing.")
        tombstone = path.with_name(path.name + f".deleting-{uuid.uuid4().hex}")
        os.replace(path, tombstone)
        try:
            self._set_state(document_id, "deleted")
        except Exception:
            try:
                os.replace(tombstone, path)
            except OSError:
                self._set_state(document_id, "quarantined")
            raise
        try:
            tombstone.unlink()
        except OSError:
            # No active reference remains; quarantine makes the cleanup issue
            # visible to reconciliation instead of silently exposing a stale
            # document.  The caller must not report this as a successful delete.
            self._set_state(document_id, "quarantined")
            raise
        return record | {"state": "deleted"}

    def remove(self, document_id: str) -> bool:
        """Legacy compatibility removal used by older callers."""
        try:
            self.delete_active(document_id)
        except KeyError:
            return False
        return True

    def reconcile(self) -> dict[str, int]:
        result = {"active": 0, "quarantined": 0, "missing": 0}
        for record in self.list_active():
            path = self.payload_path(record)
            if not path.is_file():
                self._set_state(str(record["document_id"]), "quarantined")
                result["missing"] += 1
                result["quarantined"] += 1
                continue
            if path.stat().st_size != int(record["byte_size"]) or sha256_file(path) != record["sha256"]:
                self._set_state(str(record["document_id"]), "quarantined")
                result["quarantined"] += 1
            else:
                result["active"] += 1
        return result
