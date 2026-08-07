"""Canonical Project Track lifecycle states and guarded transitions."""

from __future__ import annotations

from typing import MutableMapping


PROJECT_LIFECYCLE_STATUSES = ("draft", "active", "closed")


def normalize_project_status(value: object, *, default: str = "draft") -> str:
    """Return a supported lifecycle status without leaking arbitrary values."""
    normalized = str(value or "").strip().casefold()
    if normalized in PROJECT_LIFECYCLE_STATUSES:
        return normalized
    return default if default in PROJECT_LIFECYCLE_STATUSES else "draft"


def set_project_status(
    record: MutableMapping[str, object],
    status: object,
    *,
    can_manage: bool,
) -> tuple[str, str]:
    """Apply one authorized status transition and return ``(old, new)``.

    Permission enforcement lives here as well as in the UI so callers cannot
    bypass the disabled radio buttons by invoking the save path directly.
    """
    old = normalize_project_status(record.get("status"), default="active")
    new = str(status or "").strip().casefold()
    if new not in PROJECT_LIFECYCLE_STATUSES:
        raise ValueError(f"Unsupported project lifecycle status: {status!r}")
    if new != old and not can_manage:
        raise PermissionError("Changing project lifecycle status is not permitted")
    record["status"] = new
    return old, new
