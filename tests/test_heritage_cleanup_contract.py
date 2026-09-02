"""Focused contract tests for Heritage Track cleanup issue #164."""

from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

from Plugins.Heritage_Track.display_context import DisplayContext
from Plugins.Heritage_Track.engine_cache import PedigreeEngineCache
from Plugins.Heritage_Track.pedigree_engine import PedigreeEngine


ROOT = Path(__file__).resolve().parents[1]
HERITAGE = ROOT / "Plugins" / "Heritage_Track"
STALE_NAMES = (
    "adaptive_spacer.cpython-312.pyc",
    "adaptive_spacer.cpython-313.pyc",
    "edge_bundler.cpython-312.pyc",
    "edge_bundler.cpython-313.pyc",
    "family_optimizer.cpython-312.pyc",
    "family_optimizer.cpython-313.pyc",
    "force_layout.cpython-312.pyc",
    "force_layout.cpython-313.pyc",
    "scope_provider.cpython-313.pyc",
    "viewport_manager.cpython-313.pyc",
)


class HeritageCleanupContractTest(unittest.TestCase):
    def test_engine_cache_has_no_unreferenced_parentage_state(self):
        source = (HERITAGE / "engine_cache.py").read_text(encoding="utf-8")
        self.assertNotIn("self._parentage_hash", source)
        self.assertFalse(hasattr(PedigreeEngineCache(), "_parentage_hash"))

    def test_scope_provider_is_not_a_public_source_contract(self):
        self.assertFalse((HERITAGE / "scope_provider.py").exists())
        package_source = (HERITAGE / "__init__.py").read_text(encoding="utf-8")
        self.assertNotIn("scope_provider", package_source)
        self.assertNotIn("ScopeProvider", package_source)
        for path in HERITAGE.glob("*.py"):
            self.assertNotIn("from .scope_provider", path.read_text(encoding="utf-8"))

    def test_display_context_freezes_inputs_and_copy_with_isolated(self):
        engine = PedigreeEngine({"A": {}}, lambda _name, _record: {})
        engine.build()
        levels = {"A": 0}
        family_nodes = {"family": {"children": ["A"]}}
        context = DisplayContext(
            engine=engine,
            display_nodes={"A"},
            levels=levels,
            family_nodes=family_nodes,
        )

        # Mutating the caller-owned inputs after construction cannot alter a
        # published render context.
        levels["A"] = 9
        family_nodes["family"]["children"].append("B")
        self.assertEqual(context.levels["A"], 0)
        self.assertEqual(tuple(context.family_nodes["family"]["children"]), ("A",))
        with self.assertRaises(TypeError):
            context.family_nodes["family"]["children"] = ("B",)

        replacement = {"family": {"children": ["B"]}}
        clone = context.copy_with(family_nodes=replacement)
        replacement["family"]["children"].append("C")
        self.assertEqual(tuple(context.family_nodes["family"]["children"]), ("A",))
        self.assertEqual(tuple(clone.family_nodes["family"]["children"]), ("B",))

    def test_stale_bytecode_is_archived_with_verified_hashes(self):
        active_cache = HERITAGE / "__pycache__"
        archive_root = ROOT / "archive" / "HT (phase II)"
        inventory = archive_root / "INVENTAR.md"
        recovery = archive_root / "WIEDERHERSTELLUNG.md"
        inventory_text = inventory.read_text(encoding="utf-8")
        self.assertIn("SHA-256", inventory_text)
        self.assertIn("INVENTAR.md", recovery.read_text(encoding="utf-8"))
        for name in STALE_NAMES:
            self.assertFalse((active_cache / name).exists(), name)
            archived = archive_root / "stale_pycache" / "Plugins" / "Heritage_Track" / "__pycache__" / name
            self.assertTrue(archived.exists(), name)
            digest = hashlib.sha256(archived.read_bytes()).hexdigest()
            self.assertRegex(inventory_text, rf"\|.*{re.escape(name)}.*\| `{digest}` \|")

    def test_windows_packaging_payload_has_no_archive_boundary(self):
        script = (ROOT / "source" / "launcher" / "windows" / "package_release.ps1").read_text(encoding="utf-8")
        payload_block = script.split("$payloadPaths = @(", 1)[1].split(")", 1)[0]
        self.assertNotRegex(payload_block, r"(?i)archive|__pycache__")


if __name__ == "__main__":
    unittest.main()
