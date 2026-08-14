#!/usr/bin/env python3
"""Build the reproducible, folder-portable Linux ProgTrack tarball.

This builder intentionally runs on Windows as well as Linux. It does not try
to cross-compile Qt or Python; the managed Linux artifact carries the shared
ProgTrack payload and native Linux launcher while the target system supplies
the pinned Python/Qt/Psycopg C runtime described in the package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


VERSION = "0.3.0"
ARCH = "x86_64"
PAYLOAD_FILES = (
    "README.md",
    "LICENSE",
    "LICENSE_NOTICE.md",
    "THIRD_PARTY_NOTICES.md",
    "Username + 123456 password.png",
    "info.json",
    "info_de.json",
    "info_en.json",
    "info_it.json",
    "info_ru.json",
    "ProgTrack.v.0.2.1.py",
)
PAYLOAD_DIRECTORIES = (
    "Plugins",
    "Resources",
    "icons",
    "lang",
    "third_party_licenses",
)
MANUAL_FILES = (
    "manual/LICENSE_NOTICE.md",
    "manual/ProgTrack_User_Guide - de.html",
    "manual/ProgTrack_User_Guide - en.html",
    "manual/ProgTrack_User_Guide - it.html",
    "manual/ProgTrack_User_Guide - ru.html",
)


def _repo_root() -> Path:
    # source/launcher/linux/package_linux_release.py -> repository root
    return Path(__file__).resolve().parents[3]


def _git_commit(repo: Path, requested: str | None) -> str:
    value = requested or "HEAD"
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{value}^{{commit}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _commit_epoch(repo: Path, commit: str) -> int:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", "-s", "--format=%ct", commit],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(result.stdout.strip())


def _safe_extract_archive(repo: Path, commit: str, destination: Path) -> None:
    archive = destination.parent / "repository.tar"
    with archive.open("wb") as handle:
        subprocess.run(
            ["git", "-C", str(repo), "archive", "--format=tar", commit],
            check=True,
            stdout=handle,
        )
    with tarfile.open(archive, "r:") as tar:
        for member in tar.getmembers():
            relative = PurePosixPath(member.name)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"Unsafe archive member: {member.name}")
            target = destination / Path(*relative.parts)
            if not target.resolve().is_relative_to(destination.resolve()):
                raise RuntimeError(f"Archive traversal: {member.name}")
        tar.extractall(destination)


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_payload(archive_root: Path, stage: Path) -> None:
    for relative in (*PAYLOAD_FILES, *MANUAL_FILES):
        _copy_tree(archive_root / relative, stage / relative)
    for relative in PAYLOAD_DIRECTORIES:
        _copy_tree(archive_root / relative, stage / relative)
    _copy_tree(
        archive_root / "source" / "launcher" / "linux",
        stage / "launcher",
    )
    _copy_tree(
        archive_root / "source" / "launcher" / "linux" / "requirements-linux-managed.txt",
        stage / "requirements-linux-managed.txt",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _set_linux_modes(stage: Path) -> None:
    for relative in ("launcher/ProgTrack", "launcher/launcher.py"):
        path = stage / relative
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_metadata(stage: Path, commit: str, epoch: int) -> None:
    metadata = {
        "artifact": f"ProgTrack-{VERSION}-linux-{ARCH}.tar.gz",
        "artifact_version": VERSION,
        "application_payload_version": "0.2.1",
        "source_commit": commit,
        "source_date_epoch": epoch,
        "platform": f"linux-{ARCH}",
        "launcher": "launcher/ProgTrack",
        "runtime_mode": "managed Linux system Python/Qt/Psycopg C",
        "status": "pre-release; manual Linux validation required",
        "portable_mode": "PROGTRACK_PORTABLE=1 only in a writable folder",
        "xdg_mode": "default; XDG data/config/cache/state roots",
        "native_runtime_bundled": False,
    }
    (stage / "release_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _write_inventory(stage: Path, commit: str, epoch: int) -> None:
    files = []
    for path in sorted(p for p in stage.rglob("*") if p.is_file()):
        relative = path.relative_to(stage).as_posix()
        if relative == "linux_component_inventory.json":
            continue
        files.append(
            {
                "path": relative,
                "sha256": _sha256(path),
                "mode": oct(stat.S_IMODE(path.stat().st_mode)),
            }
        )
    inventory = {
        "source_commit": commit,
        "source_date_epoch": epoch,
        "platform": f"linux-{ARCH}",
        "python": "3.13 (distribution-managed, pinned by IT test environment)",
        "requirements": "requirements-linux-managed.txt",
        "native_runtime_bundled": [],
        "external_prerequisites": [
            "Python 3.13",
            "PyQt6 and Qt6 platform/font packages",
            "psycopg[c]==3.3.4",
            "psycopg_pool==3.3.1",
            "pypdf==6.14.2",
            "distribution libpq development/runtime libraries",
        ],
        "files": files,
    }
    (stage / "linux_component_inventory.json").write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _validate_stage(stage: Path) -> None:
    forbidden_roots = {"tests", "tmp", "outputs", "ProgTrackData", "source"}
    for path in stage.rglob("*"):
        relative = path.relative_to(stage)
        if relative.parts and relative.parts[0] in forbidden_roots:
            raise RuntimeError(f"Development/runtime data leaked into package: {relative}")
        if path.is_file() and path.suffix.lower() in {".exe", ".dll", ".pyd"}:
            raise RuntimeError(f"Windows runtime leaked into Linux package: {relative}")
    folded: dict[str, str] = {}
    for path in stage.rglob("*"):
        relative = path.relative_to(stage).as_posix()
        previous = folded.setdefault(relative.casefold(), relative)
        if previous != relative:
            raise RuntimeError(f"Case-folded path collision: {previous} / {relative}")
    for required in (
        "launcher/ProgTrack",
        "launcher/launcher.py",
        "ProgTrack.v.0.2.1.py",
        "Plugins",
        "Resources/Seed/progtrack_seed.ptdb",
        "icons/ui/manifest.json",
        "requirements-linux-managed.txt",
        "release_metadata.json",
        "linux_component_inventory.json",
    ):
        if not (stage / required).exists():
            raise RuntimeError(f"Linux package is missing {required}")


def _write_reproducible_tar(stage: Path, archive: Path, epoch: int) -> None:
    root_name = archive.name.removesuffix(".tar.gz")
    with archive.open("wb") as raw:
        import gzip

        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=epoch) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for path in sorted(stage.rglob("*")):
                    relative = path.relative_to(stage).as_posix()
                    info = tar.gettarinfo(
                        str(path), arcname=f"{root_name}/{relative}"
                    )
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = epoch
                    if relative in {"launcher/ProgTrack", "launcher/launcher.py"}:
                        info.mode = 0o755
                    if path.is_file():
                        with path.open("rb") as handle:
                            tar.addfile(info, handle)
                    else:
                        tar.addfile(info)


def build(output: Path, commit: str | None) -> dict[str, object]:
    repo = _repo_root()
    resolved = _git_commit(repo, commit)
    epoch = _commit_epoch(repo, resolved)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"ProgTrack-{VERSION}-linux-{ARCH}.tar.gz"
    checksum = output / f"{archive.name}.sha256"
    if archive.exists():
        archive.unlink()
    if checksum.exists():
        checksum.unlink()
    with tempfile.TemporaryDirectory(prefix="progtrack-linux-") as temporary:
        temp_root = Path(temporary)
        archive_root = temp_root / "repository"
        stage = temp_root / "stage"
        archive_root.mkdir()
        stage.mkdir()
        _safe_extract_archive(repo, resolved, archive_root)
        _copy_payload(archive_root, stage)
        _set_linux_modes(stage)
        _write_metadata(stage, resolved, epoch)
        _write_inventory(stage, resolved, epoch)
        _validate_stage(stage)
        _write_reproducible_tar(stage, archive, epoch)
    digest = _sha256(archive)
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return {
        "archive": str(archive),
        "sha256": digest,
        "bytes": archive.stat().st_size,
        "source_commit": resolved,
        "source_date_epoch": epoch,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the ProgTrack Linux tar.gz")
    parser.add_argument("--commit", default=None)
    parser.add_argument(
        "--output",
        default=str(_repo_root() / "source" / "release" / "linux"),
    )
    args = parser.parse_args()
    result = build(Path(args.output).resolve(), args.commit)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
