# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Shared ProgTrack backend contracts and adapters."""

from typing import TYPE_CHECKING

from .errors import (
    BackendConfigurationError,
    BackendError,
    BackendSchemaError,
    BackendUnavailableError,
    ConflictError,
    ImmutableIdentityError,
    LockConflictError,
    PermissionDeniedError,
    StandaloneLockError,
    ValidationError,
)
if TYPE_CHECKING:
    from .facade import ProgTrackBackend
    from .postgresql_admin import PostgreSQLAdministrationService, PostgreSQLDatabaseInfo


def __getattr__(name):
    # Storage workers import backend submodules with a fixed I/O deadline.
    # Load the database services only for callers that request those exports.
    if name == "ProgTrackBackend":
        from .facade import ProgTrackBackend

        return ProgTrackBackend
    if name in {"PostgreSQLAdministrationService", "PostgreSQLDatabaseInfo"}:
        from . import postgresql_admin

        return getattr(postgresql_admin, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "BackendConfigurationError",
    "BackendError",
    "BackendSchemaError",
    "BackendUnavailableError",
    "ConflictError",
    "ImmutableIdentityError",
    "LockConflictError",
    "PermissionDeniedError",
    "ProgTrackBackend",
    "PostgreSQLAdministrationService",
    "PostgreSQLDatabaseInfo",
    "StandaloneLockError",
    "ValidationError",
]
