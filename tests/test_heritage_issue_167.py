from __future__ import annotations

import copy
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QComboBox

from Plugins.Heritage_Track.heritage_track_widget import (
    HeritageTrackPlugin,
    NodeEditDialog,
    ParentageCommandError,
)
from Plugins.core.backend import ProgTrackBackend
from Plugins.core.runtime_paths import BackendProfile, RuntimePaths


class _Records:
    def __init__(self):
        self.values = {}
        self.put_count = 0

    def get(self, namespace, record_id, default=None):
        return copy.deepcopy(self.values.get((namespace, record_id), default))

    def put(self, namespace, record_id, payload, **_kwargs):
        self.put_count += 1
        self.values[(namespace, record_id)] = copy.deepcopy(payload)
        return self.put_count


class _Backend:
    def __init__(self):
        self.records = _Records()


class _Master:
    current_unit_id = "unit-a"
    current_username = "Researcher"


class _App:
    def __init__(self, *, core_create=False, core_delete=False, edit_links=True, unit_id="unit-a"):
        self.backend = _Backend()
        self.messages = {}
        self.master_track = _Master()
        self.master_track.current_unit_id = unit_id
        self.animals = {
            "Child": {
                "name": "Child", "species": "Callithrix jacchus", "sex": "female",
                "birth_date": "01.01.2020", "eizellspenderin": "Mother",
            },
            "Mother": {
                "name": "Mother", "species": "Callithrix jacchus", "sex": "female",
                "birth_date": "01.01.2010",
            },
        }
        self.archived = {}
        self.core_create = core_create
        self.core_delete = core_delete
        self.edit_links = edit_links
        self.audit = []

    def _master_can(self, action):
        if action == "heritage.view":
            return True
        if action == "heritage.edit_links":
            return self.edit_links
        if action == "core.create_animals":
            return self.core_create
        if action == "core.delete_animals":
            return self.core_delete
        if action == "heritage.delete_durable_dummy":
            return self.core_delete
        if action == "heritage.edit_genotype_colors":
            return True
        return False

    def _master_can_for_unit(self, action, owner_unit_id):
        return action == "core.create_animals" and owner_unit_id == "unit-a" and self.core_create

    def _master_audit(self, *event):
        self.audit.append(event)


class _AuthorizationOnly:
    """Managed-style authorization service without a Master UI helper."""

    def __init__(self, *, heritage_view=True):
        self.heritage_view = heritage_view
        self.calls = []

    def can(self, action):
        self.calls.append(action)
        return action == "heritage.view" and self.heritage_view


class _ManagedUnitAuthorization:
    trusted_local = False

    def __init__(self, visible_units):
        self.visible_units = {str(value).casefold() for value in visible_units}

    def can(self, action):
        return action == "heritage.view"

    def can_read_unit(self, owner_unit_id, *, allow_unassigned=False):
        owner = str(owner_unit_id or "").casefold()
        if not owner:
            return bool(allow_unassigned)
        return owner in self.visible_units


