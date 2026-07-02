import tempfile
import unittest
from pathlib import Path

from Plugins.core.animal_roles import (
    AnimalRoleRegistry,
    REQUIRED_DIALOG_BLOCKS,
    ROLE_VALUE_AMME,
    ROLE_VALUE_EXPERIMENTAL,
    ROLE_VALUE_SPENDER,
    clear_deleted_role_assignments,
    normalize_block_list,
)


class AnimalRoleRegistryTest(unittest.TestCase):
    def _registry(self):
        tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(tmpdir.cleanup)
        return AnimalRoleRegistry(Path(tmpdir.name) / "animal_roles.json")

    def test_defaults_include_built_in_roles(self):
        registry = self._registry()
        roles = registry.roles()
        values = {role["value"] for role in roles}

        self.assertIn(ROLE_VALUE_SPENDER, values)
        self.assertIn(ROLE_VALUE_EXPERIMENTAL, values)
        self.assertTrue(registry.get_by_value(ROLE_VALUE_SPENDER)["built_in"])

    def test_custom_role_uses_stable_custom_value_and_emoji(self):
        registry = self._registry()
        role = registry.make_custom_role("Training group", "🧪")

        self.assertEqual("Training group", role["label"])
        self.assertEqual("🧪", role["icon"])
        self.assertTrue(role["value"].startswith("custom.training_group"))
        self.assertEqual("basic", role["base_editor"])

    def test_custom_role_values_are_unique(self):
        registry = self._registry()
        first = registry.make_custom_role("Training group", "🧪")
        second = registry.make_custom_role(
            "Training group",
            "🧪",
            existing_values={first["value"]},
        )

        self.assertNotEqual(first["value"], second["value"])

    def test_save_and_reload_preserves_custom_roles(self):
        registry = self._registry()
        custom = registry.make_custom_role("Observation", "🔎")
        registry.save_roles([*registry.roles(), custom])

        reloaded = AnimalRoleRegistry(registry.path)
        saved = reloaded.get_by_value(custom["value"])

        self.assertIsNotNone(saved)
        self.assertEqual("Observation", saved["label"])
        self.assertEqual("🔎", saved["icon"])

    def test_display_uses_translation_for_builtins_and_label_for_custom(self):
        registry = self._registry()
        messages = {"role.spenderin": "Egg donor"}
        custom = registry.make_custom_role("Observation", "🔎")
        registry.save_roles([*registry.roles(), custom])

        self.assertEqual("♀ Egg donor", registry.display_for_value(ROLE_VALUE_SPENDER, messages))
        self.assertEqual("🔎 Observation", registry.display_for_value(custom["value"], messages))


    def test_dialog_blocks_always_include_required_blocks(self):
        blocks = normalize_block_list(["health_flags", "unknown_block"])

        for required in REQUIRED_DIALOG_BLOCKS:
            self.assertIn(required, blocks)
        self.assertIn("health_flags", blocks)
        self.assertNotIn("unknown_block", blocks)

    def test_custom_roles_get_basic_dialog_and_event_recipes(self):
        registry = self._registry()
        custom = registry.make_custom_role("Observation", "ðŸ”Ž")
        registry.save_roles([*registry.roles(), custom])

        reloaded = AnimalRoleRegistry(registry.path)
        blocks = reloaded.dialog_blocks_for_value(custom["value"], "new")
        recipe = reloaded.event_recipe_for_value(custom["value"])

        for required in REQUIRED_DIALOG_BLOCKS:
            self.assertIn(required, blocks)
        self.assertIn("health_flags", blocks)
        self.assertEqual([], recipe["available_events"])

    def test_find_by_label_exact_is_case_and_whitespace_sensitive(self):
        registry = self._registry()
        custom = registry.make_custom_role("Facility breeder", "*")
        registry.save_roles([*registry.roles(), custom])

        self.assertIsNotNone(registry.find_by_label_exact("Facility breeder"))
        self.assertIsNone(registry.find_by_label_exact("facility breeder"))
        self.assertIsNone(registry.find_by_label_exact("Facility  breeder"))

    def test_imported_role_is_confirmed_and_preserves_source_label(self):
        registry = self._registry()
        imported = registry.make_imported_role("Facility breeder", source="pta")
        registry.save_roles([*registry.roles(), imported])

        reloaded = AnimalRoleRegistry(registry.path)
        saved = reloaded.get_by_value(imported["value"])

        self.assertTrue(saved["imported"])
        self.assertEqual("confirmed", saved["review_state"])
        self.assertEqual("Facility breeder", saved["original_label"])
        self.assertEqual("basic", saved["base_editor"])

    def test_imported_role_with_existing_exact_label_integrates_existing_role(self):
        registry = self._registry()
        custom = registry.make_custom_role("Facility breeder", "*")
        registry.save_roles([*registry.roles(), custom])

        imported = registry.make_imported_role("Facility breeder", source="pta")

        self.assertEqual(custom["value"], imported["value"])
        self.assertEqual("Facility breeder", imported["original_label"])

    def test_deleted_builtin_roles_stay_deleted_after_reload(self):
        registry = self._registry()
        roles = [role for role in registry.roles() if role["value"] != ROLE_VALUE_AMME]
        registry.save_roles(roles)

        reloaded = AnimalRoleRegistry(registry.path)
        values = {role["value"] for role in reloaded.roles()}

        self.assertNotIn(ROLE_VALUE_AMME, values)
        self.assertIn(ROLE_VALUE_SPENDER, values)

    def test_deleted_role_assignments_become_roleless(self):
        animals = {
            "A": {"rolle": ROLE_VALUE_AMME},
            "B": {"rolle": ROLE_VALUE_SPENDER},
        }

        changed = clear_deleted_role_assignments(animals, [ROLE_VALUE_AMME])

        self.assertEqual(["A"], changed)
        self.assertEqual("", animals["A"]["rolle"])
        self.assertEqual(ROLE_VALUE_SPENDER, animals["B"]["rolle"])

    def test_builtin_dialog_block_overrides_are_preserved(self):
        registry = self._registry()
        roles = registry.roles()
        for role in roles:
            if role["value"] == ROLE_VALUE_SPENDER:
                role["dialog_blocks"]["new"] = ["identity", "weight"]
                role["dialog_blocks"]["edit"] = ["identity", "weight", "health_flags"]
        registry.save_roles(roles)

        reloaded = AnimalRoleRegistry(registry.path)
        new_blocks = reloaded.dialog_blocks_for_value(ROLE_VALUE_SPENDER, "new")
        edit_blocks = reloaded.dialog_blocks_for_value(ROLE_VALUE_SPENDER, "edit")

        for required in REQUIRED_DIALOG_BLOCKS:
            self.assertIn(required, new_blocks)
            self.assertIn(required, edit_blocks)
        self.assertIn("health_flags", edit_blocks)


if __name__ == "__main__":
    unittest.main()
