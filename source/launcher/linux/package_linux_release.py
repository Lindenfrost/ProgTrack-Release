#!/usr/bin/env python3
"""Build the self-contained Linux ProgTrack tarball.

The builder is deliberately platform-neutral: a Linux build host may run it
directly, while a Windows build host may assemble the exact Linux CPython and
manylinux wheels for a pre-release artifact.  Native Linux execution remains a
separate acceptance gate because an ELF runtime cannot be executed on Windows.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


VERSION = "0.3.0"
ARCH = "x86_64"
PYTHON_TAG = "cp313"
LINUX_FILES = (
    "ProgTrack",
    "launcher.py",
    "progtrack.desktop",
    "progtrack.png",
    "README.md",
    "requirements-linux-bundled.txt",
)
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
    "manual",
    "third_party_licenses",
)
MANIFEST_PATH = Path(__file__).with_name("linux_runtime_manifest.json")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verify(path: Path, expected: str) -> None:
    actual = _sha256(path)
    if actual.lower() != expected.lower():
        raise RuntimeError(f"SHA-256 mismatch for {path.name}: {actual} != {expected}")


def _download(url: str, destination: Path, expected: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        try:
            _verify(destination, expected)
            return destination
        except RuntimeError:
            destination.unlink()
    partial = destination.with_suffix(destination.suffix + ".part")
    if partial.exists():
        partial.unlink()
    urllib.request.urlretrieve(url, partial)
    _verify(partial, expected)
    partial.replace(destination)
    return destination


def _runtime_archive(manifest: dict, cache: Path, supplied: Path | None) -> Path:
    spec = manifest["python"]
    if supplied is not None:
        _verify(supplied, spec["sha256"])
        return supplied
    return _download(spec["url"], cache / spec["filename"], spec["sha256"])


def _wheel_paths(manifest: dict, cache: Path, supplied: Path | None) -> list[Path]:
    result = []
    for wheel in manifest["wheels"]:
        filename = wheel["filename"]
        path = (supplied / filename) if supplied is not None else cache / "wheels" / filename
        if supplied is None:
            path = _download(wheel["url"], path, wheel["sha256"])
        elif not path.exists():
            raise FileNotFoundError(path)
        _verify(path, wheel["sha256"])
        result.append(path)
    return result


def _safe_member_path(root: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError(f"Unsafe archive member: {name}")
    target = (root / Path(*relative.parts)).resolve()
    if not target.is_relative_to(root.resolve()):
        raise RuntimeError(f"Archive traversal: {name}")
    return target


def _extract_python(archive: Path, destination: Path) -> None:
    """Extract regular files and materialize symlinks as portable copies."""
    pending: list[tuple[Path, str]] = []
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:*") as tar:
        for member in tar.getmembers():
            target = _safe_member_path(destination, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if member.issym() or member.islnk():
                pending.append((target, member.linkname))
                continue
            if not member.isfile():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise RuntimeError(f"Cannot read runtime member: {member.name}")
            with target.open("wb") as handle:
                shutil.copyfileobj(source, handle)
            target.chmod(stat.S_IMODE(member.mode) or 0o644)
    root = destination.resolve()
    for target, linkname in pending:
        # Terminfo and a few CPython support files use ../ links. Resolve them
        # relative to the link parent while keeping the final target in runtime.
        link_target = (target.parent / Path(*PurePosixPath(linkname).parts)).resolve()
        if not link_target.is_relative_to(root):
            raise RuntimeError(f"Runtime link escapes extraction root: {target} -> {linkname}")
        if not link_target.exists():
            raise RuntimeError(f"Broken runtime link: {target} -> {linkname}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with link_target.open("rb") as source, target.open("wb") as output:
            shutil.copyfileobj(source, output)
        target.chmod(link_target.stat().st_mode & 0o777)


def _extract_wheel(wheel: Path, site_packages: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            target = _safe_member_path(site_packages, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            if target.suffix == ".so" or ".so." in target.name:
                target.chmod(0o755)


def _prepare_runtime(stage: Path, archive: Path, wheels: list[Path], manifest: dict) -> None:
    extraction = stage.parent / "python-extracted"
    _extract_python(archive, extraction)
    python_root = extraction / "python"
    runtime = stage / "runtime"
    shutil.copytree(python_root, runtime)
    site_packages = runtime / "lib" / "python3.13" / "site-packages"
    site_packages.mkdir(parents=True, exist_ok=True)
    for wheel in wheels:
        _extract_wheel(wheel, site_packages)
    # The standalone interpreter ships pip and its cross-platform distlib
    # helpers.  The release is intentionally not user-extensible at runtime;
    # removing them also prevents Windows helper executables from leaking into
    # this Linux-only archive.
    for path in site_packages.glob("pip*"):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        elif path.exists():
            path.unlink()
    for executable in ("pip", "pip3", "pip3.13", "idle3", "idle3.13", "pydoc3", "pydoc3.13"):
        path = runtime / "bin" / executable
        if path.exists():
            path.unlink()
    for path in runtime.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".exe", ".dll", ".pyd"}:
            path.unlink()
            continue
        if path.is_file() and (path.suffix == ".so" or ".so." in path.name):
            path.chmod(0o755)
    for executable in ("python", "python3", "python3.13"):
        path = runtime / "bin" / executable
        if path.exists():
            path.chmod(0o755)
    font_source = site_packages / "matplotlib" / "mpl-data" / "fonts" / "ttf"
    font_target = stage / "fonts" / "matplotlib"
    if font_source.exists():
        shutil.copytree(font_source, font_target)
    (stage / "fonts" / "README.md").write_text(
        "Bundled Matplotlib/DejaVu and STIX fonts used for portable Linux PDF and UI fallback.\n",
        encoding="utf-8",
    )
    metadata = {
        "python": manifest["python"],
        "python_abi": PYTHON_TAG,
        "wheels": [{"name": item["name"], "version": item["version"], "sha256": item["sha256"]} for item in manifest["wheels"]],
        "runtime_mode": "self-contained CPython, PyQt6, scientific stack, and Psycopg binary client",
        "native_validation": "requires Linux Mint 22.3 x86_64 manual gate",
    }
    (runtime / "runtime_metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_payload(archive_root: Path, stage: Path) -> None:
    for relative in PAYLOAD_FILES:
        _copy_tree(archive_root / relative, stage / relative)
    for relative in PAYLOAD_DIRECTORIES:
        _copy_tree(archive_root / relative, stage / relative)
    for relative in LINUX_FILES:
        _copy_tree(archive_root / "source" / "launcher" / "linux" / relative, stage / "launcher" / relative)


def _safe_extract_archive(repo: Path, commit: str, destination: Path) -> None:
    archive = destination.parent / "repository.tar"
    with archive.open("wb") as handle:
        import subprocess
        subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", commit], check=True, stdout=handle)
    with tarfile.open(archive, "r:") as tar:
        for member in tar.getmembers():
            _safe_member_path(destination, member.name)
        tar.extractall(destination)


def _git_commit(repo: Path, requested: str | None) -> str:
    import subprocess
    value = requested or "HEAD"
    result = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", f"{value}^{{commit}}"], check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _commit_epoch(repo: Path, commit: str) -> int:
    import subprocess
    result = subprocess.run(["git", "-C", str(repo), "show", "-s", "--format=%ct", commit], check=True, capture_output=True, text=True)
    return int(result.stdout.strip())


def _set_modes(stage: Path) -> None:
    for relative in ("launcher/ProgTrack", "launcher/launcher.py", "runtime/bin/python", "runtime/bin/python3", "runtime/bin/python3.13"):
        path = stage / relative
        if path.exists():
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _write_metadata(stage: Path, commit: str, epoch: int, manifest: dict) -> None:
    metadata = {
        "artifact": f"ProgTrack-{VERSION}-linux-{ARCH}.tar.gz",
        "artifact_version": VERSION,
        "application_payload_version": "0.2.1",
        "source_commit": commit,
        "source_date_epoch": epoch,
        "platform": f"linux-{ARCH}",
        "launcher": "launcher/ProgTrack",
        "runtime_mode": "self-contained CPython 3.13.15 / PyQt6 6.7.1 / Psycopg binary",
        "python_abi": PYTHON_TAG,
        "glibc_baseline": "manylinux_2_28; Linux Mint 22.3 x86_64 acceptance target",
        "native_runtime_bundled": True,
        "postgresql_client": "psycopg-binary 3.3.4 with bundled libpq/TLS libraries",
        "native_linux_validation": "required before public support claim",
        "portable_mode": "PROGTRACK_PORTABLE=1 only in a writable folder",
        "xdg_mode": "default; XDG data/config/cache/state roots",
        "python_runtime_sha256": manifest["python"]["sha256"],
    }
    (stage / "release_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_inventory(stage: Path, commit: str, epoch: int, manifest: dict) -> None:
    files = []
    for path in sorted(p for p in stage.rglob("*") if p.is_file()):
        relative = path.relative_to(stage).as_posix()
        files.append({"path": relative, "sha256": _sha256(path), "mode": oct(stat.S_IMODE(path.stat().st_mode))})
    inventory = {
        "source_commit": commit,
        "source_date_epoch": epoch,
        "platform": f"linux-{ARCH}",
        "python": "3.13.15 bundled CPython",
        "python_abi": PYTHON_TAG,
        "glibc_baseline": "manylinux_2_28",
        "native_runtime_bundled": True,
        "postgresql_client": "psycopg-binary 3.3.4; libpq/OpenSSL bundled in wheel",
        "external_prerequisites": ["Linux kernel and graphical session", "glibc compatible with manylinux_2_28"],
        "files": files,
    }
    (stage / "linux_component_inventory.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _validate_stage(stage: Path) -> None:
    forbidden_roots = {"tests", "tmp", "outputs", "ProgTrackData", "source"}
    for path in stage.rglob("*"):
        relative = path.relative_to(stage)
        if relative.parts and relative.parts[0] in forbidden_roots:
            raise RuntimeError(f"Development/runtime data leaked into package: {relative}")
        if path.is_file() and path.suffix.lower() in {".exe", ".dll", ".pyd"}:
            raise RuntimeError(f"Windows runtime leaked into Linux package: {relative}")
    for required in (
        "launcher/ProgTrack",
        "launcher/launcher.py",
        "runtime/bin/python3",
        "runtime/lib/python3.13/site-packages/PyQt6",
        "runtime/lib/python3.13/site-packages/numpy",
        "runtime/lib/python3.13/site-packages/psycopg",
        "runtime/lib/python3.13/site-packages/psycopg_binary",
        "fonts/matplotlib",
        "Resources/Seed/progtrack_seed.ptdb",
        "icons/ui/manifest.json",
        "release_metadata.json",
        "linux_component_inventory.json",
    ):
        if not (stage / required).exists():
            raise RuntimeError(f"Linux package is missing {required}")


def _write_tar(stage: Path, archive: Path, epoch: int) -> None:
    import gzip
    root_name = archive.name.removesuffix(".tar.gz")
    with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=epoch) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for path in sorted(stage.rglob("*")):
            relative = path.relative_to(stage).as_posix()
            info = tar.gettarinfo(str(path), arcname=f"{root_name}/{relative}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = epoch
            if path.is_file():
                if relative.startswith("launcher/") or relative.startswith("runtime/bin/"):
                    info.mode = 0o755 if info.mode & 0o111 else 0o644
                with path.open("rb") as handle:
                    tar.addfile(info, handle)
            else:
                tar.addfile(info)


def build(output: Path, commit: str | None, python_archive: Path | None, wheelhouse: Path | None) -> dict[str, object]:
    repo = _repo_root()
    resolved = _git_commit(repo, commit)
    epoch = _commit_epoch(repo, resolved)
    manifest = _load_manifest()
    cache = Path(os.environ.get("PROGTRACK_LINUX_BUILD_CACHE", str(Path.home() / ".cache" / "progtrack-linux")))
    archive_path = _runtime_archive(manifest, cache, python_archive)
    wheel_paths = _wheel_paths(manifest, cache, wheelhouse)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"ProgTrack-{VERSION}-linux-{ARCH}.tar.gz"
    checksum = output / f"{archive.name}.sha256"
    for path in (archive, checksum):
        if path.exists():
            path.unlink()
    with tempfile.TemporaryDirectory(prefix="progtrack-linux-turnkey-") as temporary:
        temp_root = Path(temporary)
        archive_root = temp_root / "repository"
        stage = temp_root / "stage"
        archive_root.mkdir()
        stage.mkdir()
        _safe_extract_archive(repo, resolved, archive_root)
        _copy_payload(archive_root, stage)
        _prepare_runtime(stage, archive_path, wheel_paths, manifest)
        _set_modes(stage)
        _write_metadata(stage, resolved, epoch, manifest)
        _write_inventory(stage, resolved, epoch, manifest)
        _validate_stage(stage)
        _write_tar(stage, archive, epoch)
    digest = _sha256(archive)
    checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return {"archive": str(archive), "sha256": digest, "bytes": archive.stat().st_size, "source_commit": resolved, "source_date_epoch": epoch, "native_runtime_bundled": True}


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the self-contained ProgTrack Linux tar.gz")
    parser.add_argument("--commit", default=None)
    parser.add_argument("--output", default=str(_repo_root() / "source" / "launcher" / "linux" / "release"))
    parser.add_argument("--python-archive", type=Path, default=None)
    parser.add_argument("--wheelhouse", type=Path, default=None)
    args = parser.parse_args()
    print(json.dumps(build(Path(args.output).resolve(), args.commit, args.python_archive, args.wheelhouse), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
