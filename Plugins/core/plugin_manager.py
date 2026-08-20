"""Manifest-driven plugin discovery, validation, and capability registry."""
from __future__ import annotations

import importlib
import importlib.util
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)
_VERSION_RE = re.compile(r"(?:rc\s*)?(\d+)(?:\.(\d+))?(?:\.(\d+))?", re.I)
REQUIRED_KEYS = frozenset({
    "name", "display_name", "version", "description", "author",
    "SPDX-License-Identifier", "entry_point", "dependencies",
    "data_files", "min_progtrack_version", "menu_location", "icon", "optional",
    "capabilities", "permissions", "backend_namespaces", "resources", "docs",
})

@dataclass
class ManifestDiagnostic:
    plugin_id: str
    path: str
    valid: bool
    optional: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    manifest: dict[str, Any] = field(default_factory=dict)

    @property
    def capabilities(self) -> tuple[str, ...]:
        raw = self.manifest.get("capabilities", self.manifest.get("features", []))
        return tuple(sorted(str(x).strip() for x in raw if str(x).strip())) if isinstance(raw, list) else ()

def version_tuple(value: Any) -> tuple[int, int, int]:
    match = _VERSION_RE.search(str(value or ""))
    if not match:
        return (-1, -1, -1)
    return tuple(int(part or 0) for part in match.groups())

def _module_and_attr(entry_point: str) -> tuple[str, str]:
    value = str(entry_point or "").strip()
    if "." not in value:
        raise ValueError("entry_point must be module.attribute")
    module, attr = value.rsplit(".", 1)
    if not module or not attr:
        raise ValueError("entry_point must be module.attribute")
    return module, attr

