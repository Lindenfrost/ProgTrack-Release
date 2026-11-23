import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from Plugins.core.authorization import AuthorizationService
from Plugins.core.plugin_manager import PluginManager
from Plugins.core.role_block_presets import RoleBlockPresetRegistry
from Plugins.core.backend.adapters import SQLiteAdapter
from Plugins.core.backend.repositories import DomainRecordRepository
from Plugins.core.backend.errors import ConflictError
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths


class _Records:
    def __init__(self):
        self.data = {}

    def get(self, namespace, record_id, default=None):
        return self.data.get((namespace, record_id), default)

    def put(self, namespace, record_id, payload, expected_revision=None):
        self.data[(namespace, record_id)] = payload
        return int(payload.get("revision", 1)) if isinstance(payload, dict) else 1


class _Backend:
    def __init__(self):
        self.records = _Records()


class Issue118123124Tests(unittest.TestCase):
    def test_schema_v1_migrates_to_stable_schema2_ids(self):
        backend = _Backend()
        backend.records.data[("configuration", "role-block-presets")] = {
            "schema_version": 1,
            "revision": 1,
            "presets": [{"name": "Mouse procedures", "blocks": ["custom_limit:biopsy"]}],
            "experimental_blocks": [{
                "id": "custom_limit:biopsy",
                "name": "Biopsy",
                "kind": "limiting",
                "event_type": "biopsy",
                "max_field": "max_biopsy",
            }],
            "event_definitions": [{
                "id": "custom-event:biopsy",
                "name": "Biopsy",
                "event_type": "biopsy",
                "block_id": "custom_limit:biopsy",
                "render_mode": "symbol",
                "default_color": "#123456",
            }],
        }
        registry = RoleBlockPresetRegistry(backend)
        payload = registry.payload()
        self.assertEqual(payload["schema_version"], 2)
        self.assertTrue(payload["presets"][0]["id"].startswith("preset:"))
        self.assertTrue(payload["experimental_blocks"][0]["stable_id"].startswith("experimental-block:"))
        self.assertTrue(payload["event_definitions"][0]["stable_id"].startswith("custom-event:"))

    def test_retired_definitions_are_kept_but_not_active(self):
        backend = _Backend()
        registry = RoleBlockPresetRegistry(
            backend,
            initial_payload={
                "schema_version": 2,
                "revision": 1,
                "presets": [],
                "experimental_blocks": [{
                    "id": "experimental-block:old",
                    "name": "Old",
                    "kind": "counting",
                    "event_type": "old_event",
                    "active": False,
                }],
                "event_definitions": [{
                    "id": "custom-event:old",
                    "name": "Old",
                    "event_type": "old_event",
                    "block_id": "experimental-block:old",
                    "active": False,
                }],
            },
        )
        self.assertEqual(len(registry.experimental_blocks()), 1)
        self.assertEqual(registry.active_experimental_blocks(), [])
        self.assertEqual(len(registry.event_definitions()), 1)
        self.assertEqual(registry.active_event_definitions(), [])

    def _sqlite_repository(self, root):
        paths = RuntimePaths(
            application_root=root,
            profile=BackendProfile.STANDALONE_SQLITE,
            data_root=root / "data",
            config_root=root / "config",
            cache_root=root / "cache",
            state_root=root / "state",
            database_path=root / "data" / "test.sqlite3",
            managed_root=root / "managed",
            managed_documents=root / "managed" / "documents",
            managed_config_assets=root / "managed" / "config-assets",
            logs=root / "logs",
            runtime=root / "runtime",
            exports=root / "exports",
            preferences=root / "preferences",
            profile_file=root / "config" / "backend.json",
        )
        adapter = SQLiteAdapter(paths, acquire_process_lock=False)
        adapter.migrate()
        return adapter, DomainRecordRepository(adapter)

    def test_put_many_commits_configuration_records_together(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, repository = self._sqlite_repository(Path(directory))
            try:
                revisions = repository.put_many([
                    ("configuration", "animal-roles", {"roles": ["a"]}),
                    ("configuration", "role-block-presets", {"presets": ["p"]}),
                ])
                self.assertEqual(revisions[("configuration", "animal-roles")], 1)
                self.assertEqual(revisions[("configuration", "role-block-presets")], 1)
                self.assertEqual(repository.get("configuration", "animal-roles")["roles"], ["a"])
                self.assertEqual(repository.get("configuration", "role-block-presets")["presets"], ["p"])
            finally:
                adapter.close()

    def test_put_many_rolls_back_when_a_later_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, repository = self._sqlite_repository(Path(directory))
            try:
                original_execute = __import__(
                    "Plugins.core.backend.repositories",
                    fromlist=["_execute"],
                )._execute
                writes = {"count": 0}
                def injected_execute(connection, sql, params=()):
                    statement = str(sql).lstrip().upper()
                    if statement.startswith(("INSERT", "UPDATE", "DELETE")):
                        writes["count"] += 1
                        if writes["count"] == 2:
                            raise RuntimeError("injected second-record failure")
                    return original_execute(connection, sql, params)
                with mock.patch("Plugins.core.backend.repositories._execute", side_effect=injected_execute):
                    with self.assertRaises(RuntimeError):
                        repository.put_many([
                            ("configuration", "animal-roles", {"roles": ["new"]}),
                            ("configuration", "role-block-presets", {"presets": ["new"]}),
                        ])
                self.assertIsNone(repository.get("configuration", "animal-roles"))
                self.assertIsNone(repository.get("configuration", "role-block-presets"))
            finally:
                adapter.close()

    def test_put_many_checks_all_revisions_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter, repository = self._sqlite_repository(Path(directory))
            try:
                repository.put_many([
                    ("configuration", "animal-roles", {"roles": ["old"]}),
                    ("configuration", "role-block-presets", {"presets": ["old"]}),
                ])
                with self.assertRaises(ConflictError):
                    repository.put_many(
                        [
                            ("configuration", "animal-roles", {"roles": ["new"]}),
                            ("configuration", "role-block-presets", {"presets": ["new"]}),
                        ],
                        expected_revisions={
                            ("configuration", "animal-roles"): 0,
                            ("configuration", "role-block-presets"): 1,
                        },
                    )
                self.assertEqual(repository.get("configuration", "animal-roles")["roles"], ["old"])
                self.assertEqual(repository.get("configuration", "role-block-presets")["presets"], ["old"])
            finally:
                adapter.close()

    def test_manifest_ui_entry_point_resolves_through_manager(self):
        root = Path(__file__).resolve().parents[1]
        manager = PluginManager(root / "Plugins", app_version="0.3.0")
        diagnostics = manager.validate_all(import_entry_points=True)
        self.assertTrue(diagnostics["Projects_Track"].valid)
        resolved = manager.resolve_ui_entry_point("Projects_Track")
        self.assertTrue(callable(resolved))

    def test_trusted_local_allows_actions_without_audit_actor(self):
        backend = _Backend()
        service = AuthorizationService(None, backend=backend, trusted_local=True)
        self.assertTrue(service.available)
        self.assertFalse(service.managed)
        for action in ("core.delete_animals", "master.manage_users", "reports.write"):
            self.assertTrue(service.can(action))
        self.assertTrue(service.can_read_unit("unknown-unit"))

    def test_dynamic_plot_contract_is_source_backed(self):
        source = (Path(__file__).resolve().parents[1] / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        for token in (
            "_custom_event_appearance",
            "custom_event_snapshot",
            "_validate_custom_event_limits_for_record",
            "render_mode",
            "include_retired=False",
        ):
            self.assertIn(token, source)


if __name__ == "__main__":
    unittest.main()
