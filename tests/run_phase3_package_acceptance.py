r"""Run focused Phase 3 checks against the assembled Windows and Linux 0.3.0 packages.

Run on Windows after placing both final archives and their SHA-256 sidecars in
Q:\GitHub\Release Packages. The same archives are then used for Manual Retest
3A; this script never builds or modifies a release artifact.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = Path(os.environ.get("PROGTRACK_RELEASE_PACKAGES_DIR", r"Q:\GitHub\Release Packages")).resolve()
WINDOWS_NAME = "ProgTrack-0.3.0.zip"
LINUX_NAME = "ProgTrack-0.3.0-linux-x86_64.tar.gz"
BACKEND_TESTS = (
    "tests/test_phase3_release_package_acceptance.py",
    "tests/test_current_backend_runtime.py",
    "tests/test_backend_schema_bootstrap.py",
    "tests/test_phase3_postgresql_management.py",
    "tests/test_audit_logging.py",
    "tests/test_phase3_sessions_220_232.py",
)


def _artifact(name: str) -> Path:
    artifact = PACKAGES / name
    checksum = artifact.with_name(artifact.name + ".sha256")
    if not artifact.is_file() or not checksum.is_file():
        raise FileNotFoundError(
            f"Required assembled package or checksum is missing: {artifact}"
        )
    fields = checksum.read_text(encoding="utf-8-sig").split()
    if len(fields) < 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", fields[0]):
        raise ValueError(f"Malformed SHA-256 sidecar: {checksum}")
    if Path(fields[-1]).name.casefold() != artifact.name.casefold():
        raise ValueError(f"Checksum sidecar names another artifact: {checksum}")
    digest = hashlib.sha256()
    with artifact.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    actual = digest.hexdigest()
    if actual.casefold() != fields[0].casefold():
        raise ValueError(f"SHA-256 mismatch for assembled package: {artifact}")
    print(f"[ARTIFACT] {artifact.name} sha256={actual}", flush=True)
    return artifact


def _safe_extract_zip(archive_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise ValueError(f"Unsafe Windows package entry: {info.filename}")
            mode = info.external_attr >> 16
            if mode and (mode & 0o170000) == 0o120000:
                raise ValueError(f"Unexpected symlink in Windows package: {info.filename}")
            target = (root / Path(*member.parts)).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"Package entry escapes extraction directory: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)


def _safe_extract_tar(archive_path: Path, destination: Path) -> None:
    root = destination.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"Unsafe Linux package entry: {member.name}")
            target = (root / Path(*relative.parts)).resolve()
            if not target.is_relative_to(root):
                raise ValueError(f"Package entry escapes extraction directory: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError(f"Cannot read Linux package entry: {member.name}")
                with source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            else:
                raise ValueError(f"Unexpected link or special file in Linux package: {member.name}")


def _find_package_root(extraction: Path) -> Path:
    candidates = [
        path.parent for path in extraction.rglob("ProgTrack.v.0.3.0.py")
        if (path.parent / "Plugins/core/backend").is_dir()
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Expected one complete 0.3.0 application payload under {extraction}; found {len(candidates)}"
        )
    return candidates[0]


def _run_package_tests(label: str, package_root: Path, extra_tests: tuple[str, ...], *, launcher: Path | None = None, linux_archive: Path | None = None) -> bool:
    env = os.environ.copy()
    env["PROGTRACK_TEST_PACKAGE_ROOT"] = str(package_root)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env.pop("PROGTRACK_TEST_WINDOWS_LAUNCHER", None)
    env.pop("PROGTRACK_TEST_LINUX_PACKAGE", None)
    if launcher is not None:
        env["PROGTRACK_TEST_WINDOWS_LAUNCHER"] = str(launcher)
    if linux_archive is not None:
        env["PROGTRACK_TEST_LINUX_PACKAGE"] = str(linux_archive)
    command = [
        sys.executable, "-m", "pytest", "-o", "addopts=", "-q",
        *BACKEND_TESTS, *extra_tests,
    ]
    print(f"\n[PACKAGE TESTS] {label}: {package_root}", flush=True)
    result = subprocess.run(command, cwd=ROOT, env=env, check=False)
    return result.returncode == 0


def main() -> int:
    if sys.platform != "win32":
        print("Run this acceptance command on Windows so #299 reads the packaged PE version resource.", file=sys.stderr)
        return 2

    windows_archive = _artifact(WINDOWS_NAME)
    linux_archive = _artifact(LINUX_NAME)
    outcomes: list[bool] = []
    for label, artifact, platform, extra_tests, package_kwargs in (
        (
            "Windows 0.3.0", windows_archive, "windows",
            ("tests/test_launcher_windows_copyright_299.py",), {},
        ),
        (
            "Linux 0.3.0 payload", linux_archive, "linux",
            ("tests/test_linux_release_packaging.py",), {"linux_archive": linux_archive},
        ),
    ):
        with tempfile.TemporaryDirectory(prefix="progtrack-030-package-acceptance-") as temporary:
            staging = Path(temporary)
            if platform == "windows":
                _safe_extract_zip(artifact, staging)
            else:
                _safe_extract_tar(artifact, staging)
            package_root = _find_package_root(staging)
            if platform == "windows":
                launcher = package_root / "Launcher.exe"
                if not launcher.is_file():
                    raise FileNotFoundError(f"Windows candidate package lacks Launcher.exe: {launcher}")
                package_kwargs["launcher"] = launcher
            outcomes.append(_run_package_tests(
                label, package_root, extra_tests, **package_kwargs,
            ))

    if all(outcomes):
        print("\nPhase 3 assembled-package checks passed for both exact archives.", flush=True)
        return 0
    print("\nAt least one assembled-package test run failed.", file=sys.stderr, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
