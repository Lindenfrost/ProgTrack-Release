import tempfile
import unittest
from pathlib import Path

from Plugins.core.animal_roles import (
    AnimalRoleRegistry,
    REQUIRED_DIALOG_BLOCKS,
    ROLE_VALUE_AMME,
    ROLE_VALUE_EXPERIMENTAL,
    ROLE_VALUE_OFFSPRING,
    ROLE_VALUE_PARTNER,
    ROLE_VALUE_SAMENSP,
    ROLE_VALUE_SPENDER,
    ROLE_VALUE_UNKNOWN,
    ROLE_VALUE_ZUCHTTIER,
    canonical_role_value,
    clear_deleted_role_assignments,
    import_capabilities_for_blocks,
    normalize_animal_record_roles,
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

    def test_builtin_roles_use_internal_ids_and_display_labels(self):
        registry = self._registry()
        expected_values = {
            ROLE_VALUE_SPENDER,
            ROLE_VALUE_AMME,
            ROLE_VALUE_SAMENSP,
            ROLE_VALUE_OFFSPRING,
            ROLE_VALUE_PARTNER,
            ROLE_VALUE_ZUCHTTIER,
            ROLE_VALUE_EXPERIMENTAL,
            ROLE_VALUE_UNKNOWN,
        }
        roles = [role for role in registry.roles() if role.get("built_in")]

        self.assertEqual(expected_values, {role["value"] for role in roles})
        for role in roles:
            label = role["label"]
            self.assertNotIn("_", label)
            self.assertEqual(label[:1].upper(), label[:1])

    def test_legacy_role_values_normalize_to_internal_ids(self):
        self.assertEqual(ROLE_VALUE_SPENDER, canonical_role_value("Spenderin"))
        self.assertEqual(ROLE_VALUE_AMME, canonical_role_value("amme"))
        self.assertEqual(ROLE_VALUE_SAMENSP, canonical_role_value("Samenspender"))
        self.assertEqual(ROLE_VALUE_PARTNER, canonical_role_value("Partnertier"))
        self.assertEqual(ROLE_VALUE_ZUCHTTIER, canonical_role_value("Zuchttier"))
        self.assertEqual(ROLE_VALUE_EXPERIMENTAL, canonical_role_value("Versuchstier"))

    def test_custom_role_uses_stable_custom_value_and_icon(self):
        registry = self._registry()
        role = registry.make_custom_role("Training group", "*")

        self.assertEqual("Training group", role["label"])
        self.assertEqual("*", role["icon"])
        self.assertTrue(role["value"].startswith("custom.training_group"))
        self.assertEqual("role.custom.training_group", role["label_key"])
        self.assertEqual("basic", role["base_editor"])

    def test_custom_role_values_are_unique(self):
        registry = self._registry()
        first = registry.make_custom_role("Training group", "*")
        second = registry.make_custom_role(
            "Training group",
            "*",
            existing_values={first["value"]},
        )

        self.assertNotEqual(first["value"], second["value"])

    def test_save_and_reload_preserves_custom_roles(self):
        registry = self._registry()
        custom = registry.make_custom_role("Observation", "*")
        registry.save_roles([*registry.roles(), custom])

        reloaded = AnimalRoleRegistry(registry.path)
        saved = reloaded.get_by_value(custom["value"])

        self.assertIsNotNone(saved)
        self.assertEqual("Observation", saved["label"])
        self.assertEqual("*", saved["icon"])

    def test_display_uses_translation_for_builtins_and_label_for_custom(self):
        registry = self._registry()
        messages = {"role.egg_cell_donor": "Egg cell donor"}
        custom = registry.make_custom_role("Observation", "*")
        registry.save_roles([*registry.roles(), custom])

        self.assertEqual("\u2640 Egg cell donor", registry.display_for_value(ROLE_VALUE_SPENDER, messages))
        self.assertEqual("* Observation", registry.display_for_value(custom["value"], messages))

    def test_dialog_blocks_always_include_required_blocks(self):
        blocks = normalize_block_list(["health_flags", "unknown_block"])

        for required in REQUIRED_DIALOG_BLOCKS:
            self.assertIn(required, blocks)
        self.assertIn("health_flags", blocks)
        self.assertNotIn("unknown_block", blocks)

    def test_custom_roles_get_basic_dialog_and_event_recipes(self):
        registry = self._registry()
        custom = registry.make_custom_role("Observation", "*")
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
        self.assertEqual(f"role.{imported['value']}", saved["label_key"])

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

    def test_animal_record_roles_are_normalized_to_internal_ids(self):
        animals = {
            "A": {"rolle": "Spenderin"},
            "B": {"rolle": "Samenspender"},
            "C": {"rolle": ROLE_VALUE_EXPERIMENTAL},
            "D": {"rolle": ""},
        }

        changed = normalize_animal_record_roles(animals)

        self.assertEqual(["A", "B", "D"], changed)
        self.assertEqual(ROLE_VALUE_SPENDER, animals["A"]["rolle"])
        self.assertEqual(ROLE_VALUE_SAMENSP, animals["B"]["rolle"])
        self.assertEqual(ROLE_VALUE_EXPERIMENTAL, animals["C"]["rolle"])
        self.assertEqual(ROLE_VALUE_UNKNOWN, animals["D"]["rolle"])

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

    def test_sidebar_import_capabilities_follow_dialog_blocks(self):
        caps = import_capabilities_for_blocks([
            "blood_progesterone",
            "urine_pdg",
            "weight",
            "sperm_measurements",
        ])

        self.assertEqual(
            {"blood": True, "urine": True, "weight": True, "sperm": True},
            caps,
        )

    def test_sidebar_import_capabilities_respect_plugin_gates(self):
        caps = import_capabilities_for_blocks(
            ["blood_progesterone", "urine_pdg", "weight", "sperm_measurements"],
            steroid_active=False,
            has_pdg_plugin=False,
        )

        self.assertEqual(
            {"blood": False, "urine": False, "weight": True, "sperm": False},
            caps,
        )


if __name__ == "__main__":
    unittest.main()
