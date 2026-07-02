# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.1.1
# Module: shared helpers for rewriting persisted animal references.

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable


def replace_exact_animal_reference(value: Any, old_key: str, new_key: str) -> Any:
    """Recursively replace exact animal-key references in JSON-like data."""
    if isinstance(value, dict):
        replaced: Dict[str, Any] = {}
        for key, child in value.items():
            mapped_key = new_key if key == old_key else key
            mapped_child = replace_exact_animal_reference(child, old_key, new_key)
            if mapped_key in replaced:
                existing = replaced[mapped_key]
                if isinstance(existing, dict) and isinstance(mapped_child, dict):
                    merged = mapped_child.copy()
                    merged.update(existing)
                    replaced[mapped_key] = merged
                elif not existing:
                    replaced[mapped_key] = mapped_child
                continue
            replaced[mapped_key] = mapped_child
        return replaced
    if isinstance(value, list):
        return [
            replace_exact_animal_reference(child, old_key, new_key)
            for child in value
        ]
    if isinstance(value, str) and value == old_key:
        return new_key
    return value


def backfill_reference_display_names(value: Any, animal_key: str, base_name: str) -> None:
    """Backfill short display names beside rewritten IPID references."""
    if isinstance(value, dict):
        has_ipid_reference = (
            value.get("ipid") == animal_key
            or value.get("animal") == animal_key
            or value.get("occupant_id") == animal_key
        )
        if has_ipid_reference:
            value.setdefault("ipid", animal_key)
            if not value.get("name") or value.get("name") == animal_key:
                value["name"] = base_name
        for child in value.values():
            backfill_reference_display_names(child, animal_key, base_name)
    elif isinstance(value, list):
        for child in value:
            backfill_reference_display_names(child, animal_key, base_name)


def rewrite_animal_reference_file(path: Path, old_key: str, new_key: str, base_name: str) -> bool:
    """Rewrite one JSON file if it contains exact old animal references."""
    if old_key == new_key or not path.is_file():
        return False
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception as exc:
        logging.warning("Could not read animal reference file %s: %s", path, exc)
        return False

    rewritten = replace_exact_animal_reference(data, old_key, new_key)
    backfill_reference_display_names(rewritten, new_key, base_name)
    if rewritten == data:
        return False

    fd, tmp_path = tempfile.mkstemp(prefix=f"{path.stem}_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(rewritten, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        return True
    except Exception as exc:
        logging.warning("Could not rewrite animal reference file %s: %s", path, exc)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass
        return False


def rewrite_animal_reference_files(
    base_dir: Path,
    relative_paths: Iterable[str],
    old_key: str,
    new_key: str,
    base_name: str,
) -> int:
    changed = 0
    for rel_path in relative_paths:
        if rewrite_animal_reference_file(base_dir / rel_path, old_key, new_key, base_name):
            changed += 1
    return changed


def safe_medi_document_folder_name(name: str) -> str:
    """Mirror Medi Track's document-folder sanitization."""
    safe = str(name).strip()
    for ch in r'/\:*?"<>|':
        safe = safe.replace(ch, "_")
    return safe or "unknown"


def move_medi_document_folder(base_dir: Path, old_key: str, new_key: str) -> bool:
    """Move Medi Track document folders when an animal IPID changes."""
    if old_key == new_key:
        return False
    docs_root = base_dir / "Plugins" / "Medi_Track" / "medi_track"
    old_folder = docs_root / safe_medi_document_folder_name(old_key)
    new_folder = docs_root / safe_medi_document_folder_name(new_key)
    if not old_folder.is_dir():
        return False
    try:
        docs_root.mkdir(parents=True, exist_ok=True)
        if not new_folder.exists():
            shutil.move(str(old_folder), str(new_folder))
            return True

        moved_any = False
        for child in old_folder.iterdir():
            target = new_folder / child.name
            if target.exists():
                stem = target.stem
                suffix = target.suffix
                counter = 1
                while target.exists():
                    target = new_folder / f"{stem}_{counter}{suffix}"
                    counter += 1
            shutil.move(str(child), str(target))
            moved_any = True
        try:
            old_folder.rmdir()
        except OSError:
            pass
        return moved_any
    except Exception as exc:
        logging.warning("Could not move Medi Track document folder %s -> %s: %s", old_folder, new_folder, exc)
        return False
