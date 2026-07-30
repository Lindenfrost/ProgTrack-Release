"""Canonical JSON conversion helpers."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


def _default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dumps(value: Any, *, indent: int | None = None) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":") if indent is None else None,
        indent=indent,
        default=_default,
    )


def loads(value: str | bytes | None, default: Any = None) -> Any:
    if value in (None, "", b""):
        return default
    return json.loads(value)
