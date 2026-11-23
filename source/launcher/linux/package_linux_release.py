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
import re
import shutil
import stat
import struct
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
    "release_inventory.json",
    "Username + 123456 password.png",
    "info.json",
    "info_de.json",
    "info_en.json",
    "info_it.json",
    "info_ru.json",
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
FONT_LICENSE_NOTICE = Path(__file__).with_name("fonts") / "LICENSE_NOTICE.md"
SITE_PACKAGES = "runtime/lib/python3.13/site-packages"
NATIVE_DEPENDENCIES = {
    "libcom_err": "e2fsprogs com_err",
    "libcrypt": "crypt",
    "libcrypto": "OpenSSL 3.5.4",
    "libgssapi_krb5": "MIT Kerberos GSSAPI",
    "libk5crypto": "MIT Kerberos crypto",
    "libkeyutils": "Linux keyutils",
    "libkrb5": "MIT Kerberos",
    "libkrb5support": "MIT Kerberos support",
    "liblber": "OpenLDAP BER",
    "libldap": "OpenLDAP",
    "libpcre": "PCRE",
    "libpq": "PostgreSQL libpq",
    "libsasl2": "Cyrus SASL",
    "libselinux": "SELinux",
    "libssl": "OpenSSL 3.5.4",
}
NATIVE_LICENSES = {
    "libcrypto": "LICENSE_OPENSSL.txt",
    "libssl": "LICENSE_OPENSSL.txt",
    "libpq": "LICENSE_POSTGRESQL.txt",
}
COMMON_LICENSES = {
    "LICENSE_COMPUTER_MODERN_BAKOMA.txt",
    "LICENSE_REPORTLAB_BITSTREAM_VERA.txt",
    "LICENSE_REPORTLAB_DARKGARDEN.txt",
    "LICENSE_REPORTLAB_DARKGARDEN_GPL.txt",
    "LICENSE_SQLITE.txt",
    *NATIVE_LICENSES.values(),
}


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