class PluginManager:
    """Discover every manifest and keep plugin capabilities deterministic."""
    def __init__(self, plugins_root: str | Path, *, app_version: str = "0.2.1"):
        self.plugins_root = Path(plugins_root)
        self.app_version = app_version
        self.diagnostics: dict[str, ManifestDiagnostic] = {}
        self.capability_registry: dict[str, tuple[str, ...]] = {}

    def discover(self) -> list[Path]:
        if not self.plugins_root.is_dir():
            return []
        return sorted(
            (p for p in self.plugins_root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()),
            key=lambda p: p.name.casefold(),
        )

    def validate_manifest(self, plugin_dir: Path, *, import_entry_point: bool = False) -> ManifestDiagnostic:
        manifest_path = plugin_dir / "manifest.json"
        plugin_id = plugin_dir.name
        errors: list[str] = []
        warnings: list[str] = []
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return ManifestDiagnostic(plugin_id, str(manifest_path), False, True, [f"invalid JSON: {exc}"], [], {})
        if not isinstance(data, dict):
            return ManifestDiagnostic(plugin_id, str(manifest_path), False, True, ["manifest root must be an object"], [], {})
        optional = bool(data.get("optional", True))
        missing = sorted(REQUIRED_KEYS - set(data))
        if missing:
            errors.append("missing required keys: " + ", ".join(missing))
        if str(data.get("name") or "").strip() != plugin_id:
            errors.append("name must match plugin directory")
        if version_tuple(data.get("version")) < (0, 0, 0):
            errors.append("version is not parseable")
        minimum_version = version_tuple(data.get("min_progtrack_version"))
        if minimum_version < (0, 0, 0):
            errors.append("min_progtrack_version is not parseable")
        elif minimum_version > version_tuple(self.app_version):
            errors.append(
                f"requires ProgTrack {data.get('min_progtrack_version')}, "
                f"runtime is {self.app_version}"
            )
        if not isinstance(data.get("dependencies"), list):
            errors.append("dependencies must be a list")
        if not isinstance(data.get("data_files"), list):
            errors.append("data_files must be a list")
        for key in ("features", "capabilities", "permissions", "backend_namespaces", "resources", "docs"):
            value = data.get(key, [])
            if not isinstance(value, list):
                errors.append(f"{key} must be a list")
            elif any(not str(item or "").strip() for item in value):
                errors.append(f"{key} must contain non-empty strings")
        capabilities = data.get("capabilities", [])
        if isinstance(capabilities, list) and len(set(str(item).strip() for item in capabilities)) != len(capabilities):
            errors.append("capabilities must be unique")
        try:
            _module_and_attr(str(data.get("entry_point") or ""))
        except ValueError as exc:
            errors.append(str(exc))
        if data.get("bootstrap_entry_point"):
            try:
                _module_and_attr(str(data.get("bootstrap_entry_point") or ""))
            except ValueError as exc:
                errors.append("bootstrap_entry_point: " + str(exc))
        resource_patterns = []
        for key in ("data_files", "resources"):
            values = data.get(key, [])
            if isinstance(values, list):
                resource_patterns.extend(values)
        for pattern in resource_patterns:
            pattern = str(pattern or "").strip()
            if not pattern:
                continue
            if not list(plugin_dir.glob(pattern)):
                message = f"declared resource pattern has no match: {pattern}"
                if optional:
                    warnings.append(message)
                else:
                    errors.append(message)
        for dep in data.get("dependencies", []) if isinstance(data.get("dependencies"), list) else []:
            dep = str(dep or "").strip()
            if not dep:
                errors.append("empty dependency")
                continue
            if importlib.util.find_spec(dep) is None:
                message = f"dependency is not importable: {dep}"
                if optional:
                    warnings.append(message)
                else:
                    errors.append(message)
        if import_entry_point and not errors:
            try:
                module, attr = _module_and_attr(str(data["entry_point"]))
                imported = importlib.import_module(f"Plugins.{plugin_id}.{module}")
                entry_value = getattr(imported, attr, None)
                if entry_value is None:
                    errors.append(f"entry point attribute not found: {attr}")
                elif not callable(entry_value):
                    errors.append(f"entry point is not callable: {attr}")
                bootstrap = data.get("bootstrap_entry_point")
                if bootstrap:
                    bmodule, battr = _module_and_attr(str(bootstrap))
                    bimported = importlib.import_module(f"Plugins.{plugin_id}.{bmodule}")
                    bootstrap_value = getattr(bimported, battr, None)
                    if bootstrap_value is None:
                        errors.append(f"bootstrap entry point attribute not found: {battr}")
                    elif not callable(bootstrap_value):
                        errors.append(f"bootstrap entry point is not callable: {battr}")
            except Exception as exc:
                errors.append(f"entry point import failed: {exc}")
        return ManifestDiagnostic(plugin_id, str(manifest_path), not errors, optional, errors, warnings, data)

    def validate_all(self, *, import_entry_points: bool = False) -> dict[str, ManifestDiagnostic]:
        self.diagnostics = {}
        self.capability_registry = {}
        for plugin_dir in self.discover():
            diagnostic = self.validate_manifest(plugin_dir, import_entry_point=import_entry_points)
            self.diagnostics[diagnostic.plugin_id] = diagnostic
            for capability in diagnostic.capabilities:
                owners = list(self.capability_registry.get(capability, ()))
                owners.append(diagnostic.plugin_id)
                self.capability_registry[capability] = tuple(sorted(set(owners)))
            if not diagnostic.valid:
                level = logging.ERROR if not diagnostic.optional else logging.WARNING
                logger.log(level, "Plugin manifest %s invalid: %s", diagnostic.plugin_id, "; ".join(diagnostic.errors))
        return self.diagnostics

    def is_available(self, plugin_id: str) -> bool:
        diagnostic = self.diagnostics.get(plugin_id)
        return bool(diagnostic and diagnostic.valid)

    def required_failures(self) -> dict[str, ManifestDiagnostic]:
        return {key: value for key, value in self.diagnostics.items() if not value.optional and not value.valid}

    def capabilities_for(self, capability: str) -> tuple[str, ...]:
        return self.capability_registry.get(str(capability), ())

    def resolve_bootstrap(self, plugin_id: str) -> Any:
        """Resolve the declared bootstrap entry point for application startup."""
        diagnostic = self.diagnostics.get(plugin_id)
        if diagnostic is None or not diagnostic.valid:
            raise RuntimeError(f"Plugin {plugin_id} is unavailable.")
        entry = str(
            diagnostic.manifest.get("bootstrap_entry_point")
            or diagnostic.manifest.get("entry_point")
            or ""
        )
        module, attr = _module_and_attr(entry)
        imported = importlib.import_module(f"Plugins.{plugin_id}.{module}")
        return getattr(imported, attr)

    def resolve_entry_point(self, plugin_id: str) -> Any:
        diagnostic = self.diagnostics.get(plugin_id)
        if diagnostic is None or not diagnostic.valid:
            raise RuntimeError(f"Plugin {plugin_id} is unavailable.")
        module, attr = _module_and_attr(str(diagnostic.manifest["entry_point"]))
        imported = importlib.import_module(f"Plugins.{plugin_id}.{module}")
        return getattr(imported, attr)
