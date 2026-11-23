"""Focused regression checks for Edit Animal role permission separation (#235)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "ProgTrack.v.0.3.0.py").read_text(encoding="utf-8")


def _function_body(source: str, name: str) -> str:
    start = source.index(f"    def {name}(")
    next_def = source.find("\n    def ", start + 1)
    return source[start:] if next_def < 0 else source[start:next_def]


class Issue235RoleDropdownPermissionTest(TestCase):
    def test_saved_female_role_selector_uses_role_permission_not_research(self):
        body = _function_body(SOURCE, "_dlg_female_animal")
        self.assertIn(
            "('core.edit_animal_research_data' if creating\n"
            "             else 'core.edit_animal_role'): [role_cb]",
            body,
        )
        research_group = re.search(
            r"'core\.edit_animal_research_data':\s*\[(.*?)\n\s*\]\s*\+ _pdg_inputs,",
            body,
            re.DOTALL,
        )
        self.assertIsNotNone(research_group)
        self.assertIn("role_cb if creating else None", research_group.group(1))
        self.assertNotIn("role_cb, genotype_le", research_group.group(1))

    def test_role_change_is_rejected_at_save_boundary_without_permission(self):
        body = _function_body(SOURCE, "_dlg_female_animal")
        guard = body.index("if role_changed and not self._master_can('core.edit_animal_role'):")
        cleanup = body.index("# Scalars")
        self.assertLess(guard, cleanup)
        self.assertIn("self._show_permission_denied()", body[guard:cleanup])
        self.assertIn("return", body[guard:cleanup])

    def test_role_only_account_can_reach_the_role_selector_path(self):
        body = _function_body(SOURCE, "_dlg_edit_animal")
        self.assertIn("can_role_edit = self._master_can('core.edit_animal_role')", body)
        self.assertIn("if not can_edit and not can_role_edit and not can_readonly:", body)
        self.assertIn("if not can_edit and can_role_edit and idx != 0:", body)

    def test_all_tab_role_entry_point_retains_same_permission_key(self):
        body = _function_body(SOURCE, "_dlg_change_sort")
        self.assertIn("if not self._master_can('core.edit_animal_role'):", body)
        self.assertIn("if read_only:", body)
        self.assertIn("if not self._master_can('core.edit_animal_role'):", body)

    def test_all_tab_entry_point_uses_the_same_lease_helper(self):
        body = _function_body(SOURCE, "_on_edit_in_all_tab")
        self.assertIn("self._open_animal_editor_with_lease(", body)
        self.assertIn("read_only=read_only", body)
