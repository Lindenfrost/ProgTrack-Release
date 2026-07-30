"""Configured ProgTrack backend facade."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from ..runtime_paths import RuntimePaths
from .adapters import create_adapter
from .animal_service import AnimalService
from .repositories import AuditRepository, DomainRecordRepository, LeaseRepository
from .managed_files import ManagedFileService
from .interchange import InterchangeService
from ..institution_branding import InstitutionBrandingService
from .schema_registry import SchemaRegistry


class ProgTrackBackend:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        postgres_dsn: str = "",
        acquire_process_lock: bool = True,
    ):
        self.paths = paths
        self.adapter = create_adapter(
            paths,
            postgres_dsn=postgres_dsn,
            acquire_process_lock=acquire_process_lock,
        )
        self.adapter.migrate()
        self.animals = AnimalService(self.adapter)
        self.records = DomainRecordRepository(self.adapter)
        self.leases = LeaseRepository(self.adapter)
        self.audit = AuditRepository(self.adapter)
        self.schemas = SchemaRegistry(self.adapter)
        self.documents = ManagedFileService(
            self.adapter,
            paths.managed_documents,
            paths.managed_config_assets,
        )
        self.interchange = InterchangeService(self, self.documents)
        self.branding = InstitutionBrandingService(self)
        self._bootstrap_seed_if_empty()

    def _bootstrap_seed_if_empty(self) -> None:
        snapshot = self.load_core_data()
        if snapshot.get("animals") or snapshot.get("archived_animals"):
            return
        if self.records.namespace_names():
            return
        seed = (
            self.paths.application_root
            / "Resources"
            / "Seed"
            / "progtrack_seed.ptdb"
        )
        if seed.is_file():
            preview = self.interchange.import_package(seed, require_empty=True)
            if not preview.valid:
                raise RuntimeError(
                    "Bundled ProgTrack seed is invalid: "
                    + "; ".join(preview.errors)
                )

    def close(self) -> None:
        self.adapter.close()

    def load_core_data(self) -> dict[str, Any]:
        return self.animals.load_snapshot()

    def save_core_data(self, data: Mapping[str, Any]) -> None:
        self.animals.replace_snapshot(data)
