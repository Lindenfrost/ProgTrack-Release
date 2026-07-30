"""Shared ProgTrack backend contracts and adapters."""

from .errors import (
    BackendConfigurationError,
    BackendError,
    ConflictError,
    ImmutableIdentityError,
    LockConflictError,
    PermissionDeniedError,
    ValidationError,
)
from .facade import ProgTrackBackend

__all__ = [
    "BackendConfigurationError",
    "BackendError",
    "ConflictError",
    "ImmutableIdentityError",
    "LockConflictError",
    "PermissionDeniedError",
    "ProgTrackBackend",
    "ValidationError",
]
