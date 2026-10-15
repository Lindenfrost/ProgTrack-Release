"""Strict seed compilation and staged publication through the real backend."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from Plugins.core.backend.facade import ProgTrackBackend
from Plugins.core.experimental_limits import synchronize_experimental_limits
from Plugins.core.animal_roles import canonical_role_value
from Plugins.core.runtime_paths import resolve_runtime_paths
from .common import normalize_core, prune_removed_animal_references, rewrite_references
from .config import MANIFEST, ROOT, SEED_CREATED, SEED_PACKAGE_ID, source_hashes
from .generators import add_mouse_colony, add_shire_colony, normalize_mouse_public_ids, normalize_mature_offspring_roles, complete_scientific_histories
from .projections import domain_records, _normalise_seed_project_assignments, _apply_otof_example_experiment_status, _normalise_seed_partner_relationships
from .reproduction import add_reproduction_scenarios, assign_seed_event_ids
from .validation import audit_seed_sources, validate

_IMPORT_SOURCE_HASHES = source_hashes()


@dataclass
class SeedBundle:
    core: dict[str, Any]
    domains: dict[tuple[str, str], Any]
    scenario: dict[str, Any]
    report: dict[str, Any]


def build_bundle() -> SeedBundle:
    before = source_hashes()  # Require every declared input before generating anything.
    if before != _IMPORT_SOURCE_HASHES:
        raise ValueError("Seed sources changed since compiler import; restart the compiler")
    source_dates = audit_seed_sources()
    if source_dates["errors"]:
        raise ValueError("Seed source event dates failed:\n" + "\n".join(source_dates["errors"]))
    core, keys = normalize_core()
    mouse = add_mouse_colony(core, keys)
    shire = add_shire_colony(core, keys)
    normalize_mouse_public_ids(core)
    scenario = add_reproduction_scenarios(core, keys)
    scenario.update(mouse=mouse, shire=shire)
    normalize_mature_offspring_roles(core)
    _normalise_seed_project_assignments(core)
    _apply_otof_example_experiment_status(core)
    _normalise_seed_partner_relationships(core)
    for section in ("animals", "archived_animals"):
        for animal in core[section].values():
            synchronize_experimental_limits(animal, canonical_role_value(animal.get("rolle"), default="unknown"), prefer_flat=True, prune_unsupported=True, replace_nonpositive=True)
    complete_scientific_histories(core)
    domains = domain_records(core, keys, scenario)
    assign_seed_event_ids(core)
    domains[("seed", "metadata")].update({
        "source_schema": MANIFEST["schema"], "source_hashes": before,
        "scenario_ids": [s["id"] for s in MANIFEST["scenarios"]],
        "excluded_resources": MANIFEST["excluded_resources"],
    })
    report = validate(core, domains, scenario)
    if not report["valid"]:
        raise ValueError("Seed validation failed:\n" + "\n".join(report["errors"]))
    if before != source_hashes():
        raise ValueError("Seed sources changed during compilation; retry with stable inputs")
    report["source_hashes"] = before
    report["source_event_date_audit"] = source_dates
    return SeedBundle(core, domains, scenario, report)


def coverage_markdown(report: dict[str, Any]) -> str:
    lines = [f"# ProgTrack {MANIFEST['seed_version']} fictional seed scenario coverage", "", "Generated from source/seed/manifest.json; edit scenario declarations, not this report.", "", "The package is the single fictional initialization source for both backend adapters.", "", "| Scenario | Coverage |", "|---|---|"]
    lines.extend(f"| {s['id']} | {s['description']} |" for s in MANIFEST["scenarios"])
    lines.extend(["", "## Compiled counts", ""])
    lines.extend(f"- {name}: {value}" for name, value in sorted(report["counts"].items()))
    lines.extend(["", "Demo password: `123456`. Never use these fictional accounts for production.", ""])
    return "\n".join(lines)


def compile_to(output_dir: Path, *, check: bool = False) -> dict[str, Any]:
    bundle = build_bundle()
    with tempfile.TemporaryDirectory(prefix="progtrack-seed-compile-") as temporary:
        staging = Path(temporary)
        app_root = staging / "app"
        app_root.mkdir()
        paths = resolve_runtime_paths(app_root, environ={
            "PROGTRACK_PORTABLE": "1", "PROGTRACK_BACKEND_PROFILE": "standalone_sqlite",
            "PROGTRACK_SQLITE_PATH": str(staging / "seed.sqlite3"),
            "PROGTRACK_MANAGED_ROOT": str(staging / "managed"),
        })
        backend = ProgTrackBackend(paths, acquire_process_lock=False, bootstrap_seed=False)
        package = staging / "progtrack_seed.ptdb"
        try:
            backend.save_core_data(bundle.core)
            for (namespace, record_id), payload in sorted(bundle.domains.items()):
                backend.records.put(namespace, record_id, payload)
            backend.interchange.export_package(package, package_id=SEED_PACKAGE_ID, created_at=SEED_CREATED)
            preview = backend.interchange.preview(package)
            if not preview.valid:
                raise ValueError("Exported seed failed interchange validation: " + "; ".join(preview.errors))
        finally:
            backend.close()
        report = bundle.report
        report["package_sha256"] = hashlib.sha256(package.read_bytes()).hexdigest()
        (staging / "integrity_report.json").write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")
        (staging / "SCENARIO_COVERAGE.md").write_text(coverage_markdown(report), encoding="utf-8", newline="\n")
        names = ("progtrack_seed.ptdb", "integrity_report.json", "SCENARIO_COVERAGE.md")
        if check:
            mismatches = [n for n in names if not (output_dir / n).is_file() or (output_dir / n).read_bytes() != (staging / n).read_bytes()]
            if mismatches:
                raise ValueError("Generated seed artifacts differ or are missing: " + ", ".join(mismatches))
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            # Each replace is atomic. Restore the entire previous artifact set if a
            # later publication fails; no partial new package/report combination.
            previous = {n: (output_dir / n).read_bytes() if (output_dir / n).is_file() else None for n in names}
            published = []
            try:
                for name in names:
                    target = output_dir / name
                    pending = target.with_suffix(target.suffix + ".pending")
                    shutil.copyfile(staging / name, pending)
                    os.replace(pending, target)
                    published.append(name)
            except Exception:
                for name in published:
                    target = output_dir / name
                    if previous[name] is None:
                        target.unlink(missing_ok=True)
                    else:
                        target.write_bytes(previous[name])
                raise
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compile the repository-owned fictional ProgTrack seed")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "Resources" / "Seed")
    parser.add_argument("--check", action="store_true", help="Build in isolation and compare artifacts without replacing them")
    args = parser.parse_args(argv)
    try:
        report = compile_to(args.output_dir.resolve(), check=args.check)
    except (OSError, ValueError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
