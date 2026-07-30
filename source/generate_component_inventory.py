"""Generate and validate the frozen launcher component inventory."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import re
import sys
from pathlib import Path


PINNED = (
    "psycopg", "psycopg-binary", "psycopg-pool", "pypdf", "PyQt6",
    "matplotlib", "numpy", "pandas", "scipy", "openpyxl", "reportlab",
    "Pillow", "pyqtgraph",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        raise SystemExit("usage: generate_component_inventory.py DIST_DIR")
    root = Path(argv[1]).resolve()
    files = []
    python_runtime_families = set()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        files.append({
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
        # ``python3.dll`` is the stable-ABI forwarding DLL, not a second
        # interpreter family. Only versioned runtime DLLs (for example
        # python313.dll) identify the frozen interpreter ABI.
        match = re.fullmatch(r"python(\d{3})\.dll", path.name.casefold())
        if match:
            python_runtime_families.add(match.group(1))
    expected_family = f"{sys.version_info.major}{sys.version_info.minor}"
    errors = []
    if python_runtime_families != {expected_family}:
        errors.append(
            f"Python runtime families {sorted(python_runtime_families)}; "
            f"expected only {expected_family}"
        )
    packages = {}
    for name in PINNED:
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            errors.append(f"missing build package: {name}")
    from psycopg import pq
    if pq.__impl__ != "binary":
        errors.append(f"Windows artifact requires psycopg binary, got {pq.__impl__}")
    inventory = {
        "schema": "progtrack-component-inventory/1",
        "python": sys.version,
        "python_runtime_families": sorted(python_runtime_families),
        "packages": packages,
        "psycopg_implementation": pq.__impl__,
        "libpq_version": pq.version(),
        "files": files,
        "errors": errors,
        "valid": not errors,
    }
    target = root / "component_inventory.json"
    target.write_text(
        json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    if errors:
        raise SystemExit("\n".join(errors))
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
