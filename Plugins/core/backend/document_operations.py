# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Recoverable publication of immutable managed-document payloads."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from .errors import BackendError, ConflictError, PermissionDeniedError, ValidationError
from .managed_storage import ManagedStorageUnavailable
from .repositories import AuditRepository, _execute, _fetchall, _fetchone, _placeholder, now_text


class DocumentPublicationError(ValidationError):
    """A safe diagnostic that can be paired with the same operation ID on retry."""

    def __init__(self, reason, operation_id):
        messages = {
            "checksum_mismatch": "The managed document does not match its expected size and checksum.",
            "publication_interrupted": "Managed document publication was interrupted. Retry this operation.",
            "unresolved_operations": "Resolve the managed-document records or operations before exporting.",
        }
        super().__init__(messages[reason])
        self.reason = reason
        self.operation_id = operation_id


def _id(value):
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        raise ValidationError("Managed documents require valid document and operation IDs.") from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


class DocumentOperations:
    def __init__(self, storage):
        self.storage = storage
        self.adapter = storage.adapter
        self.mark = _placeholder(self.adapter)
        self.row_lock = " FOR UPDATE" if self.adapter.dialect == "postgresql" else ""

    def intent(self, kind, *, owner_type, owner_id, document_id=None, operation_id=None,
               expected_revision=0, expected_checksum="", source=None, category="document",
               original_name=None, media_type="", created_at=None, created_by=None, reason="", owner_intent=None):
        from .managed_files import sanitize_filename

        operation_id = _id(operation_id) if operation_id is not None else str(uuid.uuid4())
        if kind == "create" and document_id is None:
            document_id = uuid.uuid5(uuid.NAMESPACE_URL, f"progtrack/document/{operation_id}")
        document_id = _id(document_id)
        if kind not in {"create", "replace", "retire"}:
            raise ValidationError("Unknown managed-document operation.")
        if kind != "create" and (type(expected_revision) is not int or expected_revision < 1
                                  or not expected_checksum):
            raise ConflictError("Reload the managed document before changing it.")
        intent = {
            "operation_id": operation_id, "document_id": document_id, "operation_kind": kind,
            "owner_type": str(owner_type), "owner_id": str(owner_id), "category": str(category),
            "expected_revision": expected_revision, "expected_checksum": str(expected_checksum),
            "reason": str(reason),
        }
        if owner_intent is not None:
            intent["owner_intent"] = owner_intent
        if source is not None:
            original_name = sanitize_filename(original_name or Path(source).name)
            try:
                size, checksum = self.storage.source_fingerprint(Path(source))
            except OSError:
                raise DocumentPublicationError("publication_interrupted", operation_id) from None
            intent.update({
                "original_name": original_name, "media_type": media_type or
                mimetypes.guess_type(original_name)[0] or "application/octet-stream",
                "byte_size": size, "sha256": checksum,
                "created_at": created_at, "created_by": created_by,
            })
        return intent

    @contextmanager
    def standalone_request(self, actor):
        if self.adapter.dialect == "postgresql":
            raise PermissionDeniedError("Shared managed documents require an authenticated operation.")
        with self.adapter.transaction(write=True) as connection:
            yield connection, actor

    def _require_scope(self, actor):
        self.storage._require_access(None)
        if not isinstance(actor, str) or not actor.strip():
            raise PermissionDeniedError("Managed documents require an identified actor.")

    def _operation(self, connection, operation_id, *, lock=False):
        return _fetchone(connection, f"SELECT * FROM document_operations WHERE operation_id={self.mark}"
                         + (self.row_lock if lock else ""), (operation_id,))

    def _current(self, connection, intent):
        row = _fetchone(connection, f"SELECT * FROM managed_files WHERE document_id={self.mark}"
                        + self.row_lock, (intent["document_id"],))
        if intent["operation_kind"] == "create":
            if row is not None:
                raise ConflictError("The managed document ID is already in use.")
            return None
        if row is None or (row["owner_type"], row["owner_id"]) != (intent["owner_type"], intent["owner_id"]):
            raise PermissionDeniedError("The document does not belong to the selected record.")
        if (row["state"] != "active" or row["revision"] != intent["expected_revision"]
                or row["sha256"] != intent["expected_checksum"]):
            raise ConflictError("The managed document changed. Reload before retrying.")
        if intent["operation_kind"] == "replace" and row["category"] != intent["category"]:
            raise ValidationError("A replacement must retain the managed document category.")
        return self.adapter.row_to_dict(row)

    def _match(self, operation, intent, actor):
        request_hash = hashlib.sha256(_json(intent).encode("utf-8")).hexdigest()
        if operation["actor_login"] != actor:
            raise PermissionDeniedError("This managed-document operation belongs to another actor.")
        if operation["request_sha256"] != request_hash:
            raise ConflictError("This managed-document operation ID was already used for a different request.")
        if operation["state"] == "abandoned":
            raise ConflictError("This managed-document operation was abandoned.")
        return operation

    def _reserve(self, connection, intent, actor):
        self._require_scope(actor)
        operation_id = intent["operation_id"]
        operation = self._operation(connection, operation_id, lock=True)
        if operation is None:
            self._current(connection, intent)
            timestamp = now_text()
            request_json = _json(intent)
            _execute(connection, "INSERT INTO document_operations("
                     "operation_id,document_id,operation_kind,actor_login,request_sha256,intent_json,state,"
                     "created_at,updated_at) VALUES(" + ",".join([self.mark] * 9) + ") ON CONFLICT DO NOTHING",
                     (operation_id, intent["document_id"], intent["operation_kind"], actor,
                      hashlib.sha256(request_json.encode("utf-8")).hexdigest(), request_json,
                      "prepared", timestamp, timestamp))
            operation = self._operation(connection, operation_id, lock=True)
            if operation is None:
                raise ConflictError("Another operation is pending for this managed document.")
        return self._match(operation, intent, actor)

    def _new_attempt(self, connection, intent, actor):
        operation = self._reserve(connection, intent, actor)
        if operation["state"] == "committed":
            return None
        attempt_id = uuid.uuid4().hex
        folder = PurePosixPath(intent["document_id"])
        attempt = {
            "attempt_id": attempt_id,
            "operation_id": intent["operation_id"],
            "category": intent["category"],
            "staging_path": (folder / f".{attempt_id}.pending").as_posix(),
            "final_path": (folder / f"{attempt_id}-{intent['original_name']}").as_posix(),
        }
        _execute(connection, "INSERT INTO document_publish_attempts("
                 "attempt_id,operation_id,category,staging_path,final_path,created_at) VALUES("
                 + ",".join([self.mark] * 6) + ")", (*attempt.values(), now_text()))
        self._touch(connection, intent["operation_id"])
        return attempt

    def _touch(self, connection, operation_id):
        _execute(connection, f"UPDATE document_operations SET updated_at={self.mark} WHERE operation_id={self.mark}",
                 (now_text(), operation_id))

    def _verified(self, path, intent):
        try:
            return (self.storage.payload_size(path) == intent["byte_size"]
                    and self.storage.payload_checksum(path) == intent["sha256"])
        except FileNotFoundError:
            return False
        except ManagedStorageUnavailable as error:
            if error.reason == "missing_payload":
                return False
            raise

    def _record_verification(self, intent, attempt, request):
        with request() as (connection, actor):
            operation = self._reserve(connection, intent, actor)
            mark = self.mark
            saved = _execute(connection, f"UPDATE document_publish_attempts SET verified_sha256={mark}, "
                             f"verified_byte_size={mark} WHERE attempt_id={mark} AND operation_id={mark} AND final_path={mark}",
                             (intent["sha256"], intent["byte_size"], attempt["attempt_id"],
                              intent["operation_id"], attempt["final_path"]))
            if saved.rowcount != 1:
                if operation["state"] == "committed":
                    return None
                raise DocumentPublicationError("publication_interrupted", intent["operation_id"])
            if operation["state"] != "committed":
                self._touch(connection, intent["operation_id"])
        return attempt

    def publish(self, intent, source, request):
        """Reserve durable intent, then verify an immutable candidate outside database transactions."""
        if getattr(self.adapter, "in_transaction", False):
            raise RuntimeError("Prepare managed payloads before entering the document commit transaction.")
        with request() as (connection, actor):
            operation = self._reserve(connection, intent, actor)
            if operation["state"] == "committed":
                return None
            attempts = _fetchall(connection, f"SELECT * FROM document_publish_attempts WHERE operation_id={self.mark}"
                                 " ORDER BY created_at DESC", (intent["operation_id"],))
        category = intent["category"]
        try:
            self.storage.require_storage(category, writable=True)
            for attempt in attempts:
                final = self.storage.operation_path(category, attempt["final_path"])
                if self._verified(final, intent):
                    return self._record_verification(intent, self.adapter.row_to_dict(attempt), request)
            with request() as (connection, actor):
                attempt = self._new_attempt(connection, intent, actor)
            if attempt is None:
                return None
            stage = self.storage.operation_path(category, attempt["staging_path"])
            final = self.storage.operation_path(category, attempt["final_path"])
            self.storage._make_destination_directory(stage.parent)
            self.storage.stage_payload(Path(source), stage)
            if not self._verified(stage, intent):
                raise DocumentPublicationError("checksum_mismatch", intent["operation_id"])
            self.storage.publish_payload(stage, final)
            if not self._verified(final, intent):
                raise DocumentPublicationError("checksum_mismatch", intent["operation_id"])
            return self._record_verification(intent, attempt, request)
        except ManagedStorageUnavailable as error:
            error.operation_id = intent["operation_id"]
            raise
        except OSError:
            raise DocumentPublicationError("publication_interrupted", intent["operation_id"]) from None

    @staticmethod
    def _result(operation):
        return json.loads(operation["result_json"]) | {"cleanup_pending": bool(operation["cleanup_pending"])}

    def complete(self, connection, actor, intent, attempt=None, *, update_owner=None):
        """Join the caller's authorized transaction for the CAS, owner update, and audit."""
        operation = self._reserve(connection, intent, actor)
        if operation["state"] == "committed":
            return self._result(operation)
        old = self._current(connection, intent)
        mark = self.mark
        kind = intent["operation_kind"]
        timestamp = now_text()
        if kind != "retire":
            if attempt is None or attempt["operation_id"] != intent["operation_id"]:
                raise DocumentPublicationError("publication_interrupted", intent["operation_id"])
            candidate = _fetchone(connection, "SELECT final_path, verified_sha256, verified_byte_size "
                                  "FROM document_publish_attempts "
                                  f"WHERE attempt_id={mark} AND operation_id={mark}",
                                  (attempt["attempt_id"], intent["operation_id"]))
            if (candidate is None or candidate["final_path"] != attempt["final_path"]
                    or candidate["verified_sha256"] != intent["sha256"]
                    or candidate["verified_byte_size"] != intent["byte_size"]):
                raise DocumentPublicationError("publication_interrupted", intent["operation_id"])
            content = {"relative_path": candidate["final_path"], **{
                key: intent[key] for key in ("original_name", "media_type", "byte_size", "sha256")
            }}
        if kind == "create":
            record = {key: intent[key] for key in ("document_id", "owner_type", "owner_id", "category")}
            record.update(content, state="active", created_at=intent["created_at"] or timestamp,
                          created_by=intent["created_by"] or actor, updated_at=timestamp, revision=1)
            _execute(connection, f"INSERT INTO managed_files({','.join(record)}) VALUES("
                     + ",".join([mark] * len(record)) + ")", tuple(record.values()))
        else:
            changes = {"revision": old["revision"] + 1, "updated_at": timestamp}
            if kind == "retire":
                changes["state"] = "deleted"
            else:
                changes.update(content)
            updated = _execute(connection, "UPDATE managed_files SET "
                               + ",".join(f"{column}={mark}" for column in changes)
                               + f" WHERE document_id={mark} AND revision={mark} AND sha256={mark} AND state='active'",
                               (*changes.values(), old["document_id"], old["revision"], old["sha256"]))
            if updated.rowcount != 1:
                raise ConflictError("The managed document changed. Reload before retrying.")
            record = old | changes
        result = json.loads(_json(record | {"operation_id": intent["operation_id"], "cleanup_pending": old is not None}))
        if update_owner is not None:
            update_owner(connection, result, actor)
        AuditRepository(self.adapter)._append_in_transaction(
            connection, actor_login=actor, category="documents", action=kind,
            entity_type=record["owner_type"], entity_id=record["owner_id"], correlation_id=intent["operation_id"],
            payload={"document_id": record["document_id"], "revision": record["revision"],
                     "sha256": record["sha256"], "byte_size": record["byte_size"], "result": "committed",
                     "reason": intent["reason"]},
        )
        _execute(connection, f"UPDATE document_operations SET state='committed', result_json={mark}, "
                 f"retired_path={mark}, retired_sha256={mark}, retired_byte_size={mark}, cleanup_pending={mark}, "
                 f"updated_at={mark} WHERE operation_id={mark}",
                 (_json(result), old["relative_path"] if old else None, old["sha256"] if old else None,
                  old["byte_size"] if old else None, int(old is not None), timestamp, intent["operation_id"]))
        return result

    def run(self, intent, source, request, *, update_owner=None):
        attempt = self.publish(intent, source, request) if intent["operation_kind"] != "retire" else None
        with request() as (connection, actor):
            result = self.complete(connection, actor, intent, attempt, update_owner=update_owner)
        self.discard_redundant_attempt(intent, attempt, request, result=result)
        return result

    def discard_redundant_attempt(self, intent, attempt, request, *, result=None):
        """Discard a verified candidate that lost to another attempt of the same operation."""
        if (attempt is None or (result is not None and result["relative_path"] == attempt["final_path"])
                or getattr(self.adapter, "in_transaction", False)):
            return
        try:
            with request() as (connection, actor):
                self._require_scope(actor)
                operation = self._operation(connection, intent["operation_id"], lock=True)
                if operation is None or operation["state"] != "committed":
                    return
                self._match(operation, intent, actor)
                mark = self.mark
                candidate = _fetchone(connection, f"SELECT * FROM document_publish_attempts "
                                      f"WHERE operation_id={mark} AND attempt_id={mark}",
                                      (intent["operation_id"], attempt["attempt_id"]))
                if (candidate is None or candidate["final_path"] != attempt["final_path"]
                        or candidate["category"] != intent["category"]
                        or candidate["verified_sha256"] != intent["sha256"]
                        or candidate["verified_byte_size"] != intent["byte_size"]):
                    return
                relative = candidate["final_path"]
                if self._result(operation)["relative_path"] == relative:
                    return
                reference = _fetchone(connection, f"SELECT 1 FROM managed_files WHERE relative_path={mark} "
                                      f"UNION ALL SELECT 1 FROM document_operations WHERE retired_path={mark} LIMIT 1",
                                      (relative, relative))
                if reference is not None:
                    return
                # Keep the schema guard through the bounded, byte-verified unlink.
                self.storage.require_storage(intent["category"], writable=True)
                path = self.storage.operation_path(intent["category"], relative)
                self.storage.remove_verified_retired_payload(path, intent["byte_size"], intent["sha256"])
        except (BackendError, OSError):
            # The committed receipt still holds. A failed cleanup remains journaled for reconciliation.
            return

    def cleanup_retired(self, operation_id, request, *, expected_revision):
        return self._cleanup_retired(operation_id, request, expected_revision=expected_revision)

    def repair_retired(self, operation_id, request, *, expected_revision):
        return self._cleanup_retired(operation_id, request, expected_revision=expected_revision,
                                     authorize_repair=self.storage.require_repair_scope)

    def _cleanup_retired(self, operation_id, request, *, expected_revision, authorize_repair=None):
        """Remove only a journaled retired version after its outer commit is visible."""
        if getattr(self.adapter, "in_transaction", False):
            raise RuntimeError("Managed payload cleanup requires a committed transaction.")
        operation_id = _id(operation_id)
        with request() as (connection, actor):
            self._require_scope(actor)
            if authorize_repair is not None:
                authorize_repair()
            entry = self._operation(connection, operation_id, lock=True)
            if entry is None or entry["state"] != "committed" or entry["retired_path"] is None:
                raise ConflictError("The document operation has no committed retired payload.")
            if entry["actor_login"] != actor and authorize_repair is None:
                raise PermissionDeniedError("This managed-document operation belongs to another actor.")
            result = self._result(entry)
            if result["revision"] != expected_revision:
                raise ConflictError("The managed document cleanup request is stale.")
            if not result["cleanup_pending"]:
                return result
            mark = self.mark
            current = _fetchone(connection, f"SELECT state, revision FROM managed_files WHERE document_id={mark}",
                                (result["document_id"],))
            active = _fetchone(connection, f"SELECT document_id FROM managed_files WHERE relative_path={mark} "
                               "AND state='active'", (entry["retired_path"],))
            if (current is None or current["revision"] < expected_revision or active is not None
                    or (entry["operation_kind"] == "retire" and current["state"] != "deleted")):
                raise ConflictError("The retired path is still in use or its document state is unresolved.")
            # The request retains the shared schema guard through the bounded
            # unlink. Restore can only reuse this path after the cleanup result
            # is committed, and pending cleanup paths remain fenced.
            try:
                self.storage.require_storage(result["category"], writable=True)
                path = self.storage.operation_path(result["category"], entry["retired_path"])
                removed = self.storage.remove_verified_retired_payload(
                    path, entry["retired_byte_size"], entry["retired_sha256"],
                )
                if not removed:
                    return result | {"cleanup_reason": "checksum_mismatch"}
            except ManagedStorageUnavailable as error:
                return result | {"cleanup_reason": error.reason}
            except OSError:
                return result | {"cleanup_reason": "publication_interrupted"}
            updated = _execute(connection, f"UPDATE document_operations SET cleanup_pending=0, updated_at={mark} "
                               f"WHERE operation_id={mark} AND state='committed' AND cleanup_pending=1",
                               (now_text(), operation_id))
            if updated.rowcount:
                AuditRepository(self.adapter)._append_in_transaction(
                    connection, actor_login=actor, category="documents", action="cleanup",
                    entity_type=result["owner_type"], entity_id=result["owner_id"], correlation_id=operation_id,
                    payload={"document_id": result["document_id"], "revision": result["revision"],
                             "sha256": entry["retired_sha256"], "byte_size": entry["retired_byte_size"],
                             "result": "cleaned"},
                )
        return result | {"cleanup_pending": False}

    def abandon_prepared(self, operation_id, request, *, expected_updated_at):
        operation_id = _id(operation_id)
        with request() as (connection, actor):
            self._require_scope(actor)
            self.storage.require_repair_scope()
            entry = self._operation(connection, operation_id, lock=True)
            if entry is None or entry["state"] != "prepared" or entry["updated_at"] != expected_updated_at:
                raise ConflictError("The prepared document operation changed. Scan storage again.")
            _execute(connection, f"UPDATE document_operations SET state='abandoned', updated_at={self.mark} "
                     f"WHERE operation_id={self.mark}", (now_text(), operation_id))
            self._repair_audit(connection, actor, entry, "abandon")

    def discard_abandoned_stage(self, operation_id, attempt_id, request):
        if getattr(self.adapter, "in_transaction", False):
            raise RuntimeError("Managed payload cleanup requires a committed transaction.")
        operation_id = _id(operation_id)
        with request() as (connection, actor):
            self._require_scope(actor)
            self.storage.require_repair_scope()
            entry = self._operation(connection, operation_id)
            candidate = _fetchone(connection, f"SELECT * FROM document_publish_attempts "
                                  f"WHERE operation_id={self.mark} AND attempt_id={self.mark}",
                                  (operation_id, str(attempt_id)))
            stale_before = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
            if entry is None or entry["state"] == "prepared" or candidate is None or candidate["created_at"] >= stale_before:
                raise ConflictError("Only aged staging files from finished or abandoned operations may be removed.")
            active = _fetchone(connection, f"SELECT document_id FROM managed_files WHERE relative_path={self.mark} "
                               "AND state='active'", (candidate["staging_path"],))
            if active is not None:
                raise ConflictError("The staging path is currently used by an active document.")
            self._repair_audit(connection, actor, entry, "discard_stage_requested", attempt_id=str(attempt_id))
            self.storage.require_storage(candidate["category"], writable=True)
            path = self.storage.operation_path(candidate["category"], candidate["staging_path"])
            self.storage._unlink_payload(path, missing_ok=True)
            self._repair_audit(connection, actor, entry, "discard_stage", attempt_id=str(attempt_id))

    def _repair_audit(self, connection, actor, entry, action, **details):
        intent = json.loads(entry["intent_json"])
        AuditRepository(self.adapter)._append_in_transaction(
            connection, actor_login=actor, category="documents", action=action,
            entity_type=intent["owner_type"], entity_id=intent["owner_id"], correlation_id=entry["operation_id"],
            payload={"document_id": intent["document_id"], "sha256": intent.get("sha256"), **details},
        )
