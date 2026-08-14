#!/usr/bin/env python3
"""Native Linux launcher for the self-contained ProgTrack payload.

The Linux artifact bundles CPython, Qt, the scientific/PDF stack, fonts, and
Psycopg's binary PostgreSQL client. Runtime data never default to the read-only
bundle: XDG data/config/cache/state roots are used unless the user explicitly
opts into ``PROGTRACK_PORTABLE=1`` in a writable folder.
"""

from __future__ import annotations

import argparse
import faulthandler
import os
import runpy
import shutil
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

LAUNCHER_VERSION = "0.3.0"
DEFAULT_SCRIPT_PATTERN = "ProgTrack.v.*.py"
_LOG_DIR: Path | None = None
_FAULT_HANDLE = None


def _writable(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".progtrack-write-test-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _xdg_root(env_name: str, fallback: Path, app_name: str) -> Path:
    value = os.environ.get(env_name)
    return (Path(value).expanduser() if value else fallback) / app_name


def runtime_roots(bundle_root: Path) -> tuple[Path, Path, Path, Path]:
    """Return data/config/cache/state roots without bundle writes by default."""
    portable = os.environ.get("PROGTRACK_PORTABLE", "").strip().lower()
    if portable in {"1", "true", "yes", "on"} and _writable(bundle_root):
        root = bundle_root / "ProgTrackData"
        return root, root / "config", root / "cache", root / "state"
    home = Path.home()
    return (
        _xdg_root("XDG_DATA_HOME", home / ".local" / "share", "ProgTrack"),
        _xdg_root("XDG_CONFIG_HOME", home / ".config", "ProgTrack"),
        _xdg_root("XDG_CACHE_HOME", home / ".cache", "ProgTrack"),
        _xdg_root("XDG_STATE_HOME", home / ".local" / "state", "ProgTrack"),
    )


def _format_paths(bundle_root: Path, roots: tuple[Path, Path, Path, Path]) -> str:
    labels = ("data", "config", "cache", "state")
    lines = [f"ProgTrack Linux launcher {LAUNCHER_VERSION}", f"bundle={bundle_root}"]
    lines.extend(f"{label}={path}" for label, path in zip(labels, roots))
    return "\n".join(lines)


def setup_environment(bundle_root: Path, *, diagnose: bool = False) -> tuple[Path, Path]:
    global _LOG_DIR, _FAULT_HANDLE
    data_root, config_root, cache_root, state_root = runtime_roots(bundle_root)
    _LOG_DIR = state_root / "logs"
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        _FAULT_HANDLE = (_LOG_DIR / "launcher_fault.log").open("a", encoding="utf-8")
        faulthandler.enable(file=_FAULT_HANDLE, all_threads=True)
    except OSError:
        _FAULT_HANDLE = None
    mpl_root = cache_root / "matplotlib"
    try:
        mpl_root.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("MPLCONFIGDIR", str(mpl_root))
    except OSError:
        pass
    os.environ.setdefault("PROGTRACK_LAUNCHER_PLATFORM", "linux-x86_64")
    os.environ.setdefault("PROGTRACK_PACKAGE_ROOT", str(bundle_root))
    os.chdir(bundle_root)
    if str(bundle_root) not in sys.path:
        sys.path.insert(0, str(bundle_root))
    if diagnose:
        print(_format_paths(bundle_root, (data_root, config_root, cache_root, state_root)))
    return bundle_root, state_root


def _configure_bundled_runtime(bundle_root: Path) -> None:
    """Expose bundled Python, Qt, fonts, and native libraries before imports."""
    runtime = bundle_root / "runtime"
    site_packages = runtime / "lib" / "python3.13" / "site-packages"
    if not site_packages.is_dir():
        return
    if str(site_packages) not in sys.path:
        sys.path.insert(0, str(site_packages))
    qt_root = site_packages / "PyQt6" / "Qt6"
    qt_lib = qt_root / "lib"
    qt_plugins = qt_root / "plugins"
    os.environ.setdefault("PYTHONNOUSERSITE", "1")
    os.environ.setdefault("QT_PLUGIN_PATH", str(qt_plugins))
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(qt_plugins / "platforms"))
    font_root = bundle_root / "fonts" / "matplotlib"
    if font_root.is_dir():
        os.environ.setdefault("QT_QPA_FONTDIR", str(font_root))
    library_paths = [runtime / "lib", qt_lib, site_packages / "psycopg_binary.libs"]
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = os.pathsep.join(
        [str(path) for path in library_paths if path.is_dir()] + ([existing] if existing else [])
    )

def _missing_dependencies() -> list[str]:
    required = ("PyQt6.QtWidgets", "matplotlib", "numpy", "pandas", "scipy", "openpyxl", "reportlab", "PIL", "pypdf", "psycopg", "psycopg_pool")
    missing: list[str] = []
    for name in required:
        try:
            __import__(name)
        except Exception:
            missing.append(name)
    return missing


def _discover_payload(bundle_root: Path) -> Path | None:
    candidates = sorted(
        (path for path in bundle_root.glob(DEFAULT_SCRIPT_PATTERN) if path.is_file()),
        key=lambda path: path.name.casefold(),
    )
    return candidates[-1] if candidates else None


def _write_error(message: str, details: str) -> None:
    if _LOG_DIR is None:
        return
    try:
        (_LOG_DIR / "launcher_error.log").open("a", encoding="utf-8").write(
            f"[{datetime.now(timezone.utc).isoformat()}] {message}\n{details}\n"
        )
    except OSError:
        pass


def _bundle_root() -> Path:
    here = Path(__file__).resolve()
    candidates = (here.parent.parent, here.parents[3], here.parents[2])
    for candidate in candidates:
        if _discover_payload(candidate) is not None:
            return candidate
    return candidates[0]

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ProgTrack Linux launcher")
    parser.add_argument("--diagnose-paths", action="store_true")
    parser.add_argument("--skip-dependency-check", action="store_true")
    parser.add_argument("script_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    bundle_root = _bundle_root()
    _configure_bundled_runtime(bundle_root)
    setup_environment(bundle_root, diagnose=args.diagnose_paths)
    if args.diagnose_paths:
        return 0
    if not args.skip_dependency_check:
        missing = _missing_dependencies()
        if missing:
            print(
                "Bundled Linux runtime is incomplete; missing imports: "
                + ", ".join(missing)
                + ". Rebuild the release from its pinned runtime manifest.",
                file=sys.stderr,
            )
            return 78
    script = _discover_payload(bundle_root)
    if script is None:
        print(
            f"ProgTrack payload not found: {bundle_root / DEFAULT_SCRIPT_PATTERN}",
            file=sys.stderr,
        )
        return 2
    sys.argv = [str(script), *args.script_args]
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    except Exception as exc:  # pragma: no cover - defensive launcher boundary
        details = traceback.format_exc()
        print(f"Error starting ProgTrack: {exc}\n{details}", file=sys.stderr)
        _write_error(str(exc), details)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
