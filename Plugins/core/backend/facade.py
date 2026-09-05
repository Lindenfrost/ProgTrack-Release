"""Configured ProgTrack backend facade."""

from __future__ import annotations

import os
import copy
import json
from pathlib import Path
from typing import Any, Mapping

from ..runtime_paths import RuntimePaths
from .adapters import create_adapter
from .animal_service import AnimalService
from .repositories import (
    AuditRepository,
    DomainRecordRepository,
    LeaseRepository,
    _execute,
    _fetchall,
    _fetchone,
    _json_placeholder,
    _placeholder,
    now_text,
)
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

    def save_core_data(
        self,
        data: Mapping[str, Any],
        *,
        expected_parentage_revision: str | None = None,
    ) -> None:
        self.animals.replace_snapshot(
            data,
            expected_parentage_revision=expected_parentage_revision,
        )

    def delete_archived_animal_atomically(
        self,
        ipid: str,
        record: Mapping[str, Any],
        *,
        owner_unit_id: str = "",
    ) -> bool:
        """Delete one archived Core row and preserve its Heritage lineage.

        Core deletion and the optional former-Core snapshot share the same
        adapter transaction.  The operation intentionally lives in the
        backend facade, not in the optional Heritage plugin: a disabled or
        unavailable visualization plugin must not decide whether Core
        lineage data is preserved.
        """
        key = str(ipid or "").strip()
        if not isinstance(record, Mapping):
            return False
        # Callers may identify an archived row by its display-name mapping
        # key while the normalized backend row is always keyed by stable
        # IPID.  Prefer the immutable value from the supplied record so the
        # coordinated delete cannot silently skip the intended row.
        record_ipid = str(record.get("ipid", "") or "").strip()
        if record_ipid:
            key = record_ipid
        if not key:
            return False
        mark = _placeholder(self.adapter)
        json_mark = _json_placeholder(self.adapter)
        timestamp = now_text()
        aliases = {
            key,
            str(record.get("ipid", "") or "").strip(),
            str(record.get("name", "") or "").strip(),
            str(record.get("_base_name", "") or "").strip(),
            str(record.get("display_name", "") or "").strip(),
        }
        aliases.discard("")

        def _parent_value(field: str, *legacy_fields: str) -> str:
            value = str(record.get(field, "") or "").strip()
            if value:
                return value
            for legacy in legacy_fields:
                value = str(record.get(legacy, "") or "").strip()
                if value:
                    return value
            return ""

        former_entry = {
            "ipid": key,
            "name": str(record.get("name") or record.get("_base_name") or key).strip(),
            "_base_name": str(record.get("_base_name") or record.get("name") or key).strip(),
            "display_name": str(record.get("display_name") or record.get("name") or key).strip(),
            "genotype": str(record.get("genotype", "") or "").strip(),
            "node_fill_color": str(record.get("node_fill_color", "") or "").strip(),
            "sex": str(record.get("sex", "") or "").strip(),
            "species": str(record.get("species", "") or "").strip(),
            "birth_date": str(record.get("birth_date") or record.get("geburtsdatum") or "").strip(),
            "egg_donor": _parent_value("egg_donor", "eizellspenderin"),
            "sperm_donor": _parent_value("sperm_donor", "samenspender"),
            "surrogate_mother": _parent_value("surrogate_mother", "ziehmutter"),
            "surrogate_father": _parent_value("surrogate_father", "ziehvater"),
            "heritage_only": True,
            "identity_review_required": False,
            "identity_review_reason": "",
            "unit_id": str(owner_unit_id or "").strip(),
            "dummy_kind": "former_core",
            "persistence_kind": "former_core_dummy",
            "source": "former_core_dummy",
            "updated_at": timestamp,
            "parentage_revision": "",
            "parentage_revision_display": "",
            "genetic_parentage_revision": "",
            "inbreeding_f": None,
            "inbreeding_f_cache": None,
        }

        with self.adapter.transaction(write=True) as connection:
            row = _fetchone(
                connection,
                f"SELECT archived FROM animals WHERE ipid={mark}",
                (key,),
            )
            if row is None:
                return False
            archived_value = row["archived"] if isinstance(row, dict) else row[0]
            if not bool(archived_value):
                return False

            heritage_row = _fetchone(
                connection,
                f"SELECT payload_json,revision FROM domain_records "
                f"WHERE namespace={mark} AND record_id={mark}",
                ("heritage", "graph"),
            )
            if heritage_row is None:
                heritage_payload: dict[str, Any] = {"version": "1.0.0", "animals": {}}
                heritage_revision = 0
            else:
                raw_payload = heritage_row["payload_json"] if isinstance(heritage_row, dict) else heritage_row[0]
                if isinstance(raw_payload, (dict, list)):
                    heritage_payload = copy.deepcopy(raw_payload)
                else:
                    try:
                        heritage_payload = json.loads(raw_payload or "{}")
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        # Do not delete a Core row when the optional lineage
                        # record is corrupt: silently replacing it with an
                        # empty graph would lose a parent reference.  Raising
                        # here rolls the whole transaction back.
                        raise ValueError("Heritage graph payload is invalid") from exc
                if not isinstance(heritage_payload, dict):
                    raise ValueError("Heritage graph payload must be an object")
                heritage_revision = int(
                    heritage_row["revision"] if isinstance(heritage_row, dict) else heritage_row[1]
                )
            animals = heritage_payload.setdefault("animals", {})
            if not isinstance(animals, dict):
                raise ValueError("Heritage graph animals must be an object")

            def references_alias(candidate: Mapping[str, Any]) -> bool:
                for field in (
                    "egg_donor", "sperm_donor", "surrogate_mother", "surrogate_father",
                    "eizellspenderin", "samenspender", "ziehmutter", "ziehvater",
                ):
                    if str(candidate.get(field, "") or "").strip() in aliases:
                        return True
                return False

            referenced = False
            for candidate in animals.values():
                if not isinstance(candidate, Mapping):
                    continue
                if references_alias(candidate):
                    referenced = True
                    break

            # Core parentage may be projected directly from Core without a
            # persisted Heritage graph.  Inspect every other Core record in
            # the same transaction so deleting an archived parent cannot
            # silently sever that lineage merely because Heritage is absent
            # or disabled.
            if not referenced:
                core_rows = _fetchall(
                    connection,
                    "SELECT ipid,record_json FROM animals",
                )
                for row in core_rows:
                    row_ipid = row["ipid"] if isinstance(row, dict) else row[0]
                    if str(row_ipid or "").strip() == key:
                        continue
                    raw_record = row["record_json"] if isinstance(row, dict) else row[1]
                    if isinstance(raw_record, Mapping):
                        core_record = raw_record
                    else:
                        try:
                            core_record = json.loads(raw_record or "{}")
                        except (TypeError, ValueError, json.JSONDecodeError) as exc:
                            raise ValueError("Core animal payload is invalid") from exc
                    if not isinstance(core_record, Mapping):
                        raise ValueError("Core animal payload must be an object")
                    if references_alias(core_record):
                        referenced = True
                        break
            if referenced and key not in animals:
                if not str(former_entry.get("unit_id", "") or "").strip():
                    raise ValueError(
                        "A former-Core Heritage snapshot requires an owning Unit"
                    )
                animals[key] = copy.deepcopy(former_entry)
                heritage_payload["updated_at"] = timestamp
                serialized = json.dumps(heritage_payload, ensure_ascii=False, separators=(",", ":"))
                if heritage_revision:
                    updated = _execute(
                        connection,
                        f"UPDATE domain_records SET payload_json={json_mark},revision={mark},updated_at={mark} "
                        f"WHERE namespace={mark} AND record_id={mark}",
                        (serialized, heritage_revision + 1, timestamp, "heritage", "graph"),
                    )
                    if int(updated.rowcount or 0) != 1:
                        raise ValueError("Heritage graph changed during Core deletion")
                else:
                    inserted = _execute(
                        connection,
                        "INSERT INTO domain_records(namespace,record_id,payload_json,revision,created_at,updated_at) "
                        f"VALUES({mark},{mark},{json_mark},1,{mark},{mark})",
                        ("heritage", "graph", serialized, timestamp, timestamp),
                    )
                    if int(inserted.rowcount or 0) != 1:
                        raise ValueError("Heritage graph could not be saved")

            deleted = _execute(
                connection,
                f"DELETE FROM animals WHERE ipid={mark} AND archived={mark}",
                (key, 1 if self.adapter.dialect == "sqlite" else True),
            )
            return int(deleted.rowcount or 0) == 1
