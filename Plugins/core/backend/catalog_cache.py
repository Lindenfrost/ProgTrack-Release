# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Client-local display cache. No server authority or secret lives here."""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from hashlib import file_digest, sha256
import json
import os
from pathlib import Path
import tempfile

from .catalog_revision import CatalogRevision
from .schema import BACKEND_CONTRACT, SCHEMA_VERSION
from .postgresql_admin import PostgreSQLDatabaseInfo


_FORMAT = 1
_MAX_CACHE_BYTES = 4 * 1024 * 1024
_UNREAD = object()


@dataclass(frozen=True)
class CatalogSnapshot:
    rows: tuple[PostgreSQLDatabaseInfo, ...]
    revision: CatalogRevision | None
    discovered_at: str


def _file_identity(value, *, public):
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    try:
        if public:
            with path.open("rb") as file:
                return str(path), file_digest(file, "sha256").hexdigest()
        attributes = path.stat()
        return str(path), attributes.st_dev, attributes.st_ino, attributes.st_size, attributes.st_mtime_ns
    except OSError:
        return str(path), "unavailable"


def context_key(configuration, settings, draft_generation=None) -> str:
    context = [
        settings.host.strip().casefold(), int(settings.port),
        settings.server_name.strip().casefold(), settings.sslmode, settings.user.strip(),
        _file_identity(settings.ca_file, public=True), _file_identity(settings.client_cert_file, public=True),
        _file_identity(settings.client_key_file, public=False),
        configuration.catalog_credential_generation(), draft_generation, BACKEND_CONTRACT, SCHEMA_VERSION,
    ]
    return sha256(json.dumps(context, separators=(",", ":")).encode()).hexdigest()


def read_snapshot(path: Path, context: str) -> CatalogSnapshot | None:
    try:
        with path.open("rb") as file:
            content = file.read(_MAX_CACHE_BYTES + 1)
        if len(content) > _MAX_CACHE_BYTES:
            return None
        document = json.loads(content)
        if type(document["format"]) is not int or document["format"] != _FORMAT or document["context"] != context:
            return None
        observed = document["discovered_at"]
        if datetime.fromisoformat(observed).utcoffset() is None:
            return None
        item = document["revision"]
        revision = CatalogRevision(**item) if item is not None else None
        if revision is not None and (not isinstance(revision.epoch, str) or not revision.epoch
                or type(revision.revision) is not int or revision.revision < 0 or revision.dirty is not False):
            return None
        rows = tuple(PostgreSQLDatabaseInfo(**row) for row in document["rows"])
        if len({row.name for row in rows}) != len(rows) or not all(
            isinstance(row.name, str) and bool(row.name) and isinstance(row.owner, str)
            and type(row.size_bytes) is int and row.size_bytes >= 0
            and type(row.allow_connections) is bool and type(row.is_template) is bool
            and (row.compatible is None or type(row.compatible) is bool)
            for row in rows
        ):
            return None
        return CatalogSnapshot(rows, revision, observed)
    except (OSError, AttributeError, KeyError, TypeError, ValueError):
        return None


def write_snapshot(path: Path, context: str, snapshot: CatalogSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, filename = tempfile.mkstemp(dir=path.parent, prefix=".catalog-", suffix=".tmp")
    pending = Path(filename)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump({"format": _FORMAT, "context": context, **asdict(snapshot)}, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def discover_snapshot(service, before=_UNREAD, *, cancelled=None) -> tuple[CatalogSnapshot, bool]:
    # The compatibility probes run across databases. Only mark this snapshot
    # current when the same clean revision brackets that one full discovery.
    if before is _UNREAD:
        before = service.catalog_revision()
    rows = tuple(service.list_databases())
    if cancelled is not None and cancelled():
        raise TimeoutError("The catalog request was cancelled.")
    after = service.catalog_revision()
    revision = after if before == after and after is not None and not after.dirty else None
    coherent = before == after and (after is None or not after.dirty)
    return CatalogSnapshot(rows, revision, datetime.now(timezone.utc).isoformat()), coherent
