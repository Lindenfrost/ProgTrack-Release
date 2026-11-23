"""Shared live progress reporting for the Heritage Track test suite."""

from __future__ import annotations

from contextlib import contextmanager
import time
import unittest
from typing import Iterator


def _case_text(label: str, details: dict[str, object]) -> str:
    parts = [str(label)]
    for key, value in details.items():
        if value is not None:
            parts.append(f"{key}={value}")
    return "; ".join(parts)


@contextmanager
def reported_subtest(
    testcase: unittest.TestCase,
    label: str,
    **details: object,
) -> Iterator[None]:
    """Run one unittest subtest and report its result immediately.

    The inner ``try`` is intentional: ``unittest.TestCase.subTest`` records an
    assertion failure instead of re-raising it, so the result must be printed
    before that outer context receives the failure.  Output is flushed so a
    long Heritage matrix remains inspectable while later cases continue.
    """
    case = _case_text(label, details)
    started = time.perf_counter()
    print(f"[HERITAGE][START] {case}", flush=True)
    with testcase.subTest(**details):
        try:
            yield
        except unittest.SkipTest as exc:
            elapsed = time.perf_counter() - started
            print(
                f"[HERITAGE][SKIP] {case} ({elapsed:.3f}s): {exc}",
                flush=True,
            )
            raise
        except Exception as exc:
            elapsed = time.perf_counter() - started
            message = str(exc).strip().replace("\n", " | ") or type(exc).__name__
            print(
                f"[HERITAGE][FAIL] {case} ({elapsed:.3f}s): {message}",
                flush=True,
            )
            raise
        else:
            elapsed = time.perf_counter() - started
            print(f"[HERITAGE][PASS] {case} ({elapsed:.3f}s)", flush=True)
