# Part of: ProgTrack 0.3.0
# Required ProgTrack version: 0.3.0 or newer.
"""Configured ProgTrack backend facade."""

from __future__ import annotations

import os
import copy
import json
from contextlib import contextmanager
from contextvars import ContextVar

from .core_configuration import CoreConfiguration, RECORD_IDS
from pathlib import Path
from typing import Any, Callable, Mapping

from ..runtime_paths import BackendProfile, RuntimePaths
from .adapters import create_adapter
from .animal_service import AnimalService, CoreSaveReceipt
from .authenticated_sessions import AuthenticatedSessionService
from .user_account_commands import PROTECTED_ACCOUNT_RECORDS, USERS, UserAccountCommands
from .transaction_scope import TransactionScopeAdapter
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
from .json_codec import dumps
from .interchange import InterchangeService
from ..institution_branding import InstitutionBrandingService
from .schema_registry import SchemaRegistry
from .postgresql_admin import PostgreSQLAdministrationService
from ..backend_configuration import BackendConfigurationService, PostgreSQLSettings
from .errors import BackendConfigurationError, ConflictError, PermissionDeniedError, ValidationError


class ProgTrackBackend:
    def __init__(
        self,
        paths: RuntimePaths,
        *,
        postgres_dsn: str = "",
        postgres_pool_min: int | None = None,
        postgres_pool_max: int | None = None,
        acquire_process_lock: bool = True,
        create_managed_roots: bool = True,
        storage_access: Callable[..., Path] | None = None,
        storage_client=None,
        bootstrap_seed: bool = False,
        choose_demo_seed: Callable[[], bool] | None = None,
    ):
        # Retain the legacy keyword for callers, but it conveys no consent.
        self.paths = paths
        self._demo_seed_pending = False
        self.adapter = create_adapter(
            paths,
            postgres_dsn=postgres_dsn,
            postgres_pool_min=postgres_pool_min,
            postgres_pool_max=postgres_pool_max,
            acquire_process_lock=acquire_process_lock,
        )
        new_standalone = (
            paths.profile is BackendProfile.STANDALONE_SQLITE
            and not self.adapter.path.exists()
        )
        try:
            if paths.profile is BackendProfile.SHARED_POSTGRESQL:
                self.adapter.require_current_schema()
            else:
                self.adapter.migrate()
        except BaseException:
            self.adapter.close()
            raise
        self.adapter = TransactionScopeAdapter(self.adapter)
        self.animals = AnimalService(self.adapter)
        self._authorized_record_scope = ContextVar("authenticated_domain_access", default=False)
        self._account_write_scope = ContextVar("account_write_access", default=False)
        self._account_maintenance_scope = ContextVar("account_maintenance_access", default=False)
        self._core_configuration_scope = ContextVar("core_configuration_write_access", default=False)
        self._document_repair_scope = ContextVar("document_repair_access", default=False)
        self.records = DomainRecordRepository(
            self.adapter, self._require_domain_scope,
            authorize_write=self._require_account_write, authorize_read=self._require_account_read,
        )
        self.authenticated_sessions = AuthenticatedSessionService(self.adapter)
        self.account_commands = UserAccountCommands(self)
        self.authenticated_sessions.account_commands = self.account_commands
        self.leases = LeaseRepository(self.adapter, self.authenticated_sessions)
        self.audit = AuditRepository(self.adapter)
        self.core_configuration = CoreConfiguration(self)
        self.schemas = SchemaRegistry(self.adapter)
        shared = paths.profile is BackendProfile.SHARED_POSTGRESQL
        if shared and storage_client is None:
            from ..managed_storage_worker import ManagedStorageClient

            configuration = BackendConfigurationService(paths)
            root = paths.configured_managed_root
            if root is None:
                root = paths.managed_root
            storage_client = ManagedStorageClient(configuration, root, runtime_dsn=postgres_dsn or None)
        self._set_document_service(paths, storage_access=storage_access, storage_client=storage_client,
                                   create_roots=create_managed_roots and not shared)
        self.interchange = InterchangeService(self, self.documents)
        self.branding = InstitutionBrandingService(self)
        if new_standalone and choose_demo_seed is not None:
            try:
                accepted = choose_demo_seed() is True
                self.adapter.set_installation_value("demo-seed-choice", bool(accepted))
                self._demo_seed_pending = accepted
                self._bootstrap_seed_if_empty()
            except BaseException:
                self.adapter.close()
                raise

    def _set_document_service(self, paths, *, storage_access=None, storage_client=None, create_roots=False):
        self.paths = paths
        self.managed_root = Path(paths.managed_root) if paths.managed_root is not None else None
        self.documents = ManagedFileService(
            self.adapter,
            self.managed_root / "documents" if self.managed_root is not None else None,
            self.managed_root / "config-assets" if self.managed_root is not None else None,
            self._require_domain_scope, create_roots=create_roots,
            storage_access=storage_access, storage_client=storage_client, authorize_repair=self._require_document_repair,
        )

    def reconfigure_shared_storage(self, root: str, storage_client) -> None:
        if self.paths.profile is not BackendProfile.SHARED_POSTGRESQL:
            raise BackendConfigurationError("Managed storage can only be reconfigured for Shared PostgreSQL.")
        self._set_document_service(self.paths.with_shared_managed_root(root), storage_client=storage_client)
        self.interchange.managed_files = self.documents

    def _bootstrap_seed_if_empty(self) -> None:
        if self.paths.profile is not BackendProfile.STANDALONE_SQLITE:
            return
        if not self._demo_seed_pending:
            return
        self._demo_seed_pending = False
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
        if not seed.is_file():
            raise BackendConfigurationError("The bundled example data package is missing.")
        preview = self.interchange.import_package(seed, require_empty=True)
        if not preview.valid:
            raise BackendConfigurationError(
                "Bundled ProgTrack seed is invalid: " + "; ".join(preview.errors)
            )

    def close(self) -> None:
        self.documents.close()
        self.adapter.close()

    def session_heartbeat(self, context, tokens):
        """Validate and renew the session and its leases in one transaction."""
        with self.adapter.transaction(write=True) as connection:
            account = self.authenticated_sessions.renew(connection, context)
            if self.adapter.dialect != "postgresql":
                return set(), []
            renewed = self.leases.renew_tokens(connection, context, tokens) if tokens else set()
            snapshot = self.leases.shared_snapshot(connection, context, account)
            return renewed, snapshot

    def session_status(self, context):
        """Reconcile visible leases without extending session or lease expiry."""
        with self.adapter.transaction() as connection:
            account = self.authenticated_sessions.require(connection, context)
            if self.adapter.dialect != "postgresql":
                return set(), []
            return set(), self.leases.shared_snapshot(connection, context, account)

    def _require_domain_scope(self, namespace):
        if self.adapter.dialect == "postgresql" and namespace in {
            "reports", "animal-reports", "medical", "medical.core-events", "projects", "samples",
            "housing", "reproduction", "managed_files", "heritage", "surgery-planner", "network-track",
            "embryo-track", "measurements", "sessions",
        } and not self._authorized_record_scope.get():
            raise PermissionDeniedError("Shared records require an authenticated, scoped operation.")

    def _require_account_read(self, namespace, record_id):
        if (self.adapter.dialect == "postgresql" and namespace == USERS[0]
                and record_id in (None, USERS[1]) and not self._account_maintenance_scope.get()):
            raise PermissionDeniedError("Shared account records must be read through the account service.")

    def _require_account_write(self, namespace, record_id):
        if (namespace == "configuration" and record_id in RECORD_IDS
                and not self._core_configuration_scope.get()):
            raise PermissionDeniedError("Core configuration requires an authorized configuration command.")
        if (self.adapter.dialect == "postgresql" and (namespace, record_id) == ("configuration", "institution-branding")
                and not self._authorized_record_scope.get()):
            raise PermissionDeniedError("Shared branding requires an authenticated institution-settings request.")
        if self.adapter.dialect == "postgresql" and (namespace, record_id) in PROTECTED_ACCOUNT_RECORDS and not (
            self._account_write_scope.get() or self._account_maintenance_scope.get()
        ):
            raise PermissionDeniedError("Shared account policy requires the canonical account command.")

    @contextmanager
    def _allow_account_writes(self):
        marker = self._account_write_scope.set(True)
        try:
            yield
        finally:
            self._account_write_scope.reset(marker)

    @contextmanager
    def _allow_account_maintenance(self):
        marker = self._account_maintenance_scope.set(True)
        try:
            yield
        finally:
            self._account_maintenance_scope.reset(marker)

    @contextmanager
    def _allow_authorized_records(self):
        scope = self._authorized_record_scope.set(True)
        try:
            yield
        finally:
            self._authorized_record_scope.reset(scope)

    @contextmanager
    def _allow_core_configuration_writes(self):
        marker = self._core_configuration_scope.set(True)
        try:
            yield
        finally:
            self._core_configuration_scope.reset(marker)

    @contextmanager
    def protected_request(self, context, *permissions, animal_leases=None, entity_leases=None, write=False,
                          mutate_accounts=False, snapshot=False):
        """Pin authorization, target leases and the command in one transaction."""
        options = {"snapshot": True} if snapshot else {}
        with self.adapter.transaction(write=write, **options) as connection:
            account = self.authenticated_sessions.require(connection, context, mutate_accounts=mutate_accounts)
            allowed = self.authenticated_sessions.effective_permissions(connection, account)
            if "*" not in allowed and any(permission not in allowed for permission in permissions):
                raise PermissionDeniedError("This account may not perform the requested operation.")
            leases = sorted((animal_leases or {}).items())
            for ipid, token in leases:
                self.authenticated_sessions.require_visible_animal(connection, account, allowed, ipid)
                self.leases.require_shared_lease(connection, context, "animal", ipid, token)
            project_leases = sorted((entity_leases or {}).items())
            for (kind, entity_id), token in project_leases:
                if kind == "project":
                    self.authenticated_sessions.require_visible_project(connection, account, allowed, entity_id)
                elif kind == "surgery" and entity_id in {"schedule", "draft-schedule", "settings", "block-days"}:
                    pass
                elif kind != "housing" or entity_id not in {"cage", "inspections"}:
                    raise PermissionDeniedError("No Shared edit scope is defined for this entity.")
                self.leases.require_shared_lease(connection, context, kind, entity_id, token)
            with self._allow_authorized_records():
                yield connection, account, allowed
            if write:
                self.authenticated_sessions.require(connection, context)
                for ipid, token in leases:
                    self.leases.require_shared_lease(connection, context, "animal", ipid, token)
                for (kind, entity_id), token in project_leases:
                    self.leases.require_shared_lease(connection, context, kind, entity_id, token)

    def save_shared_animal_changes(self, context, changes, leases, *, expected_parentage_revision=None, addresses=None, derive_samples=False):
        from .shared_animal_edit import save_shared_animal_changes

        if self.adapter.dialect != "postgresql":
            raise BackendConfigurationError("This animal command requires a Shared backend.")
        return save_shared_animal_changes(
            self, context, changes, leases,
            expected_parentage_revision=expected_parentage_revision, addresses=addresses, derive_samples=derive_samples,
        )

    def save_shared_animal_report(self, session, ipid, token, report, *, expected_report):
        if self.adapter.dialect != "postgresql":
            raise BackendConfigurationError("This report command requires a Shared backend.")
        with self.protected_request(session, "reports.write", animal_leases={ipid: token}, write=True) as (connection, account, _):
            reports, revision = self.records.get_with_revision("reports", "animal-reports", default={})
            if not isinstance(reports, dict):
                raise BackendConfigurationError("The reports catalogue is invalid.")
            if dumps(reports.get(ipid, {})) != dumps(expected_report):
                raise ConflictError("The animal report changed. Reload before saving.")
            reports[ipid] = report
            self.records.put("reports", "animal-reports", reports, expected_revision=revision or 0)
            self.audit._append_in_transaction(
                connection, actor_login=account["username"], category="reports", action="update",
                entity_type="animal", entity_id=ipid, payload={},
            )

    def load_shared_animal_report(self, session, ipid):
        with self.protected_request(session, "reports.view") as (connection, account, permissions):
            self.authenticated_sessions.require_visible_animal(connection, account, permissions, ipid)
            reports = self.records.get("reports", "animal-reports", default={})
            return reports.get(ipid, {}) if isinstance(reports, dict) else {}

    def load_shared_chat(self, session):
        with self.protected_request(session, "network.view"):
            messages = self.records.get("network-track", "chat", default=[])
            if not isinstance(messages, list) or any(not isinstance(line, str) for line in messages):
                raise ValidationError("The shared chat history is invalid.")
            return messages

    def load_shared_embryo_reference(self, session):
        with self.protected_request(session, "embryo_track.view"):
            return self.records.get_with_revision("embryo-track", "cranimetry-reference", default=None)

    def save_shared_embryo_reference(self, session, data, expected_revision):
        with self.protected_request(session, "embryo_track.view", write=True) as (connection, account, _):
            if account["role"] != "lord" and not (account["role"] == "user" and "researcher" in {
                job.casefold() for job in (account.get("jobs") or ())
            }):
                raise PermissionDeniedError("Only Researchers and Lords may replace Embryo Track reference data.")
            revision = self.records.put("embryo-track", "cranimetry-reference", data, expected_revision=expected_revision)
            self.audit._append_in_transaction(
                connection, actor_login=account["username"], category="embryo-track", action="replace_reference",
                entity_type="configuration", entity_id="cranimetry-reference", payload={},
            )
            return revision

    def load_shared_pdg_model(self, session, ipid):
        with self.protected_request(session, "pdg_converter.use") as (connection, account, permissions):
            self.authenticated_sessions.require_visible_animal(connection, account, permissions, ipid)
            models = self.records.get("measurements", "pdg-models", default={})
            return models.get(ipid) if isinstance(models, dict) else None

    def save_shared_pdg_model(self, session, ipid, token, model):
        with self.protected_request(
            session, "pdg_converter.use", animal_leases={ipid: token}, write=True,
        ) as (connection, account, _):
            models, revision = self.records.get_with_revision("measurements", "pdg-models", default={})
            if not isinstance(models, dict):
                raise ValidationError("The PdG model register is invalid.")
            if model is None:
                models.pop(ipid, None)
            else:
                models[ipid] = {**model, "ipid": ipid}
            self.records.put("measurements", "pdg-models", models, expected_revision=revision)
            self.audit._append_in_transaction(
                connection, actor_login=account["username"], category="measurements", action="update_pdg_model",
                entity_type="animal", entity_id=ipid, payload={},
            )

    def save_shared_chat_message(self, session, message, *, original=None):
        from datetime import datetime

        permission = "network.edit_entry" if original is not None else "network.create_entry"
        if not isinstance(message, str) or not message.strip():
            raise ValidationError("A message is required.")
        with self.protected_request(session, permission, write=True) as (connection, account, _):
            lines, revision = self.records.get_with_revision("network-track", "chat", default=[])
            if not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
                raise ValidationError("The shared chat history is invalid.")
            message = message.strip()
            if original is None:
                timestamp = datetime.now().strftime("%d/%m/%y %H:%M")
                sender = str(account.get("display_name") or account["username"])
                sender = " ".join(sender.split())
                line = f"{timestamp}\t{sender}\t{message}"
                lines.append(line)
            else:
                if lines.count(original) != 1:
                    raise ConflictError("The message changed or is ambiguous. Reload and try again.")
                timestamp, sender, _ = original.split("\t", 2)
                line = f"{timestamp}\t{sender}\t{message} [edited {datetime.now():%H:%M}]"
                lines[lines.index(original)] = line
            self.records.put("network-track", "chat", lines[-1000:], expected_revision=revision)
            self.audit._append_in_transaction(
                connection, actor_login=account["username"], category="network-track",
                action="edit" if original is not None else "append", entity_type="network-chat",
                entity_id="chat", payload={},
            )
            return line

    def load_shared_samples(self, session, record_id):
        from .shared_samples import load_shared_samples

        return load_shared_samples(self, session, record_id)

    def save_shared_samples(self, session, record_id, original, rows, expected_revision, leases):
        from .shared_samples import save_shared_samples

        return save_shared_samples(self, session, record_id, original, rows, expected_revision, leases)

    def load_shared_medical_history(self, session, ipid):
        with self.protected_request(session, "medi_track.view") as (connection, account, permissions):
            self.authenticated_sessions.require_visible_animal(connection, account, permissions, ipid)
            history = self.records.get("medical", "history", default={})
            animals = history.get("animals", {}) if isinstance(history, dict) else {}
            block = animals.get(ipid, {}) if isinstance(animals, dict) else {}
            derived = self.records.get("medical.core-events", ipid, default={})
            return block, derived

    def list_shared_medical_documents(self, session, ipid):
        with self.protected_request(session, "medi_track.view") as (connection, account, permissions):
            self.authenticated_sessions.require_visible_animal(connection, account, permissions, ipid)
            return self.documents.list_for_owner("animal-medical", ipid)

    def add_shared_medical_document(self, session, ipid, token, source, *, operation_id=None):
        request = self._document_request(
            session, "medi_track.upload_document", animal_leases={ipid: token},
        )
        return self.documents.add(source, owner_type="animal-medical", owner_id=ipid,
                                  request=request, operation_id=operation_id)

    def replace_shared_medical_document(self, session, ipid, token, source, document_id, *,
                                        expected_revision, expected_checksum, operation_id=None):
        request = self._document_request(session, "medi_track.upload_document", animal_leases={ipid: token})
        return self.documents.replace(
            source, document_id=document_id, owner_type="animal-medical", owner_id=ipid, request=request,
            operation_id=operation_id, expected_revision=expected_revision, expected_checksum=expected_checksum,
        )

    def delete_shared_medical_document(self, session, ipid, token, document_id, reason, *,
                                       expected_revision, expected_checksum, operation_id=None):
        request = self._document_request(
            session, "medi_track.delete_document", animal_leases={ipid: token},
        )
        return self.retire_medical_document(
            ipid, document_id, reason, request=request, expected_revision=expected_revision,
            expected_checksum=expected_checksum, operation_id=operation_id,
        )

    def retire_medical_document(self, ipid, document_id, reason, *, expected_revision,
                                expected_checksum, operation_id=None, actor="", request=None):
        reason = str(reason).strip()
        if not reason:
            raise ValidationError("A document deletion reason is required.")
        return self.documents.delete_active(
            document_id, owner_type="animal-medical", owner_id=ipid, reason=reason, actor=actor,
            request=request, operation_id=operation_id, expected_revision=expected_revision,
            expected_checksum=expected_checksum,
            update_owner=lambda _connection, result, _actor: self._remove_medical_document_mirror(
                ipid, result["document_id"],
            ),
        )

    @staticmethod
    def _project_document_type(owner_type):
        if owner_type not in {"project-document", "project-sop"}:
            raise ValidationError("Unknown project document type.")
        return "sop" if owner_type == "project-sop" else "document"

    def list_shared_project_documents(self, session, project_id, owner_type):
        self._project_document_type(owner_type)
        with self.protected_request(session, "project.view") as (connection, account, permissions):
            self.authenticated_sessions.require_visible_project(connection, account, permissions, project_id)
            return self.documents.list_for_owner(owner_type, project_id)

    def add_shared_project_document(self, session, project_id, token, owner_type, source, *, operation_id=None):
        kind = self._project_document_type(owner_type)
        request = self._document_request(
            session, f"project.upload_{kind}", entity_leases={("project", project_id): token},
        )
        return self.documents.add(source, owner_type=owner_type, owner_id=project_id,
                                  request=request, operation_id=operation_id)

    def replace_shared_project_document(self, session, project_id, token, owner_type, source, document_id, *,
                                        expected_revision, expected_checksum, operation_id=None):
        kind = self._project_document_type(owner_type)
        request = self._document_request(session, f"project.upload_{kind}", entity_leases={("project", project_id): token})
        return self.documents.replace(
            source, document_id=document_id, owner_type=owner_type, owner_id=project_id, request=request,
            operation_id=operation_id, expected_revision=expected_revision, expected_checksum=expected_checksum,
        )

    def delete_shared_project_document(self, session, project_id, token, owner_type, document_id, *,
                                       expected_revision, expected_checksum, operation_id=None):
        kind = self._project_document_type(owner_type)
        request = self._document_request(
            session, f"project.delete_{kind}", entity_leases={("project", project_id): token},
        )
        return self.documents.delete_active(
            document_id, owner_type=owner_type, owner_id=project_id, request=request,
            expected_revision=expected_revision, expected_checksum=expected_checksum, operation_id=operation_id,
        )

    def _document_request(self, session, permission, **leases):
        @contextmanager
        def request():
            with self.protected_request(session, permission, write=True, **leases) as (connection, account, _):
                yield connection, account["username"]
        return request

    def _require_document_repair(self):
        if not self._document_repair_scope.get():
            raise PermissionDeniedError("Managed storage repair requires an authorized backend maintenance request.")

    def _document_maintenance_request(self, session, validate_local, *, write=False):
        @contextmanager
        def request():
            if self.adapter.dialect == "postgresql":
                with self.protected_request(session, write=write, snapshot=not write) as (connection, account, _):
                    if str(account.get("role", "")).casefold() != "lord":
                        raise PermissionDeniedError("Only a current Lord may inspect or repair managed storage.")
                    marker = self._document_repair_scope.set(write)
                    try:
                        yield connection, account["username"]
                    finally:
                        self._document_repair_scope.reset(marker)
                return
            actor = validate_local() if validate_local is not None else ""
            if not actor:
                raise PermissionDeniedError("Managed storage maintenance requires local administrator access.")
            marker = self._document_repair_scope.set(write)
            try:
                with self.adapter.transaction(write=write) as connection:
                    yield connection, actor
            finally:
                self._document_repair_scope.reset(marker)
        return request

    def reconcile_managed_storage(self, *, session=None, validate_local=None):
        request = self._document_maintenance_request(session, validate_local)
        with request():
            pass
        return self.documents.reconcile(request=request)

    def repair_managed_storage(self, finding, *, session=None, validate_local=None):
        request = self._document_maintenance_request(session, validate_local, write=True)
        operations = self.documents.operations
        operation_id = finding.get("operation_id")
        if finding["reason"] == "cleanup_pending":
            return operations.repair_retired(operation_id, request, expected_revision=finding["revision"])
        if finding["reason"] == "pending_operation":
            return operations.abandon_prepared(operation_id, request, expected_updated_at=finding["updated_at"])
        if finding["reason"] == "stale_staging":
            return operations.discard_abandoned_stage(operation_id, finding["attempt_id"], request)
        raise ValidationError("This managed-storage finding requires manual review.")

    def _remove_medical_document_mirror(self, ipid, document_id):
        history, revision = self.records.get_with_revision("medical", "history", default={})
        animals = history.get("animals", {}) if isinstance(history, dict) else {}
        block = animals.get(ipid) if isinstance(animals, dict) else None
        if isinstance(block, dict) and isinstance(block.get("documents"), list):
            block["documents"] = [
                doc for doc in block["documents"]
                if not isinstance(doc, dict) or doc.get("document_id") != document_id
            ]
            self.records.put("medical", "history", history, expected_revision=revision)

    def append_shared_medical_entry(self, session, ipid, token, entry):
        if self.adapter.dialect != "postgresql":
            raise BackendConfigurationError("This medical command requires a Shared backend.")
        kind = entry.get("entry_type")
        manual = {"note", "observation", "treatment", "measurement", "other"}
        permissions = {
            "issue_start": {"medi_track.status_enable", "medi_track.status_manage"},
            "issue_resolution": {"medi_track.status_manage"},
            "project_left": {"project.project_assign"},
            "project_assigned": {"project.project_assign"},
            "severity_changed": {"project.project_assign"},
            "experiment_started": {"project.set_in_experiment"},
            "experiment_ended": {"project.unset_in_experiment"},
            "death": {"core.edit_animal_core"},
            "departure": {"core.archive_animals"},
            "identity_changed": {"core.edit_animal_identity"},
        }
        required = {"medi_track.add_docs"} if kind in manual else permissions.get(kind)
        if required is None:
            raise ValidationError("Unsupported medical entry type.")
        if kind in manual and any(entry.get(key) for key in ("issue_id", "status_type", "condition_key")):
            raise ValidationError("Manual medical entries cannot change protected source metadata.")
        with self.protected_request(
            session, animal_leases={ipid: token}, write=True
        ) as (_, account, allowed):
            if "*" not in allowed and not (required & allowed):
                raise PermissionDeniedError("This account cannot add the requested medical entry.")
            history, revision = self.records.get_with_revision(
                "medical", "history", default={"version": "1.7", "animals": {}},
            )
            if not isinstance(history, dict) or not isinstance(history.get("animals"), dict):
                raise BackendConfigurationError("The medical history catalogue is invalid.")
            from ..animal_identity import animal_base_name

            block = history["animals"].setdefault(ipid, {
                "ipid": ipid, "name": animal_base_name(ipid), "animal_id": "",
                "entries": [], "documents": [],
            })
            entries = block.get("entries") if isinstance(block, dict) else None
            if not isinstance(entries, list):
                raise BackendConfigurationError("The animal's medical history is invalid.")
            if not entry.get("id") or any(item.get("id") == entry["id"] for item in entries):
                raise ValidationError("A new medical record ID is required.")
            if kind in {"issue_start", "issue_resolution"}:
                status = entry.get("status_type")
                issue = entry.get("issue_id")
                if not issue or status not in {"sick", "abnormal"}:
                    raise ValidationError("A health issue must identify its status and issue.")
                related = [
                    item["entry_type"] for item in entries
                    if item.get("issue_id") == issue and item.get("status_type") == status
                ]
                if (kind == "issue_start" and related
                        or kind == "issue_resolution" and related != ["issue_start"]):
                    raise ValidationError("The health issue changed. Open the current record and try again.")
            record = dict(entry)
            record["signature"] = str(account.get("display_name") or account["username"])
            record["actor_role"] = account["role"]
            entries.append(record)
            self.records.put("medical", "history", history, expected_revision=revision or 0)

    def administration_service(
        self,
        settings: PostgreSQLSettings,
        *,
        discovery_password: str = "",
        client_key_passphrase: str = "",
        authorized: bool,
        session=None,
        audit_callback: Any = None,
    ) -> PostgreSQLAdministrationService:
        configuration = BackendConfigurationService(self.paths)
        return PostgreSQLAdministrationService(
            configuration,
            settings,
            discovery_password=discovery_password,
            client_key_passphrase=client_key_passphrase,
            authorized=authorized,
            validate_initiator=lambda: self.require_backend_admin_initiator(session),
            active_database=self.active_postgresql_database(),
            audit_callback=audit_callback,
        )

    def active_postgresql_database(self) -> str:
        if self.adapter.dialect != "postgresql":
            return ""
        from psycopg.conninfo import conninfo_to_dict

        options = conninfo_to_dict(self.adapter.pool.conninfo)
        return options.get("dbname") or os.environ.get("PGDATABASE") or options.get("user", "")

    def require_backend_admin_initiator(self, session) -> str:
        with self.protected_request(session) as (_, account, _):
            if str(account.get("role", "")).casefold() != "lord":
                raise PermissionDeniedError("Only a current ProgTrack Lord may administer the backend.")
            return account["username"]

    def load_core_data(self, *, session=None) -> dict[str, Any]:
        if self.adapter.dialect != "postgresql":
            return self.animals.load_snapshot()
        from ..authorization import organizational_owner_id
        from ..project_visibility import animal_visible_by_project_scope

        with self.protected_request(session, "core.view") as (connection, account, permissions):
            scope = self.authenticated_sessions.animal_scope(connection, account, permissions)
            unit_id = self.authenticated_sessions.active_unit(connection, account)
            snapshot = self.animals.load_snapshot()
            for section in ("animals", "archived_animals"):
                snapshot[section] = {
                    ipid: record for ipid, record in snapshot[section].items()
                    if animal_visible_by_project_scope(record, *scope)
                    and organizational_owner_id(record).casefold() in ("", unit_id)
                }
            return snapshot

    def save_core_data(
        self,
        data: Mapping[str, Any],
        *,
        expected_parentage_revision: str | None = None,
        clinical_context: Mapping[str, Any] | None = None,
    ) -> CoreSaveReceipt:
        if self.adapter.dialect == "postgresql":
            raise PermissionDeniedError("Shared Core writes require an authenticated command.")
        return self.animals.replace_snapshot(
            data,
            expected_parentage_revision=expected_parentage_revision,
            clinical_context=clinical_context,
        )

    def delete_archived_animal_atomically(
        self,
        ipid: str,
        record: Mapping[str, Any],
        *,
        owner_unit_id: str = "",
        clinical_context: Mapping[str, Any] | None = None,
        session=None,
        lease_token: str = "",
    ) -> bool:
        """Delete one archived Core row and preserve its Heritage lineage.

        Core deletion and the optional former-Core snapshot share the same
        adapter transaction.  The operation intentionally lives in the
        backend facade, not in the optional Heritage plugin: a disabled or
        unavailable visualization plugin must not decide whether Core
        lineage data is preserved.
        """
        if self.adapter.dialect == "postgresql":
            from .shared_animal_edit import _clinical_context

            with self.protected_request(
                session, "core.delete_animals", animal_leases={ipid: lease_token}, write=True,
            ) as (connection, account, permissions):
                current = self.animals.load_snapshot()["archived_animals"].get(ipid)
                if current is None or dumps(current) != dumps(record):
                    raise ConflictError("This animal changed while editing. Reload before deleting.")
                deleted = self._delete_archived_animal(
                    ipid, current, owner_unit_id=account.get("unit_id", ""),
                    clinical_context=_clinical_context(account, permissions),
                )
                if deleted:
                    self.audit._append_in_transaction(
                        connection, actor_login=account["username"], category="core", action="delete",
                        entity_type="animal", entity_id=ipid, payload={},
                    )
                return deleted
        return self._delete_archived_animal(
            ipid, record, owner_unit_id=owner_unit_id, clinical_context=clinical_context,
        )

    def _delete_archived_animal(self, ipid, record, *, owner_unit_id, clinical_context):
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

            if clinical_context is not None:
                from .clinical_mirroring import ClinicalMirrorTransaction
                clinical = ClinicalMirrorTransaction(self.adapter, connection, clinical_context, timestamp)
                clinical.authorize('core.delete_animals')
                for event in _fetchall(connection,
                        f'SELECT payload_json FROM animal_events WHERE animal_ipid={mark}', (key,)):
                    raw = self.adapter.row_to_dict(event)['payload_json']
                    clinical.delete(key, raw if isinstance(raw, dict) else json.loads(raw), animal_deleted=True)
                clinical.flush()

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
