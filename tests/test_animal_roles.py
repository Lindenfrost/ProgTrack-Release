import tempfile
import unittest
from pathlib import Path

from Plugins.core.animal_roles import (
    AnimalRoleRegistry,
    ROLE_VALUE_EXPERIMENTAL,
    ROLE_VALUE_SPENDER,
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


if __name__ == "__main__":
    unittest.main()
