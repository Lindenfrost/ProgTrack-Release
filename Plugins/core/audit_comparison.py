# SPDX-License-Identifier: GPL-3.0-or-later
# Part of: ProgTrack 0.2.3
"""Narrow semantic comparisons for Core audit evidence (#315)."""

from collections.abc import Mapping


def preserve_unset_optional_fields(candidate: dict, original: Mapping) -> None:
    """Keep unchanged widget blanks from rewriting existing optional fields.

    This is Save preparation, not audit suppression: an actual clearing of a
    populated value remains a change. New records keep their normal defaults.
    """
    if not original or candidate is original:
        return
    for field in ("death_date", "severity"):
        previous, current = original.get(field), candidate.get(field)
        if ((previous is None or isinstance(previous, str) and previous == "")
                and (current is None or isinstance(current, str) and current == "")):
            if field in original:
                candidate[field] = previous
            else:
                candidate.pop(field, None)


def _payload_equal(left, right) -> bool:
    """Compare complete payloads, preserving nested order and boolean types."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        return left.keys() == right.keys() and all(
            _payload_equal(value, right[key]) for key, value in left.items()
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _payload_equal(a, b) for a, b in zip(left, right)
        )
    return left == right


def event_collections_equal(before, after) -> bool:
    """Ignore only enumeration order of valid, stably identified events.

    Explicit order/ordinal fields and unknown payload fields remain part of
    the comparison. Missing/duplicate IDs fail conservatively; no deduplication,
    mutation or ID assignment is permitted while deciding audit equivalence.
    """
    def index(events):
        if not isinstance(events, list):
            return None
        result = {}
        for event in events:
            if not isinstance(event, Mapping):
                return None
            identity = event.get("event_id")
            if (not isinstance(identity, str) or not identity
                    or identity != identity.strip() or identity in result):
                return None
            result[identity] = event
        return result

    old, new = index(before), index(after)
    return old is not None and new is not None and _payload_equal(old, new)
