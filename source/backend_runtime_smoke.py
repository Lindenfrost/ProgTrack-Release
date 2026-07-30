#!/usr/bin/env python3
"""Backend-neutral Phase 2B smoke test for developer and frozen runtimes."""

from __future__ import annotations

import tempfile
import sys
import os
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from Plugins.core.backend import ProgTrackBackend
from Plugins.core.backend.errors import ConflictError, LockConflictError
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths


def _paths(root: Path, name: str) -> RuntimePaths:
    base = root / name
    return RuntimePaths(
        application_root=root / "empty-app",
        profile=BackendProfile.STANDALONE_SQLITE,
        data_root=base / "data",
        config_root=base / "config",
        cache_root=base / "cache",
        state_root=base / "state",
        database_path=base / "data" / "database" / "progtrack.sqlite3",
        managed_root=base / "data" / "managed",
        managed_documents=base / "data" / "managed" / "documents",
        managed_config_assets=base / "data" / "managed" / "config-assets",
        logs=base / "state" / "logs",
        runtime=base / "state" / "runtime",
        exports=base / "data" / "exports",
        preferences=base / "config" / "preferences",
        profile_file=base / "config" / "backend.json",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="progtrack-backend-smoke-") as temp:
        root = Path(temp)
        source_paths = _paths(root, "source")
        source_paths.create_mutable_roots()
        backend = ProgTrackBackend(source_paths)

        revision = backend.records.put("smoke", "record", {"value": 1})
        if revision != 1:
            raise RuntimeError("Initial repository revision is not 1.")
        backend.records.put(
            "smoke", "record", {"value": 2}, expected_revision=revision
        )
        try:
            backend.records.put(
                "smoke", "record", {"value": 3}, expected_revision=revision
            )
        except ConflictError:
            pass
        else:
            raise RuntimeError("A stale optimistic revision was accepted.")

        lease = backend.leases.acquire(
            "animal", "smoke-animal",
            owner_login="first", owner_display="First user",
        )
        try:
            backend.leases.acquire(
                "animal", "smoke-animal",
                owner_login="second", owner_display="Second user",
            )
        except LockConflictError:
            pass
        else:
            raise RuntimeError("A conflicting entity lease was accepted.")
        if not backend.leases.heartbeat(lease["token"]):
            raise RuntimeError("Entity lease heartbeat failed.")
        if not backend.leases.force_release_as_lord(
            "animal", "smoke-animal",
            actor_role="lord",
            reason="automated smoke test",
            audit=backend.audit,
            actor_login="smoke-lord",
        ):
            raise RuntimeError("Lord force-release failed.")

        source = root / "document.pdf"
        source.write_bytes(b"%PDF-1.4\nProgTrack managed-file smoke\n")
        managed = backend.documents.add(
            source,
            owner_type="project",
            owner_id="smoke-project",
            actor="smoke-user",
        )
        package = root / "complete.ptdb"
        backend.interchange.export_package(
            package,
            package_id="phase-2b-smoke",
            created_at="2026-07-30T00:00:00+00:00",
        )
        if not backend.interchange.preview(package).valid:
            raise RuntimeError("Exported interchange package failed validation.")
        backend.close()

        target_paths = _paths(root, "target")
        target_paths.create_mutable_roots()
        target = ProgTrackBackend(target_paths)
        preview = target.interchange.import_package(package)
        imported = target.documents.list_active()
        if not preview.valid or target.records.get("smoke", "record") != {"value": 2}:
            raise RuntimeError("Interchange record round-trip failed.")
        if len(imported) != 1 or imported[0]["document_id"] != managed["document_id"]:
            raise RuntimeError("Managed document identity was not preserved.")
        if target.documents.payload_path(imported[0]).read_bytes() != source.read_bytes():
            raise RuntimeError("Managed document payload changed during transfer.")
        target.close()

    result = "ProgTrack backend runtime smoke: PASS"
    result_path = os.environ.get("PROGTRACK_BACKEND_SMOKE_RESULT")
    if result_path:
        Path(result_path).write_text(result + "\n", encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
