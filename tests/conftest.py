"""Live per-test reporting for Heritage Track tests.

The hook is deliberately scoped to Heritage test node IDs, so running the
repository suite does not change the output contract of unrelated plugins.
Use ``-s`` when raw subtest lines are also desired; the hook itself writes via
Pytest's terminal reporter and remains visible during captured runs.
"""

from __future__ import annotations

import json
from pathlib import Path
import time

_heritage_terminal = None
_retest_journal = None


def pytest_addoption(parser) -> None:
    parser.addoption('--retest-journal', default=None,
                     help='Optional durable JSONL test-progress evidence, including failures before process exit.')


def _journal(kind, **fields) -> None:
    if _retest_journal is None:
        return
    with _retest_journal.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(dict(kind=kind, time=time.time(), **fields), ensure_ascii=False) + '\n')


def pytest_configure(config) -> None:
    global _heritage_terminal, _retest_journal
    _heritage_terminal = config.pluginmanager.get_plugin("terminalreporter")
    target = config.getoption('--retest-journal')
    _retest_journal = Path(target) if target else None
    _journal('session', args=list(config.invocation_params.args))


def pytest_collection_finish(session) -> None:
    _journal('collection', nodeids=[item.nodeid for item in session.items])


def pytest_sessionfinish(session, exitstatus) -> None:
    _journal('finish', exitstatus=int(exitstatus))


def pytest_runtest_logstart(nodeid, location) -> None:
    _journal('start', nodeid=nodeid)
    if "test_heritage" not in nodeid.lower():
        return
    print(f"[HERITAGE][START] {nodeid}", flush=True)


def pytest_runtest_logreport(report) -> None:
    _journal('result', nodeid=report.nodeid, phase=report.when,
             outcome=report.outcome, duration=report.duration,
             failure=str(report.longrepr) if report.failed else None)
    if report.when != "call" or "test_heritage" not in report.nodeid.lower():
        return
    terminal = _heritage_terminal
    if terminal is None:
        print(f"[HERITAGE][{report.outcome.upper()}] {report.nodeid} ({report.duration:.3f}s)", flush=True)
        return
    status = report.outcome.upper()
    detail = ""
    if report.failed:
        longrepr = str(report.longrepr).strip().replace("\n", " | ")
        detail = f": {longrepr[:500]}"
    terminal.write_line(
        f"[HERITAGE][{status}] {report.nodeid} ({report.duration:.3f}s){detail}"
    )