def _extract_wheel(wheel: Path, site_packages: Path) -> list[str]:
    members = []
    with zipfile.ZipFile(wheel) as archive:
        for info in archive.infolist():
            target = _safe_member_path(site_packages, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            members.append(info.filename)
    return members


def _prepare_runtime(stage: Path, archive: Path, wheels: list[Path], manifest: dict) -> tuple[dict, dict]:
    extraction = stage.parent / "python-extracted"
    _extract_python(archive, extraction)
    python_root = extraction / "python"
    runtime = stage / "runtime"
    shutil.copytree(python_root, runtime)
    site_packages = stage / SITE_PACKAGES
    site_packages.mkdir(parents=True, exist_ok=True)
    owners = {}
    components = {
        "CPython": {
            "version": manifest["python"]["version"],
            "input_sha256": manifest["python"]["sha256"],
            "license_files": ["runtime/lib/python3.13/LICENSE.txt"],
        },
        "SQLite": {
            "version": "requires runtime probe",
            "license_files": ["third_party_licenses/LICENSE_SQLITE.txt"],
        },
    }
    for wheel, pin in zip(wheels, manifest["wheels"], strict=True):
        name = pin["name"]
        licenses = []
        for member in _extract_wheel(wheel, site_packages):
            relative = f"{SITE_PACKAGES}/{member}"
            if relative in owners:
                raise RuntimeError(f"Multiple wheel owners for {relative}")
            owners[relative] = name
            if ".dist-info/" in member and Path(member).name.lower().startswith(("licen", "copying")):
                licenses.append(relative)
        if name == "PyQt6":
            licenses.append("LICENSE")
        if not licenses:
            raise RuntimeError(f"Wheel has no recorded license file: {wheel.name}")
        components[name] = {
            "version": pin["version"],
            "input_sha256": pin["sha256"],
            "license_files": sorted(licenses),
        }
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
    font_source = site_packages / "matplotlib" / "mpl-data" / "fonts" / "ttf"
    font_target = stage / "fonts" / "matplotlib"
    if font_source.exists():
        shutil.copytree(font_source, font_target)
    if not FONT_LICENSE_NOTICE.is_file():
        raise FileNotFoundError(f"Linux font license notice is missing: {FONT_LICENSE_NOTICE}")
    shutil.copy2(FONT_LICENSE_NOTICE, stage / "fonts" / "LICENSE_NOTICE.md")
    (stage / "fonts" / "README.md").write_text(
        "Bundled Matplotlib fonts used for portable Linux PDF and UI fallback. "
        "Rights and redistribution terms are in LICENSE_NOTICE.md; the upstream "
        "font license files are retained in matplotlib/.\n",
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
    return components, owners


def _copy_tree(source: Path, destination: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=False)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _application_payload(archive_root: Path) -> tuple[Path, str]:
    candidates = sorted(archive_root.glob("ProgTrack.v.*.py"))
    if len(candidates) != 1:
        raise RuntimeError(
            f"Expected exactly one committed application payload; found {len(candidates)}"
        )
    payload = candidates[0]
    match = re.fullmatch(r"ProgTrack\.v\.(\d+\.\d+\.\d+)\.py", payload.name)
    if match is None:
        raise RuntimeError(f"Unsupported application payload filename: {payload.name}")
    return payload, match.group(1)


def _copy_payload(archive_root: Path, stage: Path) -> None:
    application_payload, _payload_version = _application_payload(archive_root)
    _copy_tree(application_payload, stage / application_payload.name)
    for relative in PAYLOAD_FILES:
        _copy_tree(archive_root / relative, stage / relative)
    for relative in PAYLOAD_DIRECTORIES:
        _copy_tree(archive_root / relative, stage / relative)
    for license_file in (stage / "third_party_licenses").iterdir():
        if license_file.name not in COMMON_LICENSES:
            license_file.unlink()
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


def _archive_mode(relative: str, *, directory: bool, native: bool) -> int:
    executable = (
        relative in {"launcher/ProgTrack", "launcher/launcher.py"}
        or relative.startswith("runtime/bin/")
        or native
    )
    return 0o755 if directory or executable else 0o644


def _elf_abi(path: Path) -> dict | None:
    with path.open("rb") as handle:
        header = handle.read(20)
    if not header.startswith(b"\x7fELF"):
        return None
    if len(header) != 20 or header[4:6] != b"\x02\x01" or struct.unpack_from("<H", header, 18)[0] != 62:
        raise RuntimeError(f"Expected little-endian x86_64 ELF: {path}")
    extension = re.search(r"\.(cpython-(\d+)[^.]*|abi3)\.so$", path.name)
    abi = None
    if extension:
        abi = f"cp{extension[2]}" if extension[2] else "abi3"
        if abi not in {PYTHON_TAG, "abi3"}:
            raise RuntimeError(f"Unexpected Python extension ABI {abi}: {path}")
    return {"format": "ELF64", "machine": ARCH, "python_abi": abi}


def _write_metadata(stage: Path, commit: str, epoch: int, manifest: dict) -> None:
    _application, payload_version = _application_payload(stage)
    metadata = {
        "artifact": f"ProgTrack-{VERSION}-linux-{ARCH}.tar.gz",
        "artifact_version": VERSION,
        "application_payload_version": payload_version,
        "application_payload_file": _application.name,
        "launcher_version": VERSION,
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


def _write_inventory(stage: Path, commit: str, epoch: int, components: dict, owners: dict) -> set[str]:
    files = []
    native_files = set()
    license_review = set()
    for path in sorted(p for p in stage.rglob("*") if p.is_file()):
        relative = path.relative_to(stage).as_posix()
        native = _elf_abi(path) if relative.startswith("runtime/") else None
        record = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "mode": oct(_archive_mode(relative, directory=False, native=native is not None)),
        }
        owner = owners.get(relative)
        if relative.startswith("fonts/matplotlib/"):
            owner = "matplotlib"
        elif relative.startswith("runtime/") and relative != "runtime/runtime_metadata.json":
            owner = owner or "CPython"
        if owner:
            record["owner"] = owner
        if native:
            native_files.add(relative)
            record["native"] = native
            record["license_files"] = components[owner]["license_files"]
            if ".libs/" in relative:
                native["library"] = re.split(r"-[0-9a-f]{8}(?:-|\.so)", path.name)[0]
            if owner == "psycopg-binary" and native.get("library"):
                library = native["library"]
                native["component"] = NATIVE_DEPENDENCIES.get(library, library)
                library_license = NATIVE_LICENSES.get(library)
                if library_license:
                    record["license_files"] = [f"third_party_licenses/{library_license}"]
                else:
                    record["license_files"] = []
                    license_review.add(native["component"])
            if path.name.startswith("_sqlite3."):
                native["linked_component"] = "SQLite"
                record["license_files"] += components["SQLite"]["license_files"]
        files.append(record)
    for entry in files:
        for license_file in entry.get("license_files", []):
            if not (stage / license_file).is_file():
                raise RuntimeError(f"Missing native license file: {license_file}")
    for name, component in components.items():
        for license_file in component["license_files"]:
            if not (stage / license_file).is_file():
                raise RuntimeError(f"Missing {name} license file: {license_file}")
    inventory = {
        "schema": "progtrack-linux-component-inventory/2",
        "source_commit": commit,
        "source_date_epoch": epoch,
        "platform": f"linux-{ARCH}",
        "python": "3.13.15 bundled CPython",
        "python_abi": PYTHON_TAG,
        "glibc_baseline": "manylinux_2_28",
        "native_runtime_bundled": True,
        "postgresql_client": "psycopg-binary; libpq/OpenSSL bundled in wheel",
        "external_prerequisites": [
            "Linux x86_64, glibc 2.28 or newer, and a graphical session",
            "System C++ runtime, graphics, X11/XCB or Wayland, font and other Qt dependencies",
            "Confirm actual loaded libraries and active Qt plugins on clean Linux Mint 22.3",
        ],
        "components": components,
        "native_licenses_requiring_reconciliation": sorted(license_review),
        "files": files,
    }
    (stage / "linux_component_inventory.json").write_text(json.dumps(inventory, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return native_files


def _write_notices(stage: Path, components: dict) -> None:
    notice = [
        "# Linux third-party notices", "",
        "This archive contains the CPython runtime, SQLite through CPython, and "
        "the distributions listed below. The shipped license files are linked by "
        "archive path. The file hashes and ELF ownership records are in "
        "`linux_component_inventory.json`.", "",
        "| Component | Version | Shipped license files |",
        "| --- | --- | --- |",
    ]
    for name, component in components.items():
        links = ", ".join(f"[`{path}`]({path})" for path in component["license_files"])
        notice.append(f"| {name} | {component['version']} | {links} |")
    notice.extend([
        "", "## Native dependencies", "",
        "ProgTrack uses the bundled Psycopg binary implementation. The wheel includes "
        "PostgreSQL libpq and OpenSSL 3.5.4 as well as the native libraries listed below. "
        "Native runtime version and connection checks require Linux execution.", "",
        *(
            f"- {component} ({library})"
            + (f" [License notice](third_party_licenses/{NATIVE_LICENSES[library]})"
               if library in NATIVE_LICENSES else " — upstream license file reconciliation pending")
            for library, component in sorted(NATIVE_DEPENDENCIES.items())
        ),
        "", "The CPython, Qt, Pillow and scientific artifacts also include native code. "
        "The component inventory identifies the exact shipped binaries and their distributor "
        "license files. Redistribution review for transitive native components and Qt "
        "third-party code remains pending.", "",
        "Matplotlib fonts are also shipped under `fonts/matplotlib/`. "
        "See [font notices](fonts/LICENSE_NOTICE.md) and the "
        "[BaKoMa notice](third_party_licenses/LICENSE_COMPUTER_MODERN_BAKOMA.txt).", "",
        "ReportLab includes [Bitstream Vera](third_party_licenses/LICENSE_REPORTLAB_BITSTREAM_VERA.txt) "
        "and [DarkGarden](third_party_licenses/LICENSE_REPORTLAB_DARKGARDEN.txt) "
        "fonts with [GPL terms for DarkGarden](third_party_licenses/LICENSE_REPORTLAB_DARKGARDEN_GPL.txt).", "",
    ])
    (stage / "THIRD_PARTY_NOTICES.md").write_text("\n".join(notice), encoding="utf-8")


def _validate_stage(stage: Path) -> None:
    application_payload, payload_version = _application_payload(stage)
    metadata_path = stage / "release_metadata.json"
    if metadata_path.is_file():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("application_payload_version") != payload_version:
            raise RuntimeError("Release metadata does not match the staged application payload")
        if metadata.get("application_payload_file") != application_payload.name:
            raise RuntimeError("Release metadata names a different application payload")
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
        "fonts/LICENSE_NOTICE.md",
        "Resources/Seed/progtrack_seed.ptdb",
        "icons/ui/manifest.json",
        "release_metadata.json",
        "release_inventory.json",
        "linux_component_inventory.json",
    ):
        if not (stage / required).exists():
            raise RuntimeError(f"Linux package is missing {required}")


def _write_tar(stage: Path, archive: Path, epoch: int, native_files: set[str]) -> None:
    import gzip
    root_name = archive.name.removesuffix(".tar.gz")
    with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=epoch) as gz, tarfile.open(fileobj=gz, mode="w") as tar:
        for path in sorted(stage.rglob("*")):
            relative = path.relative_to(stage).as_posix()
            info = tar.gettarinfo(str(path), arcname=f"{root_name}/{relative}")
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mtime = epoch
            info.mode = _archive_mode(relative, directory=path.is_dir(), native=relative in native_files)
            if path.is_file():
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
        components, owners = _prepare_runtime(stage, archive_path, wheel_paths, manifest)
        _write_metadata(stage, resolved, epoch, manifest)
        _write_notices(stage, components)
        native_files = _write_inventory(stage, resolved, epoch, components, owners)
        _validate_stage(stage)
        _write_tar(stage, archive, epoch, native_files)
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
