#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright © 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack Launcher 0.1.1-log-menu
# Module: Portable Windows launcher for external ProgTrack payload scripts.
# Default target: first ProgTrack.v.*.py script in the launcher directory.

from __future__ import annotations

import argparse
import faulthandler
import multiprocessing
import os
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

DEFAULT_SCRIPT_PATTERN = "ProgTrack.v.*.py"
LAUNCHER_VERSION = "0.1.1-log-menu"
LAUNCHER_BUILD_NOTE = "Updated launcher variant with central logs folder support."
LOG_DIR_NAME = "logs"
ERROR_LOG_NAME = "launcher_error.log"
FAULT_LOG_NAME = "launcher_fault.log"
MPL_CONFIG_DIR_NAME = "matplotlib_cache"
_FAULT_LOG_HANDLE = None
_LOG_DIR = None


def _runtime_state_dir(launcher_dir: Path) -> Path:
    internal_dir = launcher_dir / "_internal"
    return internal_dir if internal_dir.exists() else launcher_dir


def setup_environment() -> Path:
    """Resolve launcher directory, set CWD, and prepend it to sys.path."""
    global _FAULT_LOG_HANDLE, _LOG_DIR

    if getattr(sys, "frozen", False):
        launcher_dir = Path(sys.executable).resolve().parent
        internal_dir = launcher_dir / "_internal"
        qt_plugins = internal_dir / "PyQt6" / "Qt6" / "plugins"
        qt_bin = internal_dir / "PyQt6" / "Qt6" / "bin"
        path_parts = [internal_dir, qt_bin]
        os.environ["PATH"] = os.pathsep.join(
            str(path) for path in path_parts if path.exists()
        ) + os.pathsep + os.environ.get("PATH", "")
        if sys.platform.startswith("win"):
            os.environ.setdefault("QT_QPA_PLATFORM", "windows")
        if qt_plugins.exists():
            os.environ["QT_PLUGIN_PATH"] = str(qt_plugins)
    else:
        launcher_dir = Path(__file__).resolve().parent

    runtime_state_dir = _runtime_state_dir(launcher_dir)

    _LOG_DIR = runtime_state_dir / LOG_DIR_NAME
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        _LOG_DIR = runtime_state_dir

    try:
        _FAULT_LOG_HANDLE = (_LOG_DIR / FAULT_LOG_NAME).open("a", encoding="utf-8")
        faulthandler.enable(file=_FAULT_LOG_HANDLE, all_threads=True)
    except OSError:
        pass

    mpl_config_dir = runtime_state_dir / MPL_CONFIG_DIR_NAME
    try:
        mpl_config_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(mpl_config_dir))
    except OSError:
        pass

    os.chdir(launcher_dir)

    launcher_dir_str = str(launcher_dir)
    if launcher_dir_str not in sys.path:
        sys.path.insert(0, launcher_dir_str)
    if getattr(sys, "frozen", False):
        internal_dir_str = str(launcher_dir / "_internal")
        if internal_dir_str not in sys.path:
            sys.path.insert(1, internal_dir_str)

    return launcher_dir


def parse_arguments() -> tuple[str | None, list[str]]:
    """Parse launcher args and forward all remaining args to the target script."""
    parser = argparse.ArgumentParser(
        description="ProgTrack Suite Launcher",
        add_help=True,
        allow_abbrev=False,
    )
    parser.add_argument(
        "--script",
        type=str,
        default=None,
        help=(
            "Python script to execute. If omitted, the launcher uses the first "
            f"{DEFAULT_SCRIPT_PATTERN} file found in the launcher directory."
        ),
    )

    args, remaining = parser.parse_known_args()
    return args.script, remaining


def _natural_name_key(path: Path) -> list[int | str]:
    """Sort filenames naturally, so v.5 comes before v.10."""
    return [
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", path.name)
    ]


def discover_default_script(launcher_dir: Path) -> Path | None:
    """Return the first matching ProgTrack script in deterministic natural order."""
    matches = sorted(
        (p for p in launcher_dir.glob(DEFAULT_SCRIPT_PATTERN) if p.is_file()),
        key=_natural_name_key,
    )
    return matches[0] if matches else None


def resolve_script_path(script_name: str | None, launcher_dir: Path) -> Path:
    """Resolve an explicit target script or discover the default ProgTrack script."""
    if script_name:
        return (launcher_dir / script_name).resolve()

    discovered = discover_default_script(launcher_dir)
    if discovered:
        return discovered.resolve()

    return (launcher_dir / DEFAULT_SCRIPT_PATTERN).resolve()


def _format_missing_script_message(script_path: Path, launcher_dir: Path) -> str:
    available_py = sorted(p.name for p in launcher_dir.glob("*.py"))
    lines = [
        f"Error: Script not found: {script_path}",
        f"Launcher directory: {launcher_dir}",
        f"Default search pattern: {DEFAULT_SCRIPT_PATTERN}",
    ]
    if available_py:
        lines.append("Available .py files in launcher directory:")
        lines.extend(f"  - {name}" for name in available_py)
    else:
        lines.append("No .py files were found in the launcher directory.")
    return "\n".join(lines)


def execute_script(script_path: Path, script_args: list[str], launcher_dir: Path) -> None:
    """Execute the target Python script in a clean __main__ namespace."""
    if not script_path.exists() or not script_path.is_file():
        print(_format_missing_script_message(script_path, launcher_dir), file=sys.stderr)
        raise SystemExit(1)

    sys.argv = [str(script_path)] + script_args

    globals_dict = {
        "__name__": "__main__",
        "__file__": str(script_path),
        "__package__": None,
        "__cached__": None,
    }

    try:
        source = script_path.read_text(encoding="utf-8")
        code = compile(source, str(script_path), "exec")
        exec(code, globals_dict)
    except SystemExit:
        raise
    except Exception as exc:
        message = f"Error executing {script_path.name}: {exc}"
        details = traceback.format_exc()
        print(message, file=sys.stderr)
        print(details, file=sys.stderr)
        try:
            log_dir = _LOG_DIR or launcher_dir
            with (log_dir / ERROR_LOG_NAME).open("a", encoding="utf-8") as handle:
                handle.write(
                    f"[{datetime.now().isoformat(timespec='seconds')}] "
                    f"{message}\n\n{details}\n"
                )
        except OSError:
            pass
        raise SystemExit(1)


def main() -> int:
    multiprocessing.freeze_support()

    launcher_dir = setup_environment()
    script_name, script_args = parse_arguments()
    script_path = resolve_script_path(script_name, launcher_dir)

    execute_script(script_path, script_args, launcher_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
