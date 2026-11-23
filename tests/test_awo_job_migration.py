"""AWO is a normal Vet-bound job, not an account role."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from Plugins.Master_Track.permissions import (
    JOB_ANIMAL_WELFARE, JOB_VET, ROLE_BASELINES, ROLE_USER,
    can, sanitize_assigned_jobs,
)

ROOT = Path(__file__).resolve().parents[1]

class AwoJobContractTests(unittest.TestCase):
    def test_awo_job_requires_vet(self):
        self.assertEqual(sanitize_assigned_jobs([JOB_ANIMAL_WELFARE]), [])
        self.assertEqual(sanitize_assigned_jobs([JOB_VET, JOB_ANIMAL_WELFARE]), [JOB_VET, JOB_ANIMAL_WELFARE])
        self.assertTrue(can(ROLE_USER, [JOB_VET, JOB_ANIMAL_WELFARE], [], [], "medi_track.filter_use"))
        self.assertFalse(can(ROLE_USER, [JOB_ANIMAL_WELFARE], [], [], "medi_track.filter_use"))

    def test_primary_role_catalog_has_no_awo_role(self):
        self.assertNotIn("animal_welfare_officer", ROLE_BASELINES)

    def test_seed_veti_is_user_with_both_jobs(self):
        import zipfile
        with zipfile.ZipFile(ROOT / "Resources" / "Seed" / "progtrack_seed.ptdb") as zf:
            rows = [json.loads(line) for line in zf.read("records/domain.jsonl").decode("utf-8").splitlines()]
        users = next(row["payload"] for row in rows if row.get("namespace") == "security" and row.get("record_id") == "users")
        veti = next(user for user in users if user["username"] == "Veti")
        self.assertEqual(veti["role"], ROLE_USER)
        self.assertEqual(veti["jobs"], [JOB_VET, JOB_ANIMAL_WELFARE])
        self.assertFalse(any(user.get("role") == JOB_ANIMAL_WELFARE for user in users))

    def test_project_welfare_picker_uses_job_filter(self):
        source = (ROOT / "Plugins" / "Projects_Track" / "project_track_tab.py").read_text(encoding="utf-8")
        self.assertIn("job_filter='animal_welfare_officer'", source)
        self.assertIn("self._job_filter not in set(ud.get('jobs') or [])", source)

if __name__ == "__main__":
    unittest.main()
