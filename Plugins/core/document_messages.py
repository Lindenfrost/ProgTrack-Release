# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Localized managed-document outcomes and retry identity for UI callers."""

from uuid import uuid4

from .backend.document_operations import DocumentPublicationError
from .backend.errors import ConflictError
from .backend.managed_storage import ManagedStorageUnavailable


def document_error(messages, error):
    if isinstance(error, ManagedStorageUnavailable):
        if error.reason == "missing_payload":
            return messages.get("backend.documents.missing_payload", "The managed document file is missing.")
        return messages.get("backend.storage.context_error", str(error))
    if isinstance(error, DocumentPublicationError):
        return messages.get(f"backend.documents.{error.reason}", str(error))
    if isinstance(error, ConflictError):
        return messages.get("backend.documents.conflict", str(error))
    return str(error)


def document_cleanup_notice(messages):
    return messages.get("backend.documents.cleanup_pending",
                        "The document change was saved. The retired payload still needs cleanup.")


def document_retry_id(widget, key):
    operations = getattr(widget, "_managed_document_retries", None)
    if operations is None:
        operations = widget._managed_document_retries = {}
    if key not in operations:
        operations[key] = str(uuid4())
    return operations[key]


def complete_document_retry(widget, key):
    widget._managed_document_retries.pop(key, None)