class HeritageIssue167Test(unittest.TestCase):
    def test_dummy_parent_selection_is_permission_gated_at_creation(self):
        allowed_app = _App(core_create=True, edit_links=True)
        allowed_plugin = HeritageTrackPlugin(allowed_app)
        self.assertTrue(allowed_plugin.create_heritage_only_animal(
            "WithParents", "Mother", "", "", "", "male",
            species="Callithrix jacchus",
        ))
        allowed_entry = allowed_plugin.store.get_all_entries()[
            "WithParents | Callithrix jacchus | undated | Heritage Track"
        ]
        self.assertEqual(allowed_entry["egg_donor"], "Mother")

        restricted_app = _App(core_create=False, edit_links=False)
        restricted_plugin = HeritageTrackPlugin(restricted_app)
        self.assertTrue(restricted_plugin.create_heritage_only_animal(
            "NoParents", "", "", "", "", "male",
            species="Callithrix jacchus",
        ))
        self.assertFalse(restricted_plugin.create_heritage_only_animal(
            "Denied", "Mother", "", "", "", "male",
            species="Callithrix jacchus",
        ))

    def test_metadata_only_command_does_not_require_parent_permission(self):
        app = _App(core_create=False, edit_links=False)
        plugin = HeritageTrackPlugin(app)
        self.assertTrue(plugin.create_heritage_only_animal(
            "MetadataOnly", "", "", "", "", "female",
            species="Callithrix jacchus",
        ))
        key = "MetadataOnly | Callithrix jacchus | undated | Heritage Track"
        self.assertTrue(plugin.set_dummy_parentage(
            animal_id=key,
            values={},
            target_metadata={"node_fill_color": "#ffeeaa"},
        ))
        self.assertEqual(plugin._temporary_dummies[key]["node_fill_color"], "#ffeeaa")

    def test_authorization_service_is_used_when_master_helper_is_absent(self):
        app = _App(core_create=False, edit_links=False)
        app._master_can = None
        app.authorization = _AuthorizationOnly(heritage_view=True)
        plugin = HeritageTrackPlugin(app)
        self.assertTrue(plugin.create_heritage_only_animal(
            "ManagedViewer", "", "", "", "", "female",
            species="Callithrix jacchus",
        ))
        self.assertIn("heritage.view", app.authorization.calls)

        app.authorization.heritage_view = False
        self.assertFalse(plugin.create_heritage_only_animal(
            "DeniedManagedViewer", "", "", "", "", "female",
            species="Callithrix jacchus",
        ))

    def test_former_core_source_cannot_be_forged_through_public_command(self):
        app = _App(core_create=True, core_delete=True)
        plugin = HeritageTrackPlugin(app)
        with self.assertRaises(ParentageCommandError) as raised:
            plugin.set_dummy_parentage(
                animal_id="Forged",
                source="former_core_dummy",
                target_metadata={
                    "heritage_only": True,
                    "dummy_kind": "former_core",
                    "persistence_kind": "former_core_dummy",
                    "species": "Callithrix jacchus",
                },
                values={},
            )
        self.assertEqual(raised.exception.code, "heritage_track.error.permission_denied")
        self.assertEqual(app.backend.records.put_count, 0)

    def test_view_only_direct_dummy_is_session_scoped_and_not_written(self):
        app = _App(core_create=False, edit_links=False)
        plugin = HeritageTrackPlugin(app)
        self.assertTrue(plugin.create_heritage_only_animal(
            "Temporary", "", "", "", "", "female", species="Callithrix jacchus"
        ))
        key = "Temporary | Callithrix jacchus | undated | Heritage Track"
        self.assertIn(key, plugin._temporary_dummies)
        self.assertNotIn(key, plugin.store.get_all_entries())
        self.assertEqual(app.backend.records.put_count, 0)
        self.assertEqual(app.audit, [])
        plugin.on_tab_hidden()
        self.assertNotIn(key, plugin._temporary_dummies)

    def test_core_create_eligibility_makes_direct_dummy_durable(self):
        app = _App(core_create=True, core_delete=True)
        plugin = HeritageTrackPlugin(app)
        self.assertTrue(plugin.create_heritage_only_animal(
            "Durable", "", "", "", "", "female", species="Callithrix jacchus"
        ))
        key = "Durable | Callithrix jacchus | undated | Heritage Track"
        entry = plugin.store.get_all_entries()[key]
        self.assertEqual(entry["persistence_kind"], "direct_dummy")
        self.assertEqual(entry["unit_id"], "unit-a")
        self.assertNotIn(key, plugin._temporary_dummies)
        self.assertGreaterEqual(app.backend.records.put_count, 1)
        self.assertTrue(plugin.delete_heritage_only_animal(key))

    def test_cage_unit_id_on_core_record_is_not_organizational_scope(self):
        app = _App(core_create=False)
        app.animals["Mother"]["unit_id"] = "cage-unit-17"
        plugin = HeritageTrackPlugin(app)
        self.assertIn("Mother", plugin.parent_candidate_options("female", "Callithrix jacchus"))

    def test_managed_unit_service_hides_cross_unit_core_parent_even_without_ui_unit(self):
        app = _App(core_create=False)
        app.master_track.current_unit_id = ""
        app._master_can = None
        app.authorization = _ManagedUnitAuthorization({"unit-a"})
        app.animals["Mother"]["organization_unit_id"] = "unit-b"
        plugin = HeritageTrackPlugin(app)
        self.assertNotIn(
            "Mother",
            plugin.parent_candidate_options("female", "Callithrix jacchus"),
        )
        self.assertEqual(
            plugin.resolve_parent_reference("Mother", "Callithrix jacchus"),
            ("Mother", "missing"),
        )

    def test_core_animal_parentage_is_read_only(self):
        app = _App(core_create=True)
        plugin = HeritageTrackPlugin(app)
        before = copy.deepcopy(app.animals["Child"])
        with self.assertRaises(ParentageCommandError) as raised:
            plugin.set_parentage(animal_id="Child", values={"egg_donor": "Mother"})
        self.assertEqual(raised.exception.code, "heritage_track.error.core_read_only")
        self.assertEqual(app.animals["Child"], before)
        self.assertEqual(app.backend.records.put_count, 0)

    def test_core_visual_overlay_does_not_create_core_shadow_entry(self):
        app = _App(core_create=True)
        app.animals["Child"]["genotype"] = "WT/WT"
        plugin = HeritageTrackPlugin(app)
        self.assertTrue(plugin.set_node_visual("Child", None, "#ffeeaa"))
        self.assertEqual(app.animals["Child"]["genotype"], "WT/WT")
        payload = app.backend.records.values[("heritage", "graph")]
        self.assertEqual(payload.get("animals", {}), {})
        self.assertEqual(payload["genotype_colors"]["wt/wt"], "#ffeeaa")
        self.assertNotIn("h-/h-", payload["genotype_colors"])
        writes_before = app.backend.records.put_count
        self.assertFalse(plugin.set_node_visual("Child", "H-/H-", "#aaffee"))
        self.assertEqual(app.backend.records.put_count, writes_before)
        self.assertEqual(payload["genotype_colors"]["wt/wt"], "#ffeeaa")

    def test_former_core_snapshot_is_durable_and_keeps_lineage(self):
        app = _App(core_create=True, core_delete=True)
        plugin = HeritageTrackPlugin(app)
        mother = copy.deepcopy(app.animals["Mother"])
        self.assertTrue(plugin.promote_core_to_former_dummy(
            "Mother", mother, authorized=True
        ))
        entry = plugin.store.get_all_entries()["Mother"]
        self.assertTrue(entry["heritage_only"])
        self.assertEqual(entry["persistence_kind"], "former_core_dummy")
        self.assertEqual(entry["dummy_kind"], "former_core")
        self.assertEqual(plugin.store.get_parentage("Child", app.animals["Child"])["egg_donor"], "Mother")

    def test_node_editor_enforces_core_read_only_and_dummy_catalogue(self):
        qt_app = QApplication.instance() or QApplication([])
        real = NodeEditDialog(
            None, {}, "Child", "", "WT/WT", ["Mother"], [], "Mother", "",
            sex="female", animal_species="Callithrix jacchus",
            sex_editable=False, genotype_editable=False, parents_editable=False,
            color_editable=False,
        )
        self.assertFalse(real.mother_combo.isEnabled())
        self.assertFalse(real.father_combo.isEnabled())
        self.assertFalse(real.sex_combo.isEnabled())
        self.assertEqual(real.sex_combo.toolTip(), "Sex is maintained by the main animal record.")
        self.assertEqual(real.sex_combo.accessibleDescription(), real.sex_combo.toolTip())
        self.assertIn("background: #f0f0f0", real.sex_combo.styleSheet())
        # A disabled field must retain the canonical Core value even if a
        # caller changes the widget programmatically.
        real.sex_combo.setCurrentIndex(real.sex_combo.findData("male"))
        self.assertEqual(real.values()["sex"], "female")
        self.assertFalse(real.genotype_edit.isEnabled())
        dummy = NodeEditDialog(
            None, {}, "Dummy", "", "WT/WT", [], [], "", "",
            animal_species="Callithrix jacchus", genotype_editable=True,
            genotype_options=["WT/WT", "H-/H-"], parents_editable=True,
        )
        self.assertIsInstance(dummy.genotype_edit, QComboBox)
        self.assertTrue(dummy.sex_combo.isEnabled())
        self.assertEqual(dummy.values()["genotype"], "WT/WT")
        restricted_dummy = NodeEditDialog(
            None,
            {"heritage_track.parents.permission_required": "edit-links required"},
            "Restricted Dummy", "", "", [], [], "", "",
            animal_species="Callithrix jacchus", parents_editable=False,
            parents_read_only_core=False,
        )
        self.assertFalse(restricted_dummy.mother_combo.isEnabled())
        self.assertFalse(restricted_dummy.father_combo.isEnabled())
        self.assertEqual(restricted_dummy.mother_combo.toolTip(), "edit-links required")
        self.assertEqual(restricted_dummy.father_combo.toolTip(), "edit-links required")
        self.assertEqual(
            restricted_dummy.mother_combo.accessibleDescription(),
            "edit-links required",
        )
        # The service boundary also ignores a forged Core sex mutation.
        app = _App(core_create=True)
        plugin = HeritageTrackPlugin(app)
        before = copy.deepcopy(app.animals["Child"])
        writes_before = app.backend.records.put_count
        plugin.set_manual_sex("Child", "male")
        self.assertEqual(app.animals["Child"], before)
        self.assertEqual(app.backend.records.put_count, writes_before)
        app.archived["Archived"] = {
            "name": "Archived", "species": "Callithrix jacchus", "sex": "female",
            "death_date": "01.01.2025",
        }
        archived_before = copy.deepcopy(app.archived["Archived"])
        plugin.set_manual_sex("Archived", "male")
        self.assertEqual(app.archived["Archived"], archived_before)
        self.assertEqual(app.backend.records.put_count, writes_before)
        real.deleteLater()
        dummy.deleteLater()
        restricted_dummy.deleteLater()
        qt_app.processEvents()

    def test_durable_dummy_edit_is_one_atomic_write_and_invalid_metadata_rolls_back(self):
        app = _App(core_create=True, core_delete=True)
        app.animals["Mother"]["species"] = "Callitrix jacchus"
        plugin = HeritageTrackPlugin(app)
        self.assertTrue(plugin.create_heritage_only_animal(
            "Atomic", "", "", "", "", "female",
            species="Callitrix jacchus",
        ))
        key = "Atomic | Callitrix jacchus | undated | Heritage Track"
        before = copy.deepcopy(plugin.store.get_all_entries()[key])
        writes_before = app.backend.records.put_count

        self.assertTrue(plugin.set_dummy_parentage(
            actor="manager",
            animal_id=key,
            values={
                "egg_donor": "Mother", "sperm_donor": "",
                "surrogate_mother": "", "surrogate_father": "",
            },
            target_metadata={
                "sex": "male", "genotype": "WT/WT", "node_fill_color": "#ffeeaa",
            },
        ))
        self.assertEqual(app.backend.records.put_count, writes_before + 1)
        edited = plugin.store.get_all_entries()[key]
        self.assertEqual(edited["egg_donor"], "Mother")
        self.assertEqual(edited["sex"], "male")
        self.assertEqual(edited["genotype"], "WT/WT")
        self.assertEqual(edited["node_fill_color"], "#ffeeaa")

        writes_before = app.backend.records.put_count
        with self.assertRaises(ParentageCommandError) as raised:
            plugin.set_dummy_parentage(
                actor="manager",
                animal_id=key,
                values=None,
                target_metadata={"genotype": "not-in-species-catalogue"},
            )
        self.assertEqual(raised.exception.code, "heritage_track.error.invalid_genotype")
        self.assertEqual(app.backend.records.put_count, writes_before)
        self.assertEqual(plugin.store.get_all_entries()[key], edited)
        self.assertNotEqual(before, edited)

    def test_durable_dummy_metadata_and_delete_require_current_unit_and_permission(self):
        app = _App(core_create=True, core_delete=True, edit_links=False)
        plugin = HeritageTrackPlugin(app)
        self.assertTrue(plugin.create_heritage_only_animal(
            "Owned", "", "", "", "", "female",
            species="Callithrix jacchus",
        ))
        key = "Owned | Callithrix jacchus | undated | Heritage Track"
        stored_before = copy.deepcopy(plugin.store.get_all_entries()[key])
        writes_before = app.backend.records.put_count

        # A viewer without edit-links cannot alter durable metadata, even if
        # a caller forges a direct command.  A different Unit cannot remove it.
        self.assertFalse(plugin.set_manual_sex(key, "male"))
        self.assertEqual(plugin.store.get_all_entries()[key], stored_before)
        app.core_delete = False
        self.assertFalse(plugin.delete_heritage_only_animal(key))
        self.assertEqual(plugin.store.get_all_entries()[key], stored_before)
        app.master_track.current_unit_id = "unit-b"
        self.assertFalse(plugin.delete_heritage_only_animal(key))
        self.assertEqual(plugin.store.get_all_entries()[key], stored_before)
        self.assertEqual(app.backend.records.put_count, writes_before)

    def test_duplicate_dummy_identity_cannot_overwrite_existing_metadata(self):
        app = _App(core_create=True, core_delete=True)
        first = HeritageTrackPlugin(app)
        self.assertTrue(first.create_heritage_only_animal(
            "Race", "", "", "WT/WT", "#ffeeaa", "female",
            species="Callitrix jacchus",
        ))
        key = "Race | Callitrix jacchus | undated | Heritage Track"
        saved = copy.deepcopy(first.store.get_all_entries()[key])
        second = HeritageTrackPlugin(app)
        self.assertFalse(second.create_heritage_only_animal(
            "Race", "", "", "H-/H-", "#aaffee", "male",
            species="Callitrix jacchus",
        ))
        current = second.store.get_all_entries()[key]
        for field in ("genotype", "node_fill_color", "sex", "dummy_kind", "persistence_kind", "unit_id"):
            self.assertEqual(current[field], saved[field])

    def test_real_core_ipid_alias_is_read_only(self):
        app = _App(core_create=True)
        app.animals["Display Name"] = app.animals.pop("Child")
        app.animals["Display Name"]["name"] = "Display Name"
        app.animals["Display Name"]["ipid"] = "core-ipid-1"
        plugin = HeritageTrackPlugin(app)
        before = copy.deepcopy(app.animals["Display Name"])
        with self.assertRaises(ParentageCommandError) as raised:
            plugin.set_parentage(
                animal_id="core-ipid-1", values={"egg_donor": "Mother"}
            )
        self.assertEqual(raised.exception.code, "heritage_track.error.core_read_only")
        self.assertEqual(app.animals["Display Name"], before)

    def test_new_core_record_after_render_snapshot_cannot_be_treated_as_dummy(self):
        app = _App(core_create=True)
        plugin = HeritageTrackPlugin(app)
        plugin.build_engine()
        app.animals["Late Core"] = {
            "name": "Late Core", "ipid": "late-core-1",
            "species": "Callithrix jacchus", "sex": "female",
            "birth_date": "01.01.2020",
        }
        with self.assertRaises(ParentageCommandError) as raised:
            plugin.set_parentage(
                animal_id="Late Core",
                values={"egg_donor": "Mother"},
            )
        self.assertEqual(raised.exception.code, "heritage_track.error.core_read_only")
        self.assertNotIn("Late Core", plugin.store.get_all_entries())

    def test_command_boundary_prefers_fresh_backend_core_read(self):
        app = _App(core_create=True)
        latest = {
            "animals": {
                "Backend Core": {
                    "name": "Backend Core", "ipid": "backend-core-1",
                    "species": "Callithrix jacchus", "sex": "female",
                    "birth_date": "01.01.2020",
                }
            },
            "archived_animals": {},
        }
        app.backend.load_core_data = lambda: copy.deepcopy(latest)
        plugin = HeritageTrackPlugin(app)
        plugin.build_engine()
        with self.assertRaises(ParentageCommandError) as raised:
            plugin.set_parentage(
                animal_id="Backend Core",
                values={"egg_donor": "Mother"},
            )
        self.assertEqual(raised.exception.code, "heritage_track.error.core_read_only")
        self.assertNotIn("Backend Core", plugin.store.get_all_entries())

    def test_core_delete_atomically_preserves_referenced_former_core_dummy(self):
        root = Path(tempfile.mkdtemp(prefix="progtrack-167-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        base = root / "runtime"
        paths = RuntimePaths(
            application_root=Path(__file__).resolve().parents[1],
            profile=BackendProfile.STANDALONE_SQLITE,
            data_root=base / "data", config_root=base / "config",
            cache_root=base / "cache", state_root=base / "state",
            database_path=base / "data" / "database" / "progtrack.sqlite3",
            managed_root=base / "managed", managed_documents=base / "managed" / "documents",
            managed_config_assets=base / "managed" / "config-assets",
            logs=base / "state" / "logs", runtime=base / "state" / "runtime",
            exports=base / "data" / "exports", preferences=base / "config" / "preferences",
            profile_file=base / "config" / "backend.json",
        )
        paths.create_mutable_roots()
        backend = ProgTrackBackend(paths, acquire_process_lock=False, bootstrap_seed=False)
        self.addCleanup(backend.close)
        mother = {
            "ipid": "core-mother-1", "name": "Mother", "species": "Callithrix jacchus",
            "birth_date": "01.01.2010", "origin": "Example", "sex": "female",
            "events": [], "eizellspenderin": "", "samenspender": "",
        }
        backend.save_core_data({"animals": {}, "archived_animals": {"core-mother-1": mother}})
        backend.records.put("heritage", "graph", {
            "version": "1.0.0",
            "animals": {
                "Child": {
                    "name": "Child", "species": "Callithrix jacchus", "sex": "female",
                    "birth_date": "01.01.2020", "heritage_only": True,
                    "dummy_kind": "direct", "persistence_kind": "direct_dummy",
                    "unit_id": "unit-a", "egg_donor": "core-mother-1",
                }
            },
        })
        self.assertTrue(backend.delete_archived_animal_atomically(
            "Mother", mother, owner_unit_id="unit-a"
        ))
        snapshot = backend.load_core_data()
        self.assertNotIn("core-mother-1", snapshot["archived_animals"])
        graph = backend.records.get("heritage", "graph")
        former = graph["animals"]["core-mother-1"]
        self.assertEqual(former["dummy_kind"], "former_core")
        self.assertEqual(former["persistence_kind"], "former_core_dummy")
        self.assertEqual(former["unit_id"], "unit-a")

        # Core-only parentage is also considered when no Heritage shadow exists.
        second_mother = dict(mother, ipid="core-mother-2", name="Mother 2")
        core_child = dict(
            mother, ipid="core-child-1", name="Core Child", birth_date="01.01.2020",
            sex="male", eizellspenderin="core-mother-2",
        )
        backend.save_core_data({
            "animals": {"core-child-1": core_child},
            "archived_animals": {"core-mother-2": second_mother},
        })
        self.assertTrue(backend.delete_archived_animal_atomically(
            "core-mother-2", second_mother, owner_unit_id="unit-a"
        ))
        self.assertIn("core-mother-2", backend.records.get("heritage", "graph")["animals"])

        # A corrupt lineage payload must fail closed rather than deleting the
        # Core row and silently losing an otherwise unknown reference.
        third_mother = dict(mother, ipid="core-mother-3", name="Mother 3")
        backend.save_core_data({"animals": {}, "archived_animals": {"core-mother-3": third_mother}})
        backend.records.put("heritage", "graph", "not-a-graph-object")
        with self.assertRaises(ValueError):
            backend.delete_archived_animal_atomically(
                "core-mother-3", third_mother, owner_unit_id="unit-a"
            )
        self.assertIn("core-mother-3", backend.load_core_data()["archived_animals"])

    def test_core_delete_rejects_non_mapping_record_before_field_access(self):
        root = Path(tempfile.mkdtemp(prefix="progtrack-167-type-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        base = root / "runtime"
        paths = RuntimePaths(
            application_root=Path(__file__).resolve().parents[1],
            profile=BackendProfile.STANDALONE_SQLITE,
            data_root=base / "data", config_root=base / "config",
            cache_root=base / "cache", state_root=base / "state",
            database_path=base / "data" / "database" / "progtrack.sqlite3",
            managed_root=base / "managed", managed_documents=base / "managed" / "documents",
            managed_config_assets=base / "managed" / "config-assets",
            logs=base / "state" / "logs", runtime=base / "state" / "runtime",
            exports=base / "data" / "exports", preferences=base / "config" / "preferences",
            profile_file=base / "config" / "backend.json",
        )
        paths.create_mutable_roots()
        backend = ProgTrackBackend(paths, acquire_process_lock=False, bootstrap_seed=False)
        self.addCleanup(backend.close)
        self.assertFalse(backend.delete_archived_animal_atomically("missing", None))


if __name__ == "__main__":
    unittest.main()
