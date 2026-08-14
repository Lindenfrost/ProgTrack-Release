"""Shared ProgTrack backend contracts and adapters."""

from .errors import (
    BackendConfigurationError,
    BackendError,
    ConflictError,
    ImmutableIdentityError,
    LockConflictError,
    PermissionDeniedError,
    StandaloneLockError,
    ValidationError,
)
from .facade import ProgTrackBackend
from .postgresql_admin import PostgreSQLAdministrationService, PostgreSQLDatabaseInfo

__all__ = [
    "BackendConfigurationError",
    "BackendError",
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
