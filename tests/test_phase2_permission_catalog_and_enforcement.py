"""Phase 2 permission catalog, guards and managed-document deletion contracts."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from Plugins.Master_Track.permissions import (
    ALL_PERMISSIONS,
    INTERNAL_PERMISSIONS,
    JOB_BUNDLES,
    JOB_ANIMAL_WELFARE,
    JOB_VET,
    ROLE_GUEST,
    ROLE_MASTER,
    can,
    get_permission_namespace_label,
    get_permission_tooltip,
)
from Plugins.core.backend import ProgTrackBackend
from tests.test_current_backend_runtime import runtime_paths


class PermissionCatalogTests(unittest.TestCase):
    def test_catalog_is_unique_and_labels_cover_every_permission(self):
        self.assertEqual(len(ALL_PERMISSIONS), len(set(ALL_PERMISSIONS)))
        self.assertTrue(set(INTERNAL_PERMISSIONS).isdisjoint(ALL_PERMISSIONS))
        for language in ("en", "de", "it", "ru"):
            for permission in ALL_PERMISSIONS:
                self.assertIn(permission, get_permission_tooltip(permission, language))
            self.assertTrue(get_permission_namespace_label("medi_track", language))
        for bundle in JOB_BUNDLES.values():
            self.assertTrue(set(bundle).issubset(ALL_PERMISSIONS))

    def test_final_role_matrix_and_fail_closed_retired_keys(self):
        self.assertTrue(can(ROLE_MASTER, [], [], [], "master.create_users"))
        self.assertFalse(can(ROLE_MASTER, [], [], [], "project.manage_species_scope"))
        self.assertFalse(can("user", ["keeper"], [], [], "cage.edit"))
        self.assertFalse(can("user", ["vet"], [], [], "medi_track.delete_document"))
        self.assertTrue(can("user", [JOB_VET, JOB_ANIMAL_WELFARE], [], [], "medi_track.filter_use"))
        self.assertTrue(can("user", [JOB_VET, JOB_ANIMAL_WELFARE], [], [], "medi_track.delete_document"))
        self.assertFalse(can("user", [JOB_ANIMAL_WELFARE], [], [], "medi_track.filter_use"))
        self.assertFalse(can(ROLE_GUEST, [], [], [], "flow_track.open"))
        self.assertTrue(can("user", ["researcher"], [], [], "medi_track.filter_use"))
        self.assertTrue(can("user", ["researcher"], [], [], "flow_track.use"))

    def test_scheduler_view_is_available_to_researcher_manager_and_vet_only(self):
        for job in ("researcher", "manager", "vet"):
            self.assertTrue(
                can("user", [job], [], [], "op_scheduler.view"),
                f"{job} must be able to open the read-only scheduler",
            )
        self.assertFalse(can("user", ["keeper"], [], [], "op_scheduler.view"))
        self.assertTrue(can("user", ["researcher"], [], [], "op_scheduler.use"))
        self.assertFalse(can("user", ["manager"], [], [], "op_scheduler.use"))
        self.assertTrue(can("user", ["vet"], [], [], "op_scheduler.use"))

    def test_medical_export_is_job_bundle_gated(self):
        for job in ("manager", "vet"):
            self.assertTrue(can("user", [job], [], [], "medi_track.export"), job)
        self.assertTrue(can(ROLE_MASTER, [], [], [], "medi_track.export"))
        self.assertTrue(can("lord", [], [], [], "medi_track.export"))
        for job in ("researcher", "keeper", JOB_ANIMAL_WELFARE):
            self.assertFalse(can("user", [job], [], [], "medi_track.export"), job)
        self.assertFalse(can(ROLE_GUEST, [], [], [], "medi_track.export"))


class ManagedDocumentDeletionTests(unittest.TestCase):
    def test_delete_active_requires_existing_payload_and_removes_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            backend = ProgTrackBackend(runtime_paths(root), acquire_process_lock=False)
            self.addCleanup(backend.close)
            source = root / "medical.pdf"
            source.write_bytes(b"medical document")
            record = backend.documents.add(
                source, owner_type="animal-medical", owner_id="Arwen", actor="manager"
            )
            payload = backend.documents.payload_path(record)
            self.assertTrue(payload.is_file())
            deleted = backend.documents.delete_active(
                record["document_id"],
                owner_type=record["owner_type"],
                owner_id=record["owner_id"],
                expected_revision=record["revision"],
                expected_checksum=record["sha256"],
                actor="manager",
            )
            self.assertEqual(deleted["state"], "deleted")
            self.assertFalse(payload.exists())
            self.assertEqual(backend.documents.list_for_owner("animal-medical", "Arwen"), [])
            with self.assertRaises(Exception):
                backend.documents.delete_active(
                    record["document_id"],
                    owner_type=record["owner_type"],
                    owner_id=record["owner_id"],
                    expected_revision=record["revision"],
                    expected_checksum=record["sha256"],
                    actor="manager",
                )


class PermissionBoundarySourceTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def test_main_and_plugins_have_direct_boundaries(self):
        main = (self.ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")
        medi = (self.ROOT / "Plugins/Medi_Track/medi_track_widget.py").read_text(encoding="utf-8")
        cage = (self.ROOT / "Plugins/Cage__Track/cage_track_widget.py").read_text(encoding="utf-8")
        flow = (self.ROOT / "Plugins/Flow_Track/flow_track_widget.py").read_text(encoding="utf-8")
        self.assertIn('tabs.setTabEnabled(index, self._master_can(permission))', main)
        self.assertIn('core.use_filters', main)
        self.assertIn('medi_track.filter_use', medi)
        self.assertIn('medi_track.delete_document', medi)
        self.assertIn('cage.assign_locations', cage)
        self.assertIn('if not self._can("use")', flow)

    def test_master_controls_are_split_by_capability(self):
        dialogs = (self.ROOT / "Plugins/Master_Track/dialogs.py").read_text(encoding="utf-8")
        plugin = (self.ROOT / "Plugins/Master_Track/plugin.py").read_text(encoding="utf-8")
        for flag in ("can_edit_users", "can_assign_primary_role", "can_assign_jobs",
                     "can_grant_direct", "can_revoke_direct"):
            self.assertIn(flag, dialogs)
        for perm in ("PERM_MASTER_VIEW_USERS", "PERM_MASTER_CREATE_USERS",
                     "PERM_MASTER_EDIT_USERS", "PERM_MASTER_ASSIGN_PRIMARY_ROLE",
                     "PERM_MASTER_ASSIGN_JOBS", "PERM_MASTER_GRANT_DIRECT",
                     "PERM_MASTER_REVOKE_DIRECT"):
            self.assertIn(perm, plugin)


if __name__ == "__main__":
    unittest.main()
