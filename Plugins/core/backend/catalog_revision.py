# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Cluster catalog clock, installed explicitly in the postgres maintenance DB."""

from contextlib import contextmanager
from dataclasses import dataclass

from .errors import BackendConfigurationError


_LOCK = (942637, 215)
_TABLE = 'progtrack_catalog.database_revision'


@dataclass(frozen=True)
class CatalogRevision:
    epoch: str
    revision: int
    dirty: bool


class CatalogClockUnavailable(BackendConfigurationError):
    pass


def read_catalog_revision(connection) -> CatalogRevision:
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT epoch::text, revision, dirty FROM {_TABLE} WHERE singleton")
        row = cursor.fetchone()
    if row is None:
        raise CatalogClockUnavailable("The server database catalog revision is unavailable.")
    return CatalogRevision(*row)


@contextmanager
def catalog_mutation(connection, *, administrator=None):
    """Publish invalidation before work that may commit outside this connection.

    The connection is dedicated and autocommitting. A crash or exception after
    dirty publication leaves the clock dirty. A new successful mutation or an
    explicit IT reconciliation can publish a later settled revision.
    """
    if not connection.autocommit:
        raise CatalogClockUnavailable("Catalog writes require a dedicated maintenance connection.")
    acquired = False
    published = False
    try:
        with connection.cursor() as cursor:
            if administrator is not None:
                cursor.execute("SELECT session_user, current_user")
                if cursor.fetchone() != (administrator, administrator):
                    raise CatalogClockUnavailable("The catalog clock connection does not use the authorized administrator.")
            cursor.execute("SELECT pg_try_advisory_lock(%s,%s)", _LOCK)
            acquired = cursor.fetchone()[0]
            if not acquired:
                raise CatalogClockUnavailable("Another server catalog operation is in progress.")
            cursor.execute(f"UPDATE {_TABLE} SET revision=revision+1, dirty=TRUE WHERE singleton")
            if cursor.rowcount != 1:
                raise CatalogClockUnavailable("The server database catalog revision is unavailable.")
            published = True
        yield
        with connection.cursor() as cursor:
            cursor.execute(f"UPDATE {_TABLE} SET revision=revision+1, dirty=FALSE WHERE singleton")
            if cursor.rowcount != 1:
                raise CatalogClockUnavailable("The operation completed, but its catalog revision could not be published.")
    except CatalogClockUnavailable:
        raise
    except Exception:
        # A missing or unreadable marker must fail before the first side effect.
        if not published:
            raise CatalogClockUnavailable("The server database catalog clock is not provisioned or accessible.") from None
        raise
    finally:
        if acquired:
            # Closing the dedicated connection is the backstop if unlock fails.
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_unlock(%s,%s)", _LOCK)
            except Exception:
                pass
