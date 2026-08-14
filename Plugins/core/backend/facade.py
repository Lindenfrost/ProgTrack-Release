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
from .postgresql_admin import PostgreSQLAdministrationService
from ..backend_configuration import BackendConfigurationService, PostgreSQLSettings
from .errors import BackendConfigurationError


class ProgTrackBackend:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        postgres_dsn: str = "",
        postgres_pool_min: int | None = None,
        postgres_pool_max: int | None = None,
        acquire_process_lock: bool = True,
        bootstrap_seed: bool = True,
    ):
        self.paths = paths
        self.adapter = create_adapter(
            paths,
            postgres_dsn=postgres_dsn,
            postgres_pool_min=postgres_pool_min,
            postgres_pool_max=postgres_pool_max,
            acquire_process_lock=acquire_process_lock,
        )
        self.adapter.migrate()
        self.animals = AnimalService(self.adapter)
        self.records = DomainRecordRepository(self.adapter)
        self.leases = LeaseRepository(self.adapter)
        self.audit = AuditRepository(self.adapter)
        self.schemas = SchemaRegistry(self.adapter)
        managed_root = Path(paths.managed_root)
        if paths.profile.value == "shared_postgresql":
            server_root = self.adapter.get_installation_value(
                "managed_document_root", None
            )
            if server_root:
                managed_root = Path(str(server_root)).expanduser()
            else:
                if not str(managed_root).strip() or not managed_root.is_absolute():
                    raise BackendConfigurationError(
                        "Shared PostgreSQL requires a deployment-managed document root."
                    )
                self.adapter.set_installation_value(
                    "managed_document_root", str(managed_root)
                )
            if not managed_root.is_dir():
                raise BackendConfigurationError(
                    "The Shared PostgreSQL managed document root is unavailable: "
                    + str(managed_root)
                )
        self.managed_root = managed_root
        self.documents = ManagedFileService(
            self.adapter,
            managed_root / "documents",
            managed_root / "config-assets",
        )
        self.interchange = InterchangeService(self, self.documents)
        self.branding = InstitutionBrandingService(self)
        if bootstrap_seed:
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

    def administration_service(
        self,
        settings: PostgreSQLSettings,
        *,
        password: str = "",
        authorized: bool,
        actor_login: str = "",
        audit_callback: Any = None,
    ) -> PostgreSQLAdministrationService:
        configuration = BackendConfigurationService(self.paths)
        return PostgreSQLAdministrationService(
            configuration,
            settings,
            password=password,
            authorized=authorized,
            actor_login=actor_login,
            audit_callback=audit_callback,
        )

    def load_core_data(self) -> dict[str, Any]:
        return self.animals.load_snapshot()

    def save_core_data(self, data: Mapping[str, Any]) -> None:
        self.animals.replace_snapshot(data)
