"""Live per-test reporting for Heritage Track tests.

The hook is deliberately scoped to Heritage test node IDs, so running the
repository suite does not change the output contract of unrelated plugins.
Use ``-s`` when raw subtest lines are also desired; the hook itself writes via
Pytest's terminal reporter and remains visible during captured runs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time

import pytest

# Release-package acceptance tests must import the unpacked candidate payload,
# not silently fall back to the checkout from which pytest was started.
_candidate_package_root = os.environ.get("PROGTRACK_TEST_PACKAGE_ROOT")
if _candidate_package_root:
    _candidate_path = Path(_candidate_package_root).resolve()
    if str(_candidate_path) in sys.path:
        sys.path.remove(str(_candidate_path))
    sys.path.insert(0, str(_candidate_path))

_heritage_terminal = None
_retest_journal = None
_heritage_nodeids: set[str] = set()

# Heritage Track renderer/plugin behavior is being reworked in Phase 4. Keep
# its existing tests discoverable but out of the Phase 3 default run. The
# #156 module is intentionally exempt: it tests the Core-owned parentage
# boundary now used independently of Heritage Track.
_phase4_heritage_modules = {
    path.name
    for path in Path(__file__).parent.glob("test_heritage_*.py")
    if path.name != "test_heritage_issue_156.py"
}
_phase4_heritage_modules.update({
    "test_issue_211_manual_animal_repositioning.py",
    "test_issue_242_heritage_category_tabs.py",
    "test_issue_249_route_recovery.py",
})
_phase4_heritage_cases = {
    "test_heritage_to_reports_keeps_the_normalized_subject",
}


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


def pytest_collection_modifyitems(items) -> None:
    for item in items:
        module_name = Path(str(item.path)).name
        if module_name in _phase4_heritage_modules or item.name in _phase4_heritage_cases:
            item.add_marker(pytest.mark.phase4_heritage)
        if (module_name == "test_phase023_core_repair_regressions.py"
                and item.name == "test_309_effective_status_uses_authorized_checkbox_or_coerced_result"):
            item.add_marker(pytest.mark.isolated_qt)
        if (module_name == "test_phase023_core_repair_regressions.py"
                and item.name == "test_268_seven_editors_reject_invalid_rows_without_saving"):
            item.add_marker(pytest.mark.isolated_qt)
        if (module_name == "test_phase023_core_repair_regressions.py"
                and item.name == "test_247_deletion_all_editors_first_middle_last_and_empty_sqlite"):
            item.add_marker(pytest.mark.isolated_qt)
        if (module_name == "test_phase023_core_repair_regressions.py"
                and item.name == "test_315_real_new_event_save_all_five_adapters_and_experimental_offspring"):
            item.add_marker(pytest.mark.isolated_qt)
        if (module_name == "test_phase023_core_repair_regressions.py"
                and item.name == "test_319_builtin_event_buttons_and_headers_remain_localized"):
            item.add_marker(pytest.mark.isolated_qt)
        if (module_name == "test_phase023_core_repair_regressions.py"
                and item.name == "test_318_new_invalid_missing_birth_cancel_denied_create_and_read_only_stay_safe"):
            item.add_marker(pytest.mark.isolated_qt)
        if (module_name == "test_phase023_core_repair_regressions.py"
                and item.name == "test_309_five_editors_decline_retry_accept_sqlite"):
            item.add_marker(pytest.mark.isolated_qt)
        if module_name == "test_ui_svg_palette_contrast.py":
            item.add_marker(pytest.mark.isolated_qt)
        if item.get_closest_marker("phase4_heritage") or item.get_closest_marker("extended_heritage"):
            _heritage_nodeids.add(item.nodeid)


def pytest_sessionfinish(session, exitstatus) -> None:
    _journal('finish', exitstatus=int(exitstatus))


def pytest_runtest_logstart(nodeid, location) -> None:
    _journal('start', nodeid=nodeid)
    if nodeid not in _heritage_nodeids:
        return
    print(f"[HERITAGE][START] {nodeid}", flush=True)


def pytest_runtest_logreport(report) -> None:
    _journal('result', nodeid=report.nodeid, phase=report.when,
             outcome=report.outcome, duration=report.duration,
             failure=str(report.longrepr) if report.failed else None)
    if report.when != "call" or report.nodeid not in _heritage_nodeids:
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
