"""Typed backend errors shared by SQLite and PostgreSQL."""


class BackendError(RuntimeError):
    pass


class BackendConfigurationError(BackendError):
    pass


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
