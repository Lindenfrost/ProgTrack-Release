# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Dimitri L. Lindenwald and Deutsches Primatenzentrum GmbH
# Part of: ProgTrack 0.2.2
# Module: shared helpers for rewriting backend-resident animal references.

"""Reference rewriting helpers for explicit legacy-archive maintenance.

The application never calls these helpers for runtime persistence: the
configured backend remains the sole mutable data authority.  The filesystem
functions are deliberately kept as small, opt-in tools for maintaining an
external legacy archive and for deterministic seed-authoring tests.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable


def safe_medi_document_folder_name(animal_key: str) -> str:
    """Return the historical attachment-folder name for an animal key."""
    text = str(animal_key or "").strip()
    return "".join(
        char if char.isalnum() or char in "-_()" else "_"
        for char in text
    ).strip("._") or "animal"


def replace_exact_animal_reference(value: Any, old_key: str, new_key: str) -> Any:
    """Recursively replace exact animal-key references in a data structure."""
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


def _archive_path(root: Path, relative: str) -> Path:
    """Resolve an archive-relative path and reject traversal outside *root*."""
    base = Path(root).resolve()
    path = (base / relative).resolve()
    if path != base and base not in path.parents:
        raise ValueError(f"Archive path escapes root: {relative}")
    return path


def rewrite_animal_reference_files(
    root: Path,
    relative_paths: Iterable[str],
    old_key: str,
    new_key: str,
    base_name: str,
) -> int:
    """Rewrite explicit JSON files in an external legacy archive.

    This is not a runtime fallback and is never used by the UI.  It exists for
    archive/seed preparation only; callers must provide the files explicitly.
    The return value is the number of files whose JSON content changed.
    """
    changed = 0
    for relative in relative_paths:
        path = _archive_path(Path(root), str(relative))
        if not path.is_file():
            continue
        try:
            original = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rewritten = replace_exact_animal_reference(original, old_key, new_key)
        backfill_reference_display_names(rewritten, new_key, base_name)
        if rewritten == original:
            continue
        path.write_text(
            json.dumps(rewritten, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        changed += 1
    return changed


def move_medi_document_folder(root: Path, old_key: str, new_key: str) -> bool:
    """Move one legacy medical-document folder inside an external archive."""
    docs_root = _archive_path(root, "Plugins/Medi_Track/medi_track")
    old_folder = docs_root / safe_medi_document_folder_name(old_key)
    new_folder = docs_root / safe_medi_document_folder_name(new_key)
    if not old_folder.is_dir():
        return False
    if old_folder == new_folder:
        return True
    new_folder.parent.mkdir(parents=True, exist_ok=True)
    if new_folder.exists():
        for child in old_folder.iterdir():
            destination = new_folder / child.name
            if destination.exists():
                stem, suffix = child.stem, child.suffix
                counter = 1
                while (new_folder / f"{stem}_{counter}{suffix}").exists():
                    counter += 1
                destination = new_folder / f"{stem}_{counter}{suffix}"
            shutil.move(str(child), str(destination))
        old_folder.rmdir()
    else:
        os.replace(old_folder, new_folder)
    return True
