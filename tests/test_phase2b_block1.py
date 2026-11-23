import tempfile
import unittest
from pathlib import Path

from Plugins.core.authorization import (
    AuthorizationService,
    CanonicalUnitService,
    OrganizationUnit,
)
from Plugins.core.plugin_manager import PluginManager
from Plugins.Master_Track.auth import UserDB


class FakeRecords:
    def __init__(self):
        self.data = {}
    def get(self, namespace, record_id, default=None):
        return self.data.get((namespace, record_id), default)
    def put(self, namespace, record_id, payload, expected_revision=None):
        self.data[(namespace, record_id)] = payload
        return 1


class FakeBackend:
    def __init__(self):
        self.records = FakeRecords()


class FakeMaster:
    is_logged_in = True
    current_unit_id = "org-hus"
    def can(self, action):
        return action in {"core.edit_animal_core", "plots.view"}
    def _current_user_record(self):
        return {"unit_id": "org-hus"}


class Block1Tests(unittest.TestCase):
    def test_missing_or_disabled_master_enters_trusted_local_mode(self):
        backend = FakeBackend()
        missing = AuthorizationService(None, backend=backend, trusted_local=True)
        self.assertTrue(missing.can("core.edit_animal_core"))
        self.assertTrue(missing.can("plots.view"))
        self.assertTrue(missing.trusted_local)
        self.assertFalse(missing.managed)
        disabled = AuthorizationService(
            FakeMaster(), disabled=True, backend=backend, trusted_local=True
        )
        self.assertTrue(disabled.can("core.delete_animals"))
        self.assertTrue(disabled.can_read_unit("org-other"))

    def test_canonical_unit_read_filter_is_fail_closed_for_managed_mode(self):
        backend = FakeBackend()
        backend.records.put("security", "organization-units", {
            "schema_version": 1,
            "units": [{"unit_id": "org-hus", "display_name": "HUS"}],
        })
        service = AuthorizationService(FakeMaster(), backend=backend)
        self.assertTrue(service.can_read_unit("org-hus"))
        self.assertFalse(service.can_read_unit("org-it"))
        self.assertFalse(service.can_read_unit(None))
        self.assertTrue(service.can_read_unit(None, allow_unassigned=True))
        records = [{"id": 1, "unit_id": "org-hus"}, {"id": 2, "unit_id": "org-it"}]
        self.assertEqual(service.filter_records(records), [records[0]])

    def test_authorized_user_and_unit_scope(self):
        backend = FakeBackend()
        backend.records.put("security", "organization-units", {
            "schema_version": 1,
            "units": [{"unit_id": "org-hus", "display_name": "HUS"}],
        })
        service = AuthorizationService(FakeMaster(), backend=backend)
        self.assertTrue(service.can("core.edit_animal_core", owner_unit_id="org-hus"))
        self.assertFalse(service.can("core.edit_animal_core", owner_unit_id="org-it"))
        self.assertEqual(service.units.validate("org-hus"), "org-hus")
        self.assertRaises(ValueError, service.units.validate, "unit_mus_colony")
        backend.records.put("security", "organization-units", {
            "schema_version": 1,
            "units": [{"unit_id": "org-hus", "display_name": "HUS", "active": False}],
        })
        self.assertFalse(service.can("core.edit_animal_core", owner_unit_id="org-hus"))

    def test_no_free_text_unit_compatibility_fallback(self):
        user = {"username": "u", "unit": "HUS"}
        UserDB._ensure_user_defaults(user)
        self.assertEqual(user.get("unit_id"), "")

    def test_manifest_registry_validates_all_installed_manifests(self):
        root = Path(__file__).resolve().parents[1]
        manager = PluginManager(root / "Plugins", app_version="0.3.0")
        diagnostics = manager.validate_all(import_entry_points=True)
        self.assertEqual(len(diagnostics), 13)
        self.assertFalse(manager.required_failures())
        self.assertTrue(all(item.valid for item in diagnostics.values()))
        self.assertIn("pedigree_graph_visualization", manager.capability_registry)

    def test_optional_invalid_manifest_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Plugins"
            plugin = root / "Optional"
            plugin.mkdir(parents=True)
            (plugin / "manifest.json").write_text(
                '{"name":"Optional","display_name":"Optional","version":"0.1.0",'
                '"description":"x","author":"x","SPDX-License-Identifier":"MIT",'
                '"entry_point":"x.Plugin","dependencies":["not_a_real_module"],'
                '"data_files":[],"min_progtrack_version":"0.1.0",'
                '"menu_location":"Tools","icon":"x","optional":true,"features":[],'
                '"capabilities":[],"permissions":[],"backend_namespaces":[],"resources":[],"docs":[]}',
                encoding="utf-8",
            )
            manager = PluginManager(root)
            result = manager.validate_all()["Optional"]
            self.assertTrue(result.valid)
            self.assertTrue(result.warnings)
            self.assertFalse(manager.required_failures())

    def test_manifest_rejects_incompatible_runtime(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Plugins"
            plugin = root / "Future"
            plugin.mkdir(parents=True)
            (plugin / "manifest.json").write_text(
                '{"name":"Future","display_name":"Future","version":"9.0.0",'
                '"description":"x","author":"x","SPDX-License-Identifier":"MIT",'
                '"entry_point":"__init__.initialize","dependencies":[],"data_files":[],'
                '"min_progtrack_version":"9.0.0","menu_location":"Tools","icon":"x",'
                '"optional":true,"features":[],"capabilities":[],"permissions":[],'
                '"backend_namespaces":[],"resources":[],"docs":[]}',
                encoding="utf-8",
            )
            result = PluginManager(root, app_version="0.2.1").validate_all()["Future"]
            self.assertFalse(result.valid)
            self.assertTrue(any("requires ProgTrack" in item for item in result.errors))

    def test_canonical_unit_does_not_read_housing_units(self):
        backend = FakeBackend()
        backend.records.put("housing", "cage", {"structures": {"units": {
            "unit_mus_colony": {"display_name": "Mouse Colony Unit"},
        }}})
        units = CanonicalUnitService(backend)
        self.assertIsNone(units.get("unit_mus_colony"))
        units.save([OrganizationUnit("org-it", "IT")], authorized=True)
        self.assertEqual(units.get("org-it").display_name, "IT")


if __name__ == "__main__":
    unittest.main()
