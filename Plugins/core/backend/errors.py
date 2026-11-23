# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Typed backend errors shared by SQLite and PostgreSQL."""


class BackendError(RuntimeError):
    pass


class BackendConfigurationError(BackendError):
    pass


class BackendUnavailableError(BackendConfigurationError):
    """The configured Shared PostgreSQL connection is unavailable."""


class BackendSchemaError(BackendConfigurationError):
    """Core schema initialization or compatibility verification failed."""

    def __init__(self, message: str, *, reason: str = "incompatible"):
        super().__init__(message)
        self.reason = reason


class StandaloneLockError(BackendConfigurationError):
    """A live Standalone writer owns the configured SQLite database."""

    def __init__(self, *, lock_path: str, owner: object):
        super().__init__("The Standalone database is already open for writing.")
        self.lock_path = str(lock_path)
        self.owner = owner


class ValidationError(BackendError):
    pass


class ConflictError(BackendError):
    pass


class ImmutableIdentityError(ConflictError):
    pass


class LockConflictError(ConflictError):
    def __init__(self, message: str, *, owner: str = "", expires_at: str = ""):
        super().__init__(message)
        self.owner = owner
        self.expires_at = expires_at


class PermissionDeniedError(BackendError):
    pass


class AuthenticationError(PermissionDeniedError):
    """The supplied credentials did not authenticate an active account."""


class SessionRevokedError(PermissionDeniedError):
    """The authenticated session has expired or has been replaced."""

    def __init__(self, message="The authentication session is no longer valid.", *, reason=""):
        super().__init__(message)
        self.reason = reason


class FirstAccountUnavailableError(ConflictError):
    """Initial account creation has already completed."""
