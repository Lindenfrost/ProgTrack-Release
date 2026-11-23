# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Deployment timing limits for the Shared session and lease services."""

import logging
import os


def _configured_seconds(name, default, minimum, maximum):
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if minimum <= value <= maximum:
        return value
    logging.warning("%s must be %s–%s. Using %s seconds.", name, minimum, maximum, default)
    return default


def heartbeat_seconds():
    return _configured_seconds("PROGTRACK_SESSION_HEARTBEAT_SECONDS", 30, 5, 60)


def session_timeout_seconds():
    return _configured_seconds("PROGTRACK_SESSION_TIMEOUT_SECONDS", 120, max(60, 2 * heartbeat_seconds()), 600)


def lease_timeout_seconds():
    return _configured_seconds("PROGTRACK_LEASE_TIMEOUT_SECONDS", 120, max(60, 2 * heartbeat_seconds()), 600)
