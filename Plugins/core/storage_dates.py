# SPDX-License-Identifier: GPL-3.0-or-later
"""Lossless date views at the storage/UI boundary (#316).

The source spelling is an encoding detail, not an extra persisted field.
Keep the type importable so report/cache workers can pickle these views.
"""

from datetime import datetime


class StoredDateTime(datetime):
    """Immutable date value carrying its original durable spelling."""

    def __deepcopy__(self, memo):
        return self

    def __reduce_ex__(self, protocol):
        return (_restore, (self.isoformat(), getattr(self, '_storage_text', None)))


def stored_datetime(parsed: datetime, literal: str | None) -> StoredDateTime:
    result = StoredDateTime(parsed.year, parsed.month, parsed.day, parsed.hour,
                            parsed.minute, parsed.second, parsed.microsecond,
                            tzinfo=parsed.tzinfo, fold=parsed.fold)
    if literal is not None:
        result._storage_text = literal
    return result


def _restore(iso_value, literal):
    return stored_datetime(datetime.fromisoformat(iso_value), literal)
